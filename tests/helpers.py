"""Test helpers. Importing this module creates the temporary project the whole suite runs in.

The client and server find their folders from their own file locations, so the tests import the
client from a copy of the project in a temporary folder: every path then points into the copy and
the real workspace/, logs and permissions.json are never touched. The server is never imported;
it runs as a child process, as it does in the app.
"""

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import gradio as gr
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp.types import (CallToolResult, ElicitRequest, ElicitRequestFormParams, ElicitResult,
                       InputRequiredResult, TextContent)

REPO = Path(__file__).resolve().parent.parent
# The code under test; a sabotage run points this at a deliberately broken copy
SOURCE = Path(os.environ.get("MCP_TEST_SOURCE", REPO)).resolve()

ELICITATION_TIMEOUT = 3
os.environ["MCP_DEMO_ELICITATION_TIMEOUT"] = str(ELICITATION_TIMEOUT)

PROJECT = Path(tempfile.mkdtemp(prefix="mcp-part1-tests-")).resolve()
for _side in ("client", "server"):
    shutil.copytree(SOURCE / _side, PROJECT / _side,
                    ignore=shutil.ignore_patterns("__pycache__", "*.log", "logs",
                                                  "permissions.json"))
(PROJECT / "workspace").mkdir()
sys.path.insert(0, str(PROJECT / "client"))
from client import innermost_error  # noqa: E402  pylint: disable=wrong-import-position

WORKSPACE = PROJECT / "workspace"
SERVER_SCRIPT = str(PROJECT / "server" / "server.py")
CLIENT_LOG = PROJECT / "client" / "client_audit.log"
SERVER_LOG = PROJECT / "server" / "logs" / "server_audit.log"
PERMISSIONS = PROJECT / "client" / "permissions.json"
CRASH_SERVER = str(Path(__file__).resolve().parent / "servers" / "crash_server.py")


class FakeSession:
    """Stands in for the MCP session in unit tests: records calls, never starts a server."""

    def __init__(self, text="ok", is_error=False, raises=None):
        self.calls, self.text, self.is_error, self.raises = [], text, is_error, raises

    async def call_tool(self, name, arguments, **_rounds):
        self.calls.append((name, dict(arguments)))
        if self.raises:
            raise self.raises
        return CallToolResult(content=[TextContent(type="text", text=self.text)],
                              is_error=self.is_error)


def outcome_text(outcome) -> str:
    """The text of a tool outcome's first content block."""
    return outcome.content[0].text


def _read_log(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def client_log() -> list[dict]:
    """The client's audit records."""
    return _read_log(CLIENT_LOG)


def server_log() -> list[dict]:
    """The server's audit records."""
    return _read_log(SERVER_LOG)


def server_params(folder=None) -> StdioServerParameters:
    """How the tests launch the real server: optionally with --allowed-folder."""
    args = [SERVER_SCRIPT] + (["--allowed-folder", str(folder)] if folder is not None else [])
    return StdioServerParameters(command=sys.executable, args=args, env=dict(os.environ))


async def call_server(tool, arguments, folder=None, elicitation=None) -> tuple[bool, str]:
    """Call the real server directly, as a different 2026-07-28 client would: mcp's Client drives
    any input_required rounds through the scripted elicitation callback (None means the
    capability isn't declared). Returns (is_error, text); a protocol error counts as an error."""
    try:
        async with Client(server_params(folder), mode="2026-07-28",
                          elicitation_callback=elicitation) as client:
            result = await client.call_tool(tool, arguments)
    except Exception as e:  # pylint: disable=broad-exception-caught
        error = innermost_error(e)
        if not isinstance(error, MCPError):
            raise
        return True, str(error)
    return result.is_error, result.content[0].text


@contextlib.asynccontextmanager
async def raw_session(folder=None):
    """A 2026-07-28 session to the real server whose rounds the test drives by hand, as an
    attacker's client would (elicitation declared; the callback is never used)."""
    async with Client(server_params(folder), mode="2026-07-28",
                      elicitation_callback=elicitation_answer("decline")) as client:
        yield client.session


class RoundsSession:
    """A scripted 2026-07-28 session: the first call returns input_required with one question,
    the retry returns a result. Records (name, arguments, request_state) per call."""

    def __init__(self, with_state=True):
        self.calls, self.with_state = [], with_state

    async def call_tool(self, name, arguments, input_responses=None, request_state=None,
                        allow_input_required=False):  # pylint: disable=unused-argument
        self.calls.append((name, dict(arguments), request_state))
        if input_responses is None:
            question = ElicitRequest(params=ElicitRequestFormParams(
                message="OK?", requested_schema={"type": "object",
                                                 "properties": {"ok": {"type": "boolean"}}}))
            return InputRequiredResult(input_requests={"q": question},
                                       request_state="sealed" if self.with_state else None)
        return CallToolResult(content=[TextContent(type="text", text="done")], is_error=False)


def elicitation_answer(action, content=None, delay=0, seen=None):
    """An elicitation callback that answers action/content, recording each question in seen."""
    async def answer(_context, params):
        if seen is not None:
            seen.append((params.message, params.requested_schema))
        await asyncio.sleep(delay)
        return ElicitResult(action=action, content=content)
    return answer


def select_row(name):
    """The event Gradio sends when a tool table row is clicked."""
    return gr.SelectData(None, {"index": [0, 0], "value": name, "row_value": [name, "", ""]})
