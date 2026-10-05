# Elicitation: server-initiated structured input

Date: 2026-10-05
Status: implemented (plan: docs/superpowers/plans/2026-10-05-elicitation.md)

## Purpose

Part 1 of the project (`client/app.py`, no LLM) teaches the deterministic controls of the MCP
security model. The overview diagram shows elicitation as **server-initiated structured input**:

1. The server needs extra confirmation or data.
2. The server sends a **schema**: fields, types and validation rules.
3. The client presents it to the user, **validates the input against the schema**, and returns
   structured data to the server.

This feature encodes that lesson with one scenario: `delete_file` asks the user to confirm.

Success: someone running the demo sees a question arrive from the server in the middle of a call,
sees a form the client built from the server's schema, sees the client reject input that breaks the
schema, answers it, and sees the server check the answer again before acting.

Not in scope (considered and set aside): other scenarios (a `create_note` tool, an overwrite
choice in `write_file`), and a malicious server eliciting secrets.

## Concepts

- **Mid-call.** The server only learns what it needs while carrying out the call (the file exists,
  its size and date). Its `elicitation/create` request is nested inside the open `tools/call`, which
  waits for the answer:

  ```
  client → server   tools/call  id=7   delete_file(filepath)          ← call opens
  server → client   roots/list  id=s1                                 ← client answers itself
  client → server   result      id=s1  [file:///…/workspace]
  server → client   elicitation/create id=s2 {message, requestedSchema} ← for the human
  client → server   result      id=s2  {action: "accept", content: {…}}
  server → client   result      id=7   "Deleted projects/notes.txt"    ← call closes
  ```

- **Around the caller.** The question goes through the client to the **human**, not to whatever
  made the call. A confirmation passed as a tool argument could be filled in by the caller (in
  Part 2, an LLM); an elicitation answer can't be.
- **Two layers of consent, two owners.** The client's permission policy decides whether the call is
  sent; the server's elicitation confirms details only the server knows.
- **Validate on both sides.** The client validates against the schema before answering; the server
  validates again, because it can't trust the client, and checks rules a schema can't express.
- **Schema vocabulary** (MCP spec): a flat object of primitive properties: string (`minLength`,
  `maxLength`, `format`), number/integer (`minimum`, `maximum`), boolean, enum; plus `required`.
- **Answers:** `accept` (with content), `decline` (an explicit no), `cancel` (dismissed).

## Design

### 1. Server: `delete_file` asks for confirmation (`server/server.py`)

The confirmation is a model that FastMCP turns into the requested schema:

| Field | Type and rules | Meaning |
|---|---|---|
| `confirm_name` | string, required | Type the file path to confirm, e.g. `projects/notes.txt` |
| `reason` | string, required, 5–200 characters | Why it's being deleted; recorded in the server audit log |
| `keep_backup` | boolean, default false | Keep a `.bak` copy instead of deleting outright |

Steps in `delete_file`:

1. The existing checks: inside the workspace, inside the client's roots, a file path not a folder.
2. The file must exist; otherwise "File {filepath} not found". The server doesn't ask about a file
   that isn't there.
3. The client must have declared the elicitation capability; otherwise refuse: "Deletion needs
   confirmation, but this client can't be asked" (fail closed).
4. Ask, with details only the server knows: "Confirm deletion of {filepath} ({size}, modified
   {date})". Wait at most 2 minutes; a timeout counts as cancel.
5. Handle the answer:
   - **accept**: validate again (types, lengths) and check `confirm_name` equals the requested
     `filepath` exactly; a mismatch is refused: "Confirmation did not match: you typed '{x}',
     expected '{filepath}'".
   - **decline**: refuse "Deletion declined by the user".
   - **cancel** (or timeout): refuse "Deletion cancelled" (with "no answer within 2 minutes" for a
     timeout).
6. Resolve the path again after the answer (the user may take minutes; same reasoning as for roots).
7. Delete, or with `keep_backup` rename to `{name}.bak`; if that exists, `{name}.{timestamp}.bak`.
   Never overwrite an existing backup.
8. The server audit log records the outcome; its detail includes the answer (accept / decline /
   cancel), the reason, and whether a backup was kept.

The client's default policy for `delete_file` is `deny`; the demo sets it to `ask` or `allow`. With
`ask`: client policy ASK → user approves → sent → server asks → user confirms → deleted.

### 2. Client: answering mid-call (`client/client.py`)

- `connect()` passes `elicitation_callback=self._on_elicitation` to `ClientSession`, declaring the
  elicitation capability.
- `_send()` runs the tool call as a background task and waits for whichever comes first: the call
  finishes, or the server asks for input.
