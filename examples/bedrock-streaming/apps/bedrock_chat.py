"""Minimal wAwesomeChat-style UI using the real wapyt chat widget."""

__widgetset__ = "wapyt"
__version__ = "0.2.0.dev1"
APP_LOADING_TITLE = "Bedrock streaming lab"

import asyncio
import time

import js
from pyodide.ffi import create_proxy
from wapyt.chat import ChatAgentConfig, ChatConfig, ChatMessageConfig
from wapyt.layout import CellConfig, LayoutConfig, MainWindow

from chat_backend import BedrockChat


class bedrock_chat(MainWindow):
    layout_config = LayoutConfig(
        rows=[
            CellConfig(id="heading", height="112px", html=(
                '<div style="padding:20px 24px;font-family:system-ui;color:#dbeafe;'
                'background:#101b30;height:100%;box-sizing:border-box">'
                '<div style="font-size:22px;font-weight:700">Bedrock streaming lab</div>'
                '<div style="margin-top:5px;color:#9fb4d4">Amazon Nova Micro · '
                '512 output tokens maximum</div>'
                '<div style="margin-top:9px;display:flex;gap:18px;align-items:center">'
                '<span id="stream-status" role="status">Ready to chat</span>'
                '<button id="clear-conversation" type="button">New conversation</button>'
                '</div></div>'
            )),
            CellConfig(id="chat", grow=1),
        ], borderless=True, gap="0px",
    )

    def load_ui(self):
        self.set_theme("dark")
        js.document.body.classList.add("bedrock-demo")
        style = js.document.createElement("style")
        style.textContent = (
            ".bedrock-demo .rag-sidebar,.bedrock-demo .model-selector{display:none!important}"
            ".bedrock-demo .artifact-panel{display:none!important}"
            ".bedrock-demo #clear-conversation{background:#233c60;color:#eef5ff;"
            "border:1px solid #41628d;border-radius:6px;padding:5px 10px;cursor:pointer}"
            ".bedrock-demo #clear-conversation:disabled{opacity:.5;cursor:wait}"
        )
        js.document.head.appendChild(style)
        self.history = []
        self.busy = False
        self.backend = BedrockChat()
        self.widget = self.add_chat("chat", ChatConfig(
            agent=ChatAgentConfig(name="Nova Micro", subtitle="Live from Amazon Bedrock"),
            auto_append_user_messages=False,
            enable_artifacts=False,
            layout_mode="advanced",
            layout_show_sidebar=False,
            storage_key="pytincture-bedrock-streaming-demo",
            input_placeholder="Ask Nova Micro a question...",
            composer_help_text="Enter to send. Shift+Enter for a newline.",
            extra={"models": ["amazon.nova-micro-v1:0"]},
            default_model="amazon.nova-micro-v1:0",
        ))
        self.widget.clear_messages()
        self.widget.on_send(self.handle_send)
        self.clear_proxy = create_proxy(self.clear)
        js.document.getElementById("clear-conversation").addEventListener("click", self.clear_proxy)
        self._welcome()

    def _welcome(self):
        self.widget.add_message(ChatMessageConfig(
            role="assistant",
            content="Ask me a question and watch the answer arrive as a live stream. "
                    "Try: explain response streaming in five short steps.",
        ))

    def _status(self, value):
        js.document.getElementById("stream-status").textContent = value

    def clear(self, event=None):
        if self.busy:
            return
        self.history = []
        self.widget.clear_messages()
        self._welcome()
        self._status("Ready to chat")

    def handle_send(self, payload):
        prompt = (payload or {}).get("text", "").strip()
        if self.busy:
            self._status("Wait for this answer to finish before sending another.")
            return
        if not prompt:
            return
        if len(prompt) > 4000:
            self._status("Please keep your message under 4,000 characters.")
            return
        self.busy = True
        js.document.getElementById("clear-conversation").disabled = True
        self.widget.add_message(ChatMessageConfig(role="user", content=prompt))
        self.task = asyncio.ensure_future(self._reply(prompt))
        return True

    async def _reply(self, prompt):
        message_id = self.widget.start_stream(ChatMessageConfig(role="assistant", content=""))
        started = time.monotonic()
        first = None
        chunks = 0
        text = ""
        usage = {}
        complete = False
        failed = False
        stream = None
        stop_reason = ""
        self._status("Connecting to Bedrock...")
        try:
            # Send only a bounded recent conversation; credentials stay on Lambda.
            messages = self.history[-6:] + [{"role": "user", "content": prompt}]
            while sum(len(m["content"]) for m in messages) > 16000 and len(messages) > 1:
                messages = messages[2:]
            stream = self.backend.reply(messages)
            async for event in stream:
                kind = event.get("type")
                if kind == "delta":
                    delta = event["text"]
                    if first is None:
                        first = time.monotonic() - started
                    chunks += 1
                    text += delta
                    self.widget.append_stream(message_id, delta)
                    self._status("Streaming · first text %.2fs · %d chunks" % (first, chunks))
                elif kind == "usage":
                    usage = event
                elif kind == "error":
                    failed = True
                    self.widget.append_stream(message_id, "\n\n" + event["message"])
                    self._status("Bedrock request failed: " + event["code"])
                elif kind == "done":
                    complete = True
                    stop_reason = event.get("stop_reason", "")
            if not complete and not failed:
                failed = True
                self.widget.append_stream(message_id, "\n\nThe stream ended before completion. Please retry.")
                self._status("Incomplete stream")
            if complete and not failed:
                self.history = messages + [{"role": "assistant", "content": text}]
                label = "Output limit reached" if stop_reason == "max_tokens" else "Complete"
                self._status("%s · first text %.2fs · %.2fs total · %d chunks · %d output tokens" % (
                    label, first or 0, time.monotonic() - started, chunks, usage.get("output_tokens", 0)))
        except Exception:
            self.widget.append_stream(message_id, "\n\nUnable to complete the request. Check the demo service and retry.")
            self._status("Connection error")
        finally:
            if stream is not None and hasattr(stream, "aclose"):
                await stream.aclose()
            self.widget.finish_stream(message_id)
            self.busy = False
            js.document.getElementById("clear-conversation").disabled = False
