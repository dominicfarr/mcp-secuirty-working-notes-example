"""Read JSON-lines audit logs into table rows for the Audit tab."""

import json
from pathlib import Path

CLIENT_COLUMNS = ["Time (UTC)", "Request", "Tool", "Arguments", "Outcome", "Detail"]
SERVER_COLUMNS = ["Time (UTC)", "Tool", "Arguments", "Outcome", "Detail"]


def audit_rows(path: str | Path, with_request_id: bool) -> list[list[str]]:
    """Rows for an audit log, newest first. Lines that aren't valid records are shown, not hidden,
    so a damaged or tampered log is visible."""
    path = Path(path)
    if not path.exists():
        return []

    rows = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        try:
            entry = json.loads(line)
            row = [entry["ts"][:19].replace("T", " "), entry["tool"], json.dumps(entry["args"]),
                   entry["outcome"], entry.get("detail", "")]
        except (ValueError, KeyError, TypeError):
            row = ["", "", "", "?", f"unreadable line {number}"]
            entry = {}
        if with_request_id:
            row.insert(1, entry.get("request_id", ""))
        rows.append(row)
    return rows[::-1]
