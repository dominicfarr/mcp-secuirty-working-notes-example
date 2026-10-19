# Migration to MCP 2026-07-28 (Part 2, sub-project 2)

Date: 2026-10-19
Status: implemented, not committed (plan: docs/superpowers/plans/2026-10-19-mrtr-migration.md)

## Purpose

Move the reference onto MCP protocol revision **2026-07-28** before Part 2 (LLM in the loop) is
built, so Part 2 starts on the current protocol. The revision changes two things Part 1 relies
on:

- **Multi Round-Trip Requests (MRTR, SEP-2322)** replace server-initiated requests. A server no
  longer sends `roots/list` or `elicitation/create` mid-call and waits. It ends the call with an
  `input_required` result carrying its questions (`inputRequests`) and opaque state
  (`requestState`); the client answers and re-sends the call, with a new request id, carrying the
  answers (`inputResponses`) and the state echoed back. Nothing on the server waits between
  rounds.
- **Roots are deprecated (SEP-2577).** New implementations SHOULD NOT adopt them; existing ones
  SHOULD pass folders through tool parameters, resource URIs or server configuration.

The security interest: what used to live in a paused server function's memory (the path, the
identity of the file the user was asked about) now travels through the client and comes back on
a new, untrusted request.

Sub-project order (decided 2026-10-19): 1. dependency upgrade (done, `7c1324c`); **2. this
migration** (the bridge post follows as its own spec); 3. LLM host; 4. attacks and defences;
5. Part 2 blog series.

Success: the reference speaks only 2026-07-28; every Part 1 guarantee still holds, proved by
tests named after it; the MRTR-specific guarantees below are proved, each seen failing against a
deliberately broken copy; the README describes the new flow; two dated notes on Part 1 posts
point readers to the change.

Out of scope: the bridge post itself (own spec, after the migration is committed, because its
snippets pin that commit); the LLM host; the HTTP transport; 2026-07-28 subscriptions, tasks,
`server/discover` beyond what the client library does; sampling.

## Background (verified 2026-10-19)

- mcp 2.3's `Client` drives MRTR itself: on `input_required` it calls the client's existing
  roots and elicitation callbacks and re-sends the call with the answers and the echoed state
  (`input_required_max_rounds` caps the rounds). A throwaway probe confirmed the round trip and
  that the state comes back exactly as sent.
- fastmcp 4.1 tools ask by returning `InputRequiredResult` and read `ctx.input_responses` /
  `ctx.request_state` on the next round. Returning it on a handshake-era connection is an error
  ("use ctx.elicit()"), so one asking code path cannot serve both eras.
- fastmcp seals `requestState` by default (AES-256-GCM under a per-process key; the boundary
  enforces expiry, binding to the originating request, audience and principal, fail-closed). The
  expiry is `RequestStateSecurity.ephemeral(ttl=...)` (default 600 s). This meets the spec: servers
  MUST treat `requestState` as attacker-controlled, MUST protect its integrity when it affects
  access, SHOULD bind it to principal, expiry and originating request.
- The server always serves both eras (`serve_dual_era_loop`); there is no switch to refuse the
  handshake era.

## Design

### 1. Protocol: 2026-07-28 only

- **Client** connects with the version pinned (`mode="2026-07-28"`), never `auto`, so it can't
  fall back. It records the version as today; `test_client_speaks_the_handshake_protocol` becomes
  `test_client_speaks_2026_07_28`.
- **Server** refuses the handshake era at connection: a fastmcp middleware rejects
  `initialize` with a clear error ("this server speaks MCP 2026-07-28 only"). Fail closed.

### 2. The allowed folder replaces roots

- The client starts the server with `--allowed-folder <absolute path>`. The server's limit is the
  overlap of that folder with its own `workspace/`, by the rule `allowed_areas` applies to roots
  today: a folder inside the workspace narrows it, one containing it gives exactly the workspace,
  any other gives nothing (every file call refused). No argument: the workspace.
- The path is resolved and checked once per call with no await in between (the window that
  roots opened mid-call is gone). Symlink-safe resolution (`resolve_in_workspace`) and the
  no-leak error messages are unchanged.
- Removed: `client_roots`, `file_uri_path`, `resolve_in_roots`, `ROOTS_TIMEOUT_SECONDS`, the
  client's roots callback and `roots_answered` bookkeeping.
