"""Gradio GUI for the MCP permission client: tools and permissions."""

import sys
from pathlib import Path
import gradio as gr
from mcp.shared.exceptions import McpError
from audit_view import audit_rows, CLIENT_COLUMNS, SERVER_COLUMNS
from tools_view import (first_line, policy_label, args_template, parse_arguments, timeline_for,
                        invalid_arguments_timeline, not_pending_timeline, approval_card,
                        root_choices, root_display, parse_root, declared_line, root_prefix)
from client import MCPPermissionClient, ToolRequest, PROJECT_DIR, DEFAULT_ROOT


# DEMO ONLY: the server's private audit log, read straight from disk. A real MCP client has no
# access to it; this demo can only show it because client and server share one machine.
SERVER_AUDIT_LOG = Path(__file__).resolve().parent.parent / "server" / "logs" / "server_audit.log"


# Height in pixels of each Audit tab table; longer logs scroll inside it.
AUDIT_TABLE_HEIGHT = 320

# Colour carries meaning: blue for neutral actions (the theme's primary colour), green to approve,
# red to reject (Gradio's "stop" variant).
THEME = gr.themes.Default(primary_hue="blue")
APPROVE_GREEN, APPROVE_GREEN_HOVER = gr.themes.colors.green.c600, gr.themes.colors.green.c700
APP_CSS = f"""
.approve-btn {{ background: {APPROVE_GREEN} !important; border-color: {APPROVE_GREEN} !important;
               color: white !important; }}
.approve-btn:hover {{ background: {APPROVE_GREEN_HOVER} !important; }}
"""


def flow_outputs(timeline: str, pending: ToolRequest | None = None):
    """Outputs shared by send/approve/reject: timeline, pending id, card, card text, Send."""
    return (timeline,
            pending.request_id if pending else "",
            gr.update(visible=pending is not None),
            approval_card(pending) if pending else "",
            gr.update(interactive=pending is None))


