"""MCP server reference implementations for clients with per-tool permissions."""

import functools
import inspect
from pathlib import Path
import warnings
from urllib.parse import unquote, urlparse
import anyio
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError
from mcp.types import ClientCapabilities, RootsCapability
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
    Delete a file from the workspace.

    Args:
        filepath: Path to the file relative to the workspace
    """
    file_path = await resolve_in_roots(filepath, ctx)
    try:
        file_path.unlink()
        return f"Successfully deleted {filepath}"
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
