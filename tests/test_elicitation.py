"""The server asks the human through the client, and both sides validate the answer."""

import asyncio

import pytest
from mcp.types import (CallToolResult, ElicitRequestFormParams, ElicitRequestURLParams,
                       ElicitResult, InputRequiredResult, ListRootsRequest, TextContent)

from client import (CANCELLED_CAUSE, INPUT_AUTO_DECLINED, QUESTION_TIMEOUT_SECONDS, CallInProgress,
                    InputInvalid, MCPPermissionClient, ToolRequest, is_supported_schema)
from helpers import (ELICITATION_TIMEOUT, WORKSPACE, call_server, client_log,
                     elicitation_answer, outcome_text)


def good(name, backup=False):
    return {"confirm_name": name, "reason": "old draft", "keep_backup": backup}


@pytest.mark.integration
@pytest.mark.anyio
async def test_delete_asks_with_details_only_the_server_knows():
    (WORKSPACE / "a.txt").write_text("hello")
    seen = []
    is_error, text = await call_server("delete_file", {"filepath": "a.txt"},
                                       elicitation=elicitation_answer("accept", good("a.txt"),
                                                                      seen=seen))
    assert (is_error, text) == (False, "Deleted a.txt") and not (WORKSPACE / "a.txt").exists()
    message, schema = seen[0]
    assert message.startswith("Confirm deletion of a.txt (5 B, modified ")
    assert schema["required"] == ["confirm_name", "reason"]
    assert (schema["properties"]["reason"]["minLength"],
            schema["properties"]["reason"]["maxLength"]) == (5, 200)
    assert schema["properties"]["keep_backup"]["type"] == "boolean"


@pytest.mark.integration
@pytest.mark.anyio
async def test_client_validates_against_the_schema_before_sending(make_app):
    app = make_app({"delete_file": "allow"})
    (WORKSPACE / "a.txt").write_text("x")
    paused = await app.request_tool("delete_file", {"filepath": "a.txt"})
    question = paused.input_request
    assert question is not None and (WORKSPACE / "a.txt").exists()
    with pytest.raises(InputInvalid) as invalid:
        await app.answer_input(question.input_id, "accept", {"reason": "no"})
    assert "confirm_name: required" in invalid.value.errors
    assert any(error.startswith("reason:") for error in invalid.value.errors)
    done = await app.answer_input(question.input_id, "accept", good("a.txt"))
    assert not done.is_error and not (WORKSPACE / "a.txt").exists()


@pytest.mark.integration
@pytest.mark.anyio
async def test_server_validates_again():
    (WORKSPACE / "c.txt").write_text("x")
    is_error, text = await call_server(
        "delete_file", {"filepath": "c.txt"},
        elicitation=elicitation_answer("accept", {"confirm_name": "c.txt", "reason": "no"}))
    assert is_error and "Confirmation was not valid: reason" in text
    assert (WORKSPACE / "c.txt").exists()


@pytest.mark.integration
@pytest.mark.anyio
async def test_confirmation_must_match_the_file():
    (WORKSPACE / "c.txt").write_text("x")
    is_error, text = await call_server("delete_file", {"filepath": "c.txt"},
                                       elicitation=elicitation_answer("accept", good("other.txt")))
    assert is_error and "you typed 'other.txt', expected 'c.txt'" in text
    assert (WORKSPACE / "c.txt").exists()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("action, expected", [("decline", "Deletion declined by the user"),
                                              ("cancel", "Deletion cancelled")])
async def test_decline_and_cancel_delete_nothing(action, expected):
    (WORKSPACE / "c.txt").write_text("x")
    is_error, text = await call_server("delete_file", {"filepath": "c.txt"},
                                       elicitation=elicitation_answer(action))
    assert is_error and expected in text and (WORKSPACE / "c.txt").exists()


@pytest.mark.integration
@pytest.mark.anyio
async def test_approval_then_question_works_end_to_end(make_app):
    app = make_app({"delete_file": "ask"})
    (WORKSPACE / "b.txt").write_text("x")
    asked = await app.request_tool("delete_file", {"filepath": "b.txt"})
    paused = await app.approve(asked.request.request_id)
    assert paused.input_request is not None
    done = await app.answer_input(paused.input_request.input_id, "decline")
    assert done.is_error and "declined" in outcome_text(done) and (WORKSPACE / "b.txt").exists()


@pytest.mark.integration
@pytest.mark.anyio
async def test_client_without_elicitation_cannot_delete():
    (WORKSPACE / "c.txt").write_text("x")
    is_error, text = await call_server("delete_file", {"filepath": "c.txt"}, elicitation=None)
    assert is_error and "this client can't be asked" in text and (WORKSPACE / "c.txt").exists()


@pytest.mark.integration
@pytest.mark.anyio
async def test_unanswered_question_does_not_block_the_client(make_app):
    app = make_app({"delete_file": "allow"})
    (WORKSPACE / "a.txt").write_text("x")
    paused = await app.request_tool("delete_file", {"filepath": "a.txt"})
    await asyncio.sleep(QUESTION_TIMEOUT_SECONDS + 1)  # the form was abandoned
    tools = await asyncio.wait_for(app.list_tools(), 15)
    assert any(tool.name == "read_file" for tool in tools)
    with pytest.raises(KeyError):
        await app.answer_input(paused.input_request.input_id, "accept", good("a.txt"))
    assert (WORKSPACE / "a.txt").exists()
    assert any(entry["outcome"] == "INPUT CANCELLED" and "no answer" in entry["detail"]
               for entry in client_log())


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


