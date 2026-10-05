# Tools Tab Approval Flow Implementation Plan

> **Status: executed 2026-10-02.** This plan is a historical record of how the work was done. The code has since changed (final-review fixes, connection handling, server descriptions, table columns); the spec `docs/superpowers/specs/2026-10-02-tools-tab-approval-flow-design.md` describes the current design and its Change history lists what differs from this plan.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Gradio app's Tools tab teach client-side permissions: requests are checked against policy, and `ask` requests are approved or rejected by request ID, exactly as made, once.

**Architecture:** `client.py` gains a binding request/approve/reject API (`ToolRequest`, `ToolOutcome`, `pending` dict) replacing `call_tool_with_permission`. A new `tools_view.py` holds pure presentation helpers (argument parsing, schema templates, step-timeline Markdown). `app.py`'s Tools tab is rebuilt around a tool table, one Send button, a step timeline and an approval card, and loads tools on page load.

**Tech Stack:** Python 3.13, `mcp==1.16.0` (ClientSession over stdio), `fastmcp==2.12.5` (server), `gradio==5.49.1`.

**Spec:** `docs/superpowers/specs/2026-10-02-tools-tab-approval-flow-design.md`

## Global Constraints

- **No git commits.** The user commits; leave all changes uncommitted.
- **No new dependencies.** No pytest; verification uses inline scripts run with the venv Python.
- Run Python as `mcp_security_env/bin/python` from the repo root.
- Out of scope, do not modify: `host.py`, `server.py`, `permissions.py`, and the Resources / Prompts / Permissions tab builders in `app.py`.
- Verification must not write to the repo's `data/`, `logs/` or `client_audit.log`: use a temp copy or a temp `permissions_file`.
- Pylint must report no messages for `app.py client.py audit.py tools_view.py` with the repo `.pylintrc` (max line length 100). Lint command:
  ```bash
  SP=$(mcp_security_env/bin/python -c "import site;print(site.getsitepackages()[0])"); pylint --init-hook="import sys; sys.path.insert(0,'$SP')" app.py client.py audit.py tools_view.py
  ```
- Approval takes only a request ID; nothing on the approve path may read tool name or arguments from the GUI.

## Review Focus

