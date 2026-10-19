"""MRTR: the state the server hands the client must come back sealed and unchanged, for the same
request; anything else deletes nothing."""

import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import ElicitResult, InputRequiredResult

from helpers import WORKSPACE, raw_session

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def accept(name):
    return {"confirm": ElicitResult(action="accept",
                                    content={"confirm_name": name, "reason": "old draft"})}


def tampered(state: str) -> str:
    """The sealed state with one character in its middle changed."""
    i = len(state) // 2
    return state[:i] + ("A" if state[i] != "A" else "B") + state[i + 1:]


async def first_round(session, name) -> str:
    result = await session.call_tool("delete_file", {"filepath": name}, allow_input_required=True)
    assert isinstance(result, InputRequiredResult)
    return result.request_state


async def test_tampered_request_state_is_rejected():
    (WORKSPACE / "a.txt").write_text("x")
    async with raw_session() as session:
        state = await first_round(session, "a.txt")
        with pytest.raises(MCPError, match="Invalid or expired requestState"):
            await session.call_tool("delete_file", {"filepath": "a.txt"},
                                    input_responses=accept("a.txt"), request_state=tampered(state))
        assert (WORKSPACE / "a.txt").exists()
        # Control: the untouched state on the same request is accepted, so the rejection above
        # was the tampering
        result = await session.call_tool("delete_file", {"filepath": "a.txt"},
                                         input_responses=accept("a.txt"), request_state=state)
    assert not result.is_error and not (WORKSPACE / "a.txt").exists()


async def test_request_state_cannot_be_reused_for_another_file():
    for name in ("a.txt", "b.txt"):
        (WORKSPACE / name).write_text("x")
    async with raw_session() as session:
        state = await first_round(session, "a.txt")
        with pytest.raises(MCPError, match="Invalid or expired requestState"):
            await session.call_tool("delete_file", {"filepath": "b.txt"},
                                    input_responses=accept("b.txt"), request_state=state)
        assert (WORKSPACE / "b.txt").exists()
        # Control: the same state on the request it was minted for is accepted
        result = await session.call_tool("delete_file", {"filepath": "a.txt"},
                                         input_responses=accept("a.txt"), request_state=state)
    assert not result.is_error and not (WORKSPACE / "a.txt").exists()


async def test_answer_without_request_state_deletes_nothing():
    (WORKSPACE / "a.txt").write_text("x")
    async with raw_session() as session:
        result = await session.call_tool("delete_file", {"filepath": "a.txt"},
                                         input_responses=accept("a.txt"),
                                         allow_input_required=True)
    assert isinstance(result, InputRequiredResult)  # asked again
    assert (WORKSPACE / "a.txt").exists()
