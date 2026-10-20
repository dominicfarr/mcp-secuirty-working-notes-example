"""Part 2 host: the Part 1 app plus a Chat tab in which a model calls the server's tools.

Every call the model makes goes through the same client as the Tools tab: policy, approval,
audit, and the server's questions to the user. This is the baseline for Part 2's attacks: no
LLM-specific defences yet (see README "Part 2: model in the loop")."""

import sys

import gradio as gr
from mcp.shared.exceptions import MCPError

from agent import BUSY, RUNNING, Agent, Step, TurnResult
from app import LAUNCH_SETTINGS, MCPPermissionClientApp, flow_outputs
from client import InputInvalid
from model import ChatModel, OpenAICompatibleModel
from tools_view import parse_arguments, server_text

# Beside the Part 1 app (7863), not instead of it
HOST_LAUNCH_SETTINGS = {**LAUNCH_SETTINGS, "server_port": 7864}
STEP_RESULT_CHARS = 120  # a step line shows the start of a result; the model gets all of it
NOT_PENDING = ("⚠ That request is not pending any more (answered elsewhere?). "
               "Start a new chat to go on.")


def step_line(step: Step) -> str:
    """A tool step for the chat. Arguments and results are model and server text: code spans,
    never links."""
    result = step.result.splitlines()[0][:STEP_RESULT_CHARS] if step.result else "(empty)"
    return (f"🔧 model called `{step.tool}` {server_text(step.arguments_json)} → "
            f"{server_text(result)}")


