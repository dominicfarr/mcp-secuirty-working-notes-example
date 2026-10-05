"""Test helpers. Importing this module creates the temporary project the whole suite runs in.

The client and server find their folders from their own file locations, so the tests import the
client from a copy of the project in a temporary folder: every path then points into the copy and
the real workspace/, logs and permissions.json are never touched. The server is never imported;
it runs as a child process, as it does in the app.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import gradio as gr
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, ElicitResult, ListRootsResult, Root, TextContent

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

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if self.raises:
            raise self.raises
        return CallToolResult(content=[TextContent(type="text", text=self.text)],
                              isError=self.is_error)


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


async def call_server(tool, arguments, roots=None, elicitation=None) -> tuple[bool, str]:
    """Call the real server directly, as a different MCP client would (scripted callbacks; None
    means the capability isn't declared). Returns (is_error, text)."""
    params = StdioServerParameters(command=sys.executable, args=[SERVER_SCRIPT],
                                   env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, list_roots_callback=roots,
                                 elicitation_callback=elicitation) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
    return result.isError, result.content[0].text


def roots_answer(paths, delay=0):
    """A roots/list callback answering with these folders (after an optional delay)."""
    async def answer(_context):
        await asyncio.sleep(delay)
        return ListRootsResult(roots=[Root(uri=Path(p).as_uri(), name="root") for p in paths])
    return answer


def raw_roots(*uris):
    """A roots/list callback that sends these URIs unvalidated, as a non-Python client could."""
    async def answer(_context):
        return ListRootsResult.model_construct(
            roots=[Root.model_construct(uri=uri, name="root") for uri in uris])
    return answer


def elicitation_answer(action, content=None, delay=0, seen=None):
    """An elicitation callback that answers action/content, recording each question in seen."""
    async def answer(_context, params):
        if seen is not None:
            seen.append((params.message, params.requestedSchema))
        await asyncio.sleep(delay)
        return ElicitResult(action=action, content=content)
    return answer


def select_row(name):
    """The event Gradio sends when a tool table row is clicked."""
    return gr.SelectData(None, {"index": [0, 0], "value": name, "row_value": [name, "", ""]})
