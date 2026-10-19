# Dependency upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the repo to mcp 2.3 / fastmcp 4.1 / gradio 6 on the handshake protocol (2025-11-25) with every Part 1 guarantee intact, plus two new guarantees.

**Architecture:** Pin the new versions and regenerate the lock; apply the API renames; fix three behaviour changes the upgrade introduces (gradio theme/CSS moved to `launch()`, elicitation handler cancelled by the server, URL-mode elicitation advertised); add a protocol-era guard test; update docs.

**Tech Stack:** Python 3.13, mcp 2.3.0, fastmcp 4.1.1, gradio 6.30.0, pydantic 2.14.1, pytest, uv (for the lock).

**Spec:** docs/superpowers/specs/2026-10-19-dependency-upgrade-design.md

## Global Constraints

- Pins, exact: `mcp==2.3.0`, `fastmcp==4.1.1`, `gradio==6.30.0`, `pydantic==2.14.1`; `jsonschema==4.26.0` unchanged.
- Lock regenerated with: `uv pip compile --universal --generate-hashes --python-version 3.13 requirements-dev.txt -o requirements-dev.lock`
- Client stays on the handshake protocol (`ClientSession.initialize()`); no 2026-07-28 work.
- `client/host.py` untouched, still excluded from lint and Sonar.
- Existing tests keep their names and guarantees; edits are API renames only, except `test_colours_carry_meaning`.
- Never weaken an assertion or lengthen a timeout to make a test pass.
- No git commits or tags (the user commits). Every task ends with the full suite run instead.
- Run everything with the project venv: `mcp_security_env/bin/python -m pytest`.

## Review Focus

1. A server sends a URL-mode elicitation: the client must decline it, never open or show the URL as a link, and audit it (Task 4 test).
2. The server gives up on a question before the client does: the stale question must close, be audited, and not be answerable later (existing `test_unanswered_question_does_not_block_the_client`, Task 3).
3. The app launches with the theme and CSS actually applied, not silently dropped (Task 2 test checks the launch settings).
4. A future library default moves the client off the handshake protocol: the suite must fail (Task 5 test).
5. A roots or elicitation API disappears again in a later upgrade: the server must still fail closed (covered by the existing roots and elicitation tests, which errored rather than passed in the spike; no new test).

---

### Task 1: Versions, lock and API renames

**Files:**
- Modify: `requirements.txt` (pins)
- Modify: `requirements-dev.lock` (regenerated)
- Modify: `server/server.py:15,117,118` (`MCPError`, roots via session)
- Modify: `client/client.py:16,181,211,272,399,407,413` (`MCPError`, snake_case fields)
- Modify: `client/app.py` (`input_schema`)
- Modify: `tests/helpers.py`, `tests/test_elicitation.py`, `tests/test_connection.py` (renames; `MCPError` constructor)

**Interfaces:**
- Produces: `from mcp.shared.exceptions import MCPError` everywhere (replaces `McpError`); `MCPError(code, message)`.

- [ ] **Step 1: Pin the versions**

In `requirements.txt` set:

```
mcp==2.3.0
fastmcp==4.1.1
gradio==6.30.0
pydantic==2.14.1
jsonschema==4.26.0
```

(keep the `# only needed for client/host.py (Part 2)` / `#openai==2.6.1` lines as they are).

- [ ] **Step 2: Regenerate the lock and install**

```bash
uv pip compile --universal --generate-hashes --python-version 3.13 requirements-dev.txt -o requirements-dev.lock
mcp_security_env/bin/python -m pip install -r requirements-dev.txt
mcp_security_env/bin/python -c "import importlib.metadata as m; print(*[p+'='+m.version(p) for p in ('mcp','fastmcp','gradio','pydantic')])"
```

Expected: `mcp=2.3.0 fastmcp=4.1.1 gradio=6.30.0 pydantic=2.14.1`.

- [ ] **Step 3: Run the suite to see the baseline failure**

Run: `mcp_security_env/bin/python -m pytest -q`
Expected: collection errors, `ImportError: cannot import name 'McpError' from 'mcp.shared.exceptions'`.

- [ ] **Step 4: Rename `McpError` → `MCPError`**

