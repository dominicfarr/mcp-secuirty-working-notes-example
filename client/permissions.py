"""Shared permission policy: secure defaults layered under the permissions.json overrides."""

import json
from pathlib import Path

PERMISSIONS_FILE = Path(__file__).parent / "permissions.json"

DEFAULT_PERMISSIONS = {
    "read_file": "allow",
    "write_file": "ask",
    "delete_file": "deny",
    "execute_command": "deny",
}


def load_permissions(path: Path = PERMISSIONS_FILE) -> dict:
    """Load permissions from file, layered over the defaults."""
    permissions = dict(DEFAULT_PERMISSIONS)
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            permissions.update(json.loads(text))
    return permissions
