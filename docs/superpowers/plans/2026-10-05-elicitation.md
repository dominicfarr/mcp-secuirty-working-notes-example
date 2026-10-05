# Elicitation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `delete_file` asks the user, through the client, to confirm with structured input defined by a schema; the client builds a form from the schema, validates the answer, and resumes the paused call.

**Architecture:** The server describes the confirmation as a pydantic model, asks with `ctx.elicit()`, validates the answer again and checks rules a schema can't express. The client declares the elicitation capability, runs each tool call as a background task, and returns early when the server asks; `answer_input()` validates against the schema with `jsonschema` and resumes the call. The Tools tab renders the form with `@gr.render` from the received schema.

**Tech Stack:** Python 3.13, `mcp==1.16.0`, `fastmcp==2.12.5`, `gradio==5.49.1`, `pydantic==2.11.10`, `jsonschema==4.26.0` (already installed by `mcp`).

**Spec:** `docs/superpowers/specs/2026-10-05-elicitation-design.md`

## Global Constraints

- **No git commits.** The user commits; leave everything uncommitted.
- **No new dependencies.** `jsonschema` is already installed (a dependency of `mcp`); the README lists it because the client now imports it.
- Tests remain deferred: each change is verified by a check script in `.superpowers/sdd/2026-10-05-elicitation/`, run and seen to FAIL first. Check scripts run in temp copies of `client/`, `server/`, `workspace/`.
- Run Python as `mcp_security_env/bin/python`. Layout: `client/`, `server/`, `workspace/`.
- Never stop processes by name pattern. Before launching the app, check port 7863 (`lsof -nP -iTCP:7863 -sTCP:LISTEN`); if in use, skip. Stop only the PID you started.
- `client/host.py` is out of scope.
- Pylint 10/10 on each side:
  ```bash
  SP=$(mcp_security_env/bin/python -c "import site;print(site.getsitepackages()[0])"); for d in client server; do (cd $d && pylint --rcfile=../.pylintrc --init-hook="import sys; sys.path.insert(0,'$SP')" $(ls *.py | grep -v host.py)); done
  ```
- The confirmation timeout is 2 minutes. `MCP_DEMO_ELICITATION_TIMEOUT` (seconds) overrides it **for check scripts only**.
- Keep docs in sync (README and specs updated in Task 3).

## Review Focus

1. **The user answers after the server has given up** (timeout): nothing is deleted, and the client reports "not sent: the server is no longer waiting" with the server's "cancelled (no answer within …)" result. Pinned in Task 2.
2. **A client sends content that breaks the schema anyway** (buggy or hostile, bypassing client validation): the server validates again and refuses "Confirmation was not valid: …". Pinned in Task 1.
3. **A `.bak` file already exists**: the backup gets a timestamped name; the existing `.bak` is never overwritten. Pinned in Task 1.
4. **A required field is left empty in the form**: reported as "<field>: required"; the form stays open; nothing is sent. Pinned in Tasks 2 and 3.
5. **An integer field comes back from Gradio as a float** (`3.0`): converted to `3` so schema validation passes. Pinned in Task 3.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `server/server.py` | Modify | `DeleteConfirmation` model, `confirm_deletion()`, `human_size()`, `duration_text()`, `backup_path()`, `AUDIT_NOTE`; `delete_file` asks before deleting; `@audited` adds a tool's note. |
| `client/client.py` | Modify | `InputRequest`, `InputInvalid`, `CallInProgress`, `is_supported_schema()`, `schema_errors()`; elicitation callback; `_send()` runs the call in the background; `_follow()`, `_finish()`, `answer_input()`. |
| `.pylintrc` | Modify | `max-attributes=15` (client gains question state). |
| `client/tools_view.py` | Modify | `FormField`, `form_fields()`, `form_content()`, `input_header()`, `answer_line()`, `not_waiting_timeline()`; timeline shows questions and answers. |
| `client/app.py` | Modify | `field_widget()`, wider `flow_outputs()`, `gui_answer_input()`, `_build_input_card()` with `@gr.render`. |
| `README.md`, specs | Modify | Describe elicitation (Task 3). |

---

### Task 1: Server asks before deleting

**Files:**
- Modify: `server/server.py`
- Create: `.superpowers/sdd/2026-10-05-elicitation/elicitation_server_check.sh`

**Interfaces:**
- Consumes: existing `resolve_in_roots(filepath, ctx)`, `@audited`, `WORKSPACE_DIR`.
- Produces (observable over MCP; Tasks 2–3 rely on these):
  - Elicitation message: `"Confirm deletion of {filepath} ({size}, modified {YYYY-MM-DD HH:MM})"`; schema with properties `confirm_name` (string), `reason` (string, `minLength` 5, `maxLength` 200), `keep_backup` (boolean, default false); `required` = `["confirm_name", "reason"]`.
  - Results: `"Deleted {filepath}"`, `"Deleted {filepath} (backup kept as {relative backup path})"`.
  - Refusals: `"Deletion declined by the user"`, `"Deletion cancelled"`, `"Deletion cancelled (no answer within {duration})"`, `"Confirmation did not match: you typed '{x}', expected '{filepath}'"`, `"Confirmation was not valid: {field}: {message}"`, `"Deletion needs confirmation, but this client can't be asked"`, `"File {filepath} not found"`.
  - Server audit detail on success: `"answer: accept; reason: {reason}; backup: yes|no"`.

- [ ] **Step 1: Write the check script and run it to see it fail**

Create `.superpowers/sdd/2026-10-05-elicitation/elicitation_server_check.sh` (then `chmod +x`):

