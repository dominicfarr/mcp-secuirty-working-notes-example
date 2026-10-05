# Tools tab: client permission approval flow

Date: 2026-10-02 (updated 2026-10-03)
Status: implemented. This document describes the code as built, including changes made after the
original design was approved (see Change history).

## Purpose

The Gradio app (`app.py`) is the no-LLM, zero-token-cost demo of the MCP security model. Its
**Tools** tab teaches the **client-side permission** layer: a tool request is checked against the
client's policy (allow / ask / deny), and when the policy is `ask` the user approves or rejects
*that exact request* before anything reaches the server.

Success: someone running the demo can see who decides, when, and what is logged, and cannot
approve something they did not request or alter a request after it was reviewed.

## Scope

In scope:

- `client.py`: binding request/approve/reject API with request IDs; connection handling.
- `audit.py`: `request_id` on client audit records.
- `tools_view.py`: pure presentation helpers for the Tools tab.
- `app.py`: the Tools tab only.
- `server.py`: functional-only tool descriptions; file-path validation.

(Since 2026-10-03 the client files live in `client/` and the server files in `server/`; see Change history.)

Out of scope (separate conversations):

- MCP elicitation (server-initiated `elicitation/create`): since implemented, see
  `docs/superpowers/specs/2026-10-05-elicitation-design.md`. The old auto-approving
  `request_elicitation()` stub was removed.
- The Permissions tab. (The Resources and Prompts tabs were removed on 2026-10-03; resources
  and prompts are Part 2 topics, see README "Project structure".)
- `host.py` (LLM host, work in progress). See Known follow-ups.
- Automated tests. See Known follow-ups.

## Concepts the demo teaches

- **Permissions** are client/host controls applied before a request leaves the client. The server
  never sees them. They protect the user from the server and from whatever drives the calls.
- **Approval is bound to a request.** Approving means "send this stored request, once", not
  "send whatever is currently in the form".
- **Server-supplied data is untrusted.** Tool names and descriptions come from the server
  (`tools/list`); the client never derives policy from them. The table labels which columns come
  from the server and which from the client.
- **Fail closed.** A policy value the client doesn't recognise denies the call.

## Design

### 1. Client API (`client.py`)

Data types:

```python
POLICIES = ("allow", "ask", "deny")

@dataclass(frozen=True)
class ToolRequest:
    request_id: str      # uuid4().hex[:8]
    tool_name: str
    arguments: dict      # deep copy taken at request time

@dataclass
class ToolOutcome:
    request: ToolRequest
    decision: str        # "ALLOWED" | "DENIED" | "ASK" | "REJECTED"
    reason: str          # "policy: allow", "policy: deny", "policy: invalid value 'X'",
                         # "awaiting approval", "user approved", "rejected by user",
                         # "policy changed to deny"
    content: list | None = None  # server result content, when the request was sent
    is_error: bool = False
```

Reason strings used across modules are constants: `REASON_AWAITING`, `REASON_USER_APPROVED`,
`REASON_POLICY_CHANGED`, `REASON_REJECTED`.

`call_tool_with_permission(tool_name, arguments, approved)` is removed and replaced by:

- `async request_tool(tool_name, arguments) -> ToolOutcome`
  - Builds a `ToolRequest` (deep-copied arguments, new request id).
  - Policy not in `POLICIES` (e.g. a typo such as `"Deny"`): log `DENIED`
    ("policy: invalid value …"), return `DENIED`. Nothing sent.
  - Policy `deny`: log `DENIED`, return `DENIED`. Nothing sent.
  - Policy `allow`: send via `_send`, return `ALLOWED` with content.
  - Policy `ask`: store in `self.pending[request_id]`, log `ASK`, return `ASK` with a **deep copy**
    of the request, so the stored request cannot be changed through the returned outcome.
    Nothing sent.
