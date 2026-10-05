"""The server confines its file tools to its workspace."""

import os
import shutil

import pytest
from mcp.types import ListRootsResult, Root

from helpers import PROJECT, WORKSPACE, call_server, outcome_text

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.mark.parametrize("path", ["../outside.txt", "../../etc/passwd", "/etc/hosts"])
async def test_path_traversal_is_refused(make_app, path):
    app = make_app({"read_file": "allow"})
    outcome = await app.request_tool("read_file", {"filepath": path})
    assert outcome.is_error and "outside the workspace" in outcome_text(outcome)


@pytest.mark.parametrize("tool", ["read_file", "write_file", "delete_file"])
async def test_blank_or_folder_path_is_refused(make_app, tool):
    app = make_app({tool: "allow"})
    (WORKSPACE / "sub").mkdir()
    for path, expected in [("", "A file path is required"), (".", "'.' is not a file path"),
                           ("sub", "'sub' is not a file path")]:
        arguments = {"filepath": path, "content": "x"} if tool == "write_file" else {"filepath": path}
        outcome = await app.request_tool(tool, arguments)
        assert outcome.is_error and expected in outcome_text(outcome), (path, outcome_text(outcome))
    assert (WORKSPACE / "sub").is_dir()


async def test_errors_never_reveal_server_paths(make_app):
    app = make_app({"read_file": "allow", "write_file": "allow"})
    (WORKSPACE / "sub").mkdir()
    for tool, arguments in [("write_file", {"filepath": "nodir/a.txt", "content": "x"}),
                            ("read_file", {"filepath": "missing.txt"}),
                            ("read_file", {"filepath": "sub"}),
                            ("read_file", {"filepath": "../x.txt"})]:
        outcome = await app.request_tool(tool, arguments)
        assert outcome.is_error and str(PROJECT) not in outcome_text(outcome), outcome_text(outcome)


async def test_symlink_swapped_during_a_call_is_not_followed():
    outside = PROJECT / "outside"
    outside.mkdir(exist_ok=True)
    (WORKSPACE / "projects").mkdir()

    async def swap_then_answer(_context):
        shutil.rmtree(WORKSPACE / "projects")
        os.symlink(outside, WORKSPACE / "projects")
        return ListRootsResult(roots=[Root(uri=WORKSPACE.as_uri(), name="workspace")])

    is_error, text = await call_server("write_file", {"filepath": "projects/x.txt", "content": "x"},
                                       roots=swap_then_answer)
    assert is_error and "outside the workspace" in text
    assert not (outside / "x.txt").exists()
