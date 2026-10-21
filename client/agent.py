"""The host's turn loop: a model calls tools, and every call goes through the client.

The model sees every tool (denied ones too), tool descriptions and results reach it verbatim,
and resources and prompts enter its context only when the user picks them. The server's
questions go to the user and never into the model's context: the model sees only each call's
final result.

Part 2's defences sit on top of Part 1's controls, decided outside the model (on by default;
both off is the baseline the attacks land on):
- Rule A: once untrusted content (a tool result, an attached resource, a server prompt) has
  entered the chat, every tool but a read-only one asks, until New chat.
- Rule B: a call the user said no to (rejected, or the server's question declined or cancelled)
  is refused unasked if the model repeats it identically in the same turn."""

import contextlib
import json
from dataclasses import dataclass

from client import InputInvalid, MCPPermissionClient, ToolOutcome
from model import AssistantMessage, ChatModel, ToolCall, tool_specs

SYSTEM_PROMPT = "You help the user work with files in their workspace using the tools provided."
TOOL_ROUNDS_MAX = 8  # robustness (cost, runaway loops), not a defence
DENIED = "Denied by client policy"
REJECTED = "Rejected by the user"
RULE_B_REFUSED = "Rejected by the user earlier in this turn; not asked again"
BUSY = "answer or reject the pending request first"
RUNNING = "the model is still working on the last message"
QUESTION_CLOSED = "Error: the server's question was closed before it was answered"
STOPPED = f"(stopped after {TOOL_ROUNDS_MAX} tool rounds)"
WAITING_APPROVAL = "waiting for your approval"
WAITING_ANSWER = "the server is asking you a question"


@dataclass
class TurnResult:
    """Where a turn stands: finished with a reply, paused on the user, or failed."""
    reply: str = ""
    paused: ToolOutcome | None = None  # an approval (decision ASK) or a server question
    error: str = ""


@dataclass(frozen=True)
class Step:
    """One tool call as the chat shows it: what the model asked for and what came of it."""
    tool: str
    arguments_json: str
    result: str


def result_text(outcome: ToolOutcome) -> str:
    """What a finished call gives the model: the server's text, verbatim (an error prefixed)."""
    text = "\n".join(block.text for block in outcome.content or [] if hasattr(block, "text"))
    return f"Error: {text}" if outcome.is_error else text


def resource_text(uri: str, contents) -> str:
    """An attached resource as it joins the user's message: its text, verbatim. Binary
    contents are never decoded into the context."""
    parts = [c.text if hasattr(c, "text") else f"[binary content from {uri} not included]"
             for c in contents]
    return f"Attached resource {uri}:\n" + "\n".join(parts)


def assistant_entry(reply: AssistantMessage) -> dict:
    """The model's reply as a chat-completions message."""
    entry = {"role": "assistant", "content": reply.text}
    if reply.tool_calls:
        entry["tool_calls"] = [{"id": c.id, "type": "function",
                                "function": {"name": c.name, "arguments": c.arguments_json}}
                               for c in reply.tool_calls]
    return entry