```bash
perl -pi -e 's/\bMcpError\b/MCPError/g' server/server.py client/client.py tests/test_connection.py
```

In `tests/test_connection.py`, the constructor now takes code and message (no `ErrorData`):

```python
    client.session = FakeSession(raises=MCPError(-32000, "Connection closed"))
```

and drop `from mcp.types import ErrorData` if nothing else uses it.

- [ ] **Step 5: Rename camelCase result fields**

```bash
perl -pi -e 's/\bserverInfo\b/server_info/g; s/\bisError\b/is_error/g; s/\brequestedSchema\b/requested_schema/g; s/\binputSchema\b/input_schema/g' client/client.py client/app.py tests/helpers.py tests/test_elicitation.py
grep -rn "serverInfo\|isError\|requestedSchema\|inputSchema" client/*.py server/*.py tests/ | grep -v host.py
```

Expected: no output from the grep.

- [ ] **Step 6: Read roots through the session**

fastmcp 4 removed `ctx.list_roots()`. In `server/server.py` `client_roots`:

```python
        with anyio.fail_after(ROOTS_TIMEOUT_SECONDS):
            roots = (await ctx.session.list_roots()).roots
```

- [ ] **Step 7: Run the suite**

Run: `mcp_security_env/bin/python -m pytest -q`
Expected: 69 passed, 3 failed: `test_colours_carry_meaning` (Task 2), `test_unanswered_question_does_not_block_the_client` (Task 3), `test_unsupported_form_is_declined_and_audited` (Task 4). If anything else fails, stop and diagnose before continuing.

---

### Task 2: gradio 6 theme and CSS at launch

**Files:**
- Modify: `client/app.py` (`gr.Blocks(...)` at ~183; `main()` at ~347-349; new `LAUNCH_SETTINGS` next to `THEME`)
- Test: `tests/test_gui.py::test_colours_carry_meaning`

**Interfaces:**
- Produces: `app.LAUNCH_SETTINGS: dict` with keys `server_name`, `server_port`, `theme`, `css`.

- [ ] **Step 1: Rewrite the test to check what gradio 6 uses**

```python
def test_colours_carry_meaning():
    # gradio 6 applies the theme and CSS at launch, so check what main() launches with
    assert app_module.LAUNCH_SETTINGS["theme"].primary_500 == gr.themes.colors.blue.c500
    assert ".approve-btn" in app_module.LAUNCH_SETTINGS["css"]
    ui = app_module.MCPPermissionClientApp("unused.py").create_interface()
    buttons = {c["props"].get("value"): c["props"] for c in ui.get_config_file()["components"]
               if c["type"] == "button"}
    assert "approve-btn" in buttons["Approve"]["elem_classes"]
    assert buttons["Reject"]["variant"] == "stop"
```

- [ ] **Step 2: Run it to see it fail**

Run: `mcp_security_env/bin/python -m pytest -q tests/test_gui.py::test_colours_carry_meaning`
Expected: FAIL, `AttributeError: module 'app' has no attribute 'LAUNCH_SETTINGS'`.

- [ ] **Step 3: Move theme and CSS to launch**

In `client/app.py`, after `APP_CSS` is defined:

```python
# gradio 6 applies the theme and CSS at launch(); passed to gr.Blocks they are silently ignored
LAUNCH_SETTINGS = {"server_name": "127.0.0.1", "server_port": 7863, "theme": THEME, "css": APP_CSS}
```

Change the Blocks line to:

```python
        with gr.Blocks(title="MCP Permission Client") as interface:
```

and in `main()`:

```python
    interface.queue().launch(**LAUNCH_SETTINGS)
```

- [ ] **Step 4: Run it to see it pass, then the suite**

Run: `mcp_security_env/bin/python -m pytest -q tests/test_gui.py`
Expected: all pass.

- [ ] **Step 5: Check the real app**

Run `mcp_security_env/bin/python client/app.py server/server.py` in the background, open `http://127.0.0.1:7863` in a headless browser (puppeteer, as in the blog checks) and confirm the Approve button's computed background is green (`rgb(22, 163, 74)` is gradio green c600) and there are no gradio warnings about `theme`/`css` in the app's output. Stop the app by the PID you started.

