"""MCP client base class that enforces per-tool permissions."""

import sys
import copy
import json
import uuid
import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path
import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED, ListRootsResult, Root, TextContent
from pydantic import AnyUrl
from permissions import PERMISSIONS_FILE, load_permissions
from audit import AuditLog

POLICIES = ("allow", "ask", "deny")

# The project folder, and the folder the client declares as its root by default: the user's
# files (a real host would use the folder the user opened).
PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = PROJECT_DIR / "workspace"

REASON_AWAITING = "awaiting approval"
REASON_USER_APPROVED = "user approved"
REASON_POLICY_CHANGED = "policy changed to deny"
REASON_REJECTED = "rejected by user"


def innermost_error(error: BaseException) -> BaseException:
    """Unwrap nested exception groups to the first underlying exception."""
    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    return error


@dataclass(frozen=True)
class ToolRequest:
    """A tool call exactly as requested; frozen so an approval can't be redirected."""
    request_id: str
    tool_name: str
    arguments: dict


@dataclass
class ToolOutcome:
    """The client's decision on a ToolRequest and, if it was sent, what the server returned."""
    request: ToolRequest
    decision: str  # "ALLOWED" | "DENIED" | "ASK" | "REJECTED"
    reason: str
    content: list | None = None
    is_error: bool = False
    roots_answered: list[str] | None = None  # roots the client sent when the server asked


