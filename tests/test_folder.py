"""The folder the server is launched with narrows it; it never widens it."""

from pathlib import Path

import pytest

from client import CANCELLED_CAUSE
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


@pytest.mark.integration
@pytest.mark.anyio
async def test_folder_change_closes_an_open_question(make_app):
    # Changing the folder restarts the server: a question from the old server can't be answered
    # into the new one, and closing it is audited
    (WORKSPACE / "a.txt").write_text("x")
    app = make_app({"delete_file": "allow", "read_file": "allow"})
    paused = await app.request_tool("delete_file", {"filepath": "a.txt"})
    assert paused.input_request is not None
    await app.set_folder("workspace")
    with pytest.raises(KeyError):
        await app.answer_input(paused.input_request.input_id, "accept",
                               {"confirm_name": "a.txt", "reason": "old draft"})
    assert any(entry["outcome"] == "INPUT CANCELLED" and CANCELLED_CAUSE in entry["detail"]
               for entry in client_log())
    reading = await app.request_tool("read_file", {"filepath": "a.txt"})
    assert not reading.is_error and app.session is not None  # the new connection survived
    assert (WORKSPACE / "a.txt").exists()