- `async approve(request_id) -> ToolOutcome`
  - Pops the request from `self.pending`. Unknown or already-used id raises `KeyError`
    (the app turns it into a message). Approval is single-use and cannot be replayed.
  - Re-checks policy. Unless it is still `allow` or `ask`: log `DENIED` ("policy changed to
    deny"), return `DENIED`. Nothing sent.
  - Otherwise send the stored request via `_send` with reason "user approved".
  - Takes no tool name or arguments, so the caller cannot change what is sent.
- `reject(request_id) -> ToolOutcome`
  - Pops the request (unknown id raises `KeyError`), logs `REJECTED` ("rejected by user").
- `_send(request, reason)` logs `ALLOWED`, makes the call, and in a `finally` logs `COMPLETED` or
  `ERROR` (with the server's error text). `McpError` and `OSError` (including `ConnectionError`)
  become an error outcome so the timeline can show them; other exceptions propagate after the
  `ERROR` entry is written.

`check_permission()` is unchanged (argument-specific rule first, then tool rule, default `ask`).

### 2. Connection handling (`client.py`)

anyio requires the stdio connection to be closed in the task that opened it, and Gradio runs each
event in its own task. So one dedicated **holder task** owns the connection:

- `connect()` (guarded by an `asyncio.Lock`, a no-op when `session` is set) starts
  `_hold_connection()` as a task and waits for it to hand over the initialised session. If the
  connection fails, the holder closes what it opened in its own task and passes the error back;
  `innermost_error()` unwraps the stdio transport's nested `ExceptionGroup`s to the real error
  (usually `McpError: Connection closed`).
- `disconnect()` cancels the holder task, which closes the connection in its own task. `cleanup()`
  calls it. Connection state is simply `session is not None`.
- Every server call goes through the `_server_call()` context manager. If the connection turns out
  to be dead (`anyio.ClosedResourceError` / `BrokenResourceError`, or `McpError` with code
  `CONNECTION_CLOSED`), it disconnects so the next call reconnects, and raises
  `ConnectionError` (or re-raises the `McpError`).

### 3. Audit (`audit.py`, `client.py`)

- `AuditLog.record(tool, arguments, outcome, detail="", **context)`; client entries pass
  `request_id=...`.
- Client entries for one request share its id, e.g. `ASK` → `ALLOWED (user approved)` →
  `COMPLETED`.
- The server audit log is unchanged; the request id is a client-side concept.

### 4. Tools tab (`app.py`, `tools_view.py`)

Layout, top to bottom:

1. **Status line**: hidden unless loading failed (shows the error).
2. **Caption**: "Tool and Description are reported by the MCP server (`tools/list`). Policy is this
   client's own setting: its built-in default, or your override in `permissions.json`."
3. **Tool table** (`gr.Dataframe`, read-only) with columns **Tool (server)**,
   **Description (server)** (first non-blank line of the tool description) and
   **Policy (client)**, shown as `<policy> · default` or `<policy> · permissions.json` depending on
   whether it differs from the client default (`policy_label()`). A **↻ Refresh** button reloads
   it. Gradio's built-in column sort/filter menu and cell selection are left at their defaults.
4. **Selected tool** (read-only text box).
5. **Arguments (JSON)** text box.
6. **Send request** button: disabled until a row is selected, and while an approval is pending.
7. **Step timeline** (`gr.Markdown`), redrawn after each step:

   ```
   Request a1b2c3d4: write_file {"filepath": "a.txt", "content": "<500 chars>"}
   ✓ 1  Request built
   ⏸ 2  Client policy: ASK, waiting for your approval
   ·  3  Sent to server
   ·  4  Result
   ```

8. **Approval card** (`gr.Group`, visible only while a request is pending): request id, tool,
   frozen arguments, **Approve** and **Reject** buttons. The pending id is held in `gr.State`,
   never read back from the form.

Behaviour:

- **Page load** (`interface.load`): connect, list tools, fill the table.
- **Row select**: sets the selected tool from the clicked row's values (`evt.row_value[0]`, so it
  is correct after sorting) and fills Arguments with a JSON template from the tool's `inputSchema`
  properties (e.g. `{"filepath": "", "content": ""}`).
- **Send request** → `request_tool()`; timeline shows the outcome:
  - allow: `✓ 2 Client policy: ALLOW` → `✓ 3 Sent to server` → `✓ 4 Result` / `✗ 4 Error`, with the
    result text below.
  - deny (or invalid policy value): `✗ 2 Client policy: DENY` → `· 3 Not sent`; no card.
  - ask: `⏸ 2 …waiting for your approval`; card shown, Send disabled.
- **Approve** → `approve(id)`; card hidden; timeline completes as for allow, with
  `✓ 2 Client policy: ASK, user approved`.
- **Reject** → `reject(id)`; card hidden; `✗ 2 Rejected by you` → `· 3 Not sent`.
- Step 4 shows text content; non-text content is shown as a short type note.
- The old "List Tools", "Call Tool" and "Approve & Execute" buttons and the text tool list are
  removed.

### 5. Server tool descriptions and paths (`server.py`)

- Tool docstrings (sent to clients as descriptions) describe only what each tool does. They carry
  no risk levels or policy wording, because the client must not take policy from the server.
- `resolve_in_workspace()` rejects paths outside `workspace/` ("Access denied …") and paths that resolve
  to a folder, including `""` and `"."` ("A file path is required" / "'x' is not a file path"),
  so tools never operate on a directory and errors don't expose the server's absolute paths.

### 6. Error handling

| Situation | Behaviour |
|---|---|
| Invalid arguments (bad JSON, or JSON that isn't an object) | `✗ 1 Invalid arguments: <message>`. No request created, nothing logged. Empty means `{}`. |
| No tool selected | Send request disabled. |
| Server unreachable on load | Empty table, status line shows the error; Refresh retries. |
| Server error during the call | `✗ 4 Error` with the server's message; client logs `ERROR`. |
| Server crashes or connection lost | `✗ 4 Error` with the connection error; the client drops the dead connection and the next call or Refresh reconnects. |
| Approve/reject an unknown or used id | `✗ Request <id> is no longer pending`. |
| Policy changed (to deny or an invalid value) before approval | `✗ 2 Client policy: DENY (changed since request)`; nothing sent. |
| Blank or folder file path | Server returns "A file path is required" / "'x' is not a file path"; shown as `✗ 4 Error`. |

## Known limitations

- Pending requests live on the single client instance, so all browser tabs share them. Acceptable
  for a local, single-user demo.
- Reloading the page loses the pending request from the GUI (`gr.State` resets), while
  `client.pending` keeps it.
- The table shows policies as of its last load; Permissions-tab changes appear after the Tools tab's Refresh.
- One pending request at a time in the GUI (the client supports several).
- Gradio table cells can't be dropdowns, so policies are not edited in the table (considered and
  set aside on 2026-10-03).

## Known follow-ups

- **`host.py`** still calls `call_tool_with_permission()`, which this change removes. It will
  import but its tool calls fail at runtime until it is migrated to `request_tool()` /
  `approve()` / `reject()` in its own piece of work.
- **Tests** (deferred): unit tests for the client flow with a fake session (deny/allow/ask,
  invalid policy, single-use approve, outcome mutation, reject, policy changed before approval,
  shared `request_id` in audit), integration tests against `server.py` over stdio (including a
  server that crashes mid-call, and blank/folder paths). Requires `pytest` as a dev dependency.
- **Deferred minor issues** from the final review:
  - Double-clicking Approve/Reject can replace the success timeline with "no longer pending"
    (security unaffected).
  - `args_template` fails on schemas with a list `type` or boolean property schemas (not produced
    by `server.py`).
  - Deeply nested JSON arguments raise `RecursionError` (error toast) instead of "Invalid
    arguments".
  - `AuditLog.record`'s `**context` can override the fixed fields.

## Change history

- 2026-10-05: Tools tab gains a "Server asks for input" card with a form generated from the
  server's schema; the timeline shows questions and answers (see
  `docs/superpowers/specs/2026-10-05-elicitation-design.md`). The tool table's status line now sits
  directly above the table.