---

### Task 3: Close questions the server abandons

**Files:**
- Modify: `client/client.py` (`_on_elicitation` ~267-280, `_expire` ~282-294)
- Test: `tests/test_elicitation.py::test_unanswered_question_does_not_block_the_client` (unchanged)

**Interfaces:**
- Produces: `MCPPermissionClient._expire(question: InputRequest, reason: str)`.

- [ ] **Step 1: Confirm the test fails for the known reason**

Run: `mcp_security_env/bin/python -m pytest -q tests/test_elicitation.py::test_unanswered_question_does_not_block_the_client`
Expected: FAIL, `Failed: DID NOT RAISE KeyError` (the question stayed open).

- [ ] **Step 2: Expire on cancellation as well as timeout**

In `_on_elicitation`, replace the `try/except TimeoutError` block with:

```python
        try:
            return await asyncio.wait_for(asyncio.shield(question.answer),
                                          QUESTION_TIMEOUT_SECONDS)
        except TimeoutError:
            self._expire(question, f"no answer within {QUESTION_TIMEOUT_SECONDS:g} seconds")
            return ElicitResult(action="cancel")
        except asyncio.CancelledError:
            # mcp 2 cancels this handler when the server stops waiting; close the question too
            self._expire(question, "no answer: the server stopped waiting")
            raise
```

and make `_expire` take the reason:

```python
    def _expire(self, question: InputRequest, reason: str):
        """Nobody answered in time: close the question so the session can carry on, record it,
        and log the call's outcome when it ends (no handler is waiting for it any more)."""
        if not question.answer.done():
            question.answer.set_result(ElicitResult(action="cancel"))
        _, call = self._open_questions.pop(question.input_id, (None, None))
        if call is None:
            return
        call.inputs.append((question.message, "cancel", None))
        self.audit_log.record(call.request.tool_name, {}, "INPUT CANCELLED",
                              f"{reason}: {question.message}", request_id=call.request.request_id)
        call.task.add_done_callback(lambda _task: self._finish_unattended(call))
```

- [ ] **Step 3: Run the elicitation tests**

Run: `mcp_security_env/bin/python -m pytest -q tests/test_elicitation.py tests/test_connection.py`
Expected: all pass except `test_unsupported_form_is_declined_and_audited` (Task 4). In particular `test_late_answer_deletes_nothing` and `test_interrupted_request_does_not_steal_the_next_question` still pass.

---

### Task 4: Decline URL-mode elicitation

**Files:**
- Modify: `client/client.py` (imports ~17; `_on_elicitation` ~267-274)
- Modify: `tests/test_elicitation.py` (imports; `test_unsupported_form_is_declined_and_audited` construction; new test)

**Interfaces:**
- Consumes: `INPUT_AUTO_DECLINED`, `CallInProgress`, `ToolRequest`, `client._follow` (existing).
- Produces: `test_url_elicitation_is_declined_and_audited`.

- [ ] **Step 1: Fix the existing test's construction**

`ElicitRequestParams` is now a union (form | URL) and can't be constructed. In `tests/test_elicitation.py` import `ElicitRequestFormParams, ElicitRequestURLParams` from `mcp.types` (drop `ElicitRequestParams`) and in `test_unsupported_form_is_declined_and_audited` use:

```python
        None, ElicitRequestFormParams(message="nested form", requested_schema=nested)), 5)
```

- [ ] **Step 2: Write the URL-mode test**

```python
@pytest.mark.anyio
async def test_url_elicitation_is_declined_and_audited(make_client):
    client, _ = make_client()
    params = ElicitRequestURLParams(message="Sign in to continue",
                                    url="https://example.com/login", elicitation_id="e1")
    # Declined at once: this client only presents forms and never opens a server's URL
    answer = await asyncio.wait_for(client._on_elicitation(  # pylint: disable=protected-access
        None, params), 5)
    assert answer.action == "decline"
    call = CallInProgress(ToolRequest("r2", "delete_file", {"filepath": "x"}), "policy: allow", 0)
    finished = asyncio.get_running_loop().create_future()
    call.task = asyncio.ensure_future(finished)
    asyncio.get_running_loop().call_later(0.1, finished.set_result, CallToolResult(
        content=[TextContent(type="text", text="done")], is_error=False))
    outcome = await client._follow(call)  # pylint: disable=protected-access
    [(message, answer_text, _)] = outcome.inputs
    assert answer_text == INPUT_AUTO_DECLINED and "https://example.com/login" in message
    assert any(entry["outcome"] == "INPUT DECLINED" and entry.get("request_id") == "r2"
               and "https://example.com/login" in entry["detail"] for entry in client_log())
```