class MCPPermissionClientApp(MCPPermissionClient):
    """GUI client application with permission management interface."""

    def __init__(self, server_script: str):
        super().__init__(server_script)
        self.tool_schemas: dict[str, dict] = {}

    async def gui_load_tools(self):
        """Connect and list tools for the table; show a status line instead if that fails."""
        try:
            tools = await self.list_tools()
        except (McpError, OSError) as e:
            return [], gr.update(value=f"⚠ Could not load tools: {e}", visible=True)

        self.tool_schemas = {tool.name: tool.inputSchema for tool in tools}
        rows = [[tool.name, first_line(tool.description), policy_label(tool.name, self.permissions)]
                for tool in tools]
        return rows, gr.update(value="", visible=False)

    def gui_select_tool(self, pending_id: str, evt: gr.SelectData):
        """Select the clicked tool and fill Arguments with a template from its schema."""
        tool_name = evt.row_value[0]
        prefix = root_prefix(self.roots[0], DEFAULT_ROOT)
        return (tool_name, args_template(self.tool_schemas.get(tool_name), prefix),
                gr.update(interactive=not pending_id))

    async def gui_send_request(self, tool_name: str, arguments_json: str):
        """Send a request through the client policy and show where it got to."""
        try:
            arguments = parse_arguments(arguments_json)
        except ValueError as e:
            return flow_outputs(invalid_arguments_timeline(tool_name, str(e)))

        outcome = await self.request_tool(tool_name, arguments)
        pending = outcome.request if outcome.decision == "ASK" else None
        return flow_outputs(timeline_for(outcome), pending)

    async def gui_approve(self, pending_id: str):
        """Approve the pending request by id; the client sends its stored copy."""
        try:
            outcome = await self.approve(pending_id)
        except KeyError:
            return flow_outputs(not_pending_timeline(pending_id))
        return flow_outputs(timeline_for(outcome))

    def gui_reject(self, pending_id: str):
        """Reject the pending request by id; nothing is sent."""
        try:
            outcome = self.reject(pending_id)
        except KeyError:
            return flow_outputs(not_pending_timeline(pending_id))
        return flow_outputs(timeline_for(outcome))

    def gui_load_roots(self):
        """Root presets (workspace and its subfolders) and the root currently declared."""
        return (gr.update(choices=root_choices(DEFAULT_ROOT),
                          value=root_display(self.roots[0], PROJECT_DIR)),
                declared_line(self.root_uris()))

    def gui_set_root(self, value: str):
        """Declare the chosen folder as the client's root; an empty value keeps the current one."""
        path = parse_root(value or "")
        if path is None:
            gr.Warning("Enter a folder path")
        else:
            self.set_roots([path])
        return (gr.update(value=root_display(self.roots[0], PROJECT_DIR)),
                declared_line(self.root_uris()))

    async def gui_configure_permission(self, tool_name: str, policy: str):
        """Configure permission for a tool."""
        if not tool_name:
            return "Please enter a tool name"

        if policy not in ["allow", "deny", "ask"]:
            return "Policy must be: allow, deny, or ask"

        self.permissions[tool_name] = policy
        self.save_permissions()

        return (f"Permission updated: {tool_name} = {policy}\n"
                f"Permissions saved to {self.permissions_file}")

    def gui_get_permission(self, tool_name: str):
        """Return the current policy for a tool, so the GUI can show it."""
        return self.permissions.get(tool_name, "ask")

    async def gui_load_tool_names(self):
        """Tool names for the Permissions tab dropdown; empty, with a warning, if loading fails."""
        try:
            tools = await self.list_tools()
        except (McpError, OSError) as e:
            gr.Warning(f"Could not load tools: {e}")
            return gr.update(choices=[])
        return gr.update(choices=[tool.name for tool in tools])

    def gui_load_audit(self):
        """Rows for both audit panels: this client's log, and the server's (demo only)."""
        return (audit_rows(self.audit_log.path, with_request_id=True),
                audit_rows(SERVER_AUDIT_LOG, with_request_id=False))

    def create_interface(self):
        """Create the Gradio interface with permission management."""

        with gr.Blocks(title="MCP Permission Client", theme=THEME, css=APP_CSS) as interface:
            gr.Markdown("""
            # MCP Permission Client
            Manage permissions, view audit logs, and interact with MCP tools securely.
            """)

            with gr.Tabs():
                with gr.Tab("Tools"):
                    self._build_tools_tab(interface)
                with gr.Tab("Permissions"):
                    self._build_permissions_tab(interface)
                with gr.Tab("Audit"):
                    self._build_audit_tab(interface)

        return interface

    def _build_tools_tab(self, interface: gr.Blocks):
        """Build the Tools tab: pick a tool, send a request, watch the client policy decide."""
        gr.Markdown("### Call tools through the client's permission policy")
        tools_status = gr.Markdown(visible=False)
        gr.Markdown("Tool and Description are reported by the MCP server (`tools/list`). "
                    "Policy is this client's own setting: its built-in default, or your "
                    "override in `permissions.json`.")
        gr.Markdown("**Where things run:** This app is the MCP client. The server is a separate "
                    "process it started on this machine (stdio), so both see the same disk; "
                    "`workspace/` is the user's files. A tool call carries only the tool name and "
                    "arguments. The server asks the client for its roots during the call. A remote "
                    "(HTTP) server would run, and write, on its own machine, where your `file://` "
                    "roots would mean nothing: file roots are mainly for local servers.")
        self._build_root_control(interface)
        tool_table = gr.Dataframe(
            headers=["Tool (server)", "Description (server)", "Policy (client)"],
            interactive=False, label="Tools (click a row to select)")
        refresh_btn = gr.Button("↻ Refresh", size="sm")

        selected_tool = gr.Textbox(label="Selected tool", interactive=False)
        tool_args = gr.Textbox(label="Arguments (JSON)", lines=4)
        send_btn = gr.Button("Send request", variant="primary", interactive=False)
        pending_id = gr.State("")

        timeline = gr.Markdown()
        with gr.Group(visible=False) as approval_group:
            approval_text = gr.Markdown()
            with gr.Row():
                approve_btn = gr.Button("Approve", variant="primary", elem_classes=["approve-btn"])
                reject_btn = gr.Button("Reject", variant="stop")

        interface.load(fn=self.gui_load_tools, outputs=[tool_table, tools_status])
        refresh_btn.click(fn=self.gui_load_tools, outputs=[tool_table, tools_status])
        tool_table.select(fn=self.gui_select_tool, inputs=pending_id,
                          outputs=[selected_tool, tool_args, send_btn])

        flow = [timeline, pending_id, approval_group, approval_text, send_btn]
        send_btn.click(fn=self.gui_send_request, inputs=[selected_tool, tool_args], outputs=flow)
        approve_btn.click(fn=self.gui_approve, inputs=pending_id, outputs=flow)
        reject_btn.click(fn=self.gui_reject, inputs=pending_id, outputs=flow)

    def _build_root_control(self, interface: gr.Blocks):
        """Root picker: the folder this client declares to the server as its MCP root."""
        root_box = gr.Dropdown(
            label="Client root (sent to the server as an MCP root)",
            info="Tells the server which folder it may work in. The server enforces it, and "
                 "can't be given more than its own workspace.",
            choices=[], allow_custom_value=True)
        declared = gr.Markdown()
        interface.load(fn=self.gui_load_roots, outputs=[root_box, declared])
        root_box.input(fn=self.gui_set_root, inputs=root_box, outputs=[root_box, declared])

    def _build_permissions_tab(self, interface: gr.Blocks):
        """Build the Permissions tab: view and change this client's policy for each tool."""
        gr.Markdown("### Client permission policies")
        refresh_btn = gr.Button("↻ Refresh", size="sm")
        perm_tool_name = gr.Dropdown(label="Tool Name", choices=[], allow_custom_value=True)
        perm_policy = gr.Radio(choices=["allow", "deny", "ask"], label="Permission Policy",
                               value="ask")
        save_perm_btn = gr.Button("Save Permission", variant="primary")
        perm_result = gr.Textbox(label="Result", lines=3)

        interface.load(fn=self.gui_load_tool_names, outputs=perm_tool_name)
        refresh_btn.click(fn=self.gui_load_tool_names, outputs=perm_tool_name)
        perm_tool_name.change(fn=self.gui_get_permission, inputs=perm_tool_name,
                              outputs=perm_policy)
        save_perm_btn.click(fn=self.gui_configure_permission,
                            inputs=[perm_tool_name, perm_policy], outputs=perm_result)

    def _build_audit_tab(self, interface: gr.Blocks):
        """Build the Audit tab: the client's own log above the server's (demo only)."""
        gr.Markdown("### Audit logs\n"
                    "Each side keeps its own record. A request the client denies never reaches "
                    "the server, so it appears only in the client log.")
        refresh_btn = gr.Button("↻ Refresh", size="sm")
        # Stacked at full width so long arguments and details stay readable; each table has a
        # fixed height and scrolls.
        gr.Markdown("**Client audit** (this client's own log, `client/client_audit.log`)")
        client_table = gr.Dataframe(headers=CLIENT_COLUMNS, interactive=False, wrap=True,
                                    max_height=AUDIT_TABLE_HEIGHT,
                                    label="Client log, newest first")
        gr.Markdown("**Server audit: operator view, demo only** "
                    "(`server/logs/server_audit.log`)\n\n"
                    "⚠ A real client can't see the server's log. It is shown here only "
                    "because this demo runs both on one machine; it is read from disk, "
                    "not through MCP.")
        server_table = gr.Dataframe(headers=SERVER_COLUMNS, interactive=False, wrap=True,
                                    max_height=AUDIT_TABLE_HEIGHT,
                                    label="Server log, newest first")

        interface.load(fn=self.gui_load_audit, outputs=[client_table, server_table])
        refresh_btn.click(fn=self.gui_load_audit, outputs=[client_table, server_table])

def main():
    """Launch the GUI client against the MCP server script given on the command line."""
    if len(sys.argv) < 2:
        print("Usage: python client/app.py <server_script>")
        print("Example: python client/app.py server/server.py")
        sys.exit(1)

    server_script = sys.argv[1]

    client = MCPPermissionClientApp(server_script)
    interface = client.create_interface()
    interface.queue().launch(server_name="127.0.0.1", server_port=7863)


if __name__ == "__main__":
    main()
