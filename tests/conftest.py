"""Fixtures: every test starts with a clean temporary project and its own client."""

import json
import shutil

import pytest

import helpers
from helpers import CLIENT_LOG, PERMISSIONS, PROJECT, SERVER_LOG, SERVER_SCRIPT, WORKSPACE


def pytest_sessionfinish(session, exitstatus):  # pylint: disable=unused-argument
    """Remove the temporary project."""
    shutil.rmtree(PROJECT, ignore_errors=True)


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio, as the app does."""
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_project():
    """An empty workspace, no logs and no permissions overrides before every test."""
    shutil.rmtree(WORKSPACE, ignore_errors=True)
    WORKSPACE.mkdir()
    for path in (CLIENT_LOG, SERVER_LOG, PERMISSIONS):
        path.unlink(missing_ok=True)


def _write_permissions(permissions):
    PERMISSIONS.write_text(json.dumps(permissions or {}))


@pytest.fixture
def make_client():
    """A client with a fake session: unit tests of client behaviour, no server started."""
    import client as client_module  # pylint: disable=import-outside-toplevel

    def make(permissions=None):
        _write_permissions(permissions)
        made = client_module.MCPPermissionClient(SERVER_SCRIPT)
        made.session = helpers.FakeSession()
        return made, made.session
    return make


@pytest.fixture
async def make_app():
    """GUI app clients that talk to a real server; all are cleaned up after the test."""
    import app as app_module  # pylint: disable=import-outside-toplevel
    made = []

    def make(permissions=None, server_script=SERVER_SCRIPT):
        _write_permissions(permissions)
        made.append(app_module.MCPPermissionClientApp(server_script))
        return made[-1]
    yield make
    for each in made:
        await each.cleanup()
