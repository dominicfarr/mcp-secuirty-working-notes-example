"""MCP client base class that enforces per-tool permissions."""

import os
import sys
import copy
import json
import uuid
import asyncio
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
import anyio
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import (CONNECTION_CLOSED, ElicitRequestParams, ElicitResult, ListRootsResult,
                       Root, TextContent)
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

# Client audit outcomes for the user's answer to a server's question (elicitation)
INPUT_OUTCOMES = {"accept": "INPUT ACCEPTED", "decline": "INPUT DECLINED",
                  "cancel": "INPUT CANCELLED"}
# Answer recorded in ToolOutcome.inputs when the client declined without asking the user
INPUT_AUTO_DECLINED = "declined automatically (the client can't present this form)"
# Field types the client can present as a form (MCP elicitation: flat, primitive fields)
SUPPORTED_FIELD_TYPES = ("string", "number", "integer", "boolean")
# How long the client waits for the user to answer a server's question: a little longer than the
# server waits (2 minutes, or MCP_DEMO_ELICITATION_TIMEOUT in the checks). While a question is
# open the MCP library reads nothing else from the server, so an abandoned form (a closed or
# reloaded page) must not hold the connection for ever.
QUESTION_TIMEOUT_SECONDS = float(os.environ.get("MCP_DEMO_ELICITATION_TIMEOUT", "120")) + 5


def innermost_error(error: BaseException) -> BaseException:
    """Unwrap nested exception groups to the first underlying exception."""
    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    return error


def is_supported_schema(schema: dict) -> bool:
    """A flat object of primitive fields: a form the client can present faithfully."""
    properties = schema.get("properties")
    if schema.get("type") != "object" or not isinstance(properties, dict):
        return False
    return all(isinstance(spec, dict) and spec.get("type") in SUPPORTED_FIELD_TYPES
               for spec in properties.values())


def schema_errors(schema: dict, content: dict) -> list[str]:
    """Readable problems with content against the server's schema; empty if it's valid."""
    errors = [f"{name}: required" for name in schema.get("required", []) if name not in content]
    for error in Draft202012Validator(schema).iter_errors(content):
        if error.validator != "required":
            field_name = ".".join(str(part) for part in error.path) or "form"
            errors.append(f"{field_name}: {error.message}")
    return errors


