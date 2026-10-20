"""The model behind the host: any OpenAI-compatible chat-completions endpoint.

Local Ollama by default; OpenAI, or Anthropic's OpenAI-compatible endpoint, by changing
HOST_MODEL_BASE_URL, HOST_MODEL and HOST_MODEL_API_KEY. Nothing here decides what a tool call
may do: that is the client's job."""

import os
from dataclasses import dataclass
from typing import Protocol

from openai import AsyncOpenAI

DEFAULT_BASE_URL = "http://localhost:11434/v1"  # Ollama's OpenAI-compatible API
DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_API_KEY = "ollama"  # Ollama ignores it; the SDK requires one


@dataclass(frozen=True)
class ToolCall:
    """One tool call as the model wrote it; the arguments are unparsed JSON text."""
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class AssistantMessage:
    """The model's reply: text, tool calls, or both."""
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


class ChatModel(Protocol):  # pylint: disable=too-few-public-methods
    """What the agent needs from a model."""
    name: str

    async def complete(self, messages: list[dict], tools: list[dict]) -> AssistantMessage:
        """The model's next reply to the conversation so far, given these tools."""


def tool_specs(tools) -> list[dict]:
    """MCP tools as OpenAI function specs: name, description and input schema exactly as the
    server sent them (the baseline passes server text to the model unchanged)."""
    return [{"type": "function",
             "function": {"name": tool.name, "description": tool.description or "",
                          "parameters": tool.input_schema}}
            for tool in tools]


class OpenAICompatibleModel:  # pylint: disable=too-few-public-methods
    """A chat-completions model reached through the openai SDK."""

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, client=None):
        self.name = model or os.environ.get("HOST_MODEL", DEFAULT_MODEL)
        base_url = base_url or os.environ.get("HOST_MODEL_BASE_URL", DEFAULT_BASE_URL)
        if client is None:
            client = AsyncOpenAI(
                base_url=base_url,
                api_key=api_key or os.environ.get("HOST_MODEL_API_KEY", DEFAULT_API_KEY))
            base_url = str(client.base_url)  # as the SDK normalised it (trailing slash)
        self.base_url = base_url
        self._client = client

    async def complete(self, messages: list[dict], tools: list[dict]) -> AssistantMessage:
        """One chat completion; tools are sent only if there are any."""
        extra = {"tools": tools} if tools else {}
        response = await self._client.chat.completions.create(model=self.name,
                                                              messages=messages, **extra)
        message = response.choices[0].message
        calls = tuple(ToolCall(call.id, call.function.name, call.function.arguments or "")
                      for call in message.tool_calls or [])
        return AssistantMessage(message.content or "", calls)