```bash
#!/bin/bash
# delete_file asks for confirmation through elicitation and checks the answer again.
REPO=/Users/dom/Projects/mcp-secuirty-working-notes-example
T=$(mktemp -d) && cp -R "$REPO/server" "$T"/ && rm -rf "$T/server/logs" && mkdir -p "$T/workspace"
(cd "$T" && MCP_DEMO_ELICITATION_TIMEOUT=3 "$REPO/mcp_security_env/bin/python" - > out.txt 2>&1 <<'PY'
import asyncio, json, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ElicitResult

WS = Path.cwd().resolve() / "workspace"
SEEN = []

def answer(action, content=None, delay=0):
    async def cb(context, params):
        SEEN.append((params.message, params.requestedSchema))
        await asyncio.sleep(delay)
        return ElicitResult(action=action, content=content)
    return cb

async def delete(cb, filepath):
    params = StdioServerParameters(command=sys.executable, args=["server/server.py"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w, elicitation_callback=cb) as s:
            await s.initialize()
            res = await s.call_tool("delete_file", {"filepath": filepath})
            return res.isError, res.content[0].text

def make(name, text="hello"):
    (WS / name).write_text(text)

def ok(name, backup=False):
    return {"confirm_name": name, "reason": "old draft", "keep_backup": backup}

async def main():
    make("a.txt")
    err, text = await delete(answer("accept", ok("a.txt")), "a.txt")
    assert not err and text == "Deleted a.txt" and not (WS / "a.txt").exists(), text
    message, schema = SEEN[-1]
    assert message.startswith("Confirm deletion of a.txt (5 B, modified "), message
    assert schema["required"] == ["confirm_name", "reason"], schema
    assert schema["properties"]["reason"]["minLength"] == 5, schema
    assert schema["properties"]["reason"]["maxLength"] == 200, schema
    assert schema["properties"]["keep_backup"]["type"] == "boolean", schema

    make("b.txt")
    err, text = await delete(answer("accept", ok("b.txt", True)), "b.txt")
    assert not err and "backup kept as b.txt.bak" in text, text
    assert (WS / "b.txt.bak").read_text() == "hello" and not (WS / "b.txt").exists()
    make("b.txt", "second")                                                     # Review Focus 3
    err, text = await delete(answer("accept", ok("b.txt", True)), "b.txt")
    assert not err and (WS / "b.txt.bak").read_text() == "hello", "existing backup overwritten"
    assert len(list(WS.glob("b.txt.*.bak"))) == 1, sorted(p.name for p in WS.iterdir())

    make("c.txt")
    cases = [
        (answer("decline"), "Deletion declined by the user"),
        (answer("cancel"), "Deletion cancelled"),
        (answer("accept", ok("other.txt")),
         "Confirmation did not match: you typed 'other.txt', expected 'c.txt'"),
        (answer("accept", {"confirm_name": "c.txt", "reason": "no"}),
         "Confirmation was not valid: reason"),                                # Review Focus 2
        (None, "Deletion needs confirmation, but this client can't be asked"),
        (answer("accept", ok("c.txt"), delay=10), "Deletion cancelled (no answer within 3 seconds)"),
    ]
    for cb, expected in cases:
        err, text = await delete(cb, "c.txt")
        assert err and expected in text and (WS / "c.txt").exists(), (expected, text)

    before = len(SEEN)
    err, text = await delete(answer("accept", ok("nope.txt")), "nope.txt")
    assert err and "File nope.txt not found" in text and len(SEEN) == before, "asked about a missing file"

    log = [json.loads(line) for line in Path("server/logs/server_audit.log").read_text().splitlines()]
    deletes = [e for e in log if e["tool"] == "delete_file"]
    assert "answer: accept; reason: old draft; backup: no" in deletes[0]["detail"], deletes[0]
    assert any("declined" in e["detail"] for e in deletes), deletes

asyncio.run(main())
print("ELICITATION SERVER OK")
PY
grep -q "ELICITATION SERVER OK" out.txt && echo "ELICITATION SERVER OK" || { echo "ELICITATION SERVER FAIL"; grep -E "Error|assert" out.txt | grep -v Warning | tail -2; })
rm -rf "$T"
```

Run: `.superpowers/sdd/2026-10-05-elicitation/elicitation_server_check.sh`
Expected: `ELICITATION SERVER FAIL` with an `AssertionError` on the first case (the file is deleted without a question, so `SEEN` is empty: `IndexError` or assertion on `SEEN[-1]`).

- [ ] **Step 2: Imports and constants**

In `server/server.py`, add to the standard-library imports:

```python
import os
from contextvars import ContextVar
from datetime import datetime
```

Add after `from fastmcp.exceptions import ToolError`:

```python
from fastmcp.server.elicitation import CancelledElicitation, DeclinedElicitation
```

Change `from mcp.types import ClientCapabilities, RootsCapability` to:

```python
from mcp.types import ClientCapabilities, ElicitationCapability, RootsCapability
from pydantic import BaseModel, Field, ValidationError
```

After `ROOTS_TIMEOUT_SECONDS = 5` add:

```python

# How long to wait for the user to answer a confirmation. The environment variable is for the
# check scripts only; the demo uses 2 minutes.
ELICITATION_TIMEOUT_SECONDS = float(os.environ.get("MCP_DEMO_ELICITATION_TIMEOUT", "120"))

# A note a tool adds to its own audit record, e.g. the user's answer to a confirmation.
AUDIT_NOTE: ContextVar[str] = ContextVar("audit_note", default="")
```

