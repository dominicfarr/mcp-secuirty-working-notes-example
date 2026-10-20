"""MCP server reference implementations for clients with per-tool permissions."""

import argparse
import functools
import inspect
import json
import os
import sys
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
import warnings
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from mcp.server.request_state import RequestStateSecurity
from mcp.types import (ClientCapabilities, ElicitationCapability, ElicitRequest,
                       ElicitRequestFormParams, ElicitResult, InputRequiredResult)
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

# How long a confirmation stays valid. Nothing on the server waits for the answer (MCP 2026-07-28):
# the sealed requestState carries it, and the framework refuses it once this has passed. The
# environment variable is for the tests only; the demo uses 2 minutes.
CONFIRMATION_TTL_SECONDS = float(os.environ.get("MCP_DEMO_ELICITATION_TIMEOUT", "120"))

# A note a tool adds to its own audit record, e.g. the user's answer to a confirmation.
AUDIT_NOTE: ContextVar[str] = ContextVar("audit_note", default="")



class ModernProtocolOnly(Middleware):
    """Refuse handshake-era clients. Only they send initialize, and this server's confirmations
    use multi round-trip requests, which exist only on MCP 2026-07-28."""

    async def on_initialize(self, context, call_next):  # pylint: disable=unused-argument
        raise ToolError("this server speaks MCP 2026-07-28 only")


# requestState is sealed (encrypted, integrity-checked, bound to the request, expiring) because it
# passes through the client: the spec requires servers to treat it as attacker-controlled.
mcp = FastMCP("Permission-Based MCP Server",
              request_state_security=RequestStateSecurity.ephemeral(ttl=CONFIRMATION_TTL_SECONDS),
              middleware=[ModernProtocolOnly()])


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
            outcome = "input_required" if isinstance(result, InputRequiredResult) else "success"
            detail = ""
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
    """Resolve filepath under WORKSPACE_DIR, rejecting paths that escape it (e.g. '../', an
    absolute path, or a symlink pointing out)."""
    # realpath resolves '..' and symlinks; commonpath then requires the result to be inside the
    # workspace. (The idiom static analysers recognise as a path-traversal check.)
    base = os.path.realpath(WORKSPACE_DIR)
    resolved = os.path.realpath(os.path.join(base, filepath))
    if os.path.commonpath((base, resolved)) != base:
        raise ToolError(f"Access denied: {filepath} is outside the workspace")
    base, file_path = Path(base), Path(resolved)
    # "", "." or a folder would otherwise reach the tools as a directory
    if file_path == base or file_path.is_dir():
        raise ToolError(f"'{filepath}' is not a file path" if filepath
                        else "A file path is required")
    return file_path


# The folder the client launched this server with (--allowed-folder), or None for the whole
# workspace. It is server configuration: roots, which did this job as a request, are deprecated as
# of MCP 2026-07-28 (SEP-2577).
ALLOWED_FOLDER: Path | None = None


def allowed_area() -> Path | None:
    """Where the file tools may work: the launch folder's overlap with the workspace. A folder
    inside the workspace narrows it, one containing it gives exactly the workspace, and any other
    gives nothing (None)."""
    base = WORKSPACE_DIR.resolve()
    if ALLOWED_FOLDER is None:
        return base
    folder = ALLOWED_FOLDER.resolve()
    if folder.is_relative_to(base):
        return folder
    return base if base.is_relative_to(folder) else None


def resolve_allowed(filepath: str) -> Path:
    """resolve_in_workspace, then require the path to be inside the allowed folder."""
    file_path = resolve_in_workspace(filepath)
    area = allowed_area()
    if area is None or not file_path.is_relative_to(area):
        raise ToolError(f"Access denied: {filepath} is outside the allowed folder")
    return file_path


def parse_launch_arguments(argv: list[str]) -> Path | None:
    """The --allowed-folder the client launched this server with, if any."""
    parser = argparse.ArgumentParser(description="MCP server for the permission-client demo")
    parser.add_argument("--allowed-folder", type=Path, default=None,
                        help="the only folder (within the workspace) the file tools may use")
    return parser.parse_args(argv).allowed_folder


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


# The key of the one question delete_file asks; the answer comes back under the same key
CONFIRM_KEY = "confirm"


