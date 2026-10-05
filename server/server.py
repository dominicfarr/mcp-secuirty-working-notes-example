"""MCP server reference implementations for clients with per-tool permissions."""

import functools
import inspect
import os
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
import warnings
from urllib.parse import unquote, urlparse
import anyio
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import CancelledElicitation, DeclinedElicitation
from mcp.shared.exceptions import McpError
from mcp.types import ClientCapabilities, ElicitationCapability, RootsCapability
from pydantic import BaseModel, Field, ValidationError
from audit import AuditLog

# Suppress deprecation warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

SERVER_DIR = Path(__file__).parent

# Server configuration: the folder this server may work in (the user's files). The file tools
# are confined to it. Never put server config or logs here.
WORKSPACE_DIR = SERVER_DIR.parent / "workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)

# Server-private, outside the workspace so tools can't read, rewrite or delete the audit trail.
LOG_DIR = SERVER_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Server-side audit trail: the operator's record of every tool call this server received.
# Deliberately not exposed to clients as a resource.
AUDIT_LOG = AuditLog(LOG_DIR / "server_audit.log", actor="server")

# How long to wait for the client to answer a roots/list request before refusing the call.
ROOTS_TIMEOUT_SECONDS = 5

# How long to wait for the user to answer a confirmation. The environment variable is for the
# check scripts only; the demo uses 2 minutes.
ELICITATION_TIMEOUT_SECONDS = float(os.environ.get("MCP_DEMO_ELICITATION_TIMEOUT", "120"))

# A note a tool adds to its own audit record, e.g. the user's answer to a confirmation.
AUDIT_NOTE: ContextVar[str] = ContextVar("audit_note", default="")

mcp = FastMCP("Permission-Based MCP Server")


def audited(func):
    """Log every call to a tool, including denied and failed ones, to the server audit log.

    Works for sync and async tools. FastMCP's Context argument is not a tool argument, so it is
    left out of the record.
    """
    signature = inspect.signature(func)

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        arguments = {name: value
                     for name, value in signature.bind(*args, **kwargs).arguments.items()
                     if not isinstance(value, Context)}
        note_token = AUDIT_NOTE.set("")
        outcome, detail = "error", "unexpected error"
        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            outcome, detail = "success", ""
            return result
        except ToolError as e:
            detail = str(e)
            raise
        finally:
            detail = "; ".join(part for part in (detail, AUDIT_NOTE.get()) if part)
            AUDIT_NOTE.reset(note_token)
            AUDIT_LOG.record(func.__name__, arguments, outcome, detail)

    return wrapper


def resolve_in_workspace(filepath: str) -> Path:
    """Resolve filepath under WORKSPACE_DIR, rejecting paths that escape it (e.g. '../')."""
    base = WORKSPACE_DIR.resolve()
    file_path = (WORKSPACE_DIR / filepath).resolve()
    if not file_path.is_relative_to(base):
        raise ToolError(f"Access denied: {filepath} is outside the workspace")
    # "", "." or a folder would otherwise reach the tools as a directory
    if file_path == base or file_path.is_dir():
        raise ToolError(f"'{filepath}' is not a file path" if filepath
                        else "A file path is required")
    return file_path


def allowed_areas(root_paths: list[Path]) -> list[Path]:
    """Overlap of each client root with the workspace. Roots only narrow the server's own limit:
    a root inside the workspace narrows it, a root containing it gives exactly the workspace,
    and any other root gives nothing."""
    base = WORKSPACE_DIR.resolve()
    areas = []
    for root in root_paths:
        root = root.resolve()
        if root.is_relative_to(base):
            areas.append(root)
        elif base.is_relative_to(root):
            areas.append(base)
    return areas


