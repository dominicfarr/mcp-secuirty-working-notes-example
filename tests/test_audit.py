"""Both sides keep their own audit record."""

import json

import pytest

from audit import AuditLog  # the client's copy: only client/ is importable
from audit_view import audit_rows
from helpers import CLIENT_LOG, SERVER_LOG, WORKSPACE, client_log, server_log


@pytest.mark.integration
@pytest.mark.anyio
async def test_each_side_logs_in_its_own_folder(make_app):
    app = make_app({"write_file": "allow"})
    await app.request_tool("write_file", {"filepath": "a.txt", "content": "x"})
    assert CLIENT_LOG.exists() and SERVER_LOG.exists()
    assert not list(WORKSPACE.rglob("*.log")), "logs must stay out of the workspace"
    assert {entry["actor"] for entry in client_log()} == {"client"}
    assert {entry["actor"] for entry in server_log()} == {"server"}


@pytest.mark.anyio
async def test_client_entries_share_the_request_id(make_client):
    client, _ = make_client({"write_file": "ask"})
    outcome = await client.request_tool("write_file", {"filepath": "a.txt", "content": "x"})
    await client.approve(outcome.request.request_id)
    flow = [entry["outcome"] for entry in client_log()
            if entry.get("request_id") == outcome.request.request_id]
    assert flow == ["ASK", "ALLOWED", "COMPLETED"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_server_logs_every_call_including_refusals(make_app):
    app = make_app({"read_file": "allow", "write_file": "allow"})
    await app.request_tool("write_file", {"filepath": "a.txt", "content": "x"})
    await app.request_tool("read_file", {"filepath": "../outside.txt"})
    entries = server_log()
    assert [(e["tool"], e["outcome"]) for e in entries] == [("write_file", "success"),
                                                           ("read_file", "error")]
    assert "outside the workspace" in entries[-1]["detail"]


def test_long_arguments_are_summarised_not_logged(tmp_path):
    log = AuditLog(tmp_path / "audit.log", actor="client")
    log.record("write_file", {"filepath": "a.txt", "content": "x" * 500}, "ALLOWED")
    entry = json.loads((tmp_path / "audit.log").read_text())
    assert entry["args"] == {"filepath": "a.txt", "content": "<500 chars>"}


@pytest.mark.integration
@pytest.mark.anyio
async def test_tool_context_is_not_logged(make_app):
    app = make_app({"read_file": "allow"})
    (WORKSPACE / "a.txt").write_text("x")
    await app.request_tool("read_file", {"filepath": "a.txt"})
    assert server_log()[0]["args"] == {"filepath": "a.txt"}


def test_root_changes_are_audited(make_client):
    client, _ = make_client()
    uris = client.set_roots(["workspace"])
    entry = client_log()[-1]
    assert (entry["tool"], entry["outcome"], entry["args"]) == ("(roots)", "ROOTS DECLARED",
                                                               {"roots": uris})


def test_unreadable_log_lines_are_shown_not_hidden(tmp_path):
    path = tmp_path / "server_audit.log"
    path.write_text('{"ts": "2026-10-05T12:00:00+00:00", "tool": "t", "args": {}, '
                    '"outcome": "ok", "detail": ""}\nnot json\n')
    rows = audit_rows(path, with_request_id=False)
    assert rows[0][-1] == "unreadable line 2" and rows[1][1] == "t"