class Agent:
    """One conversation between the user, a model, and the server's tools."""

    def __init__(self, client: MCPPermissionClient, model: ChatModel):
        self.client, self.model = client, model
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.paused: ToolOutcome | None = None
        self._queue: list[ToolCall] = []  # the current reply's tool calls not yet answered
        self._rounds = 0
        self._attachments: list[str] = []
        self._tool_names: set[str] = set()
        self._steps: list[Step] = []
        self._running = False  # one turn at a time: the context must stay a valid sequence
        self.rule_a = True  # changes ask once untrusted content is in the chat
        # Whether server text (a tool result, an attached resource, a server prompt) has entered
        # the model's context since New chat. Tracked whatever rule_a says, so switching the rule
        # on later still covers what was read before.
        self._untrusted = False
        self.rule_b = True  # a call the user said no to isn't asked again in the same turn
        self._refused: list[tuple[str, dict]] = []  # this turn's calls the user said no to

    async def send(self, text: str) -> TurnResult:
        """The user's message (with anything attached) starts a turn."""
        return await self._exclusive(self._send, text)

    async def use_prompt(self, name: str, arguments: dict) -> TurnResult:
        """A server prompt the user picked: its messages join the conversation with their
        declared roles, and the model replies."""
        return await self._exclusive(self._use_prompt, name, arguments)

    async def attach_resource(self, uri: str) -> str:
        """Read a resource the user chose; it joins their next message. Returns what was
        attached. Raises (and attaches nothing) if the server refuses."""
        attached = resource_text(uri, await self.client.read_resource(uri))
        self._attachments.append(attached)
        return attached

    async def approve(self, request_id: str) -> TurnResult:
        """The user approved the call they were shown (by id): the client sends its stored
        copy. RuntimeError if that call isn't the one paused."""
        return await self._exclusive(self._approve, request_id)

    async def reject(self, request_id: str) -> TurnResult:
        """The user rejected the call they were shown: nothing is sent; the model is told."""
        return await self._exclusive(self._reject, request_id)

    async def answer(self, input_id: str, action: str,
                     content: dict | None = None) -> TurnResult:
        """The user answered the server's question they were shown. InputInvalid leaves it
        open."""
        return await self._exclusive(self._answer, input_id, action, content)

    async def new_chat(self):
        """Start over. A paused approval is rejected and a paused question cancelled, both
        audited by the client. RuntimeError while a turn is running."""
        if self._running:
            raise RuntimeError(RUNNING)
        paused, self.paused = self.paused, None
        if paused is not None and paused.input_request is None:
            with contextlib.suppress(KeyError):
                self.client.reject(paused.request.request_id)
        elif paused is not None:
            with contextlib.suppress(KeyError, InputInvalid):
                await self.client.answer_input(paused.input_request.input_id, "cancel")
        self.messages = self.messages[:1]
        self._queue, self._attachments, self._rounds = [], [], 0
        self._untrusted = False

    def drain_steps(self) -> list[Step]:
        """The tool steps since the last call, for the chat to show."""
        steps, self._steps = self._steps, []
        return steps

    async def _exclusive(self, step, *args) -> TurnResult:
        """Run one step of a turn; refuse while another is running."""
        if self._running:
            return TurnResult(error=RUNNING)
        self._running = True
        try:
            return await step(*args)
        finally:
            self._running = False

    async def _send(self, text: str) -> TurnResult:
        if self.paused is not None:
            return TurnResult(error=BUSY)
        content = "\n\n".join([*self._attachments, text])
        self._untrusted = self._untrusted or bool(self._attachments)
        self._attachments = []
        self.messages.append({"role": "user", "content": content})
        return await self._start()

    async def _use_prompt(self, name: str, arguments: dict) -> TurnResult:
        if self.paused is not None:
            return TurnResult(error=BUSY)
        for message in await self.client.get_prompt(name, arguments):
            text = getattr(message.content, "text", "")
            self.messages.append({"role": message.role, "content": text})
            self._untrusted = True
        return await self._start()

    async def _approve(self, request_id: str) -> TurnResult:
        outcome = self._paused_on(question=False, shown_id=request_id)
        return await self._resume(self._settle(await self.client.approve(
            outcome.request.request_id)))

    async def _reject(self, request_id: str) -> TurnResult:
        outcome = self._paused_on(question=False, shown_id=request_id)
        self.client.reject(outcome.request.request_id)
        self.paused = None
        self._refused.append((outcome.request.tool_name, outcome.request.arguments))
        return await self._resume(REJECTED)

    async def _answer(self, input_id: str, action: str, content: dict | None) -> TurnResult:
        outcome = self._paused_on(question=True, shown_id=input_id)
        try:
            resumed = await self.client.answer_input(outcome.input_request.input_id, action,
                                                     content)
        except KeyError:  # closed meanwhile: timed out, or the server restarted
            self.paused = None
            return await self._resume(QUESTION_CLOSED)
        if action in ("decline", "cancel"):  # the user said no to this call
            self._refused.append((outcome.request.tool_name, outcome.request.arguments))
        return await self._resume(self._settle(resumed))

    async def _start(self) -> TurnResult:
        self._rounds, self._refused = 0, []
        return await self._run()

    async def _run(self) -> TurnResult:
        """Answer queued tool calls, then ask the model, until it replies with text, the user
        is needed, or the round cap is reached."""
        while True:
            while self._queue:
                result = await self._dispatch(self._queue[0])
                if result is None:
                    return TurnResult(paused=self.paused)
                self._answer_call(result)
            if self._rounds >= TOOL_ROUNDS_MAX:
                return TurnResult(reply=STOPPED)
            try:
                tools = tool_specs(await self.client.list_tools())
                self._tool_names = {spec["function"]["name"] for spec in tools}
                reply = await self.model.complete(self.messages, tools)
            except Exception as e:  # pylint: disable=broad-exception-caught
                return TurnResult(error=f"Model error: {e}")
            self.messages.append(assistant_entry(reply))
            if not reply.tool_calls:
                return TurnResult(reply=reply.text)
            self._rounds += 1
            self._queue = list(reply.tool_calls)

    async def _dispatch(self, call: ToolCall) -> str | None:
        """Send one tool call through the client. Returns the model's tool result, or None
        when the call is paused on the user."""
        if call.name not in self._tool_names:
            self.client.record_not_sent("(unknown tool)", {"name": call.name},
                                        f"unknown tool {call.name}", "model")
            return f"Error: unknown tool {call.name}"
        try:
            arguments = json.loads(call.arguments_json or "{}")
        except json.JSONDecodeError:
            arguments = None
        if not isinstance(arguments, dict):
            self.client.record_not_sent(call.name, {}, "arguments were not valid JSON", "model")
            return "Error: arguments were not valid JSON"
        if self.rule_b and (call.name, arguments) in self._refused:
            self.client.refuse_repeat(call.name, arguments, "model")
            return RULE_B_REFUSED
        return self._settle(await self.client.request_tool(
            call.name, arguments, source="model", untrusted_seen=self.rule_a and self._untrusted))

    def _settle(self, outcome: ToolOutcome) -> str | None:
        """The model's tool result for an outcome, or None (and paused) if the user is
        needed."""
        self.paused = None  # e.g. an approval the policy now denies is no longer paused
        if outcome.decision == "DENIED":
            return DENIED
        if outcome.decision == "REJECTED":
            return REJECTED
        if outcome.decision == "ASK" or outcome.input_request is not None:
            self.paused = outcome
            waiting = WAITING_ANSWER if outcome.input_request else WAITING_APPROVAL
            self._steps.append(Step(self._queue[0].name, self._queue[0].arguments_json, waiting))
            return None
        self._untrusted = True  # the server's text is about to enter the model's context
        return result_text(outcome)

    async def _resume(self, result: str | None) -> TurnResult:
        if result is None:
            return TurnResult(paused=self.paused)
        self._answer_call(result)
        return await self._run()

    def _answer_call(self, result: str):
        """Give the first queued call its tool result."""
        call = self._queue.pop(0)
        self._steps.append(Step(call.name, call.arguments_json, result))
        self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    def _paused_on(self, question: bool, shown_id: str) -> ToolOutcome:
        """The paused outcome, if it is the approval or question the user was shown."""
        paused = self.paused
        if paused is None or (paused.input_request is not None) != question:
            raise RuntimeError("nothing is waiting for that")
        waiting_id = paused.input_request.input_id if question else paused.request.request_id
        if waiting_id != shown_id:
            raise RuntimeError("that is not the request waiting now")
        return paused