class MCPPermissionHostApp(MCPPermissionClientApp):
    """The Part 1 app, plus a chat with a model that uses the same client."""
    APP_TITLE = "MCP Host (Part 2: model in the loop)"

    def __init__(self, server_script: str, model: ChatModel | None = None):
        super().__init__(server_script)
        self.model = model or OpenAICompatibleModel()
        self.agent = Agent(self, self.model)
        self.chat: list[dict] = []
        self._prompts: dict = {}

    def _tabs(self) -> list:
        return [("Chat", self._build_chat_tab), *super()._tabs()]

    def _chat_outputs(self, result: TurnResult | None = None, status: str = ""):
        """The 11 chat-flow outputs: conversation, status, then the shown request's id, the
        approval card and the question card exactly as the Tools tab shows them."""
        self.chat.extend({"role": "assistant", "content": step_line(step)}
                         for step in self.agent.drain_steps())
        if result is not None and result.reply:
            self.chat.append({"role": "assistant", "content": result.reply})
        if result is not None and result.error:
            status = f"⚠ {result.error}"
        paused = self.agent.paused
        question = paused.input_request if paused is not None else None
        pending = paused.request if paused is not None and question is None else None
        return (list(self.chat), status) + flow_outputs("", pending, question)[1:]

    async def gui_chat_send(self, text: str):
        """Send the user's message and run the turn until it finishes or needs the user."""
        if not text.strip():
            return self._chat_outputs(status="Type a message first.")
        if self.agent.paused is None:
            self.chat.append({"role": "user", "content": text})
        return self._chat_outputs(await self.agent.send(text))

    async def gui_chat_approve(self, request_id: str):
        """Approve the call the user was shown (by id); nothing else."""
        try:
            return self._chat_outputs(await self.agent.approve(request_id))
        except (KeyError, RuntimeError):
            return self._chat_outputs(status=NOT_PENDING)

    async def gui_chat_reject(self, request_id: str):
        """Reject the call the user was shown (by id)."""
        try:
            return self._chat_outputs(await self.agent.reject(request_id))
        except (KeyError, RuntimeError):
            return self._chat_outputs(status=NOT_PENDING)

    async def gui_chat_answer(self, input_id: str, action: str, content: dict | None = None):
        """Answer the server's question the user was shown (never shown to the model)."""
        try:
            return self._chat_outputs(await self.agent.answer(input_id, action, content))
        except InputInvalid as e:
            return (gr.skip(),) * 10 + ("⚠ " + "  \n⚠ ".join(e.errors),)
        except RuntimeError:
            return self._chat_outputs(status="⚠ No question is waiting.")

    async def gui_chat_attach(self, uri: str) -> str:
        """Read a resource the user chose; it goes with their next message."""
        uri = uri.strip()
        try:
            attached = await self.agent.attach_resource(uri)
        except (MCPError, OSError, ValueError) as e:
            return f"⚠ Not attached: {server_text(str(e))}"
        return (f"Attached {server_text(uri)} ({len(attached)} characters); it goes with your "
                "next message.")

    async def gui_load_prompts(self):
        """The server's prompts, for the prompt picker."""
        try:
            prompts = await self.list_prompts()
        except (MCPError, OSError):
            return gr.update(choices=[])
        self._prompts = {prompt.name: prompt for prompt in prompts}
        return gr.update(choices=list(self._prompts))

    def gui_prompt_template(self, name: str) -> str:
        """An arguments template from the prompt's declared arguments."""
        prompt = self._prompts.get(name)
        names = [argument.name for argument in (prompt.arguments or [])] if prompt else []
        return "{" + ", ".join(f'"{n}": ""' for n in names) + "}"

    async def gui_chat_prompt(self, name: str, arguments_json: str):
        """Use a server prompt: its messages join the conversation and the model replies."""
        if not name:
            return self._chat_outputs(status="⚠ Pick a prompt first.")
        try:
            arguments = parse_arguments(arguments_json)
        except ValueError as e:
            return self._chat_outputs(status=f"⚠ Prompt arguments: {e}")
        try:
            result = await self.agent.use_prompt(name, arguments)
        except (MCPError, OSError, ValueError) as e:
            return self._chat_outputs(status=f"⚠ {server_text(str(e))}")
        if result.error not in (BUSY, RUNNING):  # the prompt's messages joined the context
            self.chat.append({"role": "user",
                              "content": f"(server prompt `{name}` {arguments_json})"})
        return self._chat_outputs(result)

    async def gui_chat_new(self):
        """Clear the conversation; anything pending is rejected or cancelled (audited)."""
        try:
            await self.agent.new_chat()
        except RuntimeError as e:
            return self._chat_outputs(status=f"⚠ {e}; try again when it replies.")
        self.chat = []
        return self._chat_outputs(status="New chat.")

    def _build_chat_tab(self, interface: gr.Blocks):
        """The Chat tab: a model calls the server's tools through this client."""
        gr.Markdown("### Chat with a model that calls the server's tools through this client")
        gr.Markdown(f"Model: `{self.model.name}`. Every tool call it makes meets the policy on "
                    "the Permissions tab, waits here for your approval when the policy is "
                    "*ask*, and is in the Audit tab with source `model`. Questions from the "
                    "server come to you, never to the model. **Baseline:** no LLM-specific "
                    "defences yet.")
        chatbot = gr.Chatbot(label="Conversation", height=420)
        status = gr.Markdown()
        message = gr.Textbox(label="Message", lines=2)
        send_btn = gr.Button("Send", variant="primary")
        approval_group, approval_text, approve_btn, reject_btn = self._build_approval_card()
        pending_id = gr.State("")  # the request on the approval card: approval binds to it
        flow = []
        input_card = self._build_input_card(flow, answer=self.gui_chat_answer)
        flow.extend([chatbot, status, pending_id, approval_group, approval_text, send_btn,
                     *input_card])
        self._build_context_controls(interface, flow)
        new_btn = gr.Button("New chat", variant="secondary", size="sm")

        send_btn.click(self.gui_chat_send, inputs=message, outputs=flow).then(
            lambda: "", outputs=message)
        approve_btn.click(self.gui_chat_approve, inputs=pending_id, outputs=flow)
        reject_btn.click(self.gui_chat_reject, inputs=pending_id, outputs=flow)
        new_btn.click(self.gui_chat_new, outputs=flow)

    def _build_context_controls(self, interface: gr.Blocks, flow: list):
        """Resources and prompts: the user picks them; the model can't fetch them."""
        with gr.Accordion("Add context (you choose it; the model can't fetch it)", open=False):
            resource_uri = gr.Textbox(label="Resource URI", value="workspace://")
            attach_btn = gr.Button("Attach to my next message")
            attach_status = gr.Markdown()
            prompt_name = gr.Dropdown(label="Server prompt", choices=[])
            prompt_args = gr.Textbox(label="Prompt arguments (JSON)", lines=3)
            prompt_btn = gr.Button("Use prompt")
        attach_btn.click(self.gui_chat_attach, inputs=resource_uri, outputs=attach_status)
        interface.load(self.gui_load_prompts, outputs=prompt_name)
        prompt_name.change(self.gui_prompt_template, inputs=prompt_name, outputs=prompt_args)
        prompt_btn.click(self.gui_chat_prompt, inputs=[prompt_name, prompt_args], outputs=flow)


def main():
    """Launch the host against the MCP server script given on the command line."""
    if len(sys.argv) < 2:
        print("Usage: python client/host.py <server_script>")
        print("Example: python client/host.py server/server.py")
        print("Model: HOST_MODEL_BASE_URL, HOST_MODEL, HOST_MODEL_API_KEY "
              "(default: local Ollama, qwen2.5:7b)")
        sys.exit(1)
    host = MCPPermissionHostApp(sys.argv[1])
    host.create_interface().queue().launch(**HOST_LAUNCH_SETTINGS)


if __name__ == "__main__":
    main()