- `_on_elicitation()` builds an `InputRequest` (id, the server's message, the requested schema, the
  server's name from the initialize handshake, and a future), stores it, signals the waiting
  `_send()`, and awaits the future. `_send()` returns a `ToolOutcome` whose new field
  `input_request` holds the question while the call is still running.
- `answer_input(input_id, action, content=None) -> ToolOutcome`:
  - **accept**: validate `content` against the schema with `jsonschema` first. Errors are raised as
    a list of readable messages (e.g. "reason: too short (minimum 5 characters)"); nothing is sent
    and the question stays open.
  - **decline** / **cancel**: no content.
  - Resolves the future (the callback returns the answer to the server), then waits again for the
    call to finish or another question, and returns the next `ToolOutcome`.
  - An unknown or already-answered id raises `KeyError`. The client can't tell that the server
    has stopped waiting (see Error handling), so a late answer is sent like any other.
- A schema the client can't render faithfully (nested objects, unsupported types) is declined
  automatically, with a note in the outcome.
- `ToolOutcome` gains `inputs`: what the server asked and how the user answered, for the timeline.
- Client audit log: `INPUT ACCEPTED` / `INPUT DECLINED` / `INPUT CANCELLED` per answer, with the
  request id; accepted content is summarised like other arguments. `COMPLETED` / `ERROR` is
  written when the call finishes, as now.
- `approve()` uses the same `_send()`, so approval followed by a question works.
- `jsonschema` is already installed by `mcp`; the README's `pip install` line lists it because the
  client imports it directly.

### 3. Tools tab (`client/app.py`, `client/tools_view.py`)

A **"Server asks for input"** card, separate from the approval card, visible while a question is
waiting:

```
┌ Server "Permission-Based MCP Server" asks (elicitation/create) ─────────┐
│ Confirm deletion of projects/notes.txt (2.3 KB, modified 2026-10-05)    │
│ confirm_name *  [                    ]  required · type the file path   │
│ reason *        [                    ]  required · 5–200 characters     │
│ keep_backup     [ ]                                                     │
│ ▸ Schema sent by the server   (collapsible raw JSON schema)            │
│ ⚠ reason: too short (minimum 5 characters)                              │
│ [ Accept ]   [ Decline ]   [ Cancel ]                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

- Fields are generated from the schema with `@gr.render`: string → text box, boolean → checkbox,
  number/integer → number box with its limits, enum → radio buttons; rules shown as hint text;
  required fields marked `*`.
- The collapsible section shows the raw schema as received.
- Colours: Accept green, Decline red, Cancel neutral.
- The card names the server that is asking.
- Send request stays disabled while a question is waiting.
- Timeline: under step 3, unnumbered lines for the question and the answer:

  ```
  ⏸ Server asked for input (elicitation/create): Confirm deletion of projects/notes.txt …
  ✓ You answered: accept {"confirm_name": "projects/notes.txt", "reason": "old draft", "keep_backup": true}
  ```

### 4. Error handling

| Situation | Behaviour |
|---|---|
| Input fails the schema rules | Errors shown in the card; card stays open; nothing sent |
| Accepted but the server's own check fails | `✗ 4 Error: Confirmation did not match …` |
| Declined / cancelled | `✗ 4 Error: Deletion declined by the user` / `… cancelled` |
| No answer within 2 minutes | Server treats it as cancel and ignores a later answer; the call's result is "Deletion cancelled (no answer within 2 minutes)" and nothing is deleted. The client can't know sooner: the MCP library handles the question inside its message loop, and the protocol sends no "stopped waiting" signal |
| Client lacks elicitation support | Server refuses: "Deletion needs confirmation, but this client can't be asked" |
| File doesn't exist | "File {filepath} not found"; no question asked |
| File changed while the user decided (inode, size or modification time) | "File {filepath} changed while waiting for confirmation; not deleted" |
| Nobody answers (page closed or reloaded) | The client closes the question shortly after the server's timeout; the session carries on |
| Schema the client can't render | Declined automatically, noted in the timeline |
| Connection lost while waiting | `✗ 4 Error` with the connection error |

## Checks

Covered by the Part 1 test suite (`tests/`, see `docs/superpowers/specs/2026-10-05-part1-tests-design.md`). (The cases below were first verified by check scripts during implementation.)

- **Server** (real server, scripted client): accept deletes; `keep_backup` renames to `.bak` (and
  never overwrites an existing one); decline, cancel and a name mismatch refuse; a client without
  elicitation support is refused; a timeout counts as cancel; server-side validation rejects bad
  content even if the client sends it; the audit log records answer, reason and backup.
- **Client**: the call pauses and resumes; schema errors keep the question open and send nothing;
  an unsupported schema is declined automatically; a late or unknown answer is refused; `INPUT …`
  audit entries; approval followed by a question works end to end.
- **GUI**: a form is generated for each field type; the schema is shown; the timeline lines appear;
  Send is disabled while a question waits.
- All existing checks pass; pylint 10/10 on `client/` and `server/`.

## Known limitations

- One question at a time in the GUI, like approvals.
- Reloading the page while a question waits loses the card. The client closes the question itself
  a few seconds after the server's timeout (answering cancel and auditing "INPUT CANCELLED: no
  answer"), because while a question is open the MCP library reads nothing else from the server.
- The 2-minute timeout is fixed (`MCP_DEMO_ELICITATION_TIMEOUT` shortens it for the check scripts;
  the client forwards `MCP_DEMO_*` variables to the server it starts, because stdio passes only a
  few safe variables).
- While a question waits, the client can't receive other messages from the server (the MCP
  library is inside the callback); with one request at a time this doesn't matter in the demo.

## Docs to update with the implementation

- README: Part 1 lists elicitation; a short section on elicitation (mid-call, around the caller,
  two owners of consent, validate on both sides), extending "How a tool call travels" with the
  `elicitation/create` exchange; `jsonschema` in the `pip install` line.
- Tools-tab spec: Change history entry.

## Change history

- 2026-10-05: implemented. Late answers can't be detected by the client (see Error handling);
  `MCP_DEMO_*` variables forwarded to the server for the check scripts.
- 2026-10-05: final-review fixes: client-side question timeout (an abandoned form no longer blocks
  the session); stray queue readers cancelled; automatic declines audited; the server refuses to
  delete a file that changed while the user decided; the auto-approving `request_elicitation()`
  stub removed. Deferred minors: backup rename race, audit note on failed confirmations, fast
  double-click on the card, malformed server schemas, card field order.
