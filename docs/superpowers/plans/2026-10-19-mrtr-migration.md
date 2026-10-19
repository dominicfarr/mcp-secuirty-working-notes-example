# MRTR migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the reference to MCP 2026-07-28: roots replaced by a launch folder, `delete_file` confirmed over multi round-trip requests with sealed state, the client driving the rounds itself.

**Architecture:** Task 1 replaces roots with `--allowed-folder` while still on the handshake protocol (everything stays green). Task 2 switches both sides to 2026-07-28: server middleware refuses `initialize`, `delete_file` becomes two rounds with sealed `requestState`, the client connects with `mcp.Client(mode="2026-07-28")` and drives `input_required` rounds itself. Task 3 adds the attacker-style MRTR tests. Task 4 docs, Task 5 app check and review.

**Tech Stack:** Python 3.13, mcp 2.3.0, fastmcp 4.1.1, gradio 6.30.0, pytest.

**Spec:** docs/superpowers/specs/2026-10-19-mrtr-migration-design.md (section 8 records what planning refined)

## Global Constraints

- Protocol: client pinned to `"2026-07-28"` (never `auto`); server refuses `initialize` with "this server speaks MCP 2026-07-28 only".
- Folder: `--allowed-folder <absolute path>`; limit = overlap with `workspace/` (inside narrows, containing gives exactly the workspace, elsewhere gives nothing); error text "outside the allowed folder".
- Sealed state: `RequestStateSecurity.ephemeral(ttl=CONFIRMATION_TTL_SECONDS)`, `CONFIRMATION_TTL_SECONDS = float(os.environ.get("MCP_DEMO_ELICITATION_TIMEOUT", "120"))` (the suite sets 3).
- Client rounds: `INPUT_ROUNDS_MAX = 3`; every round re-sends `request.arguments`.
- Existing guarantees keep their names unless the spec renames them; never weaken an assertion or lengthen a timeout.
- No git commits (the user commits); `client/host.py` untouched; run with `mcp_security_env/bin/python -m pytest`.
- Lint: pylint 10/10 on `client/` (excluding `host.py`) and `server/`.

## Review Focus