- 2026-10-05: colour carries meaning: theme primary colour blue (neutral actions), Approve green,
  Reject red (Gradio `stop` variant).
- 2026-10-05: Audit tab tables stacked vertically at full width, each 320 px high with
  scrolling (side by side made long values hard to read). `pyrightconfig.json` treats `client/` and
  `server/` as separate import roots for Pylance.
- 2026-10-04: timeline step 3 shows the server's `roots/list` round trip; the args template
  prefixes `filepath` with the client's root; "Where things run" note (see the roots spec, section 4).
- 2026-10-03: Tools tab gains a Client root control above the table (see
  `docs/superpowers/specs/2026-10-03-roots-design.md`); server file tools are async and
  roots-aware; server error messages no longer include absolute paths.
- 2026-10-02: design approved; implemented per
  `docs/superpowers/plans/2026-10-02-tools-tab-approval-flow.md`.
- 2026-10-02: final review fixes: deep copy on `ASK` outcome; fail-closed policy values;
  holder-task connection handling with reconnect after a server crash (replaces the planned
  `AsyncExitStack` approach).
- 2026-10-03: outside this spec's Tools-tab scope, recorded for context: the Permissions tab loads its
  tool list on page load and holds only policies; a new Audit tab shows the client log beside the server
  log, the latter marked "operator view, demo only" and read from disk by `app.py` (not via MCP).
- 2026-10-03: project split into `client/`, `server/` and `workspace/` (replaces `data/`); each side
  has its own `audit.py`. File paths in this document refer to files now under `client/` or `server/`;
  the server's file tools are confined to `workspace/` (`resolve_in_workspace`, "outside the workspace").
- 2026-10-03: Resources and Prompts tabs removed from `app.py` (client methods and the server's
  `security_review` prompt kept for `host.py`, Part 2); the app now has Tools and Permissions tabs.
- 2026-10-03: server rejects blank/folder file paths; server descriptions made functional-only;
  table columns relabelled Tool (server) | Description (server) | Policy (client) with policy
  source.
