# Part 1 test suite

Date: 2026-10-05
Status: implemented (plan: docs/superpowers/plans/2026-10-05-part1-tests.md)

## Purpose

Part 1 (`client/app.py`, no LLM) is feature-complete: tool permissions, binding approval, audit on
both sides, server path validation, roots and elicitation. Its behaviour is currently verified by
22 throwaway check scripts in `.superpowers/sdd/*/`. This suite replaces them with a `pytest` suite
in the repo that is:

1. **A readable security spec**: tests are grouped by control and named as the guarantees they
   prove, so a reader of this reference repo can read them as a list of guarantees.
2. **A safety net for Part 2**: fast and reliable locally, so changes made while building the LLM
   layer (`client/host.py`) can't silently break Part 1's guarantees.
3. **Run in CI**: GitHub Actions runs lint and tests on every push and pull request.

Success: `pytest` runs the whole suite from a clean checkout, never touching the user's
`workspace/` or logs; every old check is covered (see the mapping table); CI is green on Linux and
macOS.

Out of scope: a coverage percentage target (the guarantee list is the target), browser-level GUI
tests (GUI tests call handlers and inspect the interface configuration), and `client/host.py`
(Part 2).

## Design

### 1. How the tests run

- **Isolation by a session copy.** The client and server locate their folders from their own file
  locations (`client.PROJECT_DIR`, `DEFAULT_ROOT`, `permissions.json`; the server's `workspace/` and
  `logs/`). At the start of the run, `tests/conftest.py` copies `client/` and `server/` into a
  temporary project folder (plus an empty `workspace/`), puts that copy's `client/` on `sys.path`,
  and the tests import `client`, `app`, `tools_view`, `audit_view` from it. Every path points into
  the temporary project with no patching. The repo's own `workspace/`, `server/logs/` and
  `client/*.log` are never written.
- **A clean slate per test.** A fixture empties the temporary `workspace/`, removes the client and
  server logs, writes a fresh `permissions.json` (the test can pass its own policies), and yields a
  new `MCPPermissionClientApp` whose server script is the copy's `server/server.py`; it calls
  `cleanup()` afterwards.
- **The server is tested only from outside, over MCP.** Tests never import `server.py`; they start
  it as a child process (through the client, or through a raw `ClientSession` with scripted
  callbacks when a test needs to behave like a different client). This keeps client and server
  separate parties and avoids the clash between the two `audit.py` modules.
- **Async tests** use the `anyio` pytest plugin (`anyio` is already installed): `@pytest.mark.anyio`
  with an `anyio_backend` fixture returning `"asyncio"`.
- **Short timeouts.** `MCP_DEMO_ELICITATION_TIMEOUT=3` is set before the copy is imported, so
  elicitation timeout tests take seconds. (The client forwards `MCP_DEMO_*` variables to the server.)
- **Markers.** Tests that start a real server are marked `integration`. `pytest -m "not
  integration"` runs the fast tests in seconds; `pytest` runs everything (about 1 minute).
- **Config.** `pytest.ini` (the project has no `pyproject.toml`): `testpaths = tests`, the
  `integration` marker registered, strict markers.
- **Dependencies.** `requirements.txt` pins the runtime packages (`mcp==1.16.0`, `fastmcp==2.12.5`,
  `gradio==5.49.1`, `pydantic==2.11.10`, `jsonschema==4.26.0`, and `openai==2.6.1` for `host.py`).
  `requirements-dev.txt` contains `-r requirements.txt` and a pinned `pytest`. The README and CI
  install from these files, so versions are listed once. `pytest` is the only new dependency.

### 2. The guarantees

`[I]` starts a real server; `[U]` doesn't.

**`tests/test_permissions.py`: the client's policy decides what is sent**
- `test_allow_sends_the_call` [I]
- `test_deny_never_reaches_the_server` [I]: the server log has no entry
- `test_ask_holds_the_call_until_approved` [U]
- `test_unknown_policy_value_fails_closed` [U]: `"Deny"`, `"block"`
- `test_policy_change_before_approval_is_enforced` [U]
- `test_policy_shows_default_or_override_source` [U]

**`tests/test_approval.py`: an approval is bound to one request**
- `test_approval_sends_the_stored_request_not_the_form` [U]
- `test_approval_is_single_use` [U]
- `test_returned_outcome_cannot_redirect_the_approval` [U]
- `test_reject_sends_nothing` [U]

