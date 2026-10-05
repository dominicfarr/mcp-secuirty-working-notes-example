"""Audit log format for the server: one JSON object per line.

The client keeps its own copy of this module (client/audit.py) and writes the same record
shape, so the two logs can be correlated (same tool, arguments and timestamps) without either
side reading the other's. Keep the two copies in step.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

# String arguments longer than this are logged as their length, not their content,
# so file contents and other large payloads don't end up in the audit trail.
MAX_LOGGED_STRING = 100


def summarise_arguments(arguments: dict) -> dict:
    """Return arguments safe to log, replacing long strings with their length."""
    return {
        key: f"<{len(value)} chars>"
        if isinstance(value, str) and len(value) > MAX_LOGGED_STRING else value
        for key, value in arguments.items()
    }


class AuditLog:  # pylint: disable=too-few-public-methods
    """An append-only JSON-lines audit log owned by one actor ("client" or "server")."""

    def __init__(self, path: Path, actor: str):
        self.path = path
        self.actor = actor

    def record(self, tool: str, arguments: dict, outcome: str, detail: str = "", **context):
        """Append one audit record. Extra keyword arguments (e.g. request_id) become fields."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": self.actor,
            "tool": tool,
            "args": summarise_arguments(arguments),
            "outcome": outcome,
            "detail": detail,
            **context,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
