"""Pure helpers for the Tools tab: argument parsing, schema templates and the step timeline."""

import json
from pathlib import Path
from mcp.types import TextContent
from audit import summarise_arguments
from permissions import DEFAULT_PERMISSIONS
from client import ToolOutcome, ToolRequest, REASON_USER_APPROVED, REASON_POLICY_CHANGED

DONE, WAIT, FAIL, TODO = "✓", "⏸", "✗", "·"

# Placeholder value per JSON Schema type for the arguments template
JSON_DEFAULTS = {"string": "", "integer": 0, "number": 0, "boolean": False,
                 "array": [], "object": {}}

# Root preset meaning "/": declaring the whole disk, which still gives the server only its own limit
WHOLE_DISK = "/ (whole disk)"


def root_choices(workspace: Path) -> list[str]:
    """Root presets: the workspace, each of its subfolders, and the whole disk."""
    subfolders = (sorted(d.name for d in workspace.iterdir() if d.is_dir())
                  if workspace.is_dir() else [])
    return ([f"{workspace.name}/"] + [f"{workspace.name}/{name}/" for name in subfolders]
            + [WHOLE_DISK])


def root_display(path: Path, project_dir: Path) -> str:
    """How a root is shown in the dropdown: relative to the project folder where possible."""
    if path == Path("/"):
        return WHOLE_DISK
    if path.is_relative_to(project_dir):
        relative = path.relative_to(project_dir).as_posix()
        return "./" if relative == "." else f"{relative}/"
    return f"{path}/"


def parse_root(value: str) -> str | None:
    """The path typed or picked in the dropdown, or None if it's empty."""
    value = value.strip()
    if not value:
        return None
    return "/" if value == WHOLE_DISK else value


def declared_line(uris: list[str]) -> str:
    """The line under the dropdown: exactly what the client sends to the server."""
    return "Declared: " + ", ".join(f"`{uri}`" for uri in uris)


def first_line(text: str | None) -> str:
    """First non-blank line of a tool description, for the tool table."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def policy_label(tool_name: str, permissions: dict) -> str:
    """Policy for the tool table, with where it comes from: the client default or the file."""
    default = DEFAULT_PERMISSIONS.get(tool_name, "ask")
    policy = permissions.get(tool_name, default)
    return f"{policy} · {'default' if policy == default else 'permissions.json'}"


def args_template(input_schema: dict | None, path_prefix: str = "") -> str:
    """JSON template with a placeholder for each property in a tool's input schema.

    A `filepath` property starts with path_prefix, so it points inside the client's root."""
    properties = (input_schema or {}).get("properties", {})
    template = {name: path_prefix if name == "filepath" else JSON_DEFAULTS.get(spec.get("type"), "")
                for name, spec in properties.items()}
    return json.dumps(template, indent=2) if template else "{}"


def root_prefix(root: Path, workspace: Path) -> str:
    """The client's root as a path prefix relative to the workspace ("ROOT/"), or "" when the
    root is the workspace itself or lies outside it."""
    if root != workspace and root.is_relative_to(workspace):
        return f"{root.relative_to(workspace).as_posix()}/"
    return ""


def parse_arguments(arguments_json: str) -> dict:
    """Parse the Arguments box; empty means {}. Raises ValueError unless it is a JSON object."""
    if not arguments_json.strip():
        return {}
    arguments = json.loads(arguments_json)
    if not isinstance(arguments, dict):
        raise ValueError('arguments must be a JSON object, e.g. {"filepath": "a.txt"}')
    return arguments


def content_text(content: list | None) -> str:
    """Text of a tool result; non-text blocks become a short type note."""
    return "\n".join(block.text if isinstance(block, TextContent) else f"[{block.type} content]"
                     for block in content or [])


def render_timeline(header: str, steps: list[tuple[str | None, str]], body: str = "") -> str:
    """Markdown for a numbered step trail, with an optional result body underneath.

    A step whose mark is None is an unnumbered detail line under the step before it."""
    lines = [f"**{header}**", ""]
    number = 0
    for mark, text in steps:
        if mark is None:
            lines.append(f"&emsp;&emsp;{text}  ")
        else:
            number += 1
            lines.append(f"{mark} **{number}** &nbsp; {text}  ")
    markdown = "\n".join(lines)
    if body:
        markdown += f"\n\n````\n{body}\n````"
    return markdown


def timeline_for(outcome: ToolOutcome) -> str:
    """Step timeline for a request's current state."""
    request = outcome.request
    arguments = json.dumps(summarise_arguments(request.arguments))
    header = f"Request {request.request_id}: {request.tool_name} {arguments}"
    built = (DONE, "Request built")

    if outcome.decision == "ASK":
        return render_timeline(header, [
            built, (WAIT, "Client policy: ASK, waiting for your approval"),
            (TODO, "Sent to server"), (TODO, "Result")])

    if outcome.decision == "DENIED":
        policy = ("Client policy: DENY (changed since request)"
                  if outcome.reason == REASON_POLICY_CHANGED else "Client policy: DENY")
        return render_timeline(header, [built, (FAIL, policy), (TODO, "Not sent")])

    if outcome.decision == "REJECTED":
        return render_timeline(header, [built, (FAIL, "Rejected by you"), (TODO, "Not sent")])

    policy = ("Client policy: ASK, user approved"
              if outcome.reason == REASON_USER_APPROVED else "Client policy: ALLOW")
    steps = [built, (DONE, policy),
             (DONE, "Sent to server (tools/call: name + arguments only, no root)")]
    if outcome.roots_answered is not None:
        answered = ", ".join(f"`{uri}`" for uri in outcome.roots_answered) or "(no roots)"
        steps.append((None, "↩ Server asked the client for its roots (`roots/list`); "
                            f"client answered: {answered}"))
    steps.append((FAIL, "Error") if outcome.is_error else (DONE, "Result"))
    return render_timeline(header, steps, content_text(outcome.content))


def invalid_arguments_timeline(tool_name: str, error: str) -> str:
    """Timeline when the Arguments box can't be turned into a request."""
    return render_timeline(f"{tool_name}: request not built",
                           [(FAIL, f"Invalid arguments: {error}")])


def not_pending_timeline(request_id: str) -> str:
    """Timeline when approve/reject names a request that is no longer pending."""
    return render_timeline(f"Request {request_id}",
                           [(FAIL, f"Request {request_id} is no longer pending")])


def approval_card(request: ToolRequest) -> str:
    """Markdown for the approval card: the exact request the user is approving."""
    return (f"**Approval needed: request `{request.request_id}`**\n\n"
            f"Tool: `{request.tool_name}`\n\n"
            f"````json\n{json.dumps(request.arguments, indent=2)}\n````")