- [ ] **Step 3: Run both to see them fail**

Run: `mcp_security_env/bin/python -m pytest -q tests/test_elicitation.py -k "unsupported_form or url_elicitation"`
Expected: `test_unsupported_form_is_declined_and_audited` PASSES (Step 1 fixed it); `test_url_elicitation_is_declined_and_audited` FAILS with `AttributeError: 'ElicitRequestURLParams' object has no attribute 'requested_schema'`.

- [ ] **Step 4: Build forms only from form-mode params**

In `client/client.py` import `ElicitRequestFormParams` from `mcp.types` (keep `ElicitRequestParams` for the annotation). At the top of `_on_elicitation`:

```python
    async def _on_elicitation(self, _context, params: ElicitRequestParams) -> ElicitResult:
        """The server asks the user something mid-call: hand it to the waiting call, then wait
        for the user's answer. Only forms are presented; a form the client can't present, or
        any other mode (such as a URL to open), is declined automatically."""
        if isinstance(params, ElicitRequestFormParams):
            message, schema = params.message, params.requested_schema
        else:  # URL mode: never shown as a link or opened; recorded so the audit shows it
            message, schema = f"{params.message} [{params.mode} mode: {params.url}]", {}
        question = InputRequest(uuid.uuid4().hex[:8], self.server_name, message, schema,
                                asyncio.get_running_loop().create_future())
        if not is_supported_schema(question.schema):
            question.answer.set_result(ElicitResult(action="decline"))
```

(the rest of the method as in Task 3).

- [ ] **Step 5: Run the elicitation tests and the suite**

Run: `mcp_security_env/bin/python -m pytest -q`
Expected: 73 passed.

---

### Task 5: Guard the protocol era

**Files:**
- Modify: `client/client.py` (`__init__` ~141; `_hold_connection` ~181)
- Test: `tests/test_connection.py` (new test)

**Interfaces:**
- Produces: `MCPPermissionClient.protocol_version: str` (`""` until connected).

- [ ] **Step 1: Write the test**

In `tests/test_connection.py` add `from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS` and:

```python
@pytest.mark.integration
@pytest.mark.anyio
async def test_client_speaks_the_handshake_protocol(make_app):
    # Roots and elicitation work as Part 1 describes only on handshake-era protocols; 2026-07-28
    # replaces them with multi round-trip requests
    app = make_app()
    await app.list_tools()
    assert app.protocol_version in HANDSHAKE_PROTOCOL_VERSIONS, app.protocol_version
```

- [ ] **Step 2: Run it to see it fail**

Run: `mcp_security_env/bin/python -m pytest -q tests/test_connection.py::test_client_speaks_the_handshake_protocol`
Expected: FAIL, `AttributeError: ... has no attribute 'protocol_version'`.

- [ ] **Step 3: Record the negotiated version**

In `__init__`, after `self.server_name = ""`:

```python
        self.protocol_version = ""  # the MCP protocol revision negotiated with the server
```

In `_hold_connection`, after `self.server_name = initialized.server_info.name`:

```python
                    self.protocol_version = initialized.protocol_version
```

- [ ] **Step 4: Run it to see it pass**

Run the same command. Expected: PASS.

- [ ] **Step 5: Prove it can fail**

Copy `client/` and `server/` to the scratchpad, change the copied line to `self.protocol_version = "2026-07-28"`, and run:
`MCP_TEST_SOURCE=<scratch copy> mcp_security_env/bin/python -m pytest -q tests/test_connection.py::test_client_speaks_the_handshake_protocol`
Expected: FAIL with `2026-07-28` in the message. Delete the scratch copy.