class InputInvalid(ValueError):
    """The user's answer breaks the server's schema; nothing was sent."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class InputRequest:
    """A question the server asked mid-call (elicitation/create), waiting for the user."""
    input_id: str
    server_name: str
    message: str
    schema: dict
    answer: asyncio.Future = field(repr=False, compare=False)


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
    inputs: list = field(default_factory=list)  # (question, answer, content) per server question
    input_request: InputRequest | None = None  # set while the call waits for the user


@dataclass
class CallInProgress:
    """A tool call running in the background, possibly paused on the server's questions."""
    request: ToolRequest
    reason: str
    asked_before: int
    task: asyncio.Task | None = None
    inputs: list = field(default_factory=list)


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
        self.server_name = ""
        # Questions from the server (elicitation) on their way to the call that's waiting
        self._questions: asyncio.Queue[InputRequest] = asyncio.Queue()
        self._open_questions: dict[str, tuple[InputRequest, CallInProgress]] = {}


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
            # stdio passes the server only a few safe variables; add the demo's own settings
            env={**get_default_environment(),
                 **{k: v for k, v in os.environ.items() if k.startswith("MCP_DEMO_")}}
        )
        try:
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write, list_roots_callback=self._list_roots,
                                         elicitation_callback=self._on_elicitation) as session:
                    initialized = await session.initialize()
                    self.server_name = initialized.serverInfo.name
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


    async def _on_elicitation(self, _context, params: ElicitRequestParams) -> ElicitResult:
        """The server asks the user something mid-call: hand it to the waiting call, then wait
        for the user's answer. A form the client can't present is declined automatically."""
        question = InputRequest(uuid.uuid4().hex[:8], self.server_name, params.message,
                                params.requestedSchema, asyncio.get_running_loop().create_future())
        if not is_supported_schema(question.schema):
            question.answer.set_result(ElicitResult(action="decline"))
        self._questions.put_nowait(question)
        try:
            return await asyncio.wait_for(asyncio.shield(question.answer),
                                          QUESTION_TIMEOUT_SECONDS)
        except TimeoutError:
            self._expire(question)
            return ElicitResult(action="cancel")

    def _expire(self, question: InputRequest):
        """Nobody answered in time: close the question so the session can carry on, record it,
        and log the call's outcome when it ends (no handler is waiting for it any more)."""
        if not question.answer.done():
            question.answer.set_result(ElicitResult(action="cancel"))
        _, call = self._open_questions.pop(question.input_id, (None, None))
        if call is None:
            return
        call.inputs.append((question.message, "cancel", None))
        self.audit_log.record(call.request.tool_name, {}, "INPUT CANCELLED",
                              f"no answer within {QUESTION_TIMEOUT_SECONDS:g} seconds: "
                              f"{question.message}", request_id=call.request.request_id)
        call.task.add_done_callback(lambda _task: self._finish_unattended(call))

    def _finish_unattended(self, call: CallInProgress):
        """Log the outcome of a call no handler is waiting for."""
        with contextlib.suppress(BaseException):
            self._finish(call)

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
        """Send an allowed request. It runs in the background so the server can ask the user
        questions; returns when it finishes or when a question needs an answer."""
        self.log_audit(request, "ALLOWED", reason)
        call = CallInProgress(request, reason, self._last_roots_answer[0])
        call.task = asyncio.create_task(self._call_server(request))
        return await self._follow(call)

    async def _call_server(self, request: ToolRequest):
        """The tools/call itself; returns the server's result."""
        async with self._server_call() as session:
            return await session.call_tool(request.tool_name, arguments=request.arguments)

    async def _follow(self, call: CallInProgress) -> ToolOutcome:
        """Wait until the call finishes or the server asks the user something."""
        while True:
            next_question = asyncio.ensure_future(self._questions.get())
            try:
                done, _ = await asyncio.wait({call.task, next_question},
                                             return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                # The handler waiting on this call went away: log the outcome when it ends
                call.task.add_done_callback(lambda _task: self._finish_unattended(call))
                raise
            finally:
                # Never leave a reader on the shared queue: it would take the next question
                if not next_question.done():
                    next_question.cancel()
            if next_question not in done:
                return self._finish(call)
            question = next_question.result()
            if question.answer.done():  # declined automatically
                call.inputs.append((question.message, INPUT_AUTO_DECLINED, None))
                self.audit_log.record(call.request.tool_name, {}, INPUT_OUTCOMES["decline"],
                                      f"{INPUT_AUTO_DECLINED}: {question.message}",
                                      request_id=call.request.request_id)
                continue
            self._open_questions[question.input_id] = (question, call)
            return ToolOutcome(call.request, "ALLOWED", call.reason,
                               roots_answered=self._roots_answered_since(call.asked_before),
                               inputs=list(call.inputs), input_request=question)

    def _finish(self, call: CallInProgress) -> ToolOutcome:
        """The call has ended: log and return what the server reported."""
        request = call.request
        roots = self._roots_answered_since(call.asked_before)
        try:
            result = call.task.result()
        except (McpError, OSError) as e:
            detail = f"MCP error: {e}"
            self.log_audit(request, "ERROR", detail)
            error = [TextContent(type="text", text=detail)]
            return ToolOutcome(request, "ALLOWED", call.reason, error, True, roots, call.inputs)
        except BaseException:
            self.log_audit(request, "ERROR", "call failed before the server responded")
            raise
        if result.isError:
            first = result.content[0] if result.content else None
            detail = first.text if isinstance(first, TextContent) else "server reported an error"
            self.log_audit(request, "ERROR", detail)
        else:
            self.log_audit(request, "COMPLETED", "")
        return ToolOutcome(request, "ALLOWED", call.reason, result.content, result.isError,
                           roots, call.inputs)

    async def answer_input(self, input_id: str, action: str,
                           content: dict | None = None) -> ToolOutcome:
        """Answer the server's question and resume the call.

        KeyError if the question isn't waiting. InputInvalid if accepted content breaks the
        server's schema: nothing is sent and the question stays open."""
        if action not in INPUT_OUTCOMES:
            raise ValueError(f"action must be one of {sorted(INPUT_OUTCOMES)}")
        question, call = self._open_questions[input_id]
        content = content if action == "accept" else None
        if action == "accept":
            errors = schema_errors(question.schema, content or {})
            if errors:
                raise InputInvalid(errors)
        # If the server has already given up (timeout), it ignores this answer and the call's
        # result says so; the protocol gives the client no signal before it answers.
        del self._open_questions[input_id]
        if question.answer.done():  # closed meanwhile (the client stopped waiting)
            raise KeyError(input_id)
        self.audit_log.record(call.request.tool_name, content or {}, INPUT_OUTCOMES[action],
                              question.message, request_id=call.request.request_id)
        call.inputs.append((question.message, action, content))
        question.answer.set_result(ElicitResult(action=action, content=content))
        return await self._follow(call)

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
