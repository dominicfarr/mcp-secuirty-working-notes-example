"""The client's permission policy decides what is sent to the server."""

import pytest

from helpers import WORKSPACE, server_log
from tools_view import policy_label


@pytest.mark.integration
@pytest.mark.anyio
async def test_allow_sends_the_call(make_app):
    app = make_app({"write_file": "allow"})
    outcome = await app.request_tool("write_file", {"filepath": "a.txt", "content": "hi"})
    assert outcome.decision == "ALLOWED" and not outcome.is_error
    assert (WORKSPACE / "a.txt").read_text() == "hi"


@pytest.mark.integration
@pytest.mark.anyio
async def test_deny_never_reaches_the_server(make_app):
    app = make_app({"delete_file": "deny", "read_file": "allow"})
    (WORKSPACE / "a.txt").write_text("x")
    outcome = await app.request_tool("delete_file", {"filepath": "a.txt"})
    await app.request_tool("read_file", {"filepath": "a.txt"})  # starts the server and its log
    assert outcome.decision == "DENIED" and outcome.content is None
    assert [entry["tool"] for entry in server_log()] == ["read_file"]
    assert (WORKSPACE / "a.txt").exists()


@pytest.mark.anyio
async def test_ask_holds_the_call_until_approved(make_client):
    client, session = make_client({"write_file": "ask"})
    outcome = await client.request_tool("write_file", {"filepath": "a.txt", "content": "x"})
    assert outcome.decision == "ASK" and session.calls == []
    approved = await client.approve(outcome.request.request_id)
    assert approved.decision == "ALLOWED"
    assert session.calls == [("write_file", {"filepath": "a.txt", "content": "x"})]


@pytest.mark.anyio
@pytest.mark.parametrize("value", ["Deny", "block", ""])
async def test_unknown_policy_value_fails_closed(make_client, value):
    client, session = make_client({"write_file": value})
    outcome = await client.request_tool("write_file", {"filepath": "a.txt", "content": "x"})
    assert outcome.decision == "DENIED" and session.calls == []


@pytest.mark.anyio
async def test_policy_change_before_approval_is_enforced(make_client):
    client, session = make_client({"write_file": "ask"})
    outcome = await client.request_tool("write_file", {"filepath": "a.txt", "content": "x"})
    client.permissions["write_file"] = "deny"
    approved = await client.approve(outcome.request.request_id)
    assert approved.decision == "DENIED" and approved.reason == "policy changed to deny"
    assert session.calls == []


def test_policy_shows_default_or_override_source():
    assert policy_label("delete_file", {}) == "deny · default"
    assert policy_label("delete_file", {"delete_file": "ask"}) == "ask · permissions.json"
    assert policy_label("new_tool", {}) == "ask · default"