def ask_to_confirm(filepath: str, file_path: Path, ctx: Context) -> InputRequiredResult:
    """Round 1: ask the user, through the client, to confirm deleting file_path.

    What the answer must be checked against (the path and the file's identity) goes into
    requestState, which the framework seals: the client carries it but can't read or change it."""
    capability = ClientCapabilities(elicitation=ElicitationCapability())
    if not ctx.session.check_client_capability(capability):
        raise ToolError("Deletion needs confirmation, but this client can't be asked")
    if not file_path.exists():
        raise ToolError(f"File {filepath} not found")
    stat = file_path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    message = f"Confirm deletion of {filepath} ({human_size(stat.st_size)}, modified {modified})"
    AUDIT_NOTE.set(f"asked: {message}")
    question = ElicitRequest(params=ElicitRequestFormParams(
        message=message, requested_schema=DeleteConfirmation.model_json_schema()))
    state = {"path": str(file_path), "identity": list(file_identity(file_path))}
    return InputRequiredResult(input_requests={CONFIRM_KEY: question},
                               request_state=json.dumps(state))


def confirmed(filepath: str, answer: ElicitResult) -> DeleteConfirmation:
    """Round 2: check the user's answer. Raises ToolError unless the user accepted with a valid
    confirmation that matches the path."""
    if answer.action == "decline":
        raise ToolError("Deletion declined by the user")
    if answer.action != "accept":
        raise ToolError("Deletion cancelled")
    try:
        # The server checks the answer again: it can't trust the client's validation
        confirmation = DeleteConfirmation.model_validate(answer.content or {})
    except ValidationError as e:
        first = e.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        raise ToolError(f"Confirmation was not valid: {field}: {first['msg']}") from e
    if confirmation.confirm_name != filepath:  # a rule a schema can't express
        raise ToolError(f"Confirmation did not match: you typed '{confirmation.confirm_name}', "
                        f"expected '{filepath}'")
    return confirmation


def read_text_file(filepath: str) -> str:
    """A UTF-8 text file inside the allowed folder. Raises ToolError with a readable reason."""
    file_path = resolve_allowed(filepath)
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
def read_file(filepath: str) -> str:
    """
    Read a text file from the workspace.

    Args:
        filepath: Path to the file relative to the workspace
    """
    return read_text_file(filepath)


@mcp.tool()
@audited
def write_file(filepath: str, content: str) -> str:
    """
    Write text to a file in the workspace, replacing any existing content.

    Args:
        filepath: Path to the file relative to the workspace
        content: Content to write to the file
    """
    file_path = resolve_allowed(filepath)
    try:
        file_path.write_text(content, encoding="utf-8")
        return f"Successfully wrote to {filepath}"
    except OSError as e:
        raise ToolError(f"Error writing file {filepath}: {e.strerror}") from e


@mcp.tool()
@audited
def delete_file(filepath: str, ctx: Context) -> str | InputRequiredResult:
    """
    Delete a file from the workspace, after the user confirms.

    Args:
        filepath: Path to the file relative to the workspace
    """
    file_path = resolve_allowed(filepath)  # every round: the retry is a new, untrusted request
    answer = (ctx.input_responses or {}).get(CONFIRM_KEY)
    if not isinstance(answer, ElicitResult) or ctx.request_state is None:
        return ask_to_confirm(filepath, file_path, ctx)  # round 1, or an answer without state
    asked = json.loads(ctx.request_state)  # sealed: unchanged since round 1, and not expired
    confirmation = confirmed(filepath, answer)
    backup_note = "yes" if confirmation.keep_backup else "no"
    AUDIT_NOTE.set(f"answer: accept; reason: {confirmation.reason}; backup: {backup_note}")
    try:
        if str(file_path) != asked["path"] or list(file_identity(file_path)) != asked["identity"]:
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


# A resource is application-controlled: the user picks it and the client adds it to the
# model's context; the model can't fetch it. Same allowed-folder rule as read_file.
@mcp.resource("workspace://{filepath*}", mime_type="text/plain")
@audited
def workspace_file(filepath: str) -> str:
    """A text file in the workspace, for the user to attach to a conversation.

    Args:
        filepath: Path to the file relative to the workspace
    """
    return read_text_file(filepath)


@mcp.prompt()
def security_review(operation: str, risk_level: str) -> str:
    """
    Generate a security review prompt for an operation.

    Args:
        operation: The operation to review
        risk_level: The risk level (LOW, MEDIUM, HIGH, CRITICAL)
    """
    # A string is one user message (fastmcp 4 no longer accepts plain dicts as messages)
    return f"""Review this operation for security implications:

Operation: {operation}
Risk Level: {risk_level}

Please analyze:
1. What data or systems could be affected?
2. What are the potential security risks?
3. What safeguards should be in place?
4. Should this operation require user approval?
5. What should be logged for audit purposes?
"""


if __name__ == "__main__":
    ALLOWED_FOLDER = parse_launch_arguments(sys.argv[1:])
    mcp.run(transport="stdio")
