"""OtakuChat — a Hermes-inspired, chat-only TUI harness for local Ollama models.

No shell/tool execution is ever exposed to the model. The only "actions" it
can take are: answer, and (indirectly, via app-side hidden calls) contribute
candidate facts to curated memory. Everything else — sessions, boosting,
self-tuning — is orchestrated by the app around it.
"""
import shlex
import subprocess

from textual import work
from textual.app import App, ComposeResult
from textual.widgets import Input, Label, Markdown

from . import config, db, memory, reasoning
from .ollama_client import OllamaError, is_reachable, list_models
from .pickers import ModelPicker, SessionBrowser
from .widgets import PromptEditor

COMMANDS = [
    ("/help", "Show this list of commands."),
    ("/model [name]", "List installed Ollama models to pick from, or switch directly by name."),
    ("/memory", "Open the curated memory file (self-learned facts) in your editor."),
    ("/config", "Open config.ini (model, api url, boost mode) in your editor."),
    ("/new", "Start a fresh session, clearing the visible chat."),
    ("/sessions", "Browse and resume a past session."),
    ("/add <file>", "Attach a file's contents to the conversation context."),
    ("/think", "Cycle the reasoning boost mode: auto -> always -> off."),
    ("/prompt", "Open a full-screen editor for composing a long/multi-line prompt."),
    ("/quit", "Exit OtakuChat (alias: /exit)."),
]

BANNER = (
    "OtakuChat — local, chat-only, self-curating.\n"
    "Type a message, or try /help for the full command list."
)