- [ ] **Step 3: Let `@audited` add a tool's note**

In `audited`'s `wrapper`, change the start and the `finally` so the wrapper reads:

```python
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
```

- [ ] **Step 4: Add the confirmation model and helpers before `@mcp.tool()` of `read_file`**

```python
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
```

- [ ] **Step 5: Replace `delete_file`**

```python
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
    confirmation = await confirm_deletion(filepath, file_path, ctx)
    backup_note = "yes" if confirmation.keep_backup else "no"
    AUDIT_NOTE.set(f"answer: accept; reason: {confirmation.reason}; backup: {backup_note}")
    file_path = await resolve_in_roots(filepath, ctx)  # again: the user may have taken minutes
    try:
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
```

- [ ] **Step 6: Run the check again**

Run: `.superpowers/sdd/2026-10-05-elicitation/elicitation_server_check.sh`
Expected: `ELICITATION SERVER OK`.

- [ ] **Step 7: Run the earlier checks and lint**

Run: `.superpowers/sdd/2026-10-03-roots/run_all.sh` — every line ends `OK`. (Earlier checks don't delete files through a client that answers questions; if one does, it now gets "can't be asked" or a question — record a ruling and adjust that check's expectation to the new spec behaviour.)
Run the Global Constraints pylint command — 10.00/10 for both.

- [ ] **Step 8: No commit** (user commits).

---

### Task 2: Client answers mid-call

**Files:**
- Modify: `client/client.py`, `.pylintrc`
- Create: `.superpowers/sdd/2026-10-05-elicitation/elicitation_client_check.sh`

**Interfaces:**
- Consumes: Task 1 server behaviour and messages.
- Produces (used by Task 3):
  - `client.InputRequest(input_id: str, server_name: str, message: str, schema: dict, answer: asyncio.Future)`
  - `client.InputInvalid(ValueError)` with `.errors: list[str]`
  - `client.INPUT_AUTO_DECLINED`, `client.INPUT_TOO_LATE` (strings used as answers in `ToolOutcome.inputs`)
  - `client.is_supported_schema(schema: dict) -> bool`, `client.schema_errors(schema: dict, content: dict) -> list[str]`
  - `ToolOutcome.inputs: list[tuple[str, str, dict | None]]` (question message, answer, content), `ToolOutcome.input_request: InputRequest | None`
  - `MCPPermissionClient.server_name: str`, `async answer_input(input_id: str, action: str, content: dict | None = None) -> ToolOutcome`

- [ ] **Step 1: Write the check script and run it to see it fail**

Create `.superpowers/sdd/2026-10-05-elicitation/elicitation_client_check.sh` (then `chmod +x`):

```bash
#!/bin/bash
# The client pauses a call when the server asks, validates the answer, and resumes it.
REPO=/Users/dom/Projects/mcp-secuirty-working-notes-example
T=$(mktemp -d) && cp -R "$REPO/client" "$REPO/server" "$T"/ && rm -rf "$T/server/logs" "$T"/client/*.log && mkdir -p "$T/workspace"
echo '{"delete_file": "allow"}' > "$T/client/permissions.json"
(cd "$T/client" && MCP_DEMO_ELICITATION_TIMEOUT=3 "$REPO/mcp_security_env/bin/python" - > out.txt 2>&1 <<'PY'
import asyncio, json
from pathlib import Path
from mcp.types import ElicitRequestParams
from client import MCPPermissionClient, InputInvalid, DEFAULT_ROOT, INPUT_TOO_LATE

async def main():
    c = MCPPermissionClient("../server/server.py")
    ws = DEFAULT_ROOT
    (ws / "a.txt").write_text("x")
    o = await c.request_tool("delete_file", {"filepath": "a.txt"})
    q = o.input_request
    assert q is not None and o.content is None and not o.is_error, o
    assert q.server_name == "Permission-Based MCP Server", q.server_name
    assert q.message.startswith("Confirm deletion of a.txt") and "reason" in q.schema["properties"]
    assert (ws / "a.txt").exists(), "paused: nothing deleted yet"

    for bad, field in (({"confirm_name": "a.txt", "reason": "no"}, "reason:"),
                       ({"reason": "old draft"}, "confirm_name: required")):        # Review Focus 4
        try:
            await c.answer_input(q.input_id, "accept", bad)
            raise AssertionError(f"invalid content accepted: {bad}")
        except InputInvalid as e:
            assert any(m.startswith(field) for m in e.errors), e.errors
    good = {"confirm_name": "a.txt", "reason": "old draft", "keep_backup": False}
    o = await c.answer_input(q.input_id, "accept", good)
    assert o.input_request is None and not o.is_error and "Deleted a.txt" in o.content[0].text, o
    assert o.inputs == [(q.message, "accept", good)], o.inputs
    try:
        await c.answer_input(q.input_id, "accept", good)
        raise AssertionError("answered twice")
    except KeyError:
        pass

    (ws / "b.txt").write_text("x")
    c.permissions["delete_file"] = "ask"
    o = await c.request_tool("delete_file", {"filepath": "b.txt"})
    o = await c.approve(o.request.request_id)
    assert o.input_request is not None, "approval followed by a question"
    o = await c.answer_input(o.input_request.input_id, "decline")
    assert o.is_error and "declined" in o.content[0].text and (ws / "b.txt").exists(), o

    c.permissions["delete_file"] = "allow"                                   # Review Focus 1
    o = await c.request_tool("delete_file", {"filepath": "b.txt"})
    await asyncio.sleep(4)                                                     # server gives up after 3 s
    late = {"confirm_name": "b.txt", "reason": "too late"}
    o = await c.answer_input(o.input_request.input_id, "accept", late)
    assert o.is_error and "no answer within 3 seconds" in o.content[0].text, o.content
    assert o.inputs[-1][1] == INPUT_TOO_LATE and (ws / "b.txt").exists(), o.inputs

    nested = {"type": "object", "properties": {"x": {"type": "object", "properties": {}}}}
    result = await c._on_elicitation(None, ElicitRequestParams(message="nested", requestedSchema=nested))
    assert result.action == "decline", result
    await c.cleanup()

    log = [json.loads(line) for line in Path("client_audit.log").read_text().splitlines()]
    outcomes = [e["outcome"] for e in log]
    assert "INPUT ACCEPTED" in outcomes and "INPUT DECLINED" in outcomes, outcomes
    accepted = next(e for e in log if e["outcome"] == "INPUT ACCEPTED")
    assert accepted["request_id"] and accepted["args"]["reason"] == "old draft", accepted

asyncio.run(main())
print("ELICITATION CLIENT OK")
PY
grep -q "ELICITATION CLIENT OK" out.txt && echo "ELICITATION CLIENT OK" || { echo "ELICITATION CLIENT FAIL"; grep -E "Error|assert" out.txt | grep -v Warning | tail -2; })
rm -rf "$T"
```

Run: `.superpowers/sdd/2026-10-05-elicitation/elicitation_client_check.sh`
Expected: `ELICITATION CLIENT FAIL` with `ImportError: cannot import name 'InputInvalid'`.

- [ ] **Step 2: Imports and constants**

In `client/client.py`:
- change `from dataclasses import dataclass` to `from dataclasses import dataclass, field`
- add after `import anyio`: `from jsonschema import Draft202012Validator`
- change the `mcp.types` import to:

```python
from mcp.types import (CONNECTION_CLOSED, ElicitRequestParams, ElicitResult, ListRootsResult,
                       Root, TextContent)
```

After `REASON_REJECTED = "rejected by user"` add:

```python

# Client audit outcomes for the user's answer to a server's question (elicitation)
INPUT_OUTCOMES = {"accept": "INPUT ACCEPTED", "decline": "INPUT DECLINED",
                  "cancel": "INPUT CANCELLED"}
# Answers recorded in ToolOutcome.inputs when the user didn't answer the server directly
INPUT_AUTO_DECLINED = "declined automatically (the client can't present this form)"
INPUT_TOO_LATE = "not sent: the server is no longer waiting"
# Field types the client can present as a form (MCP elicitation: flat, primitive fields)
SUPPORTED_FIELD_TYPES = ("string", "number", "integer", "boolean")
```

- [ ] **Step 3: Schema helpers and data types**

After `innermost_error`, add:

```python
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
```

Add two fields at the end of `ToolOutcome`:

```python
    inputs: list = field(default_factory=list)  # (question, answer, content) per server question
    input_request: InputRequest | None = None  # set while the call waits for the user
```

After `ToolOutcome`, add:

```python
@dataclass
class CallInProgress:
    """A tool call running in the background, possibly paused on the server's questions."""
    request: ToolRequest
    reason: str
    asked_before: int
    task: asyncio.Task | None = None
    inputs: list = field(default_factory=list)
```

- [ ] **Step 4: Client state, capability and callback**

In `__init__`, after `self._last_roots_answer: tuple[int, list[str]] = (0, [])` add:

```python
        self.server_name = ""
        # Questions from the server (elicitation) on their way to the call that's waiting
        self._questions: asyncio.Queue[InputRequest] = asyncio.Queue()
        self._open_questions: dict[str, tuple[InputRequest, CallInProgress]] = {}
```

In `_hold_connection`, replace

```python
                async with ClientSession(read, write,
                                         list_roots_callback=self._list_roots) as session:
                    await session.initialize()
```

with

```python
                async with ClientSession(read, write, list_roots_callback=self._list_roots,
                                         elicitation_callback=self._on_elicitation) as session:
                    initialized = await session.initialize()
                    self.server_name = initialized.serverInfo.name
```

After `_list_roots`, add:

```python
    async def _on_elicitation(self, _context, params: ElicitRequestParams) -> ElicitResult:
        """The server asks the user something mid-call: hand it to the waiting call, then wait
        for the user's answer. A form the client can't present is declined automatically."""
        question = InputRequest(uuid.uuid4().hex[:8], self.server_name, params.message,
                                params.requestedSchema, asyncio.get_running_loop().create_future())
        if not is_supported_schema(question.schema):
            question.answer.set_result(ElicitResult(action="decline"))
        self._questions.put_nowait(question)
        return await question.answer
```

- [ ] **Step 5: Run calls in the background**

Replace the whole `_send` method with:

```python
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
            done, _ = await asyncio.wait({call.task, next_question},
                                         return_when=asyncio.FIRST_COMPLETED)
            if next_question not in done:
                next_question.cancel()
                return self._finish(call)
            question = next_question.result()
            if question.answer.done():  # declined automatically
                call.inputs.append((question.message, INPUT_AUTO_DECLINED, None))
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
        del self._open_questions[input_id]
        if call.task.done():  # the server stopped waiting (timeout)
            question.answer.set_result(ElicitResult(action="cancel"))
            call.inputs.append((question.message, INPUT_TOO_LATE, None))
            return self._finish(call)
        self.audit_log.record(call.request.tool_name, content or {}, INPUT_OUTCOMES[action],
                              question.message, request_id=call.request.request_id)
        call.inputs.append((question.message, action, content))
        question.answer.set_result(ElicitResult(action=action, content=content))
        return await self._follow(call)
```

- [ ] **Step 6: Allow the extra client state in `.pylintrc`**

Change `max-attributes=12` to `max-attributes=15` and the comment above it to:

```ini
# The client holds connection, policy, audit, pending-approval, roots and question state.
```

- [ ] **Step 7: Run the check again**

Run: `.superpowers/sdd/2026-10-05-elicitation/elicitation_client_check.sh`
Expected: `ELICITATION CLIENT OK`.

- [ ] **Step 8: Run the server check, earlier checks and lint**

Run: `.superpowers/sdd/2026-10-05-elicitation/elicitation_server_check.sh` → `ELICITATION SERVER OK`.
Run: `.superpowers/sdd/2026-10-03-roots/run_all.sh` → every line `OK` (the earlier `FakeSession` checks set only `session`, which still means connected).
Run the Global Constraints pylint command → 10.00/10 for both.

- [ ] **Step 9: No commit** (user commits).

---

### Task 3: The form in the Tools tab, and docs

**Files:**
- Modify: `client/tools_view.py`, `client/app.py`
- Modify: `.superpowers/sdd/2026-10-02-tools-tab-approval-flow/t3_check.py` (flow outputs grow from 5 to 10)
- Modify: `README.md`, `docs/superpowers/specs/2026-10-05-elicitation-design.md`, `docs/superpowers/specs/2026-10-02-tools-tab-approval-flow-design.md`
- Create: `.superpowers/sdd/2026-10-05-elicitation/elicitation_gui_check.sh`, `.superpowers/sdd/2026-10-05-elicitation/run_all.sh`

**Interfaces:**
- Consumes: Task 2 `InputRequest`, `InputInvalid`, `INPUT_AUTO_DECLINED`, `INPUT_TOO_LATE`, `ToolOutcome.inputs`, `ToolOutcome.input_request`, `answer_input()`.
- Produces:
  - `tools_view.FormField` (frozen dataclass: `name, label, kind, required, hint, default, choices, minimum, maximum`), `form_fields(schema) -> list[FormField]`, `form_content(fields, values) -> dict`, `input_header(question) -> str`, `answer_line(answer, content) -> str`, `not_waiting_timeline(input_id) -> str`
  - `app.field_widget(field)`, `app.flow_outputs(timeline, pending=None, question=None)` (10 outputs), `MCPPermissionClientApp.gui_answer_input(input_id, action, content=None)`, `_build_input_card(flow)`

- [ ] **Step 1: Write the check script and run it to see it fail**

Create `.superpowers/sdd/2026-10-05-elicitation/elicitation_gui_check.sh` (then `chmod +x`):

```bash
#!/bin/bash
# The Tools tab builds a form from the server's schema, keeps it open on errors, and resumes the call.
REPO=/Users/dom/Projects/mcp-secuirty-working-notes-example
T=$(mktemp -d) && cp -R "$REPO/client" "$REPO/server" "$T"/ && rm -rf "$T/server/logs" "$T"/client/*.log && mkdir -p "$T/workspace"
echo '{"delete_file": "allow"}' > "$T/client/permissions.json"
(cd "$T/client" && MCP_DEMO_ELICITATION_TIMEOUT=30 "$REPO/mcp_security_env/bin/python" - > out.txt 2>&1 <<'PY'
import asyncio
import app
from client import DEFAULT_ROOT, ToolRequest, ToolOutcome, INPUT_AUTO_DECLINED, INPUT_TOO_LATE
from tools_view import form_fields, form_content, timeline_for

schema = {"type": "object", "required": ["name", "level"], "properties": {
    "name": {"type": "string", "title": "Name", "minLength": 2, "maxLength": 10,
             "description": "Your name"},
    "level": {"type": "string", "enum": ["low", "high"]},
    "count": {"type": "integer", "minimum": 0, "maximum": 5},
    "ratio": {"type": "number"},
    "on": {"type": "boolean", "default": True}}}
fields = form_fields(schema)
assert [(f.name, f.kind, f.required) for f in fields] == [
    ("name", "text", True), ("level", "radio", True), ("count", "integer", False),
    ("ratio", "number", False), ("on", "checkbox", False)], fields
assert fields[0].label == "Name *" and "2–10 characters" in fields[0].hint and "Your name" in fields[0].hint
assert fields[1].choices == ("low", "high") and fields[2].minimum == 0 and fields[2].maximum == 5
assert fields[4].default is True
assert form_content(fields, ["Al", "low", 3.0, None, True]) == {"name": "Al", "level": "low", "count": 3, "on": True}   # Review Focus 5
assert form_content(fields, ["", None, None, None, False]) == {"on": False}                                           # Review Focus 4

r = ToolRequest("id1", "delete_file", {"filepath": "x"})
t = timeline_for(ToolOutcome(r, "ALLOWED", "policy: allow", None, True, None, [("Confirm?", "decline", None)]))
assert "⏸ Server asked for input" in t and "✗ You answered: decline" in t, t
t = timeline_for(ToolOutcome(r, "ALLOWED", "policy: allow", None, True, None, [("Q", INPUT_AUTO_DECLINED, None)]))
assert "✗ Declined automatically" in t, t
t = timeline_for(ToolOutcome(r, "ALLOWED", "policy: allow", None, True, None, [("Q", INPUT_TOO_LATE, None)]))
assert "✗ Not sent: the server is no longer waiting" in t, t

async def main():
    a = app.MCPPermissionClientApp("../server/server.py")
    cfg = a.create_interface().get_config_file()
    labels = [c["props"].get("label") for c in cfg["components"]]
    assert "Schema sent by the server" in labels, labels
    loads = sum(1 for d in cfg["dependencies"] if any(t[1] == "load" for t in d["targets"]))
    assert loads == 4, f"the form renders when a question arrives, not on page load: {loads}"

    (DEFAULT_ROOT / "g.txt").write_text("x")
    out = await a.gui_send_request("delete_file", '{"filepath": "g.txt"}')
    timeline, _pending, _approval, _approval_md, send, question, input_group, header, schema_code, _errors = out
    assert question and question["id"] and "reason" in question["schema"]["properties"], question
    assert input_group["visible"] is True and send["interactive"] is False
    assert 'Server "Permission-Based MCP Server" asks' in header and "Confirm deletion of g.txt" in header
    assert '"minLength": 5' in schema_code
    assert "⏸ Server asked for input" in timeline and "Waiting for your answer" in timeline, timeline

    out = await a.gui_answer_input(question["id"], "accept", {"confirm_name": "g.txt", "reason": "no"})
    assert all(x == {"__type__": "update"} for x in out[:9]), out[:9]       # form stays as it is
    assert "reason:" in out[9], out[9]

    out = await a.gui_answer_input(question["id"], "accept",
                                   {"confirm_name": "g.txt", "reason": "old draft", "keep_backup": False})
    assert "✓ You answered: accept" in out[0] and "Deleted g.txt" in out[0], out[0]
    assert out[5] is None and out[6]["visible"] is False and out[4]["interactive"] is True

    out = await a.gui_answer_input(question["id"], "accept", {})
    assert "This question is no longer waiting" in out[0], out[0]
    await a.cleanup()

asyncio.run(main())
print("ELICITATION GUI OK")
PY
grep -q "ELICITATION GUI OK" out.txt && echo "ELICITATION GUI OK" || { echo "ELICITATION GUI FAIL"; grep -E "Error|assert" out.txt | grep -v Warning | tail -2; })
rm -rf "$T"
```

Run: `.superpowers/sdd/2026-10-05-elicitation/elicitation_gui_check.sh`
Expected: `ELICITATION GUI FAIL` with `ImportError: cannot import name 'form_fields'`.

- [ ] **Step 2: View helpers in `client/tools_view.py`**

Change `import json` to:

```python
import json
from dataclasses import dataclass
```

Change the `client` import to:

```python
from client import (ToolOutcome, ToolRequest, InputRequest, REASON_USER_APPROVED,
                    REASON_POLICY_CHANGED)
```

After `declared_line`, add:

```python
@dataclass(frozen=True)
class FormField:
    """One input in the form the client builds from the server's schema."""
    name: str
    label: str
    kind: str  # "text" | "checkbox" | "number" | "integer" | "radio"
    required: bool
    hint: str
    default: object = None
    choices: tuple = ()
    minimum: float | None = None
    maximum: float | None = None


def field_hint(spec: dict, required: bool) -> str:
    """The rules for one field, for people: 'required · 5–200 characters · <description>'."""
    rules = ["required"] if required else []
    shortest, longest = spec.get("minLength"), spec.get("maxLength")
    if shortest is not None and longest is not None:
        rules.append(f"{shortest}–{longest} characters")
    elif shortest is not None:
        rules.append(f"at least {shortest} characters")
    elif longest is not None:
        rules.append(f"at most {longest} characters")
    if "minimum" in spec or "maximum" in spec:
        rules.append(f"{spec.get('minimum', '…')}–{spec.get('maximum', '…')}")
    if spec.get("format"):
        rules.append(f"format: {spec['format']}")
    if spec.get("description"):
        rules.append(spec["description"])
    return " · ".join(rules)


def form_fields(schema: dict) -> list[FormField]:
    """The form for a server's schema (elicitation): one field per property, in order."""
    required = set(schema.get("required", []))
    kinds = {"boolean": "checkbox", "number": "number", "integer": "integer"}
    fields = []
    for name, spec in schema.get("properties", {}).items():
        kind = "radio" if spec.get("enum") else kinds.get(spec.get("type"), "text")
        label = spec.get("title", name) + (" *" if name in required else "")
        fields.append(FormField(name, label, kind, name in required,
                                field_hint(spec, name in required), spec.get("default"),
                                tuple(spec.get("enum", ())), spec.get("minimum"),
                                spec.get("maximum")))
    return fields


def form_content(fields: list[FormField], values) -> dict:
    """The form's values as the content to send. Empty values are left out (so a missing
    required field is reported as missing); integers become whole numbers."""
    content = {}
    for form_field, value in zip(fields, values):
        if value is None or value == "":
            continue
        content[form_field.name] = int(value) if form_field.kind == "integer" else value
    return content


def input_header(question: InputRequest) -> str:
    """The input card's heading: which server is asking, and its message."""
    return (f'**Server "{question.server_name}" asks (`elicitation/create`)**\n\n'
            f"{question.message}")


def answer_line(answer: str, content: dict | None) -> str:
    """How the user's answer to a server's question appears in the timeline."""
    if answer == "accept":
        return f"✓ You answered: accept {json.dumps(content)}"
    if answer in ("decline", "cancel"):
        return f"✗ You answered: {answer}"
    return f"✗ {answer[0].upper()}{answer[1:]}"
```

In `timeline_for`, replace the last line

```python
    steps.append((FAIL, "Error") if outcome.is_error else (DONE, "Result"))
    return render_timeline(header, steps, content_text(outcome.content))
```

with

```python
    for question, answer, content in outcome.inputs:
        steps.append((None, f"⏸ Server asked for input (`elicitation/create`): {question}"))
        steps.append((None, answer_line(answer, content)))
    if outcome.input_request is not None:
        steps.append((None, "⏸ Server asked for input (`elicitation/create`): "
                            f"{outcome.input_request.message}"))
        steps.append((None, "⏸ Waiting for your answer"))
        steps.append((TODO, "Result"))
        return render_timeline(header, steps)
    steps.append((FAIL, "Error") if outcome.is_error else (DONE, "Result"))
    return render_timeline(header, steps, content_text(outcome.content))
```

After `not_pending_timeline`, add:

```python
def not_waiting_timeline(input_id: str) -> str:
    """Timeline when an answer names a question the server is no longer waiting for."""
    return render_timeline(f"Question {input_id}", [(FAIL, "This question is no longer waiting")])
```

- [ ] **Step 3: Wire the form into `client/app.py`**

Add `import json` after `import sys`. Change the client import to:

```python
from client import (MCPPermissionClient, ToolRequest, InputRequest, InputInvalid, PROJECT_DIR,
                    DEFAULT_ROOT)
```

Extend the `tools_view` import with `FormField, form_fields, form_content, input_header, not_waiting_timeline` (wrap to 100 characters).

Replace `flow_outputs` with:

```python
def flow_outputs(timeline: str, pending: ToolRequest | None = None,
                 question: InputRequest | None = None):
    """Outputs shared by send/approve/reject/answer: timeline, pending id, approval card, card
    text, Send; then the input card: question, visibility, heading, schema, errors."""
    return (timeline,
            pending.request_id if pending else "",
            gr.update(visible=pending is not None),
            approval_card(pending) if pending else "",
            gr.update(interactive=pending is None and question is None),
            {"id": question.input_id, "schema": question.schema} if question else None,
            gr.update(visible=question is not None),
            input_header(question) if question else "",
            json.dumps(question.schema, indent=2) if question else "",
            "")


def field_widget(form_field: FormField):
    """The Gradio input for one field of the server's schema."""
    if form_field.kind == "checkbox":
        return gr.Checkbox(label=form_field.label, info=form_field.hint,
                           value=bool(form_field.default))
    if form_field.kind in ("number", "integer"):
        return gr.Number(label=form_field.label, info=form_field.hint, value=form_field.default,
                         minimum=form_field.minimum, maximum=form_field.maximum,
                         precision=0 if form_field.kind == "integer" else None)
    if form_field.kind == "radio":
        return gr.Radio(choices=list(form_field.choices), label=form_field.label,
                        info=form_field.hint, value=form_field.default)
    return gr.Textbox(label=form_field.label, info=form_field.hint,
                      value=form_field.default or "")
```

In `gui_send_request`, replace the last two lines with:

```python
        outcome = await self.request_tool(tool_name, arguments)
        pending = outcome.request if outcome.decision == "ASK" else None
        return flow_outputs(timeline_for(outcome), pending, outcome.input_request)
```

In `gui_approve`, replace `return flow_outputs(timeline_for(outcome))` with:

```python
        return flow_outputs(timeline_for(outcome), None, outcome.input_request)
```

After `gui_reject`, add:

```python
    async def gui_answer_input(self, input_id: str, action: str, content: dict | None = None):
        """Send the user's answer to the server's question. If it breaks the schema, keep the
        form as it is and show the problems."""
        try:
            outcome = await self.answer_input(input_id, action, content)
        except InputInvalid as e:
            return (gr.skip(),) * 9 + ("⚠ " + "  \n⚠ ".join(e.errors),)
        except KeyError:
            return flow_outputs(not_waiting_timeline(input_id))
        return flow_outputs(timeline_for(outcome), None, outcome.input_request)
```

In `_build_tools_tab`, replace

```python
        flow = [timeline, pending_id, approval_group, approval_text, send_btn]
```

with

```python
        flow = []  # filled below; the input card's buttons are wired before the list is complete
        input_group, question_state, input_heading, input_schema, input_errors = \
            self._build_input_card(flow)
        flow.extend([timeline, pending_id, approval_group, approval_text, send_btn,
                     question_state, input_group, input_heading, input_schema, input_errors])
```

and move that block so it comes right after the `with gr.Group(visible=False) as approval_group:` block (so the input card appears below the approval card) and before the `interface.load(...)` line.

Add this method after `_build_tools_tab`:

```python
    def _build_input_card(self, flow: list):
        """The card for a server's question (elicitation): a form generated from its schema."""
        question_state = gr.State(None)
        with gr.Group(visible=False) as input_group:
            heading = gr.Markdown()
            with gr.Accordion("Schema sent by the server", open=False):
                schema_view = gr.Code(language="json", label="Schema sent by the server")
            errors = gr.Markdown()

            @gr.render(inputs=question_state, triggers=[question_state.change])
            def render_form(question):
                if not question:
                    return
                fields = form_fields(question["schema"])
                widgets = [field_widget(form_field) for form_field in fields]
                with gr.Row():
                    accept_btn = gr.Button("Accept", variant="primary",
                                           elem_classes=["approve-btn"])
                    decline_btn = gr.Button("Decline", variant="stop")
                    cancel_btn = gr.Button("Cancel", variant="secondary")

                async def accept(*values):
                    return await self.gui_answer_input(question["id"], "accept",
                                                       form_content(fields, values))

                async def decline():
                    return await self.gui_answer_input(question["id"], "decline")

                async def cancel():
                    return await self.gui_answer_input(question["id"], "cancel")

                accept_btn.click(accept, inputs=widgets, outputs=flow)
                decline_btn.click(decline, outputs=flow)
                cancel_btn.click(cancel, outputs=flow)

        return input_group, question_state, heading, schema_view, errors
```

- [ ] **Step 4: Update the earlier Tools-tab check for the wider outputs**

In `.superpowers/sdd/2026-10-02-tools-tab-approval-flow/t3_check.py`, make these exact replacements:
- `t, pid, card, card_md, send = await a.gui_send_request("write_file", '{"filepath": "a.txt", "content": "hi"}')` → `t, pid, card, card_md, send = (await a.gui_send_request("write_file", '{"filepath": "a.txt", "content": "hi"}'))[:5]`
- `t, pid2, card, _, send = await a.gui_approve(pid)` → `t, pid2, card, _, send = (await a.gui_approve(pid))[:5]`
- `t, pid, card, _, _ = await a.gui_send_request("delete_file", '{"filepath": "a.txt"}')` → `t, pid, card, _, _ = (await a.gui_send_request("delete_file", '{"filepath": "a.txt"}'))[:5]`

- [ ] **Step 5: Run the check again**

Run: `.superpowers/sdd/2026-10-05-elicitation/elicitation_gui_check.sh`
Expected: `ELICITATION GUI OK`.

- [ ] **Step 6: Combined runner, all checks, lint**

Create `.superpowers/sdd/2026-10-05-elicitation/run_all.sh` (then `chmod +x`):

```bash
#!/bin/bash
# All checks: Tools tab, roots, and elicitation.
REPO=/Users/dom/Projects/mcp-secuirty-working-notes-example; W=$REPO/.superpowers/sdd/2026-10-05-elicitation
"$REPO/.superpowers/sdd/2026-10-03-roots/run_all.sh"
for c in elicitation_server_check.sh elicitation_client_check.sh elicitation_gui_check.sh; do echo "$c: $("$W/$c" | tail -1)"; done
```

Run it — every line ends `OK`. Run the Global Constraints pylint command — 10.00/10 for both.

- [ ] **Step 7: Launch check**

```bash
lsof -nP -iTCP:7863 -sTCP:LISTEN && echo "PORT IN USE - skip" || { REPO=$PWD; T=$(mktemp -d) && cp -R client server "$T"/ && rm -rf "$T"/client/__pycache__ && (cd "$T" && $REPO/mcp_security_env/bin/python client/app.py server/server.py > app.log 2>&1 & echo $! > "$T/pid"); sleep 8; $REPO/mcp_security_env/bin/python -c "import json,urllib.request; c=json.load(urllib.request.urlopen('http://127.0.0.1:7863/config')); print([x['props'].get('label') for x in c['components'] if x['type']=='accordion'])"; kill $(cat "$T/pid"); }
```

Expected: `['Schema sent by the server']`; then the started PID is stopped.

- [ ] **Step 8: Update the docs**

In `README.md`:
- In the Part 1 bullet, replace `server-side path validation, and roots.` with `server-side path validation, roots, and elicitation (server-initiated structured input).`
- In the `pip install` line, append ` jsonschema==4.26.0`.
- In "How a tool call travels", replace the code block with:

```
1. client → server   initialize           "I support roots and elicitation"  (once, when connecting)
2. client → server   tools/call           delete_file(filepath)              ← call opens
3. server → client   roots/list           "which folders may I use?"         (the client answers itself)
4. client → server   roots/list result    ["file:///…/workspace"]
5. server → client   elicitation/create   message + schema                   (for the human)
6. client → server   elicitation result   {action: "accept", content: {…}}
7. server → client   tools/call result    "Deleted …" or "Access denied …"   ← call closes
```

- After the "What roots protect" paragraph, add:

```markdown
### Elicitation: server-initiated structured input

Sometimes the server needs something only the user can give, and only finds out while it's carrying out the call. `delete_file` asks the user to confirm: it sends a message ("Confirm deletion of notes.txt (2.3 KB, modified …)") and a **schema** describing the fields it needs, their types and validation rules (`confirm_name`, `reason` of 5–200 characters, `keep_backup`). The client builds a form from that schema, checks the user's input against it, and sends back structured data; the user can also decline or cancel. The tool call waits, paused, until the answer arrives.

- **Around the caller.** The question goes through the client to the human, not to whatever made the call. A confirmation passed as a tool argument could be filled in by the caller (in Part 2, an LLM); an elicitation answer can't.
- **Two layers of consent.** The client's permission policy decides whether the call is sent at all; the server's question confirms details only the server knows. `delete_file` defaults to `deny` in the client, so set it to `ask` or `allow` to try this.
- **Validate on both sides.** The client checks the answer against the schema before sending it; the server checks it again, because it can't trust the client, and also checks what a schema can't express (`confirm_name` must match the file path).
- **Fail closed.** A client that can't be asked (no elicitation support) can't delete; a question left unanswered for 2 minutes is treated as cancelled.
```

In `docs/superpowers/specs/2026-10-05-elicitation-design.md`, change `Status: approved design, not yet implemented` to `Status: implemented (plan: docs/superpowers/plans/2026-10-05-elicitation.md)`.

In `docs/superpowers/specs/2026-10-02-tools-tab-approval-flow-design.md`, add as the first Change history entry:

```markdown
- 2026-10-05: Tools tab gains a "Server asks for input" card with a form generated from the
  server's schema; the timeline shows questions and answers (see
  `docs/superpowers/specs/2026-10-05-elicitation-design.md`).
```

- [ ] **Step 9: No commit** (user commits).
