"""The suite itself: tests run in a temporary copy and start clean."""

from pathlib import Path

import client
from helpers import CLIENT_LOG, PERMISSIONS, PROJECT, REPO, WORKSPACE


def test_suite_runs_in_a_temporary_copy():
    assert Path(client.__file__).resolve().is_relative_to(PROJECT)
    assert not PROJECT.is_relative_to(REPO)
    assert client.DEFAULT_ROOT == WORKSPACE


def test_each_test_starts_with_a_clean_project():
    assert list(WORKSPACE.iterdir()) == []
    assert not CLIENT_LOG.exists() and not PERMISSIONS.exists()
    (WORKSPACE / "left-over.txt").write_text("x")  # the next test must not see this


def test_left_over_files_are_gone():
    assert not (WORKSPACE / "left-over.txt").exists()