**`tests/test_audit.py`: both sides keep their own record**
- `test_each_side_logs_in_its_own_folder` [I]: `client/client_audit.log`, `server/logs/server_audit.log`
- `test_client_entries_share_the_request_id` [U]
- `test_server_logs_every_call_including_refusals` [I]
- `test_long_arguments_are_summarised_not_logged` [U]
- `test_tool_context_is_not_logged` [I]
- `test_root_changes_are_audited` [U]
- `test_unreadable_log_lines_are_shown_not_hidden` [U]

**`tests/test_workspace.py`: the server confines file tools to its workspace**
- `test_path_traversal_is_refused` [I]: `../../etc/passwd`, absolute paths
- `test_blank_or_folder_path_is_refused` [I]
- `test_errors_never_reveal_server_paths` [I]
- `test_symlink_swapped_during_a_call_is_not_followed` [I]

**`tests/test_roots.py`: roots narrow the server, never widen it**
- `test_root_inside_workspace_narrows_access` [I]
- `test_wide_root_gives_only_the_workspace` [I]: `/`, the project folder
- `test_root_outside_workspace_refuses_everything` [I]
- `test_empty_roots_refuse_everything` [I]
- `test_client_without_roots_support_gets_the_workspace` [I]
- `test_only_local_file_uris_count` [I]: remote host, other schemes, NUL
- `test_slow_roots_answer_is_refused` [I]
- `test_server_asks_for_roots_during_the_call` [I]: the round trip appears in the outcome
- `test_roots_are_checked_when_the_call_is_sent` [I]: narrowing while an approval waits
- `test_typed_roots_resolve_safely` [U]: `~`, `..`, relative paths

**`tests/test_elicitation.py`: the server asks the human, and both sides validate**
- `test_delete_asks_with_details_only_the_server_knows` [I]
- `test_client_validates_against_the_schema_before_sending` [I]
- `test_server_validates_again` [I]: content sent without client checks
- `test_confirmation_must_match_the_file` [I]
- `test_decline_and_cancel_delete_nothing` [I]
- `test_approval_then_question_works_end_to_end` [I]
- `test_client_without_elicitation_cannot_delete` [I]
- `test_unanswered_question_does_not_block_the_client` [I]
- `test_late_answer_deletes_nothing` [I]
- `test_file_changed_while_deciding_is_not_deleted` [I]
- `test_backup_never_overwrites` [I]
- `test_unsupported_form_is_declined_and_audited` [U]
- `test_no_auto_approving_shortcut` [U]: the old `request_elicitation()` stub stays gone

**`tests/test_connection.py`: the client recovers**
- `test_unreachable_server_is_reported_not_raised` [I]: and Refresh reconnects once it's reachable
- `test_lost_connection_becomes_an_error_outcome` [U]
- `test_reconnects_after_the_server_crashes` [I]
- `test_interrupted_request_does_not_steal_the_next_question` [I]

**`tests/test_gui.py`: the GUI shows the security model accurately**
- `test_tabs_and_page_load_events` [U]
- `test_table_labels_server_and_client_columns` [I]
- `test_server_descriptions_carry_no_policy` [I]
- `test_invalid_arguments_are_rejected_before_sending` [U]
- `test_args_template_points_inside_the_root` [U]
- `test_root_control_shows_what_the_client_declares` [U]
- `test_timeline_shows_each_step` [U]
- `test_form_is_generated_from_the_schema` [U]
- `test_server_log_panel_is_marked_demo_only` [U]
- `test_audit_tables_are_stacked` [U]
- `test_colours_carry_meaning` [U]

### 3. CI

`.github/workflows/tests.yml`:
- On push and pull request to `main`.
- Matrix: `ubuntu-latest` and `macos-latest`, Python 3.13.
- Steps: check out; set up Python; `pip install -r requirements-dev.txt`; pylint on `client/` and
  `server/` (run from inside each folder with `--rcfile=../.pylintrc`, excluding `host.py`); `pytest`.
- A status badge at the top of the README for `dominicfarr/mcp-secuirty-working-notes-example`.
- Pylint is a CI dependency too, so it is pinned in `requirements-dev.txt`.

### 4. Docs and retiring the checks

