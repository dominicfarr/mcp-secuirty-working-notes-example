"""One real turn against a real model. Skipped unless HOST_MODEL is set; never run in CI."""

import os

import pytest

from helpers import WORKSPACE, client_log

pytestmark = [pytest.mark.live, pytest.mark.integration, pytest.mark.anyio,
              pytest.mark.skipif(not os.environ.get("HOST_MODEL"),
                                 reason="set HOST_MODEL (and HOST_MODEL_BASE_URL) to run")]


async def test_real_model_reads_a_file(make_app):
    from agent import Agent  # pylint: disable=import-outside-toplevel
    from model import OpenAICompatibleModel  # pylint: disable=import-outside-toplevel
    (WORKSPACE / "a.txt").write_text("The answer is 42.")
    app = make_app({"read_file": "allow"})
    result = await Agent(app, OpenAICompatibleModel()).send(
        "Use the read_file tool to read a.txt, then tell me the number in it.")
    assert "42" in result.reply
    assert ("read_file", "model") in [(e["tool"], e.get("source")) for e in client_log()]
