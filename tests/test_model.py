"""The model adapter speaks OpenAI-compatible chat completions and nothing else."""

from types import SimpleNamespace

import pytest

from model import AssistantMessage, OpenAICompatibleModel, ToolCall


class FakeCompletions:  # pylint: disable=too-few-public-methods
    """Stands in for client.chat.completions: records the request, returns one message."""

    def __init__(self, message):
        self.message, self.kwargs = message, None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=self.message)])


def fake_client(message):
    completions = FakeCompletions(message)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


@pytest.mark.anyio
async def test_openai_reply_is_parsed():
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="read_file",
                                                             arguments='{"filepath": "a.txt"}'))
    client, completions = fake_client(SimpleNamespace(content=None, tool_calls=[call]))
    model = OpenAICompatibleModel(model="m", client=client)
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    reply = await model.complete([{"role": "user", "content": "hi"}], tools)
    assert reply == AssistantMessage("", (ToolCall("c1", "read_file", '{"filepath": "a.txt"}'),))
    assert completions.kwargs["model"] == "m" and completions.kwargs["tools"] == tools


@pytest.mark.anyio
async def test_no_tools_are_not_sent_as_an_empty_list():
    client, completions = fake_client(SimpleNamespace(content="hello", tool_calls=None))
    reply = await OpenAICompatibleModel(model="m", client=client).complete([], [])
    assert reply == AssistantMessage("hello") and "tools" not in completions.kwargs


def test_model_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("HOST_MODEL", "gpt-x")
    monkeypatch.setenv("HOST_MODEL_BASE_URL", "https://example.test/v1")
    model = OpenAICompatibleModel()
    assert model.name == "gpt-x" and str(model.base_url) == "https://example.test/v1/"


def test_defaults_point_at_local_ollama(monkeypatch):
    for name in ("HOST_MODEL", "HOST_MODEL_BASE_URL", "HOST_MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    model = OpenAICompatibleModel()
    assert model.name == "qwen2.5:7b" and str(model.base_url) == "http://localhost:11434/v1/"