@pytest.mark.anyio
async def test_cancelled_question_is_closed_and_audited(make_client):
    # mcp cancels the handler both when the server gives up and when the connection drops, and
    # the client can't tell which: the audit must not claim one
    client, _ = make_client()
    params = ElicitRequestFormParams(message="Confirm?", requested_schema={
        "type": "object", "properties": {"ok": {"type": "boolean"}}})
    call = CallInProgress(ToolRequest("r3", "delete_file", {"filepath": "x"}), "policy: allow")
    call.task = asyncio.ensure_future(asyncio.get_running_loop().create_future())  # still running
    handler = asyncio.ensure_future(client._on_elicitation(  # pylint: disable=protected-access
        None, params))
    paused = await client._follow(call)  # pylint: disable=protected-access
    handler.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handler
    with pytest.raises(KeyError):
        await client.answer_input(paused.input_request.input_id, "decline")
    assert any(entry["outcome"] == "INPUT CANCELLED" and entry.get("request_id") == "r3"
               and entry["detail"].startswith(f"no answer: {CANCELLED_CAUSE}")
               for entry in client_log())
    call.task.cancel()


@pytest.mark.integration
@pytest.mark.anyio
async def test_file_changed_between_rounds_is_not_deleted():
    (WORKSPACE / "b.txt").write_text("original")

    async def change_then_accept(_context, _params):
        (WORKSPACE / "b.txt").write_text("replaced with something else")
        return ElicitResult(action="accept", content=good("b.txt"))

    is_error, text = await call_server("delete_file", {"filepath": "b.txt"},
                                       elicitation=change_then_accept)
    assert is_error and "changed while waiting for confirmation" in text
    assert (WORKSPACE / "b.txt").exists()


@pytest.mark.integration
@pytest.mark.anyio
async def test_backup_never_overwrites():
    (WORKSPACE / "b.txt").write_text("first")
    await call_server("delete_file", {"filepath": "b.txt"},
                      elicitation=elicitation_answer("accept", good("b.txt", backup=True)))
    (WORKSPACE / "b.txt").write_text("second")
    is_error, text = await call_server(
        "delete_file", {"filepath": "b.txt"},
        elicitation=elicitation_answer("accept", good("b.txt", backup=True)))
    assert not is_error and "backup kept as" in text
    assert (WORKSPACE / "b.txt.bak").read_text() == "first"
    assert len(list(WORKSPACE.glob("b.txt.*.bak"))) == 1


@pytest.mark.anyio
async def test_unsupported_form_is_declined_and_audited(make_client):
    client, _ = make_client()
    nested = {"type": "object", "properties": {"a": {"type": "object", "properties": {}}}}
    assert not is_supported_schema(nested)
    assert is_supported_schema({"type": "object", "properties": {"reason": {"type": "string"}}})
    # The client's real elicitation handler declines at once, without waiting for the user
    answer = await asyncio.wait_for(client._on_elicitation(  # pylint: disable=protected-access
        None, ElicitRequestFormParams(message="nested form", requested_schema=nested)), 5)
    assert answer.action == "decline"
    # The call it belongs to records and audits the automatic decline
    call = CallInProgress(ToolRequest("r1", "delete_file", {"filepath": "x"}), "policy: allow")
    finished = asyncio.get_running_loop().create_future()
    call.task = asyncio.ensure_future(finished)
    asyncio.get_running_loop().call_later(0.1, finished.set_result, CallToolResult(
        content=[TextContent(type="text", text="done")], is_error=False))
    outcome = await client._follow(call)  # pylint: disable=protected-access
    assert outcome.inputs == [("nested form", INPUT_AUTO_DECLINED, None)]
    assert any(entry["outcome"] == "INPUT DECLINED" and entry.get("request_id") == "r1"
               for entry in client_log())


@pytest.mark.anyio
async def test_url_elicitation_is_declined_and_audited(make_client):
    client, _ = make_client()
    params = ElicitRequestURLParams(message="Sign in to continue",
                                    url="https://example.com/login", elicitation_id="e1")
    # Declined at once: this client only presents forms and never opens a server's URL
    answer = await asyncio.wait_for(client._on_elicitation(  # pylint: disable=protected-access
        None, params), 5)
    assert answer.action == "decline"
    call = CallInProgress(ToolRequest("r2", "delete_file", {"filepath": "x"}), "policy: allow")
    finished = asyncio.get_running_loop().create_future()
    call.task = asyncio.ensure_future(finished)
    asyncio.get_running_loop().call_later(0.1, finished.set_result, CallToolResult(
        content=[TextContent(type="text", text="done")], is_error=False))
    outcome = await client._follow(call)  # pylint: disable=protected-access
    [(message, answer_text, _)] = outcome.inputs
    assert answer_text == INPUT_AUTO_DECLINED and "https://example.com/login" in message
    assert any(entry["outcome"] == "INPUT DECLINED" and entry.get("request_id") == "r2"
               and "https://example.com/login" in entry["detail"] for entry in client_log())


def test_no_auto_approving_shortcut():
    assert not hasattr(MCPPermissionClient, "request_elicitation")
