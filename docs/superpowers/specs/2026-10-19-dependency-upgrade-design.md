# Dependency upgrade (Part 2, sub-project 1)

Date: 2026-10-19
Status: implemented, not committed (plan: docs/superpowers/plans/2026-10-19-dependency-upgrade.md)

## Purpose

Move the repo onto the current MCP libraries before Part 2 (LLM in the loop) is built on them,
without changing any Part 1 guarantee or anything the Part 1 blog series quotes.

Part 2 is split into sub-projects, each with its own spec, plan and build:

1. **This upgrade.**
2. LLM host: `client/host.py` rebuilt on `request_tool` / `approve` / `answer_input`.
3. Attacks and defences: hostile server, one threat at a time.
4. Part 2 blog series.
5. Migration to the 2026-07-28 protocol revision (position in the order to be decided), with
   its own bridge post.

Success: all Part 1 tests pass on the new versions; four new tests guard the protocol version,
URL-mode elicitation, cancelled questions and links in server text;
pylint, the pre-push hook and the CI lock check pass; the README says which versions each part
uses; no line quoted by the Part 1 blog posts changes.

Out of scope: `client/host.py` (stays excluded from lint and Sonar), the 2026-07-28 protocol,
any change to Part 1 behaviour.

## Background: what the spike found

A throwaway spike (2026-10-19, scratchpad only) tried three routes against the 72 Part 1 tests.

- mcp is now 2.3 and fastmcp 4.1; fastmcp 4 requires mcp 2.x.
- Both mcp 2.x and fastmcp 4.x support the handshake-era protocols (2024-11-05 to 2025-11-25)
  and the new **2026-07-28** revision. 2026-07-28 removes `initialize` and the server-to-client
  request channel: `roots/list` and `elicitation/create` are replaced by Multi Round-Trip
  Requests (the tool returns `input_required`, the client re-sends the call with answers, and
  server state rides through the client in an encrypted `requestState`). Part 1's roots and
  elicitation are written for the handshake era.
- The client opens with `ClientSession.initialize()`, the handshake path, so it negotiates
  2025-11-25 (`LATEST_HANDSHAKE_VERSION`). 2026-07-28 is not reachable through `initialize`.
- **gradio 6 is forced:** gradio 5 caps pydantic below 2.12; mcp 2.3 needs 2.12 or later.
- With about 19 changed lines, 68 of 72 tests passed.
- When `ctx.list_roots()` disappeared, every file tool call errored instead of skipping the
  roots check: the server failed closed.

Route chosen: **latest libraries on the handshake protocol** (route A). The protocol migration
is sub-project 5.

## Design

### 1. Versions

`requirements.txt` pins exactly: `mcp==2.3.0`, `fastmcp==4.1.1`, `gradio==6.30.0`,
`pydantic==2.14.1` (`jsonschema` unchanged). New transitive packages (e.g. `cryptography`,
`mcp-types`, `fastmcp-slim`) come in through the lock. `requirements-dev.lock` is regenerated
with the README command. Python stays 3.13.

### 2. Code changes

Only what the new APIs require, plus the one line in section 3:

| Change | Where |
|---|---|
| `McpError` → `MCPError` (`mcp.shared.exceptions`) | `server/server.py`, `client/client.py`, tests |
| Result fields camelCase → snake_case: `serverInfo`, `isError`, `requestedSchema`, `inputSchema` | `client/client.py`, `client/app.py`, tests |
| Roots read with `(await ctx.session.list_roots()).roots` (fastmcp 4 removed `ctx.list_roots()`) | `server/server.py` `client_roots` |

The four failures left after the spike are diagnosed and fixed in the plan:

- `test_colours_carry_meaning`: gradio 6 theme API (`primary_500` gone).
- `test_lost_connection_becomes_an_error_outcome`: `MCPError` now takes a `message` argument
  (test helper).
- `test_unsupported_form_is_declined_and_audited`: a `types.UnionType` is called (test helper
  or a changed type alias).
- `test_unanswered_question_does_not_block_the_client`: an elicitation timeout guarantee. Root
  cause first; the fix restores the guarantee and never weakens the assertion or lengthens the
  timeout to make it pass.

Every existing test keeps its name and its guarantee. Edits to tests are limited to the same
API renames, except `test_colours_carry_meaning` (below).

**Found while planning (root causes confirmed in the spike copy):**

- **gradio 6 moved `theme` and `css` from `gr.Blocks` to `launch()`.** Passed to `gr.Blocks`
  they are deprecated: gradio 6.30 warns and still applies them at launch, but `ui.theme` stays
  `None` until then (which is what broke the test), and a later gradio may drop them. (Corrected
  after review: this spec first said they were silently ignored.) Fix: `app.py` keeps `LAUNCH_SETTINGS` (server name, port, `theme=THEME`, `css=APP_CSS`)
  and `main()` launches with it. `test_colours_carry_meaning` checks the launch settings carry
  the theme and the CSS instead of reading `ui.theme`, which gradio 6 leaves `None` until
  launch.