class MCPPermissionClient:
    """Base MCP client with permission checking and audit logging."""

    def __init__(self, server_script: str, permissions_file: str | Path = PERMISSIONS_FILE):
        self.server_script = server_script
        self.permissions_file = Path(permissions_file)
        self.permissions_file.parent.mkdir(exist_ok=True)
        self.audit_log = AuditLog(self.permissions_file.parent / "client_audit.log",
                                  actor="client")
        self.session: ClientSession | None = None
        self._holder: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self.permissions = load_permissions(self.permissions_file)
        self.pending: dict[str, ToolRequest] = {}
        self.roots: list[Path] = [DEFAULT_ROOT]
        # (how many times the server has asked for roots, what the client last answered)
        self._last_roots_answer: tuple[int, list[str]] = (0, [])


    async def _get_session(self) -> ClientSession:
        """Connect if needed and return an active session"""
        await self.connect()
        if self.session is None:
            raise RuntimeError("MCP session not initialised")
        return self.session

    async def connect(self):
        """Connect to the MCP server via STDIO. Safe to call multiple times."""
        async with self._connect_lock:
            if self.session is not None:
                return
            ready = asyncio.get_running_loop().create_future()
            self._holder = asyncio.create_task(self._hold_connection(ready))
            self.session = await ready

    async def _hold_connection(self, ready: asyncio.Future):
        """Open the stdio connection, hand the session to connect(), and hold it until cancelled.

        anyio requires a connection to be closed in the task that opened it, and Gradio runs
        each event in its own task, so one dedicated task owns the connection for its lifetime.
        """
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[self.server_script],
            env=None
        )
        try:
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write,
                                         list_roots_callback=self._list_roots) as session:
                    await session.initialize()
                    ready.set_result(session)
                    await asyncio.get_running_loop().create_future()  # wait for disconnect()
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Not ours to handle: pass it to connect(), waiting in another task. The stdio
            # transport wraps errors in exception groups, so unwrap to the real one.
            if not ready.done():
                ready.set_exception(innermost_error(e))
        finally:
            self.session = None

    async def disconnect(self):
        """Close the server connection, if any. The next call reconnects."""
        holder, self._holder = self._holder, None
        self.session = None
        if holder is not None and not holder.done():
            holder.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await holder

    @contextlib.asynccontextmanager
    async def _server_call(self):
        """Yield the session; if the connection turns out to be dead, drop it so the next
        call reconnects, and raise ConnectionError."""
        session = await self._get_session()
        try:
            yield session
        except (anyio.ClosedResourceError, anyio.BrokenResourceError) as e:
            await self.disconnect()
            raise ConnectionError("connection to the MCP server was lost") from e
        except McpError as e:
            if e.error.code == CONNECTION_CLOSED:
                await self.disconnect()
            raise

    def save_permissions(self):
        """Save current permissions to file."""
        self.permissions_file.write_text(json.dumps(self.permissions, indent=2),encoding="utf-8")

    def check_permission(self, tool_name: str, arguments: dict) -> str:
        """
        Check permission for a tool call.

        Returns: "allow", "deny", or "ask"
        """
        # Check for argument-specific permission
        arg_key = f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"
        if arg_key in self.permissions:
            return self.permissions[arg_key]

        # Check for general tool permission
        return self.permissions.get(tool_name, "ask")

    def log_audit(self, request: ToolRequest, decision: str, reason: str = ""):
        """Log a decision or outcome for a request to the client audit log."""
        self.audit_log.record(request.tool_name, request.arguments, decision, reason,
                              request_id=request.request_id)

    def set_roots(self, paths: list[str | Path]) -> list[str]:
        """Declare the folders the server may work in, and audit the change.

        Relative paths resolve against the project folder and ~ expands. The server asks for
        roots on every file tool call, so this applies to the next call. Returns the URIs."""
        self.roots = [self._resolve_root(path) for path in paths]
        uris = self.root_uris()
        self.audit_log.record("(roots)", {"roots": uris}, "ROOTS DECLARED")
        return uris

    def root_uris(self) -> list[str]:
        """The current roots as the file:// URIs sent to the server."""
        return [path.as_uri() for path in self.roots]

    @staticmethod
    def _resolve_root(path: str | Path) -> Path:
        """A typed root as an absolute, resolved path."""
        path = Path(path).expanduser()
        return (path if path.is_absolute() else PROJECT_DIR / path).resolve()

    async def _list_roots(self, _context) -> ListRootsResult:
        """Answer the server's roots/list request with the current roots, and remember the answer
        so the tool call in progress can show that the server asked."""
        self._last_roots_answer = (self._last_roots_answer[0] + 1, self.root_uris())
        return ListRootsResult(roots=[Root(uri=path.as_uri(), name=path.name or str(path))
                                      for path in self.roots])

    async def request_elicitation(self, schema: dict, description: str) -> dict:
        """
        Request structured user input via elicitation.

        This is a conceptual implementation. In production, this would
        trigger a UI dialog and wait for user response.

        Args:
            schema: JSON schema for the required input
            description: Human-readable description of what's needed

        Returns:
            Dictionary with user's input
        """
        # Conceptual: In real implementation, this would show a UI dialog
        # and block until user provides input or cancels
        print(f"\nElicitation requested: {description}")
        print(f"Schema: {json.dumps(schema, indent=2)}")
        print("(Conceptual - automatic approval for demo)")

        # For demo purposes, return empty dict (representing user approval)
        return {}

    async def list_tools(self):
        """List all available tools from the server."""
        async with self._server_call() as session:
            result = await session.list_tools()
        return result.tools

    async def request_tool(self, tool_name: str, arguments: dict | None = None) -> ToolOutcome:
        """Check a tool call against policy: send it, refuse it, or hold it for approval."""
        request = ToolRequest(uuid.uuid4().hex[:8], tool_name, copy.deepcopy(arguments or {}))
        permission = self.check_permission(tool_name, request.arguments)

        if permission not in POLICIES:
            # Fail closed: a typo such as "Deny" must never be treated as allow
            reason = f"policy: invalid value {permission!r}"
            self.log_audit(request, "DENIED", reason)
            return ToolOutcome(request, "DENIED", reason)

        if permission == "deny":
            self.log_audit(request, "DENIED", "policy: deny")
            return ToolOutcome(request, "DENIED", "policy: deny")

        if permission == "ask":
            self.pending[request.request_id] = request
            self.log_audit(request, "ASK", REASON_AWAITING)
            # Return a copy: the stored request must not be changeable through the outcome
            return ToolOutcome(copy.deepcopy(request), "ASK", REASON_AWAITING)

        return await self._send(request, f"policy: {permission}")

    async def approve(self, request_id: str) -> ToolOutcome:
        """Send a pending request exactly as it was made, once. KeyError if not pending."""
        request = self.pending.pop(request_id)
        if self.check_permission(request.tool_name, request.arguments) not in ("allow", "ask"):
            self.log_audit(request, "DENIED", REASON_POLICY_CHANGED)
            return ToolOutcome(request, "DENIED", REASON_POLICY_CHANGED)
        return await self._send(request, REASON_USER_APPROVED)

    def reject(self, request_id: str) -> ToolOutcome:
        """Discard a pending request without sending it. KeyError if not pending."""
        request = self.pending.pop(request_id)
        self.log_audit(request, "REJECTED", REASON_REJECTED)
        return ToolOutcome(request, "REJECTED", REASON_REJECTED)

    def _roots_answered_since(self, asked_before: int) -> list[str] | None:
        """What the client answered if the server asked for roots after asked_before, else None.

        One request at a time (as in the GUI) keeps this unambiguous."""
        asked, uris = self._last_roots_answer
        return uris if asked > asked_before else None

    async def _send(self, request: ToolRequest, reason: str) -> ToolOutcome:
        """Send an allowed request and log what the server reported."""
        self.log_audit(request, "ALLOWED", reason)

        # Record the outcome so this log is complete on its own
        outcome, detail = "ERROR", "call failed before the server responded"
        asked_before = self._last_roots_answer[0]
        try:
            async with self._server_call() as session:
                result = await session.call_tool(request.tool_name, arguments=request.arguments)
            if result.isError:
                first = result.content[0] if result.content else None
                detail = (first.text if isinstance(first, TextContent)
                          else "server reported an error")
            else:
                outcome, detail = "COMPLETED", ""
            return ToolOutcome(request, "ALLOWED", reason, result.content, result.isError,
                               self._roots_answered_since(asked_before))
        except (McpError, OSError) as e:
            detail = f"MCP error: {e}"
            error = [TextContent(type="text", text=detail)]
            return ToolOutcome(request, "ALLOWED", reason, error, True,
                               self._roots_answered_since(asked_before))
        finally:
            self.log_audit(request, outcome, detail)

    async def list_resources(self):
        """List all available resources from the server."""
        async with self._server_call() as session:
            result = await session.list_resources()
        return result.resources

    async def read_resource(self, uri: str):
        """Read a resource by URI."""
        async with self._server_call() as session:
            result = await session.read_resource(uri=AnyUrl(uri))
        return result.contents

    async def list_prompts(self):
        """List all available prompts from the server."""
        async with self._server_call() as session:
            result = await session.list_prompts()
        return result.prompts

    async def get_prompt(self, prompt_name: str, arguments: dict | None = None):
        """Get a rendered prompt template."""
        if arguments is None:
            arguments = {}
        async with self._server_call() as session:
            result = await session.get_prompt(name=prompt_name, arguments=arguments)
        return result.messages

    async def cleanup(self):
        """Clean up resources."""
        await self.disconnect()
