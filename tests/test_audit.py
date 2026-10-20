"""Both sides keep their own audit record."""

import json

import pytest

from audit import AuditLog  # the client's copy: only client/ is importable
from audit_view import CLIENT_COLUMNS, audit_rows
from helpers import (CLIENT_LOG, SERVER_LOG, WORKSPACE, call_server, client_log,
                     elicitation_answer, server_log)


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


@pytest.mark.anyio
async def test_folder_changes_are_audited(make_client):
    # Recorded in full however long the path: it is what the server was allowed to touch
    client, _ = make_client()
    folder = await client.set_folder("workspace/" + "deep/" * 30)
    entry = client_log()[-1]
    assert (entry["tool"], entry["outcome"], entry["detail"]) == ("(folder)", "FOLDER CONFIGURED",
                                                                 str(folder))
    assert len(str(folder)) > 100


def test_unreadable_log_lines_are_shown_not_hidden(tmp_path):
    path = tmp_path / "server_audit.log"
    path.write_text('{"ts": "2026-10-05T12:00:00+00:00", "tool": "t", "args": {}, '
                    '"outcome": "ok", "detail": ""}\nnot json\n')
    rows = audit_rows(path, with_request_id=False)
    assert rows[0][-1] == "unreadable line 2" and rows[1][1] == "t"



def test_client_table_shows_who_asked(tmp_path):
    path = tmp_path / "client_audit.log"
    path.write_text('{"ts": "2026-10-20T12:00:00+00:00", "tool": "t", "args": {}, '
                    '"outcome": "ALLOWED", "detail": "", "request_id": "r1", "source": "model"}\n'
                    '{"ts": "2026-10-20T12:00:01+00:00", "tool": "(folder)", "args": {}, '
                    '"outcome": "FOLDER CONFIGURED", "detail": ""}\n')
    rows = audit_rows(path, with_request_id=True)
    assert CLIENT_COLUMNS[1:4] == ["Request", "Source", "Tool"]
    assert rows[1][1:4] == ["r1", "model", "t"] and rows[0][2] == ""

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
