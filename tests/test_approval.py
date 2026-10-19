"""An approval is bound to one request: the stored copy is sent, once."""

import pytest

from helpers import RoundsSession


@pytest.mark.anyio
async def test_approval_sends_the_stored_request_not_the_form(make_client):
    client, session = make_client({"write_file": "ask"})
    arguments = {"filepath": "a.txt", "content": "hi"}
    outcome = await client.request_tool("write_file", arguments)
    arguments["filepath"] = "EVIL.txt"  # the form is edited after requesting
    await client.approve(outcome.request.request_id)
    assert session.calls == [("write_file", {"filepath": "a.txt", "content": "hi"})]


@pytest.mark.anyio
async def test_approval_is_single_use(make_client):
    client, session = make_client({"write_file": "ask"})
    outcome = await client.request_tool("write_file", {"filepath": "a.txt", "content": "x"})
    await client.approve(outcome.request.request_id)
    with pytest.raises(KeyError):
        await client.approve(outcome.request.request_id)
    assert len(session.calls) == 1


@pytest.mark.anyio
async def test_returned_outcome_cannot_redirect_the_approval(make_client):
    client, session = make_client({"write_file": "ask"})
    outcome = await client.request_tool("write_file", {"filepath": "a.txt", "content": "x"})
    outcome.request.arguments["filepath"] = "EVIL.txt"
    await client.approve(outcome.request.request_id)
    assert session.calls == [("write_file", {"filepath": "a.txt", "content": "x"})]


@pytest.mark.anyio
async def test_reject_sends_nothing(make_client):
    client, session = make_client({"write_file": "ask"})
    outcome = await client.request_tool("write_file", {"filepath": "a.txt", "content": "x"})
    rejected = client.reject(outcome.request.request_id)
    assert rejected.decision == "REJECTED" and session.calls == []
    with pytest.raises(KeyError):
        client.reject(outcome.request.request_id)
    with pytest.raises(KeyError):
        await client.approve(outcome.request.request_id)


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
