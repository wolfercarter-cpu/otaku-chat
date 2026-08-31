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

from . import config, context, db, memory, patterns, reasoning
from .ollama_client import OllamaError, get_capabilities, is_reachable, list_models
from .pickers import ModelPicker, PatternPicker, RenameSession, SessionBrowser
from .widgets import ChatInput, PromptEditor

COMMANDS = [
    ("/help", "Show this list of commands."),
    ("/model [name]", "List installed Ollama models to pick from, or switch directly by name."),
    ("/memory", "Open the curated memory file (self-learned facts) in your editor."),
    ("/config", "Open config.ini (model, api url, boost mode) in your editor."),
    ("/new", "Start a fresh session, clearing the visible chat."),
    ("/sessions", "Browse and resume a past session."),
    ("/rename [name]", "Rename the current session."),
    ("/export <file>", "Export the current session's transcript to a markdown file."),
    ("/add <file>", "Attach a file's contents to the conversation context."),
    ("/think", "Cycle the reasoning boost mode: auto -> always -> off."),
    ("/pattern [name]", "Apply a curated prompt pattern (Fabric-style) to your next message, or clear with /pattern off."),
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
        self.active_pattern: str | None = None

        # Cache the assembled system prompt string and only rebuild it when
        # memory/soul actually change (memory.py rewrites are infrequent —
        # every N turns) rather than on every single call. A byte-stable
        # system prefix is what lets Ollama reuse its KV cache turn to turn
        # instead of reprocessing the whole prompt every message.
        self._system_prompt_cache: str | None = None
        self._memory_facts_fingerprint: tuple = ()

        self.conversation_history = f"*{BANNER}*"
        self.active_stream = ""
        self.active_thinking = ""

    # -- lifecycle ---------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Label("OtakuChat", id="main-l1")
        yield Markdown(self.conversation_history, id="chat-output")
        yield ChatInput(id="main-i1")

    def on_mount(self) -> None:
        self.refresh_header()
        if not is_reachable(self.api_url):
            self.append_to_ui(
                f"\n\n*System: cannot reach Ollama at {self.api_url}. "
                "Is `ollama serve` / the ollama service running?*"
            )

    def refresh_header(self) -> None:
        """Show the active model + its capability badges (oterm-style)."""
        caps = get_capabilities(self.api_url, self.model)
        badges = []
        if caps.get("thinking"):
            badges.append("🧠")
        if caps.get("tools"):
            badges.append("🛠️")
        if caps.get("vision"):
            badges.append("👁️")
        badge_str = f"  {' '.join(badges)}" if badges else ""
        self.query_one("#main-l1", Label).update(f"OtakuChat — {self.model}{badge_str}")

    # -- system prompt assembly --------------------------------------

    def build_system_prompt(self, query: str = "", force: bool = False) -> str:
        """Assemble the system prompt, but reuse the cached string unless
        the underlying facts actually changed — keeps the prefix
        byte-stable across turns so Ollama can reuse its KV cache instead
        of reprocessing the whole system+memory block every message.

        Below memory.RELEVANCE_THRESHOLD facts this caching is exact (the
        full fact list is always included, so the fingerprint alone is
        sufficient). Above the threshold, render_memory_block ranks facts
        by relevance to `query` and the cache is intentionally bypassed —
        see memory.render_memory_block's docstring for the tradeoff.
        """
        facts_fingerprint = tuple(db.list_facts())
        over_threshold = len(facts_fingerprint) > memory.RELEVANCE_THRESHOLD
        if (
            not force
            and not over_threshold
            and self._system_prompt_cache is not None
            and facts_fingerprint == self._memory_facts_fingerprint
        ):
            return self._system_prompt_cache

        try:
            with open(self.soul_file, "r") as f:
                soul_text = f.read()
        except FileNotFoundError:
            soul_text = "You are a helpful, direct local AI chat assistant."

        memory_block = memory.render_memory_block(query=query)
        parts = [soul_text]
        if memory_block:
            parts.append(memory_block)
        prompt = "\n\n".join(parts)

        if not over_threshold:
            self._system_prompt_cache = prompt
            self._memory_facts_fingerprint = facts_fingerprint
        return prompt

    def build_messages(self) -> list[dict]:
        recent = context.get_effective_messages(self.session_id)
        last_user = next(
            (m["content"] for m in reversed(recent) if m["role"] == "user"), ""
        )
        messages = [{"role": "system", "content": self.build_system_prompt(query=last_user)}]
        messages.extend(recent)
        if self.active_pattern:
            pattern_text = patterns.get_pattern(self.active_pattern)
            if pattern_text:
                # Injected as a separate trailing system message (not folded
                # into the cached base prompt) so the byte-stable KV-cache
                # prefix is untouched and the pattern only affects this turn.
                messages.append({"role": "system", "content": pattern_text})
            # Single-use: consumed exactly here, at the point the turn is
            # actually assembled, so there's no race with the UI thread.
            self.active_pattern = None
        return messages

    # -- UI helpers ----------------------------------------------------

    def append_to_ui(self, text: str) -> None:
        self.conversation_history += text
        self.query_one("#chat-output", Markdown).update(self.conversation_history)
        self.query_one("#chat-output", Markdown).scroll_end(animate=False)

    def stream_to_ui(self, chunk: str) -> None:
        self.active_stream += chunk
        self.query_one("#chat-output", Markdown).update(
            self.conversation_history + self.active_thinking + self.active_stream
        )

    def thinking_to_ui(self, chunk: str) -> None:
        self.active_thinking += chunk
        self.query_one("#chat-output", Markdown).update(
            self.conversation_history + self.active_thinking + self.active_stream
        )

    def status_to_ui(self, note: str) -> None:
        self.query_one("#chat-output", Markdown).update(
            self.conversation_history + self.active_thinking + self.active_stream + f"\n\n*{note}*"
        )

    # -- input handling --------------------------------------------------

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        user_input = event.value
        event.input.clear()
        if not user_input.strip():
            return

        db.add_input_history(user_input)
        if isinstance(event.input, ChatInput):
            event.input.reload_history()

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
            self._system_prompt_cache = None
            self.conversation_history = f"*{BANNER}*\n\n*System: started a new session (#{self.session_id}).*"
            self.query_one("#chat-output", Markdown).update(self.conversation_history)
            return

        if lower == "/sessions":
            self.push_screen(SessionBrowser(), self.handle_session_pick)
            return

        if lower.startswith("/rename"):
            arg = cmd[len("/rename"):].strip()
            if arg:
                db.rename_session(self.session_id, arg)
                self.append_to_ui(f"\n\n*System: session renamed to '{arg}'.*")
            else:
                row = db.get_session(self.session_id)
                current_name = row["title"] if row else ""
                self.push_screen(RenameSession(current_name), self.handle_rename)
            return

        if lower.startswith("/export"):
            arg = cmd[len("/export"):].strip()
            self.handle_export(arg)
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

        if lower.startswith("/pattern"):
            arg = cmd[len("/pattern"):].strip()
            if not arg:
                self.push_screen(PatternPicker(), self.handle_pattern_pick)
            elif arg.lower() == "off":
                self.active_pattern = None
                self.append_to_ui("\n\n*System: pattern cleared.*")
            elif patterns.get_pattern(arg) is not None:
                self.active_pattern = arg
                self.append_to_ui(
                    f"\n\n*System: pattern `{arg}` will be applied to your next message "
                    "(single-use, then cleared).*"
                )
            else:
                self.append_to_ui(f"\n\n*System: no such pattern `{arg}`. Try /pattern with no argument to browse.*")
            return

        if lower.startswith("/model"):
            arg = cmd[len("/model"):].strip()
            if arg:
                self.model = arg
                config.save_model(arg)
                self.refresh_header()
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
        self._system_prompt_cache = None
        history_rows = db.get_messages(session_id)
        rendered = [f"*{BANNER}*", f"*System: resumed session #{session_id} ({row['title']}).*"]
        for r in history_rows:
            if r["role"] == "user":
                rendered.append(f"\n\n**You:**\n{r['content']}")
            elif r["role"] == "assistant":
                rendered.append(f"\n\n**Assistant:**\n{r['content']}")
        self.conversation_history = "".join(rendered) if len(rendered) > 2 else "\n\n".join(rendered)
        self.query_one("#chat-output", Markdown).update(self.conversation_history)
        self.refresh_header()

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
        self.refresh_header()
        self.append_to_ui(f"\n\n*System: switched active model to `{model_name}`.*")

    def handle_pattern_pick(self, pattern_name: str | None) -> None:
        if not pattern_name:
            return
        self.active_pattern = pattern_name
        self.append_to_ui(
            f"\n\n*System: pattern `{pattern_name}` will be applied to your next message "
            "(single-use, then cleared).*"
        )

    def handle_rename(self, new_name: str | None) -> None:
        if not new_name:
            return
        db.rename_session(self.session_id, new_name)
        self.append_to_ui(f"\n\n*System: session renamed to '{new_name}'.*")

    def handle_export(self, filepath: str) -> None:
        from pathlib import Path

        if not filepath:
            row = db.get_session(self.session_id)
            slug = (row["title"] if row else "chat").lower()
            slug = "".join(c if c.isalnum() else "-" for c in slug).strip("-") or "chat"
            filepath = f"otakuchat-{slug}-{self.session_id}.md"

        path = Path(filepath).expanduser()
        rows = db.get_messages(self.session_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(f"### {r['role'].capitalize()}\n\n{r['content']}\n\n---\n\n")
        except OSError as e:
            self.append_to_ui(f"\n\n*System: export failed — {e}*")
            return
        self.append_to_ui(f"\n\n*System: session exported to {path}*")

    def handle_add_file(self, filepath: str) -> None:
        import base64
        from pathlib import Path
        from .redact import redact_secrets

        IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

        path = Path(filepath).expanduser()
        if not path.is_file():
            self.append_to_ui(f"\n\n*System: file not found: {filepath}*")
            return

        if path.suffix.lower() in IMAGE_EXTENSIONS:
            # Vision attachment: Ollama's multimodal message format takes
            # base64 image bytes directly on the message, no OCR/description
            # step needed — only worth sending to a model whose capability
            # badge (see refresh_header) actually shows 👁️. Adapted from
            # oterm's app/widgets/image.py (ImageDirectoryTree extension
            # filtering) — otaku-chat has no file-picker UI, so this reuses
            # the existing /add command instead of a new picker screen.
            caps = get_capabilities(self.api_url, self.model)
            if not caps.get("vision"):
                self.append_to_ui(
                    f"\n\n*System: {self.model} has no vision capability (👁️) — "
                    "image attached anyway, but it may be ignored.*"
                )
            try:
                raw = path.read_bytes()
            except Exception as e:
                self.append_to_ui(f"\n\n*System: failed to read {filepath} — {e}*")
                return
            b64 = base64.b64encode(raw).decode("ascii")
            db.add_message(
                self.session_id, "user", f"[Attached image: {path.name}]",
                images=[b64],
            )
            self.append_to_ui(f"\n\n*System: attached image {path.name} to conversation context.*")
            return

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            self.append_to_ui(f"\n\n*System: failed to read {filepath} — {e}*")
            return
        content, redacted = redact_secrets(content[:8000])
        note = f"[Attached file: {path.name}]\n```\n{content}\n```"
        db.add_message(self.session_id, "user", note)
        warn = f" ({redacted} likely secret(s) redacted)" if redacted else ""
        self.append_to_ui(f"\n\n*System: attached {path.name} to conversation context{warn}.*")

    # -- chat turn -------------------------------------------------------

    def process_chat_message(self, user_input: str) -> None:
        reasoning.apply_implicit_feedback(self.last_perf_id, user_input)

        db.add_message(self.session_id, "user", user_input)
        self.append_to_ui(f"\n\n**You:**\n{user_input}")
        self.run_turn_bg()

    @work(thread=True)
    def run_turn_bg(self) -> None:
        self.active_stream = ""
        self.active_thinking = ""
        self.call_from_thread(self.status_to_ui, "thinking...")

        messages = self.build_messages()

        def on_status(note: str) -> None:
            self.call_from_thread(self.status_to_ui, note.strip("()"))

        def on_token(chunk: str) -> None:
            self.call_from_thread(self.stream_to_ui, chunk)

        def on_thinking(chunk: str) -> None:
            self.call_from_thread(self.thinking_to_ui, chunk)

        try:
            self.call_from_thread(self.append_to_ui, "\n\n**Assistant:**\n")
            result = reasoning.run_turn(
                self.api_url, self.model, messages, on_status, on_token, on_thinking
            )
        except OllamaError as e:
            self.call_from_thread(self.append_to_ui, f"\n\n*Error talking to Ollama: {e}*")
            self.active_stream = ""
            self.active_thinking = ""
            return
        except Exception as e:  # noqa: BLE001 - surface any failure, keep app alive
            self.call_from_thread(self.append_to_ui, f"\n\n*Unexpected error: {e}*")
            self.active_stream = ""
            self.active_thinking = ""
            return

        # If the model natively thought out loud, fold a collapsed-looking
        # note into the permanent transcript instead of the raw stream (the
        # raw thinking can be long; keep the visible log focused on answers).
        if result.thinking:
            think_preview = result.thinking.strip()
            if len(think_preview) > 400:
                think_preview = think_preview[:400] + "…"
            self.conversation_history += f"\n\n*[thought: {think_preview}]*\n"

        # Commit the final text (in case boosted path streamed a re-typed
        # draft rather than a live model stream)
        self.conversation_history += result.content
        self.active_stream = ""
        self.active_thinking = ""
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
            self._system_prompt_cache = None  # facts changed, must rebuild
            summary = "; ".join(added[:3])
            self.call_from_thread(
                self.append_to_ui, f"\n\n*[memory] learned: {summary}*"
            )

        compacted = context.maybe_compact(self.api_url, self.model, self.session_id)
        if compacted:
            self.call_from_thread(
                self.append_to_ui,
                "\n\n*[context] older turns compacted to stay within the model's window.*",
            )


def main() -> None:
    app = OtakuChat()
    app.run()


if __name__ == "__main__":
    main()
