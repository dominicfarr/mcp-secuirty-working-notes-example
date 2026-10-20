"""Part 2 host: a model's tool calls meet the same client as the user's."""

import json

import pytest
from mcp.shared.exceptions import MCPError

import asyncio

from agent import BUSY, DENIED, QUESTION_CLOSED, REJECTED, RUNNING, STOPPED, TOOL_ROUNDS_MAX
from helpers import WORKSPACE, calls, client_log, says, server_log

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_audit_records_the_source(make_app):
    (WORKSPACE / "a.txt").write_text("x")
    app = make_app({"read_file": "allow"})
    await app.request_tool("read_file", {"filepath": "a.txt"})
    await app.request_tool("read_file", {"filepath": "a.txt"}, source="model")
    sources = [(e["outcome"], e.get("source")) for e in client_log()]
    assert sources == [("ALLOWED", "user"), ("COMPLETED", "user"),
                       ("ALLOWED", "model"), ("COMPLETED", "model")]


async def test_resource_reads_inside_the_folder(make_app):
    (WORKSPACE / "projects").mkdir()
    (WORKSPACE / "projects" / "notes.txt").write_text("remember the milk")
    app = make_app()
    contents = await app.read_resource("workspace://projects/notes.txt")
    assert contents[0].text == "remember the milk"
    assert server_log()[-1]["tool"] == "workspace_file"
    assert client_log()[-1] | {"ts": None} == {
        "ts": None, "actor": "client", "tool": "(resource)",
        "args": {"uri": "workspace://projects/notes.txt"}, "outcome": "READ", "detail": "",
        "source": "user"}


async def test_resource_outside_folder_is_refused(make_app):
    (WORKSPACE / "projects").mkdir()
    (WORKSPACE / "top.txt").write_text("x")
    app = make_app()
    await app.set_folder("workspace/projects")
    with pytest.raises(MCPError, match="outside the allowed folder"):
        await app.read_resource("workspace://top.txt")
    assert client_log()[-1]["outcome"] == "ERROR"
    assert server_log()[-1]["outcome"] == "error"


def tool_results(model):
    """The tool results in the last context the model was sent."""
    return [m["content"] for m in model.sent[-1] if m["role"] == "tool"]


def audited(tool, outcome, source):
    return (tool, outcome, source) in [(e["tool"], e["outcome"], e.get("source"))
                                       for e in client_log()]


async def test_model_call_allowed_runs(make_agent):
    (WORKSPACE / "a.txt").write_text("hello")
    agent, _, model = make_agent(calls("read_file", {"filepath": "a.txt"}), says("It says hello."),
                                 permissions={"read_file": "allow"})
    result = await agent.send("what's in a.txt?")
    assert result.reply == "It says hello."
    assert tool_results(model) == ["hello"]


async def test_model_call_denied_is_refused_and_audited(make_agent):
    (WORKSPACE / "a.txt").write_text("x")
    agent, _, model = make_agent(calls("delete_file", {"filepath": "a.txt"}), says("I can't."),
                                 permissions={"delete_file": "deny"})
    await agent.send("delete a.txt")
    assert tool_results(model) == [DENIED]
    assert (WORKSPACE / "a.txt").exists()
    assert audited("delete_file", "DENIED", "model")
    assert not [e for e in server_log() if e["tool"] == "delete_file"]


async def test_model_call_ask_waits_for_approval(make_agent):
    agent, _, model = make_agent(calls("write_file", {"filepath": "b.txt", "content": "x"}),
                                 says("Written."), permissions={"write_file": "ask"})
    result = await agent.send("write b.txt")
    assert result.paused is not None and result.paused.decision == "ASK"
    assert not (WORKSPACE / "b.txt").exists() and len(model.sent) == 1
    done = await agent.approve(result.paused.request.request_id)
    assert done.reply == "Written." and (WORKSPACE / "b.txt").read_text() == "x"


async def test_rejected_call_is_reported_to_the_model(make_agent):
    agent, _, model = make_agent(calls("write_file", {"filepath": "b.txt", "content": "x"}),
                                 says("OK."), permissions={"write_file": "ask"})
    result = await agent.send("write b.txt")
    done = await agent.reject(result.paused.request.request_id)
    assert done.reply == "OK." and tool_results(model) == [REJECTED]
    assert not (WORKSPACE / "b.txt").exists()
    assert audited("write_file", "REJECTED", "model")


async def test_approved_call_runs_the_arguments_the_model_sent(make_agent):
    agent, _, _ = make_agent(calls("write_file", {"filepath": "b.txt", "content": "x"}),
                             says("Written."), permissions={"write_file": "ask"})
    result = await agent.send("write b.txt")
    result.paused.request.arguments["filepath"] = "other.txt"  # the copy the UI holds
    await agent.approve(result.paused.request.request_id)
    assert (WORKSPACE / "b.txt").exists() and not (WORKSPACE / "other.txt").exists()


