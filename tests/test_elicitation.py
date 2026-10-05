"""The server asks the human through the client, and both sides validate the answer."""

import asyncio

import pytest
from mcp.types import CallToolResult, ElicitRequestParams, ElicitResult, TextContent

from client import (INPUT_AUTO_DECLINED, QUESTION_TIMEOUT_SECONDS, CallInProgress, InputInvalid,
                    MCPPermissionClient, ToolRequest, is_supported_schema)
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
async def test_late_answer_deletes_nothing(make_app):
    app = make_app({"delete_file": "allow"})
    (WORKSPACE / "b.txt").write_text("x")
    paused = await app.request_tool("delete_file", {"filepath": "b.txt"})
    await asyncio.sleep(ELICITATION_TIMEOUT + 1)  # the server has given up; the client hasn't
    late = await app.answer_input(paused.input_request.input_id, "accept", good("b.txt"))
    assert late.is_error and f"no answer within {ELICITATION_TIMEOUT} seconds" in outcome_text(late)
    assert (WORKSPACE / "b.txt").exists()


@pytest.mark.integration
@pytest.mark.anyio
async def test_file_changed_while_deciding_is_not_deleted():
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
        None, ElicitRequestParams(message="nested form", requestedSchema=nested)), 5)
    assert answer.action == "decline"
    # The call it belongs to records and audits the automatic decline
    call = CallInProgress(ToolRequest("r1", "delete_file", {"filepath": "x"}), "policy: allow", 0)
    finished = asyncio.get_running_loop().create_future()
    call.task = asyncio.ensure_future(finished)
    asyncio.get_running_loop().call_later(0.1, finished.set_result, CallToolResult(
        content=[TextContent(type="text", text="done")], isError=False))
    outcome = await client._follow(call)  # pylint: disable=protected-access
    assert outcome.inputs == [("nested form", INPUT_AUTO_DECLINED, None)]
    assert any(entry["outcome"] == "INPUT DECLINED" and entry.get("request_id") == "r1"
               for entry in client_log())


def test_no_auto_approving_shortcut():
    assert not hasattr(MCPPermissionClient, "request_elicitation")