- Client: the Tools tab control becomes **"Folder the server is launched with"**; changing it
  restarts the server process (disconnect, relaunch with the new argument). The client audit
  records `FOLDER CONFIGURED` (replacing `ROOTS DECLARED`). Typed folders resolve as typed roots
  do today (relative to the project, `~` expanded).

### 3. `delete_file` in two rounds

- **Round 1** (no `input_responses`): check the client supports elicitation (else refuse, as
  today); resolve the path; check the file exists; take its identity (inode, size, mtime).
  Return `InputRequiredResult` with one form-mode elicitation (`DeleteConfirmation`, message with
  size and modification time, as today) and `request_state` = JSON of the path and identity.
- **Round 2** (`input_responses` present): if `request_state` is missing, ask again (round 1),
  never delete. Otherwise: resolve the path again; refuse if it differs from the sealed path or
  the file's identity differs ("changed while waiting for confirmation"); read the answer —
  decline / cancel refuse as today; accept is validated against the schema and `confirm_name`
  must match; then back up or delete as today.
- **Expiry:** `request_state_security=RequestStateSecurity.ephemeral(ttl=120)` keeps Part 1's
  "unanswered for 2 minutes is refused": a late answer brings back expired state and the
  boundary rejects it. The suite shortens it with an environment variable, as it does the
  elicitation timeout today. `ELICITATION_TIMEOUT_SECONDS` and the server-side wait go.
- **Audit:** each round is its own `tools/call`, so the server logs two records per delete:
  round 1 `input_required` with the question, round 2 the outcome with the user's reason as today.

### 4. Client

- The elicitation callback, form presentation, schema validation, URL-mode decline and audit
  are unchanged; the library calls the callback on each round.
- The callback is no longer cancelled by a server timeout (there is none); the client's own wait
  limit for a person stays and is audited as today.
- One request id covers both rounds in the client log (ALLOWED → INPUT ACCEPTED → COMPLETED).
- Approval binding: the re-sent call must carry exactly the approved arguments (pinned by a test).

### 5. GUI timeline

Shows the rounds: sent → server returned `input_required` (the question; the sealed state shown
truncated, as opaque) → your answer → re-sent with answers → result. The "server asked for
roots" step goes; the folder appears in the request header.

### 6. Tests

Kept unchanged: policy, approval, audit basics, path traversal, blank paths, no path leaks,
URL-mode decline, unsupported forms, schema validation on both sides, confirmation must match,
decline/cancel delete nothing, backups never overwrite, server text never a link.

Roots tests become folder tests: `test_folder_inside_workspace_narrows_access`,
`test_wide_folder_gives_only_the_workspace`, `test_folder_outside_workspace_refuses_everything`,
`test_no_folder_gives_the_workspace`, `test_folder_change_applies_to_a_pending_approval`,
`test_typed_folders_resolve_safely`. Removed with the mechanism: empty roots, non-`file://`
roots, slow roots answer, server asks for roots during the call.

Delete across rounds: `test_delete_asks_with_details_only_the_server_knows` (round 1 returns
`input_required`), `test_file_changed_between_rounds_is_not_deleted`,
`test_symlink_swapped_between_rounds_is_not_followed` (replaces the mid-call symlink test),
`test_expired_confirmation_deletes_nothing` (replaces the late-answer test),
`test_client_without_elicitation_cannot_delete`.

New MRTR guarantees (the re-sent call crafted by hand with mcp's low-level session,
`allow_input_required=True`, as an attacker would):

- `test_tampered_request_state_is_rejected`: one byte changed; rejected, nothing deleted.
- `test_request_state_cannot_be_reused_for_another_file`: state from deleting `a.txt` replayed
  on deleting `b.txt`; rejected.
- `test_answer_without_request_state_deletes_nothing`: answers but no state; asked again.
- `test_retry_sends_the_approved_arguments` (client): the re-sent call's arguments equal the
  approved request's.

Protocol: `test_client_speaks_2026_07_28`; `test_handshake_client_is_refused`.

Each new guarantee is seen failing against a deliberately broken copy (`MCP_TEST_SOURCE`), e.g.
a copy that skips the identity check must turn `test_file_changed_between_rounds_is_not_deleted`
red.

### 7. Docs and blog

- **README:** Versions (2026-07-28 only; Part 1 posts pin `03a9398`; `7c1324c` is the last
  handshake-era commit on current libraries); "How a tool call travels" becomes the MRTR rounds
  diagram; "What roots protect" becomes "The allowed folder" (deprecation, same trust caveat);
  Launch (folder control restarts the server); Elicitation (no server-side wait; 2-minute state
  expiry).