async def test_server_question_goes_to_the_user_not_the_model(make_agent):
    (WORKSPACE / "a.txt").write_text("x")
    agent, _, model = make_agent(calls("delete_file", {"filepath": "a.txt"}), says("Deleted."),
                                 permissions={"delete_file": "allow"})
    result = await agent.send("delete a.txt")
    question = result.paused.input_request
    assert "Confirm deletion of a.txt" in question.message
    done = await agent.answer(question.input_id, "accept",
                              {"confirm_name": "a.txt", "reason": "reason-7f3a9"})
    assert done.reply == "Deleted." and not (WORKSPACE / "a.txt").exists()
    seen = json.dumps(model.sent)
    assert "Confirm deletion" not in seen and "reason-7f3a9" not in seen
    assert tool_results(model) == ["Deleted a.txt"]


async def test_unknown_tool_is_not_sent(make_agent):
    agent, _, model = make_agent(calls("format_disk", {}), says("Sorry."))
    await agent.send("format the disk")
    assert tool_results(model) == ["Error: unknown tool format_disk"]
    assert audited("(unknown tool)", "NOT SENT", "model")
    assert server_log() == []


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]"])
async def test_invalid_json_arguments_are_not_sent(make_agent, arguments):
    agent, _, model = make_agent(calls("read_file", arguments), says("Sorry."),
                                 permissions={"read_file": "allow"})
    await agent.send("read something")
    assert tool_results(model) == ["Error: arguments were not valid JSON"]
    assert audited("read_file", "NOT SENT", "model")
    assert server_log() == []


async def test_tool_rounds_are_capped(make_agent):
    (WORKSPACE / "a.txt").write_text("x")
    script = [calls("read_file", {"filepath": "a.txt"}, f"c{i}") for i in range(20)]
    agent, _, model = make_agent(*script, permissions={"read_file": "allow"})
    result = await agent.send("keep reading")
    assert result.reply == STOPPED and len(model.sent) == TOOL_ROUNDS_MAX


async def test_several_calls_in_one_reply_run_in_order_and_each_is_answered(make_agent):
    from model import AssistantMessage, ToolCall  # pylint: disable=import-outside-toplevel
    two = AssistantMessage(tool_calls=(
        ToolCall("c1", "write_file", '{"filepath": "a.txt", "content": "1"}'),
        ToolCall("c2", "write_file", '{"filepath": "b.txt", "content": "2"}')))
    agent, _, model = make_agent(two, says("Both written."), permissions={"write_file": "ask"})
    first = await agent.send("write two files")
    assert first.paused.request.arguments["filepath"] == "a.txt"
    second = await agent.approve(first.paused.request.request_id)
    assert second.paused.request.arguments["filepath"] == "b.txt"
    assert (WORKSPACE / "a.txt").exists() and not (WORKSPACE / "b.txt").exists()
    done = await agent.approve(second.paused.request.request_id)
    assert done.reply == "Both written."
    tool_ids = [m["tool_call_id"] for m in model.sent[-1] if m["role"] == "tool"]
    assert tool_ids == ["c1", "c2"]


async def test_attached_resource_is_in_context(make_agent):
    (WORKSPACE / "notes.txt").write_text("remember the milk")
    agent, _, model = make_agent(says("Noted."))
    await agent.attach_resource("workspace://notes.txt")
    await agent.send("summarise")
    assert model.sent[0][-1]["content"] == (
        "Attached resource workspace://notes.txt:\nremember the milk\n\nsummarise")


async def test_failed_attachment_adds_nothing(make_agent):
    agent, app, model = make_agent(says("Hi."))
    (WORKSPACE / "projects").mkdir()
    (WORKSPACE / "top.txt").write_text("x")
    await app.set_folder("workspace/projects")
    with pytest.raises(MCPError):
        await agent.attach_resource("workspace://top.txt")
    await agent.send("hi")
    assert model.sent[0][-1]["content"] == "hi"


async def test_prompt_messages_join_the_conversation(make_agent):
    agent, _, model = make_agent(says("Reviewed."))
    result = await agent.use_prompt("security_review",
                                    {"operation": "delete a.txt", "risk_level": "HIGH"})
    assert result.reply == "Reviewed."
    last = model.sent[0][-1]
    assert last["role"] == "user" and "Operation: delete a.txt" in last["content"]
    assert audited("(prompt)", "READ", "user")


async def test_new_message_while_paused_is_refused(make_agent):
    agent, _, model = make_agent(calls("write_file", {"filepath": "b.txt", "content": "x"}),
                                 permissions={"write_file": "ask"})
    await agent.send("write b.txt")
    assert (await agent.send("and another thing")).error == BUSY
    assert len(model.sent) == 1