- README: Setup installs with `pip install -r requirements-dev.txt`; a **Tests** section (running
  all or fast tests, layout by guarantee, isolation from the user's files); the CI badge.
- The "Tests (deferred)" follow-ups in the Tools-tab, roots and elicitation specs point to this
  suite.
- Once the suite passes and every row below is covered, the `.superpowers/sdd/` folders are
  deleted, after asking the user.

## Mapping: old checks to new tests

| Check script | Covered by |
|---|---|
| `t1_check.py` (client flows, fake session) | `test_permissions` (allow/deny/ask/policy change), `test_approval`, `test_audit::test_client_entries_share_the_request_id`, `test_connection::test_lost_connection_becomes_an_error_outcome` |
| `t2_check.py` (view helpers) | `test_gui::test_timeline_shows_each_step`, `test_invalid_arguments_are_rejected_before_sending` |
| `fix_check.py` (outcome mutation, fail closed) | `test_approval::test_returned_outcome_cannot_redirect_the_approval`, `test_permissions::test_unknown_policy_value_fails_closed`, `test_policy_change_before_approval_is_enforced` |
| `t3_check.py` (GUI handlers, real server) | `test_gui::test_timeline_shows_each_step`, `test_approval`, `test_workspace::test_path_traversal_is_refused` |
| `t3b_check.sh` (failed connect, retry) | `test_connection::test_unreachable_server_is_reported_not_raised` |
| `fix2_check.py` (server crash) | `test_connection::test_reconnects_after_the_server_crashes` |
| `dir_check.sh` | `test_workspace::test_blank_or_folder_path_is_refused`, `test_errors_never_reveal_server_paths` |
| `layout_check.sh` | `test_audit::test_each_side_logs_in_its_own_folder` |
| `audit_tab_check.sh` | `test_gui::test_tabs_and_page_load_events`, `test_server_log_panel_is_marked_demo_only`, `test_audit::test_unreadable_log_lines_are_shown_not_hidden` |
| `labels_check.sh` | `test_gui::test_table_labels_server_and_client_columns`, `test_server_descriptions_carry_no_policy`, `test_permissions::test_policy_shows_default_or_override_source` |
| `tabs_check.py` | `test_gui::test_tabs_and_page_load_events` |
| `audit_layout_check.py` | `test_gui::test_audit_tables_are_stacked` |
| `colours_check.py` | `test_gui::test_colours_carry_meaning` |
| `roots_server_check.sh` | `test_roots` (narrow, wide, outside, empty, no support, slow), `test_workspace::test_errors_never_reveal_server_paths`, `test_audit::test_tool_context_is_not_logged` |
| `roots_client_check.sh` | `test_roots::test_typed_roots_resolve_safely`, `test_roots_are_checked_when_the_call_is_sent`, `test_audit::test_root_changes_are_audited` |
| `roots_gui_check.sh` | `test_gui::test_root_control_shows_what_the_client_declares` |
| `roots_hardening_check.sh` | `test_roots::test_only_local_file_uris_count`, `test_workspace::test_symlink_swapped_during_a_call_is_not_followed` |
| `roots_trace_check.sh` | `test_roots::test_server_asks_for_roots_during_the_call`, `test_gui::test_args_template_points_inside_the_root` |
| `elicitation_server_check.sh` | `test_elicitation` (asks with details, server validates again, must match, decline/cancel, no support, backup) |
| `elicitation_client_check.sh` | `test_elicitation::test_client_validates_against_the_schema_before_sending`, `test_approval_then_question_works_end_to_end`, `test_late_answer_deletes_nothing`, `test_unsupported_form_is_declined_and_audited` |
| `elicitation_gui_check.sh` | `test_gui::test_form_is_generated_from_the_schema`, `test_timeline_shows_each_step` |
| `final_fixes_check.sh` | `test_elicitation::test_unanswered_question_does_not_block_the_client`, `test_file_changed_while_deciding_is_not_deleted`, `test_unsupported_form_is_declined_and_audited`, `test_no_auto_approving_shortcut`, `test_connection::test_interrupted_request_does_not_steal_the_next_question` |

## Change history

- 2026-10-05: implemented: 72 tests (59 named guarantees plus parametrised cases and three suite
  isolation tests), about 1 minute in full, under 1 s for `-m "not integration"`. Each task's tests
  were shown to fail against deliberately broken copies of the code.
- 2026-10-05: final-review fixes: `test_unsupported_form_is_declined_and_audited` now calls the
  client's real elicitation handler (it previously passed with the decline check removed); the CI
  workflow gains `permissions: contents: read`, a 15-minute job timeout, and a cache key covering
  both requirements files.
- Deferred minor issues: actions pinned by tag rather than commit SHA; `make_app` teardown stops at
  the first failing cleanup; the path-leak check could also look for the temp folder's name; the
  isolation tests don't check the server log and one depends on test order; the slow-roots test
  could wait 7 s instead of 10; `tests/` is not linted.