async def client_roots(ctx: Context) -> list[Path] | None:
    """The client's file:// roots as paths, or None if the client doesn't support roots."""
    if not ctx.session.check_client_capability(ClientCapabilities(roots=RootsCapability())):
        return None
    try:
        with anyio.fail_after(ROOTS_TIMEOUT_SECONDS):
            roots = await ctx.list_roots()
    except (McpError, TimeoutError, ValueError) as e:
        # ValueError includes pydantic's ValidationError: a malformed roots reply
        raise ToolError("Access denied: could not get the client's roots") from e
    paths = (file_uri_path(str(root.uri)) for root in roots)
    return [path for path in paths if path is not None]


def file_uri_path(uri: str) -> Path | None:
    """The local path of a file:// URI, or None if it doesn't name a local file: another scheme,
    another host (RFC 8089), or a path containing a NUL byte. (Path.from_uri rejects file:///.)"""
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    path = unquote(parsed.path) or "/"
    return None if "\x00" in path else Path(path)


async def resolve_in_roots(filepath: str, ctx: Context) -> Path:
    """resolve_in_workspace, then require the path to be inside one of the client's roots.

    A client without roots support gets the workspace limit alone; an empty list (or no file://
    roots) is a declaration of nothing, so every path is refused."""
    resolve_in_workspace(filepath)  # report path problems before asking the client
    roots = await client_roots(ctx)
    # Resolve again after the await: the filesystem may have changed while the client answered
    # (it controls how long that takes), and the path that was checked must be the path used.
    file_path = resolve_in_workspace(filepath)
    if roots is None:
        return file_path
    if not roots:
        raise ToolError("Access denied: the client declared no roots")
    if not any(file_path.is_relative_to(area) for area in allowed_areas(roots)):
        raise ToolError(f"Access denied: {filepath} is outside the client's roots")
    return file_path


class DeleteConfirmation(BaseModel):
    """What the server asks the user before deleting a file; sent as the elicitation schema."""
    confirm_name: str = Field(description="Type the file path to confirm")
    reason: str = Field(min_length=5, max_length=200,
                        description="Why it's being deleted (recorded in the server audit log)")
    keep_backup: bool = Field(default=False,
                              description="Keep a .bak copy instead of deleting outright")


