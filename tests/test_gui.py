"""The GUI shows the security model accurately."""

import json
import re

import gradio as gr
import pytest

import app as app_module
from client import DEFAULT_ROOT, INPUT_AUTO_DECLINED, ToolOutcome, ToolRequest
from helpers import WORKSPACE, select_row
from tools_view import WHOLE_DISK, form_content, form_fields, timeline_for


def config(app):
    return app.create_interface().get_config_file()


def markdown_text(cfg):
    return " ".join(str(c["props"].get("value")) for c in cfg["components"]
                    if c["type"] == "markdown")


def test_tabs_and_page_load_events():
    app = app_module.MCPPermissionClientApp("unused.py")
    cfg = config(app)
    assert [c["props"]["label"] for c in cfg["components"] if c["type"] == "tabitem"] \
        == ["Tools", "Permissions", "Audit"]
    loads = sum(1 for d in cfg["dependencies"] if any(t[1] == "load" for t in d["targets"]))
    assert loads == 4  # tool table, root control, permissions, audit


@pytest.mark.integration
@pytest.mark.anyio
async def test_table_labels_server_and_client_columns(make_app):
    app = make_app()
    rows, _ = await app.gui_load_tools()
    cfg = config(app)
    headers = [c["props"]["value"]["headers"] for c in cfg["components"]
               if c["type"] == "dataframe" and "Tools" in str(c["props"].get("label"))][0]
    assert headers == ["Tool (server)", "Description (server)", "Policy (client)"]
    assert "reported by the MCP server" in markdown_text(cfg)
    assert dict((r[0], r[2]) for r in rows)["write_file"] == "ask · default"


@pytest.mark.integration
@pytest.mark.anyio
async def test_server_descriptions_carry_no_policy(make_app):
    app = make_app()
    for tool in await app.list_tools():
        assert not re.search(r"risk|elicitation|confirmation|approval|LOW|MEDIUM|HIGH|CRITICAL",
                             tool.description or ""), (tool.name, tool.description)


@pytest.mark.anyio
async def test_invalid_arguments_are_rejected_before_sending():
    app = app_module.MCPPermissionClientApp("unused.py")
    for bad in ("[1, 2]", '"x"', "{nope"):
        timeline = (await app.gui_send_request("read_file", bad))[0]
        assert "Invalid arguments" in timeline


@pytest.mark.anyio
async def test_args_template_points_inside_the_folder():
    app = app_module.MCPPermissionClientApp("unused.py")
    app.tool_schemas = {"write_file": {"properties": {"filepath": {"type": "string"},
                                                      "content": {"type": "string"}}}}
    await app.set_folder("workspace/FOLDER")
    assert json.loads(app.gui_select_tool("", select_row("write_file"))[1])["filepath"] == "FOLDER/"
    for folder in ("workspace", "/"):
        await app.set_folder(folder)
        assert json.loads(app.gui_select_tool("", select_row("write_file"))[1])["filepath"] == ""


@pytest.mark.anyio
async def test_folder_control_shows_what_the_server_is_launched_with():
    (WORKSPACE / "projects").mkdir()
    app = app_module.MCPPermissionClientApp("unused.py")
    box, launched = app.gui_load_folder()
    assert box["choices"] == ["workspace/", "workspace/projects/", WHOLE_DISK]
    assert launched == f"Server launched with: `--allowed-folder {DEFAULT_ROOT}`"
    box, launched = await app.gui_set_folder("workspace/projects/")
    assert str(DEFAULT_ROOT / "projects") in launched
    with pytest.warns(UserWarning, match="Enter a folder path"):  # the user is told
        box, launched = await app.gui_set_folder("   ")  # and an empty value keeps the folder
    assert box["value"] == "workspace/projects/"


def test_timeline_shows_each_step():
    request = ToolRequest("ab12", "delete_file", {"filepath": "a.txt", "content": "x" * 500})
    asked = timeline_for(ToolOutcome(request, "ASK", "awaiting approval"))
    assert "<500 chars>" in asked and "waiting for your approval" in asked
    denied = timeline_for(ToolOutcome(request, "DENIED", "policy: deny"))
    assert "Client policy: DENY" in denied and "Not sent" in denied
    rejected = timeline_for(ToolOutcome(request, "REJECTED", "rejected by user"))
    assert "Rejected by you" in rejected
    full = timeline_for(ToolOutcome(request, "ALLOWED", "user approved", None, True,
                                    ["v1.abcdefghijklmnop"], [("Confirm?", "decline", None),
                                                    ("Q", INPUT_AUTO_DECLINED, None)]))
    for line in ("Server returned `input_required` and asked", "✗ You answered: decline",
                 "✗ Declined automatically", "Re-sent `tools/call`", "`v1.abcdefghi…`"):
        assert line in full, line


def test_server_text_is_never_a_link():
    # Server-supplied text reaches the timeline's Markdown as a code span: rendered as Markdown,
    # a bare URL or [text](url) would become a clickable link. A backtick can't end the span.
    message = "Sign in `now` [here](https://evil.example/a) [url mode: https://evil.example/login]"
    request = ToolRequest("ab12", "delete_file", {"filepath": "a.txt"})
    shown = timeline_for(ToolOutcome(request, "ALLOWED", "policy: allow", None, False, None,
                                     [(message, INPUT_AUTO_DECLINED, None)]))
    assert ("`Sign in 'now' [here](https://evil.example/a) "
            "[url mode: https://evil.example/login]`") in shown
    assert shown.count("https://evil.example") == 2  # both inside that code span


def test_form_is_generated_from_the_schema():
    schema = {"type": "object", "required": ["name", "level"], "properties": {
        "name": {"type": "string", "title": "Name", "minLength": 2, "maxLength": 10,
                 "description": "Your name"},
        "level": {"type": "string", "enum": ["low", "high"]},
        "count": {"type": "integer", "minimum": 0, "maximum": 5},
        "on": {"type": "boolean", "default": True}}}
    fields = form_fields(schema)
    assert [(f.name, f.kind, f.required) for f in fields] == [
        ("name", "text", True), ("level", "radio", True), ("count", "integer", False),
        ("on", "checkbox", False)]
    assert fields[0].label == "Name *" and "2–10 characters" in fields[0].hint
    assert form_content(fields, ["Al", "low", 3.0, True]) == {"name": "Al", "level": "low",
                                                               "count": 3, "on": True}
    assert form_content(fields, ["", None, None, False]) == {"on": False}


def test_server_log_panel_is_marked_demo_only():
    text = markdown_text(config(app_module.MCPPermissionClientApp("unused.py")))
    assert "demo only" in text.lower() and "real client can't see" in text


def test_audit_tables_are_stacked():
    cfg = config(app_module.MCPPermissionClientApp("unused.py"))
    tables = [c for c in cfg["components"] if c["type"] == "dataframe"
              and str(c["props"].get("label", "")).endswith("newest first")]
    assert [t["props"]["max_height"] for t in tables] == [320, 320]


def test_colours_carry_meaning():
    # gradio 6 applies the theme and CSS at launch, so check what main() launches with
    assert app_module.LAUNCH_SETTINGS["theme"].primary_500 == gr.themes.colors.blue.c500
    assert ".approve-btn" in app_module.LAUNCH_SETTINGS["css"]
    ui = app_module.MCPPermissionClientApp("unused.py").create_interface()
    buttons = {c["props"].get("value"): c["props"] for c in ui.get_config_file()["components"]
               if c["type"] == "button"}
    assert "approve-btn" in buttons["Approve"]["elem_classes"]
    assert buttons["Reject"]["variant"] == "stop"