class OtakuChat(App):
    CSS_PATH = "master.tcss"
    TITLE = "OtakuChat"

    def __init__(self):
        super().__init__()
        db.init_db()

        self.model = config.get_model()
        self.api_url = config.get_api_url()
        self.soul_file = config.get_soul_path()

        self.session_id: int = db.create_session("New chat", self.model)
        self.turns_since_curation = 0
        self.last_perf_id: int | None = None

        self.conversation_history = f"*{BANNER}*"
        self.active_stream = ""

    # -- lifecycle ---------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Label("OtakuChat", id="main-l1")
        yield Markdown(self.conversation_history, id="chat-output")
        yield Input(
            placeholder=">>> message, or /help for commands",
            id="main-i1",
        )

    def on_mount(self) -> None:
        if not is_reachable(self.api_url):
            self.append_to_ui(
                f"\n\n*System: cannot reach Ollama at {self.api_url}. "
                "Is `ollama serve` / the ollama service running?*"
            )

    # -- system prompt assembly --------------------------------------

    def build_system_prompt(self) -> str:
        try:
            with open(self.soul_file, "r") as f:
                soul_text = f.read()
        except FileNotFoundError:
            soul_text = "You are a helpful, direct local AI chat assistant."

        memory_block = memory.render_memory_block()
        parts = [soul_text]
        if memory_block:
            parts.append(memory_block)
        return "\n\n".join(parts)

    def build_messages(self) -> list[dict]:
        rows = db.get_messages(self.session_id)
        messages = [{"role": "system", "content": self.build_system_prompt()}]
        for r in rows:
            messages.append({"role": r["role"], "content": r["content"]})
        return messages

    # -- UI helpers ----------------------------------------------------

    def append_to_ui(self, text: str) -> None:
        self.conversation_history += text
        self.query_one("#chat-output", Markdown).update(self.conversation_history)
        self.query_one("#chat-output", Markdown).scroll_end(animate=False)

    def stream_to_ui(self, chunk: str) -> None:
        self.active_stream += chunk
        self.query_one("#chat-output", Markdown).update(
            self.conversation_history + self.active_stream
        )

    def status_to_ui(self, note: str) -> None:
        self.query_one("#chat-output", Markdown).update(
            self.conversation_history + self.active_stream + f"\n\n*{note}*"
        )

    # -- input handling --------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_input = event.value
        event.input.value = ""
        if not user_input.strip():
            return

        cmd = user_input.strip()
        lower = cmd.lower()

        if lower in ("/quit", "/exit"):
            self.exit()
            return

        if lower == "/help":
            lines = ["**Commands:**"]
            for name, desc in COMMANDS:
                lines.append(f"- `{name}` — {desc}")
            self.append_to_ui("\n\n" + "\n".join(lines))
            return

        if lower == "/prompt":
            self.push_screen(PromptEditor(), self.handle_long_prompt)
            return

        if lower == "/new":
            self.session_id = db.create_session("New chat", self.model)
            self.turns_since_curation = 0
            self.last_perf_id = None
            self.conversation_history = f"*{BANNER}*\n\n*System: started a new session (#{self.session_id}).*"
            self.query_one("#chat-output", Markdown).update(self.conversation_history)
            return

        if lower == "/sessions":
            self.push_screen(SessionBrowser(), self.handle_session_pick)
            return

        if lower == "/memory":
            with self.suspend():
                subprocess.run(shlex.split(config.get_editor()) + [config.get_memory_path()])
            self.append_to_ui("\n\n*System: closed memory editor.*")
            return

        if lower == "/config":
            with self.suspend():
                subprocess.run(shlex.split(config.get_editor()) + [str(config.CONFIG_FILE)])
            self.model = config.get_model()
            self.api_url = config.get_api_url()
            self.append_to_ui(
                "\n\n*System: closed config editor. Model/API/boost settings reloaded.*"
            )
            return

        if lower == "/think":
            modes = ["auto", "always", "off"]
            current = config.get_boost_mode()
            nxt = modes[(modes.index(current) + 1) % len(modes)] if current in modes else "auto"
            config.save_boost_mode(nxt)
            self.append_to_ui(f"\n\n*System: reasoning boost mode set to '{nxt}'.*")
            return

        if lower.startswith("/model"):
            arg = cmd[len("/model"):].strip()
            if arg:
                self.model = arg
                config.save_model(arg)
                self.append_to_ui(f"\n\n*System: switched active model to `{arg}`.*")
            else:
                self.open_model_picker()
            return

        if lower.startswith("/add "):
            self.handle_add_file(cmd[5:].strip())
            return

        self.process_chat_message(user_input)

    def handle_long_prompt(self, long_text: str | None) -> None:
        if long_text:
            self.process_chat_message(long_text)

    def handle_session_pick(self, session_id: int | None) -> None:
        if session_id is None:
            return
        row = db.get_session(session_id)
        if not row:
            return
        self.session_id = session_id
        self.model = row["model"]
        self.turns_since_curation = 0
        self.last_perf_id = None
        history_rows = db.get_messages(session_id)
        rendered = [f"*{BANNER}*", f"*System: resumed session #{session_id} ({row['title']}).*"]
        for r in history_rows:
            if r["role"] == "user":
                rendered.append(f"\n\n**You:**\n{r['content']}")
            elif r["role"] == "assistant":
                rendered.append(f"\n\n**Assistant:**\n{r['content']}")
        self.conversation_history = "".join(rendered) if len(rendered) > 2 else "\n\n".join(rendered)
        self.query_one("#chat-output", Markdown).update(self.conversation_history)

    @work(thread=True)
    def open_model_picker(self) -> None:
        try:
            models = [m["name"] for m in list_models(self.api_url)]
        except OllamaError as e:
            self.call_from_thread(self.append_to_ui, f"\n\n*System: {e}*")
            return
        self.call_from_thread(self._push_model_picker, models)

    def _push_model_picker(self, models: list[str]) -> None:
        self.push_screen(ModelPicker(models, self.model), self.handle_model_pick)

    def handle_model_pick(self, model_name: str | None) -> None:
        if not model_name:
            return
        self.model = model_name
        config.save_model(model_name)
        self.append_to_ui(f"\n\n*System: switched active model to `{model_name}`.*")

    def handle_add_file(self, filepath: str) -> None:
        from pathlib import Path

        path = Path(filepath).expanduser()
        if not path.is_file():
            self.append_to_ui(f"\n\n*System: file not found: {filepath}*")
            return
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            self.append_to_ui(f"\n\n*System: failed to read {filepath} — {e}*")
            return
        note = f"[Attached file: {path.name}]\n```\n{content[:8000]}\n```"
        db.add_message(self.session_id, "user", note)
        self.append_to_ui(f"\n\n*System: attached {path.name} to conversation context.*")

    # -- chat turn -------------------------------------------------------

    def process_chat_message(self, user_input: str) -> None:
        reasoning.apply_implicit_feedback(self.last_perf_id, user_input)

        db.add_message(self.session_id, "user", user_input)
        self.append_to_ui(f"\n\n**You:**\n{user_input}")
        self.run_turn_bg()

    @work(thread=True)
    def run_turn_bg(self) -> None:
        self.active_stream = ""
        self.call_from_thread(self.status_to_ui, "thinking...")

        messages = self.build_messages()

        def on_status(note: str) -> None:
            self.call_from_thread(self.status_to_ui, note.strip("()"))

        def on_token(chunk: str) -> None:
            self.call_from_thread(self.stream_to_ui, chunk)

        try:
            self.call_from_thread(self.append_to_ui, "\n\n**Assistant:**\n")
            result = reasoning.run_turn(self.api_url, self.model, messages, on_status, on_token)
        except OllamaError as e:
            self.call_from_thread(self.append_to_ui, f"\n\n*Error talking to Ollama: {e}*")
            self.active_stream = ""
            return
        except Exception as e:  # noqa: BLE001 - surface any failure, keep app alive
            self.call_from_thread(self.append_to_ui, f"\n\n*Unexpected error: {e}*")
            self.active_stream = ""
            return

        # Commit the final text (in case boosted path streamed a re-typed
        # draft rather than a live model stream)
        self.conversation_history += result.content
        self.active_stream = ""
        self.call_from_thread(
            self.query_one("#chat-output", Markdown).update, self.conversation_history
        )

        db.add_message(self.session_id, "assistant", result.content, boosted=result.boosted)
        self.last_perf_id = result.perf_id
        self.turns_since_curation += 1

        note = reasoning.self_tune_threshold(self.model)
        if note:
            self.call_from_thread(self.append_to_ui, f"\n\n*[self-tune] {note}*")

        added = memory.maybe_curate(
            self.api_url, self.model, self.session_id, self.turns_since_curation
        )
        if added:
            self.turns_since_curation = 0
            summary = "; ".join(added[:3])
            self.call_from_thread(
                self.append_to_ui, f"\n\n*[memory] learned: {summary}*"
            )


def main() -> None:
    app = OtakuChat()
    app.run()


if __name__ == "__main__":
    main()