def human_size(size: int) -> str:
    """A file size for people: 5 B, 2.3 KB, 1.4 MB."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def duration_text(seconds: float) -> str:
    """A timeout for people: '2 minutes', '3 seconds'."""
    return f"{seconds / 60:g} minutes" if seconds >= 60 else f"{seconds:g} seconds"


def file_identity(file_path: Path) -> tuple[int, int, int]:
    """What identifies the file the user was asked about: inode, size and modification time."""
    stat = file_path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def backup_path(file_path: Path) -> Path:
    """Where to keep a backup: name.bak, or name.<timestamp>.bak if that exists. Never
    overwrites an existing backup."""
    backup = file_path.with_name(f"{file_path.name}.bak")
    if backup.exists():
        backup = file_path.with_name(f"{file_path.name}.{datetime.now():%Y%m%d%H%M%S%f}.bak")
    return backup


async def confirm_deletion(filepath: str, file_path: Path, ctx: Context) -> DeleteConfirmation:
    """Ask the user, through the client, to confirm deleting file_path, then check the answer.

    Raises ToolError unless the user accepted with a valid confirmation that matches the path.
    """
    capability = ClientCapabilities(elicitation=ElicitationCapability())
    if not ctx.session.check_client_capability(capability):
        raise ToolError("Deletion needs confirmation, but this client can't be asked")
    stat = file_path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    message = f"Confirm deletion of {filepath} ({human_size(stat.st_size)}, modified {modified})"
    try:
        with anyio.fail_after(ELICITATION_TIMEOUT_SECONDS):
            answer = await ctx.elicit(message, response_type=DeleteConfirmation)
    except TimeoutError as e:
        waited = duration_text(ELICITATION_TIMEOUT_SECONDS)
        raise ToolError(f"Deletion cancelled (no answer within {waited})") from e
    except ValidationError as e:
        # The server checks the answer again: it can't trust the client's validation
        first = e.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        raise ToolError(f"Confirmation was not valid: {field}: {first['msg']}") from e
    if isinstance(answer, DeclinedElicitation):
        raise ToolError("Deletion declined by the user")
    if isinstance(answer, CancelledElicitation):
        raise ToolError("Deletion cancelled")
    confirmation = answer.data
    if confirmation.confirm_name != filepath:  # a rule a schema can't express
        raise ToolError(f"Confirmation did not match: you typed '{confirmation.confirm_name}', "
                        f"expected '{filepath}'")
    return confirmation


@mcp.tool()
@audited
async def read_file(filepath: str, ctx: Context) -> str:
    """
    Read a text file from the workspace.

    Args:
        filepath: Path to the file relative to the workspace
    """
    file_path = await resolve_in_roots(filepath, ctx)
    try:
        return file_path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise ToolError(f"File {filepath} not found") from e
    except UnicodeDecodeError as e:
        raise ToolError(f"Error reading file {filepath}: not valid UTF-8 text") from e
    except OSError as e:
        raise ToolError(f"Error reading file {filepath}: {e.strerror}") from e


@mcp.tool()
@audited
async def write_file(filepath: str, content: str, ctx: Context) -> str:
    """
    Write text to a file in the workspace, replacing any existing content.

    Args:
        filepath: Path to the file relative to the workspace
        content: Content to write to the file
    """
    file_path = await resolve_in_roots(filepath, ctx)
    try:
        file_path.write_text(content, encoding="utf-8")
        return f"Successfully wrote to {filepath}"
    except OSError as e:
        raise ToolError(f"Error writing file {filepath}: {e.strerror}") from e


@mcp.tool()
@audited
async def delete_file(filepath: str, ctx: Context) -> str:
    """
    Delete a file from the workspace, after the user confirms.

    Args:
        filepath: Path to the file relative to the workspace
    """
    file_path = await resolve_in_roots(filepath, ctx)
    if not file_path.exists():
        raise ToolError(f"File {filepath} not found")
    asked_about = file_identity(file_path)
    confirmation = await confirm_deletion(filepath, file_path, ctx)
    backup_note = "yes" if confirmation.keep_backup else "no"
    AUDIT_NOTE.set(f"answer: accept; reason: {confirmation.reason}; backup: {backup_note}")
    file_path = await resolve_in_roots(filepath, ctx)  # again: the user may have taken minutes
    try:
        if file_identity(file_path) != asked_about:  # delete only the file the user confirmed
            raise ToolError(f"File {filepath} changed while waiting for confirmation; not deleted")
        if confirmation.keep_backup:
            backup = backup_path(file_path)
            file_path.rename(backup)
            kept = backup.relative_to(WORKSPACE_DIR.resolve()).as_posix()
            return f"Deleted {filepath} (backup kept as {kept})"
        file_path.unlink()
        return f"Deleted {filepath}"
    except FileNotFoundError as e:
        raise ToolError(f"File {filepath} not found") from e
    except OSError as e:
        raise ToolError(f"Error deleting file {filepath}: {e.strerror}") from e


@mcp.tool()
@audited
def execute_command(command: str) -> str:
    """
    Simulate running a shell command. The command is logged, not executed.

    Args:
        command: The command to execute (simulated)
    """
    # Simulate command execution without actually running it
    return f"Simulated execution of command: {command}\n(Actual execution disabled for security)"


@mcp.prompt()
def security_review(operation: str, risk_level: str) -> list[dict]:
    """
    Generate a security review prompt for an operation.

    Args:
        operation: The operation to review
        risk_level: The risk level (LOW, MEDIUM, HIGH, CRITICAL)
    """
    return [
        {
            "role": "user",
            "content": f"""Review this operation for security implications:

Operation: {operation}
Risk Level: {risk_level}

Please analyze:
1. What data or systems could be affected?
2. What are the potential security risks?
3. What safeguards should be in place?
4. Should this operation require user approval?
5. What should be logged for audit purposes?
"""
        }
    ]


if __name__ == "__main__":
    mcp.run(transport="stdio")