- **The server's give-up now cancels the client's elicitation handler.** When the server stops
  waiting, mcp 2 cancels `_on_elicitation` (`CancelledError`) before the client's own timeout,
  so `_expire` never ran and the dead question stayed answerable in the UI. Fix: expire the
  question on cancellation too (audited as `INPUT CANCELLED`, "no answer: the request was
  cancelled (the server stopped waiting or the connection closed)"), then re-raise. mcp also
  cancels the handler when the connection closes, and the callback's context doesn't say which
  happened, so the audit names both rather than claiming one (`CANCELLED_CAUSE`;
  `test_cancelled_question_is_closed_and_audited`, added after review). `test_unanswered_question_does_not_block_the_client` passes
  unchanged. (Found during implementation:) `test_late_answer_deletes_nothing` assumed the
  client couldn't know the server had given up ("the protocol gives the client no signal"),
  so a late answer was sent and the server refused it. The client now knows, so the late answer
  is refused before it is sent (`KeyError`, as for any closed question). The test keeps its
  name and guarantee, nothing deleted and the server's "no answer within N seconds" recorded
  (now in the client's audit log), with the stricter expectation.
- **The client now advertises URL-mode elicitation.** mcp 2.3's `ClientSession` declares
  `elicitation: {form, url}` whenever an elicitation callback is set; the capability is built in
  a private method, so it can't be narrowed without patching the library. URL mode (new in
  2025-11-25) asks the user to open an external URL and has no schema; the handler read
  `params.requested_schema` and would have failed. Fix: the handler builds forms only from
  `ElicitRequestFormParams`; any other mode is declined automatically and audited, with the URL
  in the audit record, through the existing auto-decline path. New guarantee:
  `test_url_elicitation_is_declined_and_audited`. (Added after review:) the declined question's
  text, URL included, also appears in the Tools tab's timeline, which is Markdown, where a bare
  URL or `[text](url)` would render as a clickable link. Server-supplied question text now goes
  into the timeline as a code span (`tools_view.server_text`); guarantee
  `test_server_text_is_never_a_link`.

### 3. New guarantee: the protocol version

The client records what it negotiated: `_hold_connection` stores
`initialized.protocol_version` as `self.protocol_version`, next to `self.server_name` (one line).

`tests/test_connection.py::test_client_speaks_the_handshake_protocol`: connect to the real
server and assert `client.protocol_version` is in `HANDSHAKE_PROTOCOL_VERSIONS` (today
`2025-11-25`). Roots and elicitation in Part 1 only work as written on that era, so a library
default that silently moves the client to 2026-07-28 must fail the suite.

Proved able to fail: a sabotaged copy of the code (run with `MCP_TEST_SOURCE`, as for other
guarantees) whose client records `"2026-07-28"` instead of the negotiated value makes the test
go red.

### 4. Docs

- README: a short **Versions** section. Part 1 was written against mcp 1.16 / fastmcp 2.12 at
  commit `03a9398`, which the blog posts pin. The repo now runs mcp 2.3 / fastmcp 4.1 / gradio 6
  on protocol 2025-11-25. The 2026-07-28 revision is planned separately. Setup text updated if
  any command changed.
- This spec and its plan; project memory (`mcp-demo-scope`) updated with the new versions and
  the sub-project list.
- **Spec-page check:** compare the roots, elicitation and tools pages of the 2025-06-18 and
  2025-11-25 specs (the Part 1 posts quote 2025-06-18). Report any change to a quoted MUST or
  SHOULD to the user; don't edit the posts.

### 5. Blog

The Part 1 posts stay pinned to `03a9398`. The plan re-runs the blog snippet checker against
the upgraded code to confirm no quoted line changed; if any did, it's reported, not fixed.

Suggested to the user (their action): tag the Part 1 state before the upgrade lands, e.g.
`git tag part-1 a8a754a`.

## Verification

- `pytest`: all 76 tests pass (72 existing + 4 new; two of them added after review).
- Each new test is seen failing first (URL mode: before the fix; protocol: against the
  sabotaged client).
- pylint on `client/` (excluding `host.py`) and `server/` is clean; `.githooks/pre-push` passes.
- The CI lock check passes locally (every pinned requirement is in the lock).
- The blog snippet checker reports no quoted line changed.
- One fresh reviewer subagent reads the diff against this spec.

CI and SonarQube Cloud run when the user pushes.

## Decisions made for the user

- Exact pins rather than ranges, as before.
- pydantic is pinned to 2.14.1 (latest at the time), not the 2.12 minimum.
- The new test checks the protocol era, not one exact version, so a future handshake-era
  revision would still pass.
