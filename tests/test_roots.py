"""Roots narrow the server; they never widen it."""

from pathlib import Path

import pytest

from helpers import (PROJECT, WORKSPACE, call_server, outcome_text, raw_roots, roots_answer)


def setup_files():
    (WORKSPACE / "projects").mkdir()
    (WORKSPACE / "top.txt").write_text("hi")
    (WORKSPACE / "projects" / "p.txt").write_text("hi")


@pytest.mark.integration
@pytest.mark.anyio
async def test_root_inside_workspace_narrows_access(make_app):
    setup_files()
    app = make_app({"write_file": "allow"})
    app.set_roots(["workspace/projects"])
    inside = await app.request_tool("write_file", {"filepath": "projects/a.txt", "content": "x"})
    outside = await app.request_tool("write_file", {"filepath": "a.txt", "content": "x"})
    assert not inside.is_error
    assert outside.is_error and "outside the client's roots" in outcome_text(outside)


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("root", [Path("/"), PROJECT])
async def test_wide_root_gives_only_the_workspace(root):
    setup_files()
    assert await call_server("read_file", {"filepath": "top.txt"}, roots=roots_answer([root])) \
        == (False, "hi")
    is_error, text = await call_server("read_file", {"filepath": "../x.txt"},
                                       roots=roots_answer([root]))
    assert is_error and "outside the workspace" in text


@pytest.mark.integration
@pytest.mark.anyio
async def test_root_outside_workspace_refuses_everything():
    setup_files()
    is_error, text = await call_server("read_file", {"filepath": "top.txt"},
                                       roots=roots_answer([PROJECT / "elsewhere"]))
    assert is_error and "outside the client's roots" in text


@pytest.mark.integration
@pytest.mark.anyio
async def test_empty_roots_refuse_everything():
    setup_files()
    is_error, text = await call_server("read_file", {"filepath": "top.txt"},
                                       roots=roots_answer([]))
    assert is_error and "the client declared no roots" in text


@pytest.mark.integration
@pytest.mark.anyio
async def test_client_without_roots_support_gets_the_workspace():
    setup_files()
    assert await call_server("read_file", {"filepath": "top.txt"}, roots=None) == (False, "hi")


@pytest.mark.integration
@pytest.mark.anyio
# raw_roots deliberately sends unvalidated roots; pydantic warns while serialising them
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")
@pytest.mark.parametrize("uri, expected", [
    ("file://otherhost{workspace}", "the client declared no roots"),
    ("{workspace_uri}%00x", "the client declared no roots"),
    ("https://example.com/x", "could not get the client's roots"),
])
async def test_only_local_file_uris_count(uri, expected):
    setup_files()
    uri = uri.format(workspace=WORKSPACE, workspace_uri=WORKSPACE.as_uri())
    is_error, text = await call_server("read_file", {"filepath": "top.txt"}, roots=raw_roots(uri))
    assert is_error and expected in text


@pytest.mark.integration
@pytest.mark.anyio
async def test_slow_roots_answer_is_refused():
    setup_files()
    is_error, text = await call_server("read_file", {"filepath": "top.txt"},
                                       roots=roots_answer([WORKSPACE], delay=10))
    assert is_error and "could not get the client's roots" in text


@pytest.mark.integration
@pytest.mark.anyio
async def test_server_asks_for_roots_during_the_call(make_app):
    setup_files()
    app = make_app({"read_file": "allow", "execute_command": "allow"})
    reading = await app.request_tool("read_file", {"filepath": "top.txt"})
    command = await app.request_tool("execute_command", {"command": "ls"})
    assert reading.roots_answered == [WORKSPACE.as_uri()]
    assert command.roots_answered is None, "no roots/list for a tool that doesn't use files"


@pytest.mark.integration
@pytest.mark.anyio
async def test_roots_are_checked_when_the_call_is_sent(make_app):
    setup_files()
    app = make_app({"write_file": "ask"})
    outcome = await app.request_tool("write_file", {"filepath": "b.txt", "content": "x"})
    app.set_roots(["workspace/projects"])  # narrowed while the approval waits
    approved = await app.approve(outcome.request.request_id)
    assert approved.is_error and "outside the client's roots" in outcome_text(approved)


def test_typed_roots_resolve_safely(make_client):
    client, _ = make_client()
    assert client.set_roots(["~"]) == [Path.home().resolve().as_uri()]
    assert client.set_roots(["workspace/../"]) == [PROJECT.as_uri()]
    assert client.set_roots(["workspace"]) == [WORKSPACE.as_uri()]