async def test_folder_change_closes_a_paused_question(make_agent):
    (WORKSPACE / "a.txt").write_text("x")
    agent, app, model = make_agent(calls("delete_file", {"filepath": "a.txt"}), says("OK."),
                                   permissions={"delete_file": "allow"})
    paused = (await agent.send("delete a.txt")).paused
    await app.set_folder("workspace")  # restarts the server
    done = await agent.answer(paused.input_request.input_id, "accept",
                              {"confirm_name": "a.txt", "reason": "old draft"})
    assert done.reply == "OK." and tool_results(model) == [QUESTION_CLOSED]
    assert (WORKSPACE / "a.txt").exists()


async def test_new_chat_rejects_pending_approval(make_agent):
    agent, app, _ = make_agent(calls("write_file", {"filepath": "b.txt", "content": "x"}),
                               permissions={"write_file": "ask"})
    await agent.send("write b.txt")
    await agent.new_chat()
    assert agent.paused is None and app.pending == {} and len(agent.messages) == 1
    assert audited("write_file", "REJECTED", "model")


async def test_model_error_ends_the_turn(make_agent):
    agent, _, _ = make_agent(RuntimeError("model down"))
    result = await agent.send("hello")
    assert result.error == "Model error: model down" and server_log() == []


async def test_tool_specs_match_the_server(make_agent):
    from model import tool_specs  # pylint: disable=import-outside-toplevel
    agent, app, model = make_agent(says("Hi."))
    await agent.send("hi")
    tools = await app.list_tools()
    assert model.tools_sent[0] == tool_specs(tools)
    assert {s["function"]["name"] for s in model.tools_sent[0]} == {t.name for t in tools}


class SlowModel:  # pylint: disable=too-few-public-methods
    """A scripted model that waits at each reply until the test lets it go."""
    name = "slow"

    def __init__(self, *replies):
        self.replies, self.sent, self.gate = list(replies), [], asyncio.Event()

    async def complete(self, messages, tools):  # pylint: disable=unused-argument
        self.sent.append([dict(m) for m in messages])
        await self.gate.wait()
        return self.replies.pop(0)


def tool_calls_answered(messages) -> bool:
    """Every assistant tool call is followed by its tool message (endpoints require it)."""
    asked = [c["id"] for m in messages if m["role"] == "assistant" for c in m.get("tool_calls", [])]
    answered = [m["tool_call_id"] for m in messages if m["role"] == "tool"]
    return asked == answered


async def test_new_turn_while_one_is_running_is_refused(make_app):
    import agent as agent_module  # pylint: disable=import-outside-toplevel
    (WORKSPACE / "a.txt").write_text("x")
    model = SlowModel(calls("read_file", {"filepath": "a.txt"}), says("Done."))
    agent = agent_module.Agent(make_app({"read_file": "allow"}), model)
    first = asyncio.create_task(agent.send("read a.txt"))
    while not model.sent:
        await asyncio.sleep(0.01)
    # Bounded: without the refusal, a second turn would wait on the same slow model
    assert (await asyncio.wait_for(agent.send("and another"), 2)).error == RUNNING
    assert (await asyncio.wait_for(agent.use_prompt("security_review", {}), 2)).error == RUNNING
    with pytest.raises(RuntimeError, match=RUNNING):
        await asyncio.wait_for(agent.new_chat(), 2)
    model.gate.set()
    assert (await first).reply == "Done."
    assert tool_calls_answered(agent.messages) and len(model.sent) == 2


async def test_approval_binds_to_the_request_shown(make_agent):
    from model import AssistantMessage, ToolCall  # pylint: disable=import-outside-toplevel
    two = AssistantMessage(tool_calls=(
        ToolCall("c1", "write_file", '{"filepath": "a.txt", "content": "1"}'),
        ToolCall("c2", "write_file", '{"filepath": "evil.txt", "content": "2"}')))
    agent, _, _ = make_agent(two, says("OK."), permissions={"write_file": "ask"})
    shown = (await agent.send("write a file")).paused.request.request_id
    await agent.approve(shown)  # a.txt; now evil.txt waits, unseen
    with pytest.raises(RuntimeError):
        await agent.approve(shown)  # a stale card, or a second browser tab
    assert (WORKSPACE / "a.txt").exists() and not (WORKSPACE / "evil.txt").exists()


async def test_bad_prompt_request_is_audited(make_app):
    app = make_app()
    with pytest.raises(ValueError):
        await app.get_prompt("security_review", {"operation": 1, "risk_level": "LOW"})
    assert audited("(prompt)", "ERROR", "user")
