"""Pure helpers for the Tools tab: argument parsing, schema templates and the step timeline."""

import json
from dataclasses import dataclass
from pathlib import Path
from mcp.types import TextContent
from audit import summarise_arguments
from permissions import DEFAULT_PERMISSIONS
from client import (ToolOutcome, ToolRequest, InputRequest, REASON_USER_APPROVED,
                    REASON_POLICY_CHANGED)

DONE, WAIT, FAIL, TODO = "✓", "⏸", "✗", "·"

# Placeholder value per JSON Schema type for the arguments template
JSON_DEFAULTS = {"string": "", "integer": 0, "number": 0, "boolean": False,
                 "array": [], "object": {}}

# Folder preset meaning "/": launching with the whole disk, which still gives the server only its
# workspace
WHOLE_DISK = "/ (whole disk)"


def folder_choices(workspace: Path) -> list[str]:
    """Folder presets: the workspace, each of its subfolders, and the whole disk."""
    subfolders = (sorted(d.name for d in workspace.iterdir() if d.is_dir())
                  if workspace.is_dir() else [])
    return ([f"{workspace.name}/"] + [f"{workspace.name}/{name}/" for name in subfolders]
            + [WHOLE_DISK])


def folder_display(path: Path, project_dir: Path) -> str:
    """How a folder is shown in the dropdown: relative to the project folder where possible."""
    if path == Path("/"):
        return WHOLE_DISK
    if path.is_relative_to(project_dir):
        relative = path.relative_to(project_dir).as_posix()
        return "./" if relative == "." else f"{relative}/"
    return f"{path}/"


def parse_folder(value: str) -> str | None:
    """The path typed or picked in the dropdown, or None if it's empty."""
    value = value.strip()
    if not value:
        return None
    return "/" if value == WHOLE_DISK else value


def launch_line(folder: Path) -> str:
    """The line under the folder control: exactly what the server is launched with."""
    return f"Server launched with: `--allowed-folder {folder}`"


@dataclass(frozen=True)
class FormField:
    """One input in the form the client builds from the server's schema."""
    name: str
    label: str
    kind: str  # "text" | "checkbox" | "number" | "integer" | "radio"
    required: bool
    hint: str
    default: object = None
    choices: tuple = ()
    minimum: float | None = None
    maximum: float | None = None


def field_hint(spec: dict, required: bool) -> str:
    """The rules for one field, for people: 'required · 5–200 characters · <description>'."""
    rules = ["required"] if required else []
    shortest, longest = spec.get("minLength"), spec.get("maxLength")
    if shortest is not None and longest is not None:
        rules.append(f"{shortest}–{longest} characters")
    elif shortest is not None:
        rules.append(f"at least {shortest} characters")
    elif longest is not None:
        rules.append(f"at most {longest} characters")
    if "minimum" in spec or "maximum" in spec:
        rules.append(f"{spec.get('minimum', '…')}–{spec.get('maximum', '…')}")
    if spec.get("format"):
        rules.append(f"format: {spec['format']}")
    if spec.get("description"):
        rules.append(spec["description"])
    return " · ".join(rules)


def form_fields(schema: dict) -> list[FormField]:
    """The form for a server's schema (elicitation): one field per property, in order."""
    required = set(schema.get("required", []))
    kinds = {"boolean": "checkbox", "number": "number", "integer": "integer"}
    fields = []
    for name, spec in schema.get("properties", {}).items():
        kind = "radio" if spec.get("enum") else kinds.get(spec.get("type"), "text")
        label = spec.get("title", name) + (" *" if name in required else "")
        fields.append(FormField(name, label, kind, name in required,
                                field_hint(spec, name in required), spec.get("default"),
                                tuple(spec.get("enum", ())), spec.get("minimum"),
                                spec.get("maximum")))
    return fields


def form_content(fields: list[FormField], values) -> dict:
    """The form's values as the content to send. Empty values are left out (so a missing
    required field is reported as missing); integers become whole numbers."""
    content = {}
    for form_field, value in zip(fields, values):
        if value is None or value == "":
            continue
        content[form_field.name] = int(value) if form_field.kind == "integer" else value
    return content


def input_header(question: InputRequest) -> str:
    """The input card's heading: which server is asking, and its message."""
    return (f'**Server "{question.server_name}" asks (`elicitation/create`)**\n\n'
            f"{question.message}")


def answer_line(answer: str, content: dict | None) -> str:
    """How the user's answer to a server's question appears in the timeline."""
    if answer == "accept":
        return f"✓ You answered: accept {json.dumps(content)}"
    if answer in ("decline", "cancel"):
        return f"✗ You answered: {answer}"
    return f"✗ {answer[0].upper()}{answer[1:]}"


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

    A `filepath` property starts with path_prefix, so it points inside the allowed folder."""
    properties = (input_schema or {}).get("properties", {})
    template = {name: path_prefix if name == "filepath" else JSON_DEFAULTS.get(spec.get("type"), "")
                for name, spec in properties.items()}
    return json.dumps(template, indent=2) if template else "{}"


def folder_prefix(folder: Path, workspace: Path) -> str:
    """The allowed folder as a path prefix relative to the workspace ("FOLDER/"), or "" when the
    folder is the workspace itself or lies outside it."""
    if folder != workspace and folder.is_relative_to(workspace):
        return f"{folder.relative_to(workspace).as_posix()}/"
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


def server_text(text: str) -> str:
    """Text a server supplied, as a Markdown code span: never a clickable link (a bare URL or
    [text](url) would otherwise be one). Backticks become quotes so the span can't be closed."""
    return f"`{text.replace('`', "'")}`"


def sealed_state_text(state: str) -> str:
    """The server's sealed requestState, shortened: the client can't read it, only carry it."""
    return f"`{state[:12]}…`" if state else "no state"


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
             (DONE, "Sent to server (tools/call: name + arguments)")]
    for question, answer, content in outcome.inputs:
        steps.append((None, "↩ Server returned `input_required` and asked: "
                            f"{server_text(question)}"))
        steps.append((None, answer_line(answer, content)))
    if outcome.input_request is not None:
        steps.append((None, "↩ Server returned `input_required` and asked: "
                            f"{server_text(outcome.input_request.message)}"))
        steps.append((None, "⏸ Waiting for your answer (nothing on the server is waiting)"))
        steps.append((TODO, "Result"))
        return render_timeline(header, steps)
    if outcome.rounds:
        steps.append((DONE, "Re-sent `tools/call` with your answers and the server's sealed "
                            f"state ({sealed_state_text(outcome.rounds[-1])}, opaque to this "
                            "client)"))
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


def not_waiting_timeline(input_id: str) -> str:
    """Timeline when an answer names a question the server is no longer waiting for."""
    return render_timeline(f"Question {input_id}", [(FAIL, "This question is no longer waiting")])


def approval_card(request: ToolRequest, note: str = "") -> str:
    """Markdown for the approval card: the exact request the user is approving, and why it is
    asking when that goes beyond the policy (note)."""
    return (f"**Approval needed: request `{request.request_id}`**\n\n"
            f"Tool: `{request.tool_name}`\n\n"
            f"````json\n{json.dumps(request.arguments, indent=2)}\n````"
            + (f"\n\n{note}" if note else ""))