- **Part 1 blog posts** (in `../dominicfarr.github.io`, not committed by me): a short dated note
  near the top (after the first paragraph, so the homepage excerpt and meta description are unchanged) of the **hub** ("Since this series, MCP 2026-07-28 changed how servers ask clients
  for input, and the reference moved to it.") and of the **roots post** ("Roots are deprecated as
  of MCP 2026-07-28 (SEP-2577); new code should pass folders through server configuration."). Each
  links to the bridge post once it exists; until then, to the README's Versions section. The
  other three posts are untouched.
- Spec and plan; project memory.

### 8. Refined while planning (each verified with a throwaway probe, 2026-10-19)

- **Refusing the handshake era happens at connection**, not per request: a fastmcp middleware
  rejects `initialize` (only handshake-era clients send it) with "this server speaks MCP
  2026-07-28 only". A 2026-07-28 client never sends `initialize`.
- **The client drives the rounds itself** rather than letting mcp's `Client.call_tool` do it
  inside the library. It connects with `mcp.Client(..., mode="2026-07-28")` and calls
  `session.call_tool(..., allow_input_required=True)`; on `input_required` it answers each
  question through the existing elicitation handler and re-sends **`request.arguments`** (the
  stored, approved request) with the answers and the echoed state, up to 3 rounds. The binding
  of approval to the re-sent call is then in this repo's code, where a test can pin it, and the
  timeline sees every round. An input request the client never declared (roots, sampling) ends
  the call with an error instead of a retry.
- **Server name:** on 2026-07-28 fastmcp sends no `serverInfo`, so the client falls back to the
  launched script's name for "Server … asks" (2025-11-25 made showing the requesting server a
  MUST).
- **Timeline:** the folder is shown by the folder control, not in each request's header; the
  rounds appear as "server returned `input_required`" with the question, your answer, and
  "re-sent `tools/call` with your answers and the server's sealed state (opaque to this client)".
- **Library-enforced guarantees prove themselves with a positive control.** Tampering, reuse on
  another request and expiry are rejected by mcp's request-state boundary, which this repo can't
  switch off to show a failure. So those tests also show the *untouched* state succeeding on the
  original request: the rejection is then due to the tampering, not to unusable state. Guarantees
  enforced by this repo's code (identity check, missing state, path re-check, approved arguments)
  are proved against broken copies as before.
- **`CANCELLED_CAUSE`** no longer mentions the server stopping waiting (it never waits): a
  cancelled question now means the call itself was cancelled (connection closed or the server
  restarted for a folder change).
- `set_folder` is async (it restarts the server); the GUI handlers that call it become async.

**Found in review (2026-10-19):**

- Under MRTR the open question lives in this repo's own call task, which `disconnect()` didn't
  cancel: after a folder change the question stayed open, unaudited, and a late answer tore down
  the *new* connection. `disconnect()` now cancels calls paused on a question (closing and
  auditing them with `CANCELLED_CAUSE`) and waits for them; `_server_call` only drops the
  connection that failed. Guarantee: `test_folder_change_closes_an_open_question`.
- `FOLDER CONFIGURED` records the folder in the detail field, in full: as an argument it was
  summarised to `<N chars>` past 100 characters. `test_folder_changes_are_audited` uses a long path.
- The seal binds state to its request and expiry but doesn't make it single-use; a replayed
  round 2 is stopped by the identity check (the file is gone). README says so.

## Verification

- `pytest` passes; every guarantee listed in section 6 exists by name.
- Each new MRTR guarantee and each rewritten delete test fails against its broken copy.
- pylint 10/10 on `client/` (excluding `host.py`) and `server/`; `.githooks/pre-push` passes;
  the CI lock check passes.
- The app runs: a delete through the GUI shows both rounds in the timeline and completes; a
  folder change restarts the server and narrows access (checked in a browser).
- One fresh reviewer subagent reads the diff against this spec.

## Decisions made for the user

- State expiry 120 s, matching Part 1's confirmation timeout, rather than the library's 600 s.
- Handshake clients are refused at `initialize` by middleware (the library serves both eras and
  has no switch).
- The folder is a launch argument rather than an environment variable, so it is visible in the
  process command line and the client audit.