1. **Arguments that are valid JSON but not an object** (`[]`, `"x"`, `1`) or empty: empty means `{}`; non-objects show "Invalid arguments", never a crash. Pinned in Task 2.
2. **Approving the same request twice** (double click, replay): the second approve is refused with `KeyError` and sends nothing. Pinned in Task 1.
3. **Editing arguments after requesting** (in the GUI box or the caller's dict): the stored request is unchanged and is what gets sent. Pinned in Task 1 (deep copy) and Task 3 (approve takes only the ID).
4. **Server not reachable when the page loads** (bad script path, server crash): the table is empty and a status line shows the error; the app does not crash. Pinned in Task 3.
5. **Tool whose schema has no properties**: argument template is `{}`. Pinned in Task 2.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `audit.py` | Modify | `AuditLog.record()` accepts extra context fields (`request_id`). |
| `client.py` | Modify | `ToolRequest`, `ToolOutcome`, reason constants, `pending`, `request_tool()`, `approve()`, `reject()`, `_send()`; remove `call_tool_with_permission()`. |
| `.pylintrc` | Modify | Allow 10 instance attributes (client gains `pending`). |
| `tools_view.py` | Create | Pure Tools-tab helpers: `first_line`, `args_template`, `parse_arguments`, `content_text`, `render_timeline`, `timeline_for`, `invalid_arguments_timeline`, `not_pending_timeline`, `approval_card`. |
| `app.py` | Modify | Tools tab UI and handlers; remove `gui_list_tools`, `gui_call_tool`, `tools_cache`. |

---

### Task 1: Binding approval API in the client

**Files:**
- Modify: `audit.py` (`AuditLog.record`)
- Modify: `client.py` (imports, new dataclasses, `__init__`, `log_audit`, replace `call_tool_with_permission`)
- Modify: `.pylintrc`

**Interfaces:**
- Consumes: `AuditLog(path, actor)`, `MCPPermissionClient.check_permission(tool_name, arguments) -> str`, `_get_session()`.
- Produces (used by Tasks 2 and 3):
  - `client.ToolRequest(request_id: str, tool_name: str, arguments: dict)` frozen dataclass
  - `client.ToolOutcome(request: ToolRequest, decision: str, reason: str, content: list | None = None, is_error: bool = False)`
  - Constants in `client`: `REASON_AWAITING = "awaiting approval"`, `REASON_USER_APPROVED = "user approved"`, `REASON_POLICY_CHANGED = "policy changed to deny"`, `REASON_REJECTED = "rejected by user"`
  - `async MCPPermissionClient.request_tool(tool_name: str, arguments: dict | None = None) -> ToolOutcome`
  - `async MCPPermissionClient.approve(request_id: str) -> ToolOutcome` (raises `KeyError` if not pending)
  - `MCPPermissionClient.reject(request_id: str) -> ToolOutcome` (raises `KeyError` if not pending)
  - `MCPPermissionClient.pending: dict[str, ToolRequest]`
  - `AuditLog.record(tool, arguments, outcome, detail="", **context)`

- [ ] **Step 1: Write the verification script and run it to see it fail**

Run from the repo root:

```bash
mcp_security_env/bin/python - <<'EOF'
import asyncio, json, tempfile
from pathlib import Path
from mcp.types import CallToolResult, TextContent, ErrorData
from mcp.shared.exceptions import McpError
from client import MCPPermissionClient


class FakeSession:
    def __init__(self):
        self.calls, self.error, self.raise_mcp = [], False, False

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if self.raise_mcp:
            raise McpError(ErrorData(code=-32000, message="Connection closed"))
        text = "boom" if self.error else "ok"
        return CallToolResult(content=[TextContent(type="text", text=text)], isError=self.error)


def make(perms):
    d = Path(tempfile.mkdtemp())
    (d / "permissions.json").write_text(json.dumps(perms))
    c = MCPPermissionClient("server.py", permissions_file=d / "permissions.json")
    c.session, c._connected = FakeSession(), True
    return c


async def main():
    c = make({"read_file": "allow", "write_file": "ask", "delete_file": "deny"})
    calls = c.session.calls

    o = await c.request_tool("delete_file", {"filepath": "a"})
    assert o.decision == "DENIED" and calls == [], "deny must not send"

    o = await c.request_tool("read_file", {"filepath": "a"})
    assert o.decision == "ALLOWED" and not o.is_error and len(calls) == 1, "allow sends once"
    assert o.content[0].text == "ok"

    args = {"filepath": "a", "content": "hi"}
    o = await c.request_tool("write_file", args)
    assert o.decision == "ASK" and len(calls) == 1, "ask must not send"
    rid = o.request.request_id
    assert rid in c.pending
    args["filepath"] = "EVIL"                      # caller mutates after requesting
    o2 = await c.approve(rid)
    assert o2.reason == "user approved"
    assert calls[-1] == ("write_file", {"filepath": "a", "content": "hi"}), "stored copy sent"

    try:
        await c.approve(rid)
        raise AssertionError("approval replay allowed")
    except KeyError:
        pass

    o = await c.request_tool("write_file", {"filepath": "b", "content": "x"})
    sent = len(calls)
    r = c.reject(o.request.request_id)
    assert r.decision == "REJECTED" and len(calls) == sent, "reject must not send"
    try:
        c.reject(o.request.request_id)
        raise AssertionError("reject replay allowed")
    except KeyError:
        pass

    o = await c.request_tool("write_file", {"filepath": "c", "content": "x"})
    c.permissions["write_file"] = "deny"
    o2 = await c.approve(o.request.request_id)
    assert o2.decision == "DENIED" and o2.reason == "policy changed to deny"
    assert len(calls) == sent, "policy change must block send"

    c.permissions["write_file"] = "allow"
    c.session.error = True
    o = await c.request_tool("write_file", {"filepath": "d", "content": "x"})
    assert o.is_error and o.content[0].text == "boom"

    c.session.raise_mcp = True
    o = await c.request_tool("read_file", {"filepath": "e"})
    assert o.is_error and "Connection closed" in o.content[0].text

    flows = {}
    for line in c.audit_log.path.read_text().splitlines():
        e = json.loads(line)
        flows.setdefault(e["request_id"], []).append(e["outcome"])
    flows = list(flows.values())
    print(flows)
    for expected in (["DENIED"], ["ALLOWED", "COMPLETED"], ["ASK", "ALLOWED", "COMPLETED"],
                     ["ASK", "REJECTED"], ["ASK", "DENIED"], ["ALLOWED", "ERROR"]):
        assert expected in flows, f"missing audit flow {expected}"


asyncio.run(main())
print("TASK 1 OK")
EOF
```

Expected: FAIL with `AttributeError: 'MCPPermissionClient' object has no attribute 'request_tool'`.

- [ ] **Step 2: Let `AuditLog.record` take context fields**

In `audit.py`, replace the `record` method with:

```python
    def record(self, tool: str, arguments: dict, outcome: str, detail: str = "", **context):
        """Append one audit record. Extra keyword arguments (e.g. request_id) become fields."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": self.actor,
            "tool": tool,
            "args": summarise_arguments(arguments),
            "outcome": outcome,
            "detail": detail,
            **context,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
```

- [ ] **Step 3: Add imports, constants and dataclasses to `client.py`**

Replace the import block at the top of `client.py` (everything from `import sys` to `from audit import AuditLog`) with:

```python
import sys
import copy
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import TextContent
from pydantic import AnyUrl
from permissions import PERMISSIONS_FILE, load_permissions
from audit import AuditLog

REASON_AWAITING = "awaiting approval"
REASON_USER_APPROVED = "user approved"
REASON_POLICY_CHANGED = "policy changed to deny"
REASON_REJECTED = "rejected by user"


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
```

- [ ] **Step 4: Add `pending` and change `log_audit`**

In `MCPPermissionClient.__init__`, add after `self.permissions = load_permissions(self.permissions_file)`:

```python
        self.pending: dict[str, ToolRequest] = {}
```

Replace `log_audit` with:

```python
    def log_audit(self, request: ToolRequest, decision: str, reason: str = ""):
        """Log a decision or outcome for a request to the client audit log."""
        self.audit_log.record(request.tool_name, request.arguments, decision, reason,
                              request_id=request.request_id)
```

- [ ] **Step 5: Replace `call_tool_with_permission` with the request/approve/reject API**

Delete the whole `call_tool_with_permission` method and put these in its place:

```python
    async def request_tool(self, tool_name: str, arguments: dict | None = None) -> ToolOutcome:
        """Check a tool call against policy: send it, refuse it, or hold it for approval."""
        request = ToolRequest(uuid.uuid4().hex[:8], tool_name, copy.deepcopy(arguments or {}))
        permission = self.check_permission(tool_name, request.arguments)

        if permission == "deny":
            self.log_audit(request, "DENIED", "policy: deny")
            return ToolOutcome(request, "DENIED", "policy: deny")

        if permission == "ask":
            self.pending[request.request_id] = request
            self.log_audit(request, "ASK", REASON_AWAITING)
            return ToolOutcome(request, "ASK", REASON_AWAITING)

        return await self._send(request, f"policy: {permission}")

    async def approve(self, request_id: str) -> ToolOutcome:
        """Send a pending request exactly as it was made, once. KeyError if not pending."""
        request = self.pending.pop(request_id)
        if self.check_permission(request.tool_name, request.arguments) == "deny":
            self.log_audit(request, "DENIED", REASON_POLICY_CHANGED)
            return ToolOutcome(request, "DENIED", REASON_POLICY_CHANGED)
        return await self._send(request, REASON_USER_APPROVED)

    def reject(self, request_id: str) -> ToolOutcome:
        """Discard a pending request without sending it. KeyError if not pending."""
        request = self.pending.pop(request_id)
        self.log_audit(request, "REJECTED", REASON_REJECTED)
        return ToolOutcome(request, "REJECTED", REASON_REJECTED)

    async def _send(self, request: ToolRequest, reason: str) -> ToolOutcome:
        """Send an allowed request and log what the server reported."""
        self.log_audit(request, "ALLOWED", reason)

        # Record the outcome so this log is complete on its own
        outcome, detail = "ERROR", "call failed before the server responded"
        try:
            session = await self._get_session()
            result = await session.call_tool(request.tool_name, arguments=request.arguments)
            if result.isError:
                first = result.content[0] if result.content else None
                detail = (first.text if isinstance(first, TextContent)
                          else "server reported an error")
            else:
                outcome, detail = "COMPLETED", ""
            return ToolOutcome(request, "ALLOWED", reason, result.content, result.isError)
        except McpError as e:
            detail = f"MCP error: {e}"
            error = [TextContent(type="text", text=detail)]
            return ToolOutcome(request, "ALLOWED", reason, error, is_error=True)
        finally:
            self.log_audit(request, outcome, detail)
```

- [ ] **Step 6: Allow the extra instance attribute in `.pylintrc`**

Append to `.pylintrc`:

```ini

[DESIGN]
# The client holds connection, policy, audit and pending-approval state.
max-attributes=10
```

- [ ] **Step 7: Run the verification script again**

Run the Step 1 command. Expected: a printed list of flows, then `TASK 1 OK`.

- [ ] **Step 8: Lint**

Run: `SP=$(mcp_security_env/bin/python -c "import site;print(site.getsitepackages()[0])"); pylint --init-hook="import sys; sys.path.insert(0,'$SP')" client.py audit.py`
Expected: `Your code has been rated at 10.00/10`.

- [ ] **Step 9: No commit** (user commits). Note that `app.py` still calls the removed `call_tool_with_permission` until Task 3; that is expected.

---

### Task 2: Pure Tools-tab helpers (`tools_view.py`)

**Files:**
- Create: `tools_view.py`

**Interfaces:**
- Consumes: `client.ToolRequest`, `client.ToolOutcome`, `client.REASON_USER_APPROVED`, `client.REASON_POLICY_CHANGED` (Task 1); `audit.summarise_arguments(arguments: dict) -> dict`.
- Produces (used by Task 3):
  - `first_line(text: str | None) -> str`
  - `args_template(input_schema: dict | None) -> str`
  - `parse_arguments(arguments_json: str) -> dict` (raises `ValueError`, which includes `json.JSONDecodeError`)
  - `content_text(content: list | None) -> str`
  - `render_timeline(header: str, steps: list[tuple[str, str]], body: str = "") -> str`
  - `timeline_for(outcome: ToolOutcome) -> str`
  - `invalid_arguments_timeline(tool_name: str, error: str) -> str`
  - `not_pending_timeline(request_id: str) -> str`
  - `approval_card(request: ToolRequest) -> str`

- [ ] **Step 1: Write the verification script and run it to see it fail**

```bash
mcp_security_env/bin/python - <<'EOF'
import json
from mcp.types import TextContent, ImageContent
from client import ToolRequest, ToolOutcome
from tools_view import (first_line, args_template, parse_arguments, content_text,
                        timeline_for, invalid_arguments_timeline, not_pending_timeline,
                        approval_card)

assert first_line("\n    Read a file LOW risk\n\n    Args: ...") == "Read a file LOW risk"
assert first_line(None) == "" and first_line("") == ""

schema = {"properties": {"filepath": {"type": "string"}, "n": {"type": "integer"},
                         "f": {"type": "boolean"}, "x": {}}}
assert json.loads(args_template(schema)) == {"filepath": "", "n": 0, "f": False, "x": ""}
assert args_template({}) == "{}" and args_template(None) == "{}"          # Review Focus 5

assert parse_arguments("") == {} and parse_arguments("   ") == {}       # Review Focus 1
assert parse_arguments('{"a": 1}') == {"a": 1}
for bad in ("[]", '"x"', "1", "{nope"):
    try:
        parse_arguments(bad)
        raise AssertionError(f"accepted {bad!r}")
    except ValueError:
        pass

img = ImageContent(type="image", data="", mimeType="image/png")
assert content_text([TextContent(type="text", text="hi"), img]) == "hi\n[image content]"
assert content_text(None) == ""

req = ToolRequest("ab12cd34", "write_file", {"filepath": "a.txt", "content": "x" * 500})
ask = timeline_for(ToolOutcome(req, "ASK", "awaiting approval"))
assert "Request ab12cd34" in ask and "<500 chars>" in ask and "waiting for your approval" in ask
deny = timeline_for(ToolOutcome(req, "DENIED", "policy: deny"))
assert "Client policy: DENY" in deny and "Not sent" in deny and "changed" not in deny
changed = timeline_for(ToolOutcome(req, "DENIED", "policy changed to deny"))
assert "DENY (changed since request)" in changed
rejected = timeline_for(ToolOutcome(req, "REJECTED", "rejected by user"))
assert "Rejected by you" in rejected and "Not sent" in rejected
done = timeline_for(ToolOutcome(req, "ALLOWED", "user approved",
                                [TextContent(type="text", text="Successfully wrote")]))
assert "user approved" in done and "Sent to server" in done and "Successfully wrote" in done
allow = timeline_for(ToolOutcome(req, "ALLOWED", "policy: allow",
                                 [TextContent(type="text", text="nope")], is_error=True))
assert "Client policy: ALLOW" in allow and "✗" in allow and "nope" in allow

assert "Invalid arguments: bad" in invalid_arguments_timeline("write_file", "bad")
assert "no longer pending" in not_pending_timeline("ab12cd34")
card = approval_card(req)
assert "ab12cd34" in card and "write_file" in card and '"filepath": "a.txt"' in card
print("TASK 2 OK")
EOF
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tools_view'`.

- [ ] **Step 2: Create `tools_view.py`**

```python
"""Pure helpers for the Tools tab: argument parsing, schema templates and the step timeline."""

import json
from mcp.types import TextContent
from audit import summarise_arguments
from client import ToolOutcome, ToolRequest, REASON_USER_APPROVED, REASON_POLICY_CHANGED

DONE, WAIT, FAIL, TODO = "✓", "⏸", "✗", "·"

# Placeholder value per JSON Schema type for the arguments template
JSON_DEFAULTS = {"string": "", "integer": 0, "number": 0, "boolean": False,
                 "array": [], "object": {}}


def first_line(text: str | None) -> str:
    """First non-blank line of a tool description, for the tool table."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def args_template(input_schema: dict | None) -> str:
    """JSON template with a placeholder for each property in a tool's input schema."""
    properties = (input_schema or {}).get("properties", {})
    template = {name: JSON_DEFAULTS.get(spec.get("type"), "")
                for name, spec in properties.items()}
    return json.dumps(template, indent=2) if template else "{}"


def parse_arguments(arguments_json: str) -> dict:
    """Parse the Arguments box; empty means {}. Raises ValueError unless it is a JSON object."""
    if not arguments_json.strip():
        return {}
    arguments = json.loads(arguments_json)
    if not isinstance(arguments, dict):
        raise ValueError('arguments must be a JSON object, e.g. {"filepath": "a.txt"}')
    return arguments


def content_text(content: list | None) -> str:
    """Text of a tool result; non-text blocks become a short type note."""
    return "\n".join(block.text if isinstance(block, TextContent) else f"[{block.type} content]"
                     for block in content or [])


def render_timeline(header: str, steps: list[tuple[str, str]], body: str = "") -> str:
    """Markdown for a numbered step trail, with an optional result body underneath."""
    lines = [f"**{header}**", ""]
    lines += [f"{mark} **{number}** &nbsp; {text}  "
              for number, (mark, text) in enumerate(steps, start=1)]
    markdown = "\n".join(lines)
    if body:
        markdown += f"\n\n````\n{body}\n````"
    return markdown


def timeline_for(outcome: ToolOutcome) -> str:
    """Step timeline for a request's current state."""
    request = outcome.request
    arguments = json.dumps(summarise_arguments(request.arguments))
    header = f"Request {request.request_id}: {request.tool_name} {arguments}"
    built = (DONE, "Request built")

    if outcome.decision == "ASK":
        return render_timeline(header, [
            built, (WAIT, "Client policy: ASK, waiting for your approval"),
            (TODO, "Sent to server"), (TODO, "Result")])

    if outcome.decision == "DENIED":
        policy = ("Client policy: DENY (changed since request)"
                  if outcome.reason == REASON_POLICY_CHANGED else "Client policy: DENY")
        return render_timeline(header, [built, (FAIL, policy), (TODO, "Not sent")])

    if outcome.decision == "REJECTED":
        return render_timeline(header, [built, (FAIL, "Rejected by you"), (TODO, "Not sent")])

    policy = ("Client policy: ASK, user approved"
              if outcome.reason == REASON_USER_APPROVED else "Client policy: ALLOW")
    result = (FAIL, "Error") if outcome.is_error else (DONE, "Result")
    return render_timeline(header, [built, (DONE, policy), (DONE, "Sent to server"), result],
                           content_text(outcome.content))


def invalid_arguments_timeline(tool_name: str, error: str) -> str:
    """Timeline when the Arguments box can't be turned into a request."""
    return render_timeline(f"{tool_name}: request not built",
                           [(FAIL, f"Invalid arguments: {error}")])


def not_pending_timeline(request_id: str) -> str:
    """Timeline when approve/reject names a request that is no longer pending."""
    return render_timeline(f"Request {request_id}",
                           [(FAIL, f"Request {request_id} is no longer pending")])


def approval_card(request: ToolRequest) -> str:
    """Markdown for the approval card: the exact request the user is approving."""
    return (f"**Approval needed: request `{request.request_id}`**\n\n"
            f"Tool: `{request.tool_name}`\n\n"
            f"````json\n{json.dumps(request.arguments, indent=2)}\n````")
```

- [ ] **Step 3: Run the verification script again**

Run the Step 1 command. Expected: `TASK 2 OK`.

- [ ] **Step 4: Lint**

Run: `SP=$(mcp_security_env/bin/python -c "import site;print(site.getsitepackages()[0])"); pylint --init-hook="import sys; sys.path.insert(0,'$SP')" tools_view.py`
Expected: `Your code has been rated at 10.00/10`.

- [ ] **Step 5: No commit** (user commits).

---

### Task 3: Rebuild the Tools tab (`app.py`)

**Files:**
- Modify: `app.py` (imports, `__init__`, remove `gui_list_tools` and `gui_call_tool`, add handlers and `flow_outputs`, replace `_build_tools_tab`, pass `interface` from `create_interface`)

**Interfaces:**
- Consumes: Task 1 `request_tool`, `approve`, `reject`, `ToolRequest`; Task 2 `first_line`, `args_template`, `parse_arguments`, `timeline_for`, `invalid_arguments_timeline`, `not_pending_timeline`, `approval_card`; existing `list_tools()`, `permissions`.
- Produces: `MCPPermissionClientApp.gui_load_tools()`, `gui_select_tool(pending_id, evt)`, `gui_send_request(tool_name, arguments_json)`, `gui_approve(pending_id)`, `gui_reject(pending_id)`; module function `flow_outputs(timeline, pending=None)`.

- [ ] **Step 1: Write the verification script and run it to see it fail**

This runs the handlers against the real server in a temp copy of the project.

```bash
T=$(mktemp -d) && cp *.py "$T"/ && echo '{"read_file": "allow", "write_file": "ask", "delete_file": "deny"}' > "$T/permissions.json" && (cd "$T" && /Users/dom/Projects/mcp-secuirty-working-notes-example/mcp_security_env/bin/python - <<'EOF'
import asyncio
import gradio as gr
import app

def sel(name):
    return gr.SelectData(None, {"index": [0, 0], "value": name, "row_value": [name, "", ""]})

async def main():
    a = app.MCPPermissionClientApp("server.py")
    ui = a.create_interface()
    rows, status = await a.gui_load_tools()
    names = [r[0] for r in rows]
    assert {"read_file", "write_file", "delete_file", "execute_command"} <= set(names), names
    assert ["write_file", "ask"] == rows[names.index("write_file")][:2]
    assert status["visible"] is False

    tool, template, send = a.gui_select_tool("", sel("write_file"))
    assert tool == "write_file" and '"content": ""' in template and send["interactive"] is True
    _, _, send = a.gui_select_tool("abc", sel("write_file"))
    assert send["interactive"] is False, "Send stays disabled while a request is pending"

    t, pid, card, card_md, send = await a.gui_send_request("write_file", '{"filepath": "a.txt", "content": "hi"}')
    assert pid and card["visible"] is True and send["interactive"] is False and pid in card_md
    assert "waiting for your approval" in t

    t, pid2, card, _, send = await a.gui_approve(pid)
    assert pid2 == "" and card["visible"] is False and send["interactive"] is True
    assert "Successfully wrote to a.txt" in t and "user approved" in t

    t, *_ = await a.gui_approve(pid)
    assert "no longer pending" in t, "replayed approval refused"

    t, pid, *_ = await a.gui_send_request("write_file", '{"filepath": "b.txt", "content": "x"}')
    t, *_ = a.gui_reject(pid)
    assert "Rejected by you" in t

    t, pid, card, _, _ = await a.gui_send_request("delete_file", '{"filepath": "a.txt"}')
    assert "Client policy: DENY" in t and pid == "" and card["visible"] is False

    t, *_ = await a.gui_send_request("read_file", '{"filepath": "a.txt"}')
    assert "Client policy: ALLOW" in t and "hi" in t

    t, *_ = await a.gui_send_request("read_file", '{"filepath": "../../etc/passwd"}')
    assert "✗" in t and "outside the data directory" in t

    t, *_ = await a.gui_send_request("read_file", "[1, 2]")
    assert "Invalid arguments" in t

    await a.cleanup()

    bad = app.MCPPermissionClientApp("does_not_exist.py")               # Review Focus 4
    rows, status = await bad.gui_load_tools()
    assert rows == [] and status["visible"] is True and "Could not load tools" in status["value"]

asyncio.run(main())
print("TASK 3 OK")
EOF
); rm -rf "$T"
```

Expected: FAIL with `AttributeError: 'MCPPermissionClientApp' object has no attribute 'gui_load_tools'`.

- [ ] **Step 2: Update imports and `__init__`**

Replace the import block of `app.py` with:

```python
import sys
import json
import gradio as gr
from mcp.shared.exceptions import McpError
from mcp.types import TextContent, TextResourceContents
from client import MCPPermissionClient, ToolRequest
from tools_view import (first_line, args_template, parse_arguments, timeline_for,
                        invalid_arguments_timeline, not_pending_timeline, approval_card)
```

Replace `__init__` with:

```python
    def __init__(self, server_script: str):
        super().__init__(server_script)
        self.tool_schemas: dict[str, dict] = {}
        self.prompts_cache = []
```

- [ ] **Step 3: Add `flow_outputs` above the class**

Insert directly above `class MCPPermissionClientApp`:

```python
def flow_outputs(timeline: str, pending: ToolRequest | None = None):
    """Outputs shared by send/approve/reject: timeline, pending id, card, card text, Send."""
    return (timeline,
            pending.request_id if pending else "",
            gr.update(visible=pending is not None),
            approval_card(pending) if pending else "",
            gr.update(interactive=pending is None))


```

- [ ] **Step 4: Replace `gui_list_tools` and `gui_call_tool` with the new handlers**

Delete both methods and put these in their place:

```python
    async def gui_load_tools(self):
        """Connect and list tools for the table; show a status line instead if that fails."""
        try:
            tools = await self.list_tools()
        except (McpError, OSError) as e:
            return [], gr.update(value=f"⚠ Could not load tools: {e}", visible=True)

        self.tool_schemas = {tool.name: tool.inputSchema for tool in tools}
        rows = [[tool.name, self.permissions.get(tool.name, "ask"), first_line(tool.description)]
                for tool in tools]
        return rows, gr.update(value="", visible=False)

    def gui_select_tool(self, pending_id: str, evt: gr.SelectData):
        """Select the clicked tool and fill Arguments with a template from its schema."""
        tool_name = evt.row_value[0]
        return (tool_name, args_template(self.tool_schemas.get(tool_name)),
                gr.update(interactive=not pending_id))

    async def gui_send_request(self, tool_name: str, arguments_json: str):
        """Send a request through the client policy and show where it got to."""
        try:
            arguments = parse_arguments(arguments_json)
        except ValueError as e:
            return flow_outputs(invalid_arguments_timeline(tool_name, str(e)))

        outcome = await self.request_tool(tool_name, arguments)
        pending = outcome.request if outcome.decision == "ASK" else None
        return flow_outputs(timeline_for(outcome), pending)

    async def gui_approve(self, pending_id: str):
        """Approve the pending request by id; the client sends its stored copy."""
        try:
            outcome = await self.approve(pending_id)
        except KeyError:
            return flow_outputs(not_pending_timeline(pending_id))
        return flow_outputs(timeline_for(outcome))

    def gui_reject(self, pending_id: str):
        """Reject the pending request by id; nothing is sent."""
        try:
            outcome = self.reject(pending_id)
        except KeyError:
            return flow_outputs(not_pending_timeline(pending_id))
        return flow_outputs(timeline_for(outcome))
```

- [ ] **Step 5: Pass the Blocks object to the Tools tab builder**

In `create_interface`, change:

```python
                with gr.Tab("Tools"):
                    self._build_tools_tab()
```

to:

```python
                with gr.Tab("Tools"):
                    self._build_tools_tab(interface)
```

- [ ] **Step 6: Replace `_build_tools_tab`**

Replace the whole `_build_tools_tab` method with:

```python
    def _build_tools_tab(self, interface: gr.Blocks):
        """Build the Tools tab: pick a tool, send a request, watch the client policy decide."""
        gr.Markdown("### Call tools through the client's permission policy")
        tools_status = gr.Markdown(visible=False)
        tool_table = gr.Dataframe(headers=["Tool", "Policy", "Description"],
                                  interactive=False, label="Tools (click a row to select)")
        refresh_btn = gr.Button("↻ Refresh", size="sm")

        selected_tool = gr.Textbox(label="Selected tool", interactive=False)
        tool_args = gr.Textbox(label="Arguments (JSON)", lines=4)
        send_btn = gr.Button("Send request", variant="primary", interactive=False)
        pending_id = gr.State("")

        timeline = gr.Markdown()
        with gr.Group(visible=False) as approval_group:
            approval_text = gr.Markdown()
            with gr.Row():
                approve_btn = gr.Button("Approve", variant="primary")
                reject_btn = gr.Button("Reject", variant="stop")

        interface.load(fn=self.gui_load_tools, outputs=[tool_table, tools_status])
        refresh_btn.click(fn=self.gui_load_tools, outputs=[tool_table, tools_status])
        tool_table.select(fn=self.gui_select_tool, inputs=pending_id,
                          outputs=[selected_tool, tool_args, send_btn])

        flow = [timeline, pending_id, approval_group, approval_text, send_btn]
        send_btn.click(fn=self.gui_send_request, inputs=[selected_tool, tool_args], outputs=flow)
        approve_btn.click(fn=self.gui_approve, inputs=pending_id, outputs=flow)
        reject_btn.click(fn=self.gui_reject, inputs=pending_id, outputs=flow)
```

- [ ] **Step 7: Run the verification script again**

Run the Step 1 command. Expected: `TASK 3 OK`.

- [ ] **Step 8: Lint all touched files**

Run the Global Constraints lint command.
Expected: `Your code has been rated at 10.00/10`.

- [ ] **Step 9: Manual GUI check**

Run `mcp_security_env/bin/python app.py server.py` and open http://127.0.0.1:7863. Check:
1. The tool table fills on page load with no button press; policies match `permissions.json` merged with defaults.
2. Click `write_file`: Selected tool and the Arguments template fill; Send enables.
3. Send with `{"filepath": "demo.txt", "content": "hi"}`: timeline shows step 2 waiting; approval card shows the request id and arguments; Send is disabled.
4. Edit the Arguments box, then Approve: the result says it wrote `demo.txt` (the edited values are ignored); card hides; Send re-enables.
5. Send again and Reject: timeline shows "Rejected by you" and "Not sent".
6. Select `delete_file` and Send: "Client policy: DENY", no card.
7. Permissions tab → View Audit Log: entries for each request share a `request_id`.

Stop the app afterwards and delete `data/demo.txt`.

- [ ] **Step 10: No commit** (user commits). Remind the user that `host.py` still calls the removed `call_tool_with_permission()` (spec Known follow-ups).
