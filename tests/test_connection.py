"""The client reports connection problems and recovers from them."""

import asyncio

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from client import innermost_error
from helpers import (CRASH_SERVER, SERVER_SCRIPT, WORKSPACE, FakeSession, client_log, outcome_text,
                     server_params)


@pytest.mark.integration
@pytest.mark.anyio
async def test_unreachable_server_is_reported_not_raised(make_app):
    app = make_app(server_script="does_not_exist.py")
    rows, status = await app.gui_load_tools()
    assert rows == [] and status["visible"] is True and "Could not load tools" in status["value"]
    app.server_script = SERVER_SCRIPT  # Refresh after the server becomes reachable
    rows, status = await app.gui_load_tools()
    assert len(rows) == 4 and status["visible"] is False


@pytest.mark.anyio
async def test_lost_connection_becomes_an_error_outcome(make_client):
    client, _ = make_client({"read_file": "allow"})
    client.session = FakeSession(raises=MCPError(-32000, "Connection closed"))
    outcome = await client.request_tool("read_file", {"filepath": "a.txt"})
    assert outcome.is_error and "Connection closed" in outcome_text(outcome)
    assert client_log()[-1]["outcome"] == "ERROR"


@pytest.mark.integration
@pytest.mark.anyio
async def test_reconnects_after_the_server_crashes(make_app):
    app = make_app({"crash": "allow", "ping": "allow"}, server_script=CRASH_SERVER)
    crashed = await app.request_tool("crash", {})
    assert crashed.is_error
    pinged = await app.request_tool("ping", {})
    assert not pinged.is_error and outcome_text(pinged) == "pong"


@pytest.mark.integration
@pytest.mark.anyio
async def test_interrupted_request_does_not_steal_the_next_question(make_app):
    app = make_app({"read_file": "allow", "delete_file": "allow"})
    (WORKSPACE / "a.txt").write_text("x")
    reading = asyncio.ensure_future(app.request_tool("read_file", {"filepath": "a.txt"}))
    await asyncio.sleep(0)
    reading.cancel()
    paused = await asyncio.wait_for(app.request_tool("delete_file", {"filepath": "a.txt"}), 15)
    assert paused.input_request is not None, "the question reached its own call"
    await app.answer_input(paused.input_request.input_id, "decline")


@pytest.mark.integration
@pytest.mark.anyio
async def test_client_speaks_2026_07_28(make_app):
    app = make_app()
    await app.list_tools()
    assert app.protocol_version == "2026-07-28"


@pytest.mark.integration
@pytest.mark.anyio
async def test_handshake_client_is_refused():
    # A handshake-era client (initialize) can't be served: this server's confirmations use multi
    # round-trip requests, which only exist on 2026-07-28
    try:
        async with Client(server_params(), mode="legacy") as client:
            await client.list_tools()
    except Exception as e:  # pylint: disable=broad-exception-caught
        error = innermost_error(e)
    else:
        pytest.fail("a handshake-era client was served")
    assert "2026-07-28 only" in str(error)