- [ ] **Step 6: Full suite and lint**

```bash
mcp_security_env/bin/python -m pytest -q
(cd client && ../mcp_security_env/bin/python -m pylint --rcfile=../.pylintrc $(ls ./*.py | grep -v host.py))
(cd server && ../mcp_security_env/bin/python -m pylint --rcfile=../.pylintrc ./*.py)
```

Expected: 74 passed; pylint 10.00/10 on both.

---

### Task 6: Docs and checks

**Files:**
- Modify: `README.md` (new "Versions" section after "Project structure"; Setup if any command changed)
- Modify: `docs/superpowers/specs/2026-10-19-dependency-upgrade-design.md` (Status: implemented)
- Modify: memory `mcp-demo-scope.md` (sub-project 1 done)

- [ ] **Step 1: README Versions section**

```markdown
### Versions

Part 1 was written against mcp 1.16 and fastmcp 2.12, at commit `03a9398`; the [blog series](https://www.domfarr.com/2026/10/01/mcp-security.html) links to that commit. The repo now runs mcp 2.3, fastmcp 4.1 and gradio 6, and the client speaks the MCP protocol revision **2025-11-25** (it connects with the `initialize` handshake; `test_client_speaks_the_handshake_protocol` fails if that changes).

MCP 2026-07-28 removes the handshake and replaces the server's mid-call `roots/list` and `elicitation/create` requests with multi round-trip requests: the tool returns `input_required` and the client re-sends the call with the answers. That changes how roots and elicitation work, so it is planned as its own step rather than folded into an upgrade.

The client declares URL-mode elicitation (mcp 2.3 always does) but declines every URL-mode request and records the URL in its audit log; it only presents forms.
```

- [ ] **Step 2: Spec-page comparison**

Fetch `https://modelcontextprotocol.io/specification/2025-06-18/{client/roots,client/elicitation,server/tools}.md` and the `2025-11-25` equivalents (use `ctx_execute` with `fetch`, not curl). For each, compare the "Security Considerations" lists and any MUST/SHOULD line the Part 1 posts quote:
- tools: "SHOULD always be a human in the loop…", Clients SHOULD prompt / show inputs / log, Servers MUST validate inputs / access controls, Clients MUST consider tool annotations untrusted
- roots: Clients MUST validate root URIs; Servers SHOULD respect boundaries / validate paths
- elicitation: Servers MUST NOT request sensitive information; Both parties SHOULD validate; Clients SHOULD allow decline

Report every difference to the user. Do not edit the blog posts.

- [ ] **Step 3: Blog snippet check**

Run the blog checker (`verify.py` from the blog work, in the scratchpad; recreate it from `docs/superpowers/plans/2026-10-06-blog-series.md` Task 11 if gone) with its `SHA` set to the working tree: compare each Part 1 snippet's lines against the current files instead of `git show SHA:`. Expected: every snippet still matches, at the same line ranges. Report any that don't; don't edit the posts.

- [ ] **Step 4: Pre-push hook and lock check**

```bash
.githooks/pre-push
for pin in $(grep -h -o '^[A-Za-z0-9_.-]*==[^ ]*' requirements.txt requirements-dev.txt); do grep -qi "^${pin} " requirements-dev.lock || echo "MISSING $pin"; done
```

Expected: hook passes (lint, unit, integration); no `MISSING` lines.

- [ ] **Step 5: Mark the spec implemented and update memory**

Spec `Status: implemented, not committed (plan: …)`. In memory `mcp-demo-scope.md`: sub-project 1 done (versions, 74 tests), next is sub-project 2 (LLM host).

---

### Task 7: Review

- [ ] **Step 1: One fresh reviewer**

Dispatch one reviewer subagent (opus) with the spec, this plan and `git diff` of the repo. Ask it to check: every change is required by the upgrade or the spec; no assertion weakened or timeout lengthened; the URL-mode decline and cancellation fixes are correct under concurrency (one question at a time, as in the GUI); the README is accurate.

- [ ] **Step 2: Act on findings**

Re-grade by effect on a reader of this security reference. Fix the important ones with a failing test first, re-run Task 6 Step 4, and list deferred minors and every decision made for the user in the final message.