1. A server returns `input_required` asking for roots or sampling (never declared): the client must end the call with an error, not answer or loop (Task 2 test `test_undeclared_input_request_ends_the_call`).
2. A server keeps returning `input_required`: the client stops after 3 rounds with an error (Task 2 test `test_rounds_are_capped`).
3. The folder is changed while a question is open: the call ends cancelled and audited, never answered into the new server (covered by `test_cancelled_question_is_closed_and_audited` with the new cause text; behaviour checked in Task 5's browser run).
4. A delete round 2 arrives for a file that has since disappeared: refused "not found", nothing else touched (Task 2 test `test_file_removed_between_rounds_is_reported`).
5. A server's `input_required` carries no `request_state`: the client must not invent one (pinned inside `test_retry_sends_the_approved_arguments` by a scripted session that omits state on a second variant).

---

### Task 1: The allowed folder replaces roots

**Files:**
- Modify: `server/server.py` (remove roots code; add `ALLOWED_FOLDER`, `allowed_area`, `resolve_allowed`, `parse_launch_arguments`; tools use `resolve_allowed`)
- Modify: `client/client.py` (`folder`, `set_folder`, `_resolve_folder`, launch argument; remove roots callback and bookkeeping; `ToolOutcome.rounds`, `CallInProgress.rounds`)
- Modify: `client/tools_view.py` (folder helpers; timeline drops roots)
- Modify: `client/app.py` (folder control)
- Rename+rewrite: `tests/test_roots.py` → `tests/test_folder.py`
- Modify: `tests/helpers.py`, `tests/test_workspace.py`, `tests/test_gui.py`, `tests/test_audit.py`, `tests/test_elicitation.py`

**Interfaces:**
- Produces: `MCPPermissionClient.folder: Path`; `async set_folder(path: str | Path) -> Path`; `ToolOutcome.rounds: list[str]` (replaces `roots_answered`, same position); `CallInProgress(request, reason)` with `rounds: list[str]`, `inputs: list`; helpers `server_params(folder=None) -> StdioServerParameters`, `call_server(tool, arguments, folder=None, elicitation=None)`; tools_view `folder_choices`, `folder_display`, `parse_folder`, `launch_line(folder: Path) -> str`, `folder_prefix`.

- [ ] **Step 1: Write the folder tests**

Delete `tests/test_roots.py`; create `tests/test_folder.py`:

```python
"""The folder the server is launched with narrows it; it never widens it."""

from pathlib import Path

import pytest

from helpers import PROJECT, WORKSPACE, call_server, client_log, outcome_text


def setup_files():
    (WORKSPACE / "projects").mkdir()
    (WORKSPACE / "top.txt").write_text("hi")
    (WORKSPACE / "projects" / "p.txt").write_text("hi")


@pytest.mark.integration
@pytest.mark.anyio
async def test_folder_inside_workspace_narrows_access(make_app):
    setup_files()
    app = make_app({"write_file": "allow"})
    await app.set_folder("workspace/projects")
    inside = await app.request_tool("write_file", {"filepath": "projects/a.txt", "content": "x"})
    outside = await app.request_tool("write_file", {"filepath": "a.txt", "content": "x"})
    assert not inside.is_error
    assert outside.is_error and "outside the allowed folder" in outcome_text(outside)


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("folder", [Path("/"), PROJECT])
async def test_wide_folder_gives_only_the_workspace(folder):
    setup_files()
    assert await call_server("read_file", {"filepath": "top.txt"}, folder=folder) == (False, "hi")
    is_error, text = await call_server("read_file", {"filepath": "../x.txt"}, folder=folder)
    assert is_error and "outside the workspace" in text


@pytest.mark.integration
@pytest.mark.anyio
async def test_folder_outside_workspace_refuses_everything():
    setup_files()
    is_error, text = await call_server("read_file", {"filepath": "top.txt"},
                                       folder=PROJECT / "elsewhere")
    assert is_error and "outside the allowed folder" in text


@pytest.mark.integration
@pytest.mark.anyio
async def test_no_folder_gives_the_workspace():
    setup_files()
    assert await call_server("read_file", {"filepath": "top.txt"}) == (False, "hi")


@pytest.mark.integration
@pytest.mark.anyio
async def test_folder_change_applies_to_a_pending_approval(make_app):
    setup_files()
    app = make_app({"write_file": "ask"})
    outcome = await app.request_tool("write_file", {"filepath": "b.txt", "content": "x"})
    await app.set_folder("workspace/projects")  # narrowed while the approval waits
    approved = await app.approve(outcome.request.request_id)
    assert approved.is_error and "outside the allowed folder" in outcome_text(approved)


@pytest.mark.anyio
async def test_typed_folders_resolve_safely(make_client):
    client, _ = make_client()
    assert await client.set_folder("~") == Path.home().resolve()
    assert await client.set_folder("workspace/../") == PROJECT
    assert await client.set_folder("workspace") == WORKSPACE
    assert client_log()[-1]["outcome"] == "FOLDER CONFIGURED"
```

In `tests/test_audit.py` replace `test_root_changes_are_audited` with:

```python
@pytest.mark.anyio
async def test_folder_changes_are_audited(make_client):
    client, _ = make_client()
    folder = await client.set_folder("workspace")
    entry = client_log()[-1]
    assert (entry["tool"], entry["outcome"], entry["args"]) == ("(folder)", "FOLDER CONFIGURED",
                                                               {"folder": str(folder)})
```

In `tests/test_gui.py` replace the two root tests:

```python
@pytest.mark.anyio
async def test_args_template_points_inside_the_folder():
    app = app_module.MCPPermissionClientApp("unused.py")
    app.tool_schemas = {"write_file": {"properties": {"filepath": {"type": "string"},
                                                      "content": {"type": "string"}}}}
    await app.set_folder("workspace/FOLDER")
    assert json.loads(app.gui_select_tool("", select_row("write_file"))[1])["filepath"] == "FOLDER/"
    for folder in ("workspace", "/"):
        await app.set_folder(folder)
        assert json.loads(app.gui_select_tool("", select_row("write_file"))[1])["filepath"] == ""


@pytest.mark.anyio
async def test_folder_control_shows_what_the_server_is_launched_with():
    (WORKSPACE / "projects").mkdir()
    app = app_module.MCPPermissionClientApp("unused.py")
    box, launched = app.gui_load_folder()
    assert box["choices"] == ["workspace/", "workspace/projects/", WHOLE_DISK]
    assert launched == f"Server launched with: `--allowed-folder {DEFAULT_ROOT}`"
    box, launched = await app.gui_set_folder("workspace/projects/")
    assert str(DEFAULT_ROOT / "projects") in launched
    with pytest.warns(UserWarning, match="Enter a folder path"):  # the user is told
        box, launched = await app.gui_set_folder("   ")  # and an empty value keeps the folder
    assert box["value"] == "workspace/projects/"
```

In `tests/test_gui.py::test_timeline_shows_each_step` change the `full = timeline_for(...)` call's roots list `["file:///w"]` to `[]` and remove `"no root"`, `"↩ Server asked the client for its roots"`, `"file:///w"` from the checked lines (Task 2 adds the round lines).

In `tests/test_elicitation.py` every `CallInProgress(ToolRequest(...), "policy: allow", 0)` becomes `CallInProgress(ToolRequest(...), "policy: allow")` (three places).

In `tests/test_workspace.py` delete `test_symlink_swapped_during_a_call_is_not_followed` (there is no longer an await inside a file call; Task 2 adds the between-rounds version) and drop `from mcp.types import ListRootsResult, Root` and any import left unused.

- [ ] **Step 2: Update the helpers**

In `tests/helpers.py` delete `roots_answer` and `raw_roots`, drop `ListRootsResult, Root` from the `mcp.types` import, and replace `call_server` with:

```python
def server_params(folder=None) -> StdioServerParameters:
    """How the tests launch the real server: optionally with --allowed-folder."""
    args = [SERVER_SCRIPT] + (["--allowed-folder", str(folder)] if folder is not None else [])
    return StdioServerParameters(command=sys.executable, args=args, env=dict(os.environ))


async def call_server(tool, arguments, folder=None, elicitation=None) -> tuple[bool, str]:
    """Call the real server directly, as a different MCP client would (a scripted elicitation
    callback; None means the capability isn't declared). Returns (is_error, text)."""
    async with stdio_client(server_params(folder)) as (read, write):
        async with ClientSession(read, write, elicitation_callback=elicitation) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
    return result.is_error, result.content[0].text
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `mcp_security_env/bin/python -m pytest -q tests/test_folder.py tests/test_audit.py tests/test_gui.py`
Expected: FAIL — `AttributeError: ... 'set_folder'` / `'gui_load_folder'`, and the server rejects `--allowed-folder` (unrecognised argument) in `test_wide_folder_gives_only_the_workspace`.

- [ ] **Step 4: Server — launch folder instead of roots**

In `server/server.py`:
- Delete `ROOTS_TIMEOUT_SECONDS`, `allowed_areas`, `client_roots`, `file_uri_path`, `resolve_in_roots`, and imports left unused (`unquote, urlparse`, `RootsCapability`, `MCPError`, `anyio` only if `confirm_deletion` no longer needs it — it still does until Task 2, keep it).
- Add `import argparse` and `import sys`, and after `resolve_in_workspace`:

```python
# The folder the client launched this server with (--allowed-folder), or None for the whole
# workspace. It is server configuration: roots, which did this job as a request, are deprecated as
# of MCP 2026-07-28 (SEP-2577).
ALLOWED_FOLDER: Path | None = None


def allowed_area() -> Path | None:
    """Where the file tools may work: the launch folder's overlap with the workspace. A folder
    inside the workspace narrows it, one containing it gives exactly the workspace, and any other
    gives nothing (None)."""
    base = WORKSPACE_DIR.resolve()
    if ALLOWED_FOLDER is None:
        return base
    folder = ALLOWED_FOLDER.resolve()
    if folder.is_relative_to(base):
        return folder
    return base if base.is_relative_to(folder) else None


def resolve_allowed(filepath: str) -> Path:
    """resolve_in_workspace, then require the path to be inside the allowed folder."""
    file_path = resolve_in_workspace(filepath)
    area = allowed_area()
    if area is None or not file_path.is_relative_to(area):
        raise ToolError(f"Access denied: {filepath} is outside the allowed folder")
    return file_path


def parse_launch_arguments(argv: list[str]) -> Path | None:
    """The --allowed-folder the client launched this server with, if any."""
    parser = argparse.ArgumentParser(description="MCP server for the permission-client demo")
    parser.add_argument("--allowed-folder", type=Path, default=None,
                        help="the only folder (within the workspace) the file tools may use")
    return parser.parse_args(argv).allowed_folder
```

- `read_file` and `write_file`: `file_path = resolve_allowed(filepath)`; they no longer need `ctx`, so remove the `ctx: Context` parameter and make them plain `def` (the `@audited` wrapper handles sync tools).
- `delete_file`: both `await resolve_in_roots(filepath, ctx)` become `resolve_allowed(filepath)`.
- Main block:

```python
if __name__ == "__main__":
    ALLOWED_FOLDER = parse_launch_arguments(sys.argv[1:])
    mcp.run(transport="stdio")
```

- [ ] **Step 5: Client — folder, launch argument, rounds field**

In `client/client.py`:
- `ToolOutcome`: replace `roots_answered: list[str] | None = None  # roots the client sent when the server asked` with `rounds: list[str] = field(default_factory=list)  # sealed states the server returned (input_required)`.
- `CallInProgress`: replace `asked_before: int` with nothing and add `rounds: list[str] = field(default_factory=list)` after `task`.
- `__init__`: replace the `self.roots` and `self._last_roots_answer` lines with `self.folder: Path = DEFAULT_ROOT  # the folder the server is launched with`.
- `DEFAULT_ROOT` comment: "the folder the server is launched with by default: the user's files".
- `_hold_connection`: `args=[self.server_script, "--allowed-folder", str(self.folder)]`; `ClientSession(read, write, elicitation_callback=self._on_elicitation)` (no roots callback).
- Replace `set_roots`, `root_uris`, `_resolve_root`, `_list_roots` with:

```python
    async def set_folder(self, path: str | Path) -> Path:
        """Set the folder the server is launched with (--allowed-folder) and audit it.

        Relative paths resolve against the project folder and ~ expands. The server reads it at
        launch, so the running server is stopped and the next call starts one with the new
        folder. Returns the resolved folder."""
        self.folder = self._resolve_folder(path)
        self.audit_log.record("(folder)", {"folder": str(self.folder)}, "FOLDER CONFIGURED")
        await self.disconnect()
        return self.folder

    @staticmethod
    def _resolve_folder(path: str | Path) -> Path:
        """A typed folder as an absolute, resolved path."""
        path = Path(path).expanduser()
        return (path if path.is_absolute() else PROJECT_DIR / path).resolve()
```

- Delete `_roots_answered_since`. In `_send`: `call = CallInProgress(request, reason)`. In `_follow`: `return ToolOutcome(call.request, "ALLOWED", call.reason, rounds=list(call.rounds), inputs=list(call.inputs), input_request=question)`. In `_finish`: drop `roots = ...`; pass `call.rounds` where `roots` was passed (both returns).
- Drop `ListRootsResult, Root` from the `mcp.types` import.

- [ ] **Step 6: GUI — the folder control**

In `client/tools_view.py`: rename `root_choices`→`folder_choices`, `root_display`→`folder_display`, `parse_root`→`parse_folder`, `root_prefix`→`folder_prefix` (same bodies; docstrings say "folder"); update the `WHOLE_DISK` comment to "Folder preset meaning "/": launching with the whole disk, which still gives the server only its workspace"; replace `declared_line` with:

```python
def launch_line(folder: Path) -> str:
    """The line under the folder control: exactly what the server is launched with."""
    return f"Server launched with: `--allowed-folder {folder}`"
```

In `timeline_for`: the sent step becomes `(DONE, "Sent to server (tools/call: name + arguments)")` and the `if outcome.roots_answered is not None:` block is deleted. `args_template` docstring: "points inside the allowed folder".

In `client/app.py`: update the import list; `gui_select_tool` uses `folder_prefix(self.folder, DEFAULT_ROOT)`; replace `gui_load_roots` / `gui_set_root` with:

```python
    def gui_load_folder(self):
        """Folder presets (workspace and its subfolders) and the folder the server is launched
        with."""
        return (gr.update(choices=folder_choices(DEFAULT_ROOT),
                          value=folder_display(self.folder, PROJECT_DIR)),
                launch_line(self.folder))

    async def gui_set_folder(self, value: str):
        """Relaunch the server with the chosen folder; an empty value keeps the current one."""
        path = parse_folder(value or "")
        if path is None:
            gr.Warning("Enter a folder path")
        else:
            await self.set_folder(path)
        return (gr.update(value=folder_display(self.folder, PROJECT_DIR)),
                launch_line(self.folder))
```

Replace `_build_root_control` with `_build_folder_control` (call it from the Tools tab instead):

```python
    def _build_folder_control(self, interface: gr.Blocks):
        """Folder picker: the folder the server is launched with (--allowed-folder)."""
        folder_box = gr.Dropdown(
            label="Folder the server is launched with (--allowed-folder)",
            info="The server may only work in this folder, and never outside its own workspace. "
                 "Changing it restarts the server.",
            choices=[], allow_custom_value=True)
        launched = gr.Markdown()
        interface.load(fn=self.gui_load_folder, outputs=[folder_box, launched])
        folder_box.input(fn=self.gui_set_folder, inputs=folder_box, outputs=[folder_box, launched])
```

and the "Where things run" Markdown becomes:

```python
        gr.Markdown("**Where things run:** This app is the MCP client. The server is a separate "
                    "process it started on this machine (stdio), so both see the same disk; "
                    "`workspace/` is the user's files. A tool call carries only the tool name and "
                    "arguments. The folder the server may use is set when the client launches it "
                    "(`--allowed-folder`); MCP roots, which used to do this, are deprecated.")
```

- [ ] **Step 7: Run the suite**

Run: `mcp_security_env/bin/python -m pytest -q`
Expected: all pass (72 existing − 10 roots/symlink tests + 6 folder tests + renamed audit/GUI tests = 70 tests; read the count, not this arithmetic, and confirm no failures). Then pylint on `client/` and `server/`: 10.00/10.

---

### Task 2: 2026-07-28 and the two-round delete

**Files:**
- Modify: `server/server.py` (middleware, sealed state, `audited`, `ask_to_confirm`, `confirmed`, `delete_file`; remove `confirm_deletion`, `duration_text`, `ELICITATION_TIMEOUT_SECONDS`)
- Modify: `client/client.py` (connect with `mcp.Client`; `_call_server` round loop; `_answer_input_requests`; constants)
- Modify: `client/tools_view.py` (timeline rounds)
- Modify: `tests/helpers.py` (`call_server` on 2026-07-28; `raw_session`; `RoundsSession`; `FakeSession.call_tool(**_)`)
- Modify: `tests/test_elicitation.py`, `tests/test_workspace.py`, `tests/test_connection.py`, `tests/test_approval.py`, `tests/test_gui.py`, `tests/test_audit.py`

**Interfaces:**
- Consumes: Task 1's `resolve_allowed`, `ToolOutcome.rounds`, `CallInProgress.rounds`, `server_params`.
- Produces: server `CONFIRM_KEY = "confirm"`; client `PROTOCOL_VERSION = "2026-07-28"`, `INPUT_ROUNDS_MAX = 3`, `CANCELLED_CAUSE` (new text); helpers `raw_session(folder=None)` (async context manager yielding a `ClientSession` on 2026-07-28 with elicitation declared), `RoundsSession(with_state=True)`.

- [ ] **Step 1: Write the protocol and round tests**

`tests/test_connection.py`: replace `test_client_speaks_the_handshake_protocol` (and its `HANDSHAKE_PROTOCOL_VERSIONS` import) with:

```python
@pytest.mark.integration
@pytest.mark.anyio
async def test_client_speaks_2026_07_28(make_app):
    app = make_app()
    await app.list_tools()
    assert app.protocol_version == "2026-07-28"


@pytest.mark.integration
@pytest.mark.anyio
async def test_handshake_client_is_refused():
    # A handshake-era client (initialize) can't be served: this server's confirmations use multi
    # round-trip requests, which only exist on 2026-07-28
    try:
        async with Client(server_params(), mode="legacy") as client:
            await client.list_tools()
    except Exception as e:  # pylint: disable=broad-exception-caught
        error = innermost_error(e)
    else:
        pytest.fail("a handshake-era client was served")
    assert "2026-07-28 only" in str(error)
```

(imports: `from mcp import Client`, `from client import innermost_error`, `server_params` from helpers).

`tests/helpers.py` — add, and make `FakeSession.call_tool(self, name, arguments, **_rounds)`:

```python
class RoundsSession:
    """A scripted 2026-07-28 session: the first call returns input_required with one question,
    the retry returns a result. Records (name, arguments, request_state) per call."""

    def __init__(self, with_state=True):
        self.calls, self.with_state = [], with_state

    async def call_tool(self, name, arguments, input_responses=None, request_state=None,
                        allow_input_required=False):  # pylint: disable=unused-argument
        self.calls.append((name, dict(arguments), request_state))
        if input_responses is None:
            question = ElicitRequest(params=ElicitRequestFormParams(
                message="OK?", requested_schema={"type": "object",
                                                 "properties": {"ok": {"type": "boolean"}}}))
            return InputRequiredResult(input_requests={"q": question},
                                       request_state="sealed" if self.with_state else None)
        return CallToolResult(content=[TextContent(type="text", text="done")], is_error=False)
```

`tests/test_approval.py` — add:

```python
@pytest.mark.anyio
@pytest.mark.parametrize("with_state", [True, False])
async def test_retry_sends_the_approved_arguments(make_client, with_state):
    client, _ = make_client({"write_file": "ask"})
    session = client.session = RoundsSession(with_state)
    arguments = {"filepath": "a.txt", "content": "hi"}
    asked = await client.request_tool("write_file", arguments)
    arguments["filepath"] = "EVIL.txt"  # the form is edited while the call is in progress
    paused = await client.approve(asked.request.request_id)
    done = await client.answer_input(paused.input_request.input_id, "accept", {"ok": True})
    assert not done.is_error
    approved = {"filepath": "a.txt", "content": "hi"}
    # Every round re-sends the approved request; the client never invents state
    assert session.calls == [("write_file", approved, None),
                             ("write_file", approved, "sealed" if with_state else None)]
```

`tests/test_elicitation.py`:
- rename `test_file_changed_while_deciding_is_not_deleted` → `test_file_changed_between_rounds_is_not_deleted` (body unchanged);
- replace `test_late_answer_deletes_nothing` with:

```python
@pytest.mark.integration
@pytest.mark.anyio
async def test_expired_confirmation_deletes_nothing(make_app):
    app = make_app({"delete_file": "allow"})
    (WORKSPACE / "b.txt").write_text("x")
    paused = await app.request_tool("delete_file", {"filepath": "b.txt"})
    await asyncio.sleep(ELICITATION_TIMEOUT + 1)  # the sealed state has expired; the client waits on
    late = await app.answer_input(paused.input_request.input_id, "accept", good("b.txt"))
    assert late.is_error and "Invalid or expired requestState" in outcome_text(late)
    assert (WORKSPACE / "b.txt").exists()
```

- add:

```python
@pytest.mark.integration
@pytest.mark.anyio
async def test_file_removed_between_rounds_is_reported():
    (WORKSPACE / "b.txt").write_text("x")

    async def remove_then_accept(_context, _params):
        (WORKSPACE / "b.txt").unlink()
        return ElicitResult(action="accept", content=good("b.txt"))

    is_error, text = await call_server("delete_file", {"filepath": "b.txt"},
                                       elicitation=remove_then_accept)
    assert is_error and "File b.txt not found" in text


@pytest.mark.anyio
async def test_undeclared_input_request_ends_the_call(make_client):
    client, _ = make_client({"read_file": "allow"})

    class AsksForRoots:
        calls = 0

        async def call_tool(self, *_args, **_kwargs):
            AsksForRoots.calls += 1
            return InputRequiredResult(input_requests={"r": ListRootsRequest()})

    client.session = AsksForRoots()
    outcome = await client.request_tool("read_file", {"filepath": "a.txt"})
    assert outcome.is_error and "roots/list" in outcome_text(outcome) and AsksForRoots.calls == 1


@pytest.mark.anyio
async def test_rounds_are_capped(make_client):
    client, _ = make_client({"read_file": "allow"})

    class AlwaysAsks:
        calls = 0

        async def call_tool(self, *_args, **_kwargs):
            AlwaysAsks.calls += 1
            return InputRequiredResult(request_state="again")

    client.session = AlwaysAsks()
    outcome = await client.request_tool("read_file", {"filepath": "a.txt"})
    assert outcome.is_error and "after 3 rounds" in outcome_text(outcome) and AlwaysAsks.calls == 3
```

(imports: `InputRequiredResult, ListRootsRequest` from `mcp.types`).

`tests/test_workspace.py` — add (imports `os`, `shutil`, `ElicitResult`):

```python
async def test_symlink_swapped_between_rounds_is_not_followed():
    outside = PROJECT / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "x.txt").write_text("keep me")
    (WORKSPACE / "projects").mkdir()
    (WORKSPACE / "projects" / "x.txt").write_text("x")

    async def swap_then_accept(_context, _params):
        shutil.rmtree(WORKSPACE / "projects")
        os.symlink(outside, WORKSPACE / "projects")
        return ElicitResult(action="accept", content={"confirm_name": "projects/x.txt",
                                                      "reason": "old draft"})

    is_error, text = await call_server("delete_file", {"filepath": "projects/x.txt"},
                                       elicitation=swap_then_accept)
    assert is_error and "outside the workspace" in text
    assert (outside / "x.txt").read_text() == "keep me"
```

`tests/test_audit.py` — add:

```python
@pytest.mark.integration
@pytest.mark.anyio
async def test_each_round_is_audited():
    (WORKSPACE / "a.txt").write_text("x")
    await call_server("delete_file", {"filepath": "a.txt"}, elicitation=elicitation_answer(
        "accept", {"confirm_name": "a.txt", "reason": "old draft"}))
    entries = server_log()
    assert [(e["tool"], e["outcome"]) for e in entries] == [("delete_file", "input_required"),
                                                           ("delete_file", "success")]
    assert entries[0]["detail"].startswith("asked: Confirm deletion of a.txt")
    assert "reason: old draft" in entries[1]["detail"]
```

`tests/test_gui.py::test_timeline_shows_each_step`: the `full` outcome's rounds become `["v1.abcdefghijklmnop"]` and the checked lines are `"Server returned `input_required` and asked", "✗ You answered: decline", "✗ Declined automatically", "Re-sent `tools/call`", "`v1.abcdefghi…`"`.

- [ ] **Step 2: Switch the helpers to 2026-07-28**

In `tests/helpers.py` (imports: `contextlib`, `from mcp import Client`, `from mcp.shared.exceptions import MCPError`, `ElicitRequest, ElicitRequestFormParams, InputRequiredResult` from `mcp.types`; `from client import innermost_error` after the `sys.path` insert):

```python
async def call_server(tool, arguments, folder=None, elicitation=None) -> tuple[bool, str]:
    """Call the real server directly, as a different 2026-07-28 client would: mcp's Client drives
    any input_required rounds through the scripted elicitation callback (None means the
    capability isn't declared). Returns (is_error, text); a protocol error counts as an error."""
    try:
        async with Client(server_params(folder), mode="2026-07-28",
                          elicitation_callback=elicitation) as client:
            result = await client.call_tool(tool, arguments)
    except Exception as e:  # pylint: disable=broad-exception-caught
        error = innermost_error(e)
        if not isinstance(error, MCPError):
            raise
        return True, str(error)
    return result.is_error, result.content[0].text


@contextlib.asynccontextmanager
async def raw_session(folder=None):
    """A 2026-07-28 session to the real server whose rounds the test drives by hand, as an
    attacker's client would (elicitation declared; the callback is never used)."""
    async with Client(server_params(folder), mode="2026-07-28",
                      elicitation_callback=elicitation_answer("decline")) as client:
        yield client.session
```

- [ ] **Step 3: Run the new tests to see them fail**

Run: `mcp_security_env/bin/python -m pytest -q tests/test_connection.py tests/test_approval.py tests/test_elicitation.py tests/test_workspace.py tests/test_audit.py tests/test_gui.py`
Expected: failures, including `test_client_speaks_2026_07_28` (`2025-11-25`), `test_handshake_client_is_refused` (served), `test_retry_sends_the_approved_arguments` (the client doesn't drive rounds: `RoundsSession` result is not a `CallToolResult`), the delete tests over `call_server` (`ctx.elicit` is unavailable on 2026-07-28), `test_each_round_is_audited`, `test_timeline_shows_each_step`.

- [ ] **Step 4: Server**

In `server/server.py` (imports: `json`; `from fastmcp.server.middleware import Middleware`; `from mcp.server.request_state import RequestStateSecurity`; `ElicitRequest, ElicitRequestFormParams, ElicitResult, InputRequiredResult` from `mcp.types`; drop `anyio`, `CancelledElicitation`, `DeclinedElicitation`):

Replace `ELICITATION_TIMEOUT_SECONDS` with:

```python
# How long a confirmation stays valid. Nothing on the server waits for the answer (MCP 2026-07-28):
# the sealed requestState carries it, and the framework refuses it once this has passed. The
# environment variable is for the tests only; the demo uses 2 minutes.
CONFIRMATION_TTL_SECONDS = float(os.environ.get("MCP_DEMO_ELICITATION_TIMEOUT", "120"))
```

Replace `mcp = FastMCP(...)` with:

```python
class ModernProtocolOnly(Middleware):
    """Refuse handshake-era clients. Only they send initialize, and this server's confirmations
    use multi round-trip requests, which exist only on MCP 2026-07-28."""

    async def on_initialize(self, context, call_next):  # pylint: disable=unused-argument
        raise ToolError("this server speaks MCP 2026-07-28 only")


# requestState is sealed (encrypted, integrity-checked, bound to the request, expiring) because it
# passes through the client: the spec requires servers to treat it as attacker-controlled.
mcp = FastMCP("Permission-Based MCP Server",
              request_state_security=RequestStateSecurity.ephemeral(ttl=CONFIRMATION_TTL_SECONDS),
              middleware=[ModernProtocolOnly()])
```

In `audited`, record the round instead of success when the tool asks for input:

```python
            if inspect.isawaitable(result):
                result = await result
            outcome = "input_required" if isinstance(result, InputRequiredResult) else "success"
            detail = ""
            return result
```

Delete `duration_text` and `confirm_deletion`; add, after `backup_path`:

```python
# The key of the one question delete_file asks; the answer comes back under the same key
CONFIRM_KEY = "confirm"


def ask_to_confirm(filepath: str, file_path: Path, ctx: Context) -> InputRequiredResult:
    """Round 1: ask the user, through the client, to confirm deleting file_path.

    What the answer must be checked against (the path and the file's identity) goes into
    requestState, which the framework seals: the client carries it but can't read or change it."""
    capability = ClientCapabilities(elicitation=ElicitationCapability())
    if not ctx.session.check_client_capability(capability):
        raise ToolError("Deletion needs confirmation, but this client can't be asked")
    if not file_path.exists():
        raise ToolError(f"File {filepath} not found")
    stat = file_path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    message = f"Confirm deletion of {filepath} ({human_size(stat.st_size)}, modified {modified})"
    AUDIT_NOTE.set(f"asked: {message}")
    question = ElicitRequest(params=ElicitRequestFormParams(
        message=message, requested_schema=DeleteConfirmation.model_json_schema()))
    state = {"path": str(file_path), "identity": list(file_identity(file_path))}
    return InputRequiredResult(input_requests={CONFIRM_KEY: question},
                               request_state=json.dumps(state))


def confirmed(filepath: str, answer: ElicitResult) -> DeleteConfirmation:
    """Round 2: check the user's answer. Raises ToolError unless the user accepted with a valid
    confirmation that matches the path."""
    if answer.action == "decline":
        raise ToolError("Deletion declined by the user")
    if answer.action != "accept":
        raise ToolError("Deletion cancelled")
    try:
        # The server checks the answer again: it can't trust the client's validation
        confirmation = DeleteConfirmation.model_validate(answer.content or {})
    except ValidationError as e:
        first = e.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        raise ToolError(f"Confirmation was not valid: {field}: {first['msg']}") from e
    if confirmation.confirm_name != filepath:  # a rule a schema can't express
        raise ToolError(f"Confirmation did not match: you typed '{confirmation.confirm_name}', "
                        f"expected '{filepath}'")
    return confirmation
```

Replace `delete_file` with:

```python
@mcp.tool()
@audited
def delete_file(filepath: str, ctx: Context) -> str | InputRequiredResult:
    """
    Delete a file from the workspace, after the user confirms.

    Args:
        filepath: Path to the file relative to the workspace
    """
    file_path = resolve_allowed(filepath)  # every round: the retry is a new, untrusted request
    answer = (ctx.input_responses or {}).get(CONFIRM_KEY)
    if not isinstance(answer, ElicitResult) or ctx.request_state is None:
        return ask_to_confirm(filepath, file_path, ctx)  # round 1, or an answer without state
    asked = json.loads(ctx.request_state)  # sealed: unchanged since round 1, and not expired
    confirmation = confirmed(filepath, answer)
    backup_note = "yes" if confirmation.keep_backup else "no"
    AUDIT_NOTE.set(f"answer: accept; reason: {confirmation.reason}; backup: {backup_note}")
    try:
        if str(file_path) != asked["path"] or list(file_identity(file_path)) != asked["identity"]:
            raise ToolError(f"File {filepath} changed while waiting for confirmation; not deleted")
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

- [ ] **Step 5: Client**

In `client/client.py`:
- Imports: `from mcp import Client, ClientSession, StdioServerParameters`; drop `stdio_client` (keep `get_default_environment`); add `INVALID_REQUEST, ElicitRequest, InputRequiredResult` to the `mcp.types` import.
- Constants (after `SUPPORTED_FIELD_TYPES`):

```python
# The MCP protocol revision this client speaks. Pinned rather than "auto", so it can't fall back
# to the handshake era, where servers ask mid-call instead of by multi round-trip requests.
PROTOCOL_VERSION = "2026-07-28"
# How many input_required rounds one tool call may take before the client gives up
INPUT_ROUNDS_MAX = 3
```

- `QUESTION_TIMEOUT_SECONDS` comment: "How long the client waits for the user to answer a server's question: a little longer than the server's confirmation stays valid (2 minutes, or MCP_DEMO_ELICITATION_TIMEOUT in the tests), so an abandoned form never holds the call for ever."
- `CANCELLED_CAUSE = "the request was cancelled (the connection closed or the server was restarted)"` with comment "Why an open question closed when its handler was cancelled: the call itself was cancelled."
- `_hold_connection` body:

```python
        try:
            async with Client(server_params, mode=PROTOCOL_VERSION,
                              elicitation_callback=self._on_elicitation) as connection:
                # serverInfo is optional on 2026-07-28 (fastmcp doesn't send it); the user must
                # still be told which server is asking, so fall back to the script launched
                info = connection.server_info
                self.server_name = info.name if info else Path(self.server_script).stem
                self.protocol_version = connection.protocol_version
                ready.set_result(connection.session)
                await asyncio.get_running_loop().create_future()  # wait for disconnect()
```

(the `except` / `finally` are unchanged; the `elicitation_callback` declares the capability — the round loop below calls the same handler).
- `_send`: `call.task = asyncio.create_task(self._call_server(request, call))`.
- Replace `_call_server` with:

```python
    async def _call_server(self, request: ToolRequest, call: CallInProgress):
        """The tools/call, through any input_required rounds (MCP 2026-07-28 multi round-trip
        requests). Every round re-sends the stored, approved request: only the user's answers and
        the server's sealed state are added. Returns the server's final result."""
        answers, state = None, None
        async with self._server_call() as session:
            for _ in range(INPUT_ROUNDS_MAX):
                result = await session.call_tool(request.tool_name, arguments=request.arguments,
                                                 input_responses=answers, request_state=state,
                                                 allow_input_required=True)
                if not isinstance(result, InputRequiredResult):
                    return result
                call.rounds.append(result.request_state or "")
                answers = await self._answer_input_requests(result.input_requests or {})
                state = result.request_state  # echoed exactly; None stays None
        raise MCPError(INVALID_REQUEST,
                       f"the server still needed input after {INPUT_ROUNDS_MAX} rounds")

    async def _answer_input_requests(self, input_requests: dict) -> dict:
        """Answer the server's questions for the next round. Only elicitation is declared; any
        other request (roots, sampling) ends the call instead of being answered."""
        answers = {}
        for key, input_request in input_requests.items():
            if not isinstance(input_request, ElicitRequest):
                raise MCPError(INVALID_REQUEST, f"the server asked for {input_request.method}, "
                                                "which this client doesn't support")
            answers[key] = await self._on_elicitation(None, input_request.params)
        return answers
```

- `answer_input` comment above `del self._open_questions[input_id]`: "A question closes when the client stops waiting (QUESTION_TIMEOUT_SECONDS) or its call is cancelled; then a late answer raises KeyError below. The server never waits: an answer sent after its sealed state expires is refused by the server."
- `CallInProgress` docstring: "A tool call running in the background, possibly paused on the server's questions between input_required rounds."

- [ ] **Step 6: Timeline**

In `client/tools_view.py` `timeline_for`, replace the inputs / input_request block with:

```python
    for question, answer, content in outcome.inputs:
        steps.append((None, "↩ Server returned `input_required` and asked: "
                            f"{server_text(question)}"))
        steps.append((None, answer_line(answer, content)))
    if outcome.input_request is not None:
        steps.append((None, "↩ Server returned `input_required` and asked: "
                            f"{server_text(outcome.input_request.message)}"))
        steps.append((None, "⏸ Waiting for your answer (nothing on the server is waiting)"))
        steps.append((TODO, "Result"))
        return render_timeline(header, steps)
    if outcome.rounds:
        steps.append((DONE, "Re-sent `tools/call` with your answers and the server's sealed "
                            f"state ({sealed_state_text(outcome.rounds[-1])}, opaque to this "
                            "client)"))
```

and add:

```python
def sealed_state_text(state: str) -> str:
    """The server's sealed requestState, shortened: the client can't read it, only carry it."""
    return f"`{state[:12]}…`" if state else "no state"
```

- [ ] **Step 7: Run the suite**

Run: `mcp_security_env/bin/python -m pytest -q`
Expected: all pass. Then pylint 10.00/10 on `client/` and `server/`.

- [ ] **Step 8: Prove the repo-enforced checks can fail**

For each, copy `client/` and `server/` to the scratchpad, apply the one change, run the named test with `MCP_TEST_SOURCE=<copy>`, expect FAIL, delete the copy:

| Change in the copy | Test that must go red |
|---|---|
| `delete_file`: replace the `if str(file_path) != asked["path"] or …:` condition with `if False:` | `test_file_changed_between_rounds_is_not_deleted` |
| `delete_file`: `if not isinstance(answer, ElicitResult):` (drop the state condition) and `asked = json.loads(ctx.request_state or json.dumps({"path": str(file_path), "identity": list(file_identity(file_path))}))` | `test_answer_without_request_state_deletes_nothing` (Task 3) — run in Task 3 Step 3 |
| `delete_file`: `file_path = Path(json.loads(ctx.request_state)["path"]) if ctx.request_state else resolve_allowed(filepath)` and the identity condition `if False:` | `test_symlink_swapped_between_rounds_is_not_followed` |
| `_call_server`: `arguments=request.arguments if answers is None else {**request.arguments, "filepath": "EVIL.txt"}` | `test_retry_sends_the_approved_arguments` |
| `_call_server`: `state = None` instead of `state = result.request_state` | `test_retry_sends_the_approved_arguments[True]` |
| `ModernProtocolOnly.on_initialize`: `return await call_next(context)` | `test_handshake_client_is_refused` |

---

### Task 3: Sealed state can't be tampered with, reused or skipped

**Files:**
- Create: `tests/test_request_state.py`

**Interfaces:**
- Consumes: `raw_session` (Task 2), server `CONFIRM_KEY = "confirm"`.

- [ ] **Step 1: Write the tests**

```python
"""MRTR: the state the server hands the client must come back sealed and unchanged, for the same
request; anything else deletes nothing."""

import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import ElicitResult, InputRequiredResult

from helpers import WORKSPACE, raw_session

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def accept(name):
    return {"confirm": ElicitResult(action="accept",
                                    content={"confirm_name": name, "reason": "old draft"})}


def tampered(state: str) -> str:
    """The sealed state with one character in its middle changed."""
    i = len(state) // 2
    return state[:i] + ("A" if state[i] != "A" else "B") + state[i + 1:]


async def first_round(session, name) -> str:
    result = await session.call_tool("delete_file", {"filepath": name}, allow_input_required=True)
    assert isinstance(result, InputRequiredResult)
    return result.request_state


async def test_tampered_request_state_is_rejected():
    (WORKSPACE / "a.txt").write_text("x")
    async with raw_session() as session:
        state = await first_round(session, "a.txt")
        with pytest.raises(MCPError, match="Invalid or expired requestState"):
            await session.call_tool("delete_file", {"filepath": "a.txt"},
                                    input_responses=accept("a.txt"), request_state=tampered(state))
        assert (WORKSPACE / "a.txt").exists()
        # Control: the untouched state on the same request is accepted, so the rejection above
        # was the tampering
        result = await session.call_tool("delete_file", {"filepath": "a.txt"},
                                         input_responses=accept("a.txt"), request_state=state)
    assert not result.is_error and not (WORKSPACE / "a.txt").exists()


async def test_request_state_cannot_be_reused_for_another_file():
    for name in ("a.txt", "b.txt"):
        (WORKSPACE / name).write_text("x")
    async with raw_session() as session:
        state = await first_round(session, "a.txt")
        with pytest.raises(MCPError, match="Invalid or expired requestState"):
            await session.call_tool("delete_file", {"filepath": "b.txt"},
                                    input_responses=accept("b.txt"), request_state=state)
        assert (WORKSPACE / "b.txt").exists()
        # Control: the same state on the request it was minted for is accepted
        result = await session.call_tool("delete_file", {"filepath": "a.txt"},
                                         input_responses=accept("a.txt"), request_state=state)
    assert not result.is_error and not (WORKSPACE / "a.txt").exists()


async def test_answer_without_request_state_deletes_nothing():
    (WORKSPACE / "a.txt").write_text("x")
    async with raw_session() as session:
        result = await session.call_tool("delete_file", {"filepath": "a.txt"},
                                         input_responses=accept("a.txt"),
                                         allow_input_required=True)
    assert isinstance(result, InputRequiredResult)  # asked again
    assert (WORKSPACE / "a.txt").exists()
```

- [ ] **Step 2: Run them**

Run: `mcp_security_env/bin/python -m pytest -q tests/test_request_state.py`
Expected: 3 passed. These guarantees already hold after Task 2 (the first two are enforced by mcp's sealing; the controls inside each test show the rejection is caused by the tampering or reuse). If `pytest.raises(MCPError)` sees an `ExceptionGroup` instead, unwrap with `innermost_error` in a small helper rather than loosening the match.

- [ ] **Step 3: Prove the repo-enforced one can fail**

Apply Task 2 Step 8's second row to a scratch copy; `MCP_TEST_SOURCE=<copy> … tests/test_request_state.py::test_answer_without_request_state_deletes_nothing` must FAIL. Delete the copy. Full suite: all pass.

---

### Task 4: Docs and the Part 1 notes

**Files:**
- Modify: `README.md`
- Modify: `../dominicfarr.github.io/_posts/2026-10-01-mcp-security.md`, `../dominicfarr.github.io/_posts/2026-10-03-mcp-security-roots.md` (not committed)
- Modify: spec status; memory `mcp-demo-scope.md`

- [ ] **Step 1: README**

- **Versions:** replace the section with: the repo speaks MCP **2026-07-28** only (handshake clients are refused); the Part 1 posts pin `03a9398` (mcp 1.16 / fastmcp 2.12, handshake era); `7c1324c` is the last handshake-era commit on current libraries; roots are deprecated (SEP-2577) and replaced by `--allowed-folder`; the client declares URL-mode elicitation (mcp always does) but declines it and shows server text only as plain text.
- **Launch:** the Tools tab paragraph about **Client root** becomes **Folder the server is launched with**: the folder is passed as `--allowed-folder`; changing it restarts the server; overlap rules as before; `filepath` stays relative to `workspace/`.
- **How a tool call travels:** replace the 7-step block with:

```
1. client → server   tools/call       delete_file(filepath)                      ← round 1
2. server → client   result: input_required
                       inputRequests: { confirm: elicitation/create form }
                       requestState:  sealed { path, file identity }            ← call over; server forgets it
3. (the user answers in the client; nothing on the server waits)
4. client → server   tools/call       delete_file(filepath), new request id      ← round 2: same arguments
                       + inputResponses { confirm: {action: accept, content: …} }
                       + requestState (echoed unchanged)
5. server            unseals the state (rejects tampered, reused or expired state), resolves the path
                     again, checks the file is the one the user was asked about, validates the answer
6. server → client   result: "Deleted …" or an error                             ← call closes
```

  and replace "What roots protect" with **The allowed folder**: the client launches the server with `--allowed-folder`; only the server enforces it (a malicious server ignores it), so the protection for the user is still not running untrusted servers, plus OS permissions and sandboxing; roots did this job as a runtime request and are deprecated as of 2026-07-28.
- **Elicitation:** "The tool call waits, paused" becomes "the server ends round 1 with the question and sealed state; the client re-sends the call with the answer"; "Fail closed" bullet: a confirmation is valid for 2 minutes (the sealed state's expiry); a late answer is refused.

- [ ] **Step 2: Part 1 notes**

At the top of each post body (after the front matter, before the first line), add one italic paragraph:

- hub: `*Update, 2026-10-19: since this series, MCP 2026-07-28 changed how servers ask clients for input (multi round-trip requests), and the reference repo moved to it. The posts and their code links describe the earlier, handshake-era version; see the repo's [Versions](https://github.com/dominicfarr/mcp-secuirty-working-notes-example#versions) notes.*`
- roots post: `*Update, 2026-10-19: roots are deprecated as of MCP 2026-07-28 (SEP-2577). New code should pass folders through server configuration instead; the reference repo now launches its server with an allowed folder. See its [Versions](https://github.com/dominicfarr/mcp-secuirty-working-notes-example#versions) notes.*`

Build the blog (`BUNDLE_PATH=<scratchpad>/bundle bundle exec jekyll build -d <scratchpad>/site`) and confirm both notes render. Do not commit.

- [ ] **Step 3: Status and memory**

Spec `Status: implemented, not committed (plan: …)`. Memory `mcp-demo-scope.md`: sub-project 2 implemented (test count), next is the bridge post spec (after the user commits), then the LLM host.

---

### Task 5: The app, and review

- [ ] **Step 1: Pre-push hook and lock check**

`PATH="$PWD/mcp_security_env/bin:$PATH" .githooks/pre-push` passes; lock check prints no `MISSING`.

- [ ] **Step 2: The app in a browser**

Launch the app on a spare port via `LAUNCH_SETTINGS` (as in the upgrade), kill only the PID you start. With puppeteer: set `delete_file` to `allow`, create `workspace/demo.txt`, select `delete_file`, send `{"filepath": "demo.txt"}`; the timeline shows "Server returned `input_required` and asked"; fill the form (`confirm_name` = `demo.txt`, reason ≥ 5 chars), submit; the timeline shows "Re-sent `tools/call`" and "Deleted demo.txt"; the file is gone. Then choose `workspace/projects/` in the folder control and confirm a write to `a.txt` is refused "outside the allowed folder".

- [ ] **Step 3: One fresh reviewer**

Dispatch one reviewer subagent (opus) with the spec (including section 8), this plan, the ledger's `Ruling:` lines and `git diff`. Review Focus as above, plus: the round loop under cancellation (folder change mid-question), `request_state` handling exactly per the MRTR client MUSTs (echo exactly; omit when absent; never reuse for another request), and whether any Part 1 guarantee was dropped rather than rewritten.

- [ ] **Step 4: Act on findings**

Re-grade by effect; fix Critical/Important with a failing test first; full suite green; deferred minors and every ruling in the final message.
