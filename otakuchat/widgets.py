from textual.screen import Screen
from textual.widgets import Input, Static, TextArea
from textual import events


class HistoryInput(Input):
    """Input with aider-style persistent up/down history recall.

    History is loaded lazily from db.get_input_history() and walked with
    Up/Down like a shell. Editing a recalled line and pressing Enter still
    submits normally (App.on_input_submitted handles persistence of new
    entries); this widget only manages in-memory recall state.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_index: int = 0
        self._draft: str = ""
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        from . import db

        self._history = db.get_input_history()
        self._history_index = len(self._history)
        self._loaded = True

    def reload_history(self) -> None:
        """Call after a new entry is persisted so recall includes it."""
        self._loaded = False
        self._ensure_loaded()

    async def _on_key(self, event: events.Key) -> None:
        self._ensure_loaded()

        if event.key == "up":
            if not self._history:
                return
            if self._history_index == len(self._history):
                self._draft = self.value
            if self._history_index > 0:
                self._history_index -= 1
                self.value = self._history[self._history_index]
                self.cursor_position = len(self.value)
            event.prevent_default()
            event.stop()
            return

        if event.key == "down":
            if not self._history:
                return
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
                self.value = self._history[self._history_index]
            elif self._history_index == len(self._history) - 1:
                self._history_index += 1
                self.value = self._draft
            else:
                return
            self.cursor_position = len(self.value)
            event.prevent_default()
            event.stop()
            return


class PromptTextArea(TextArea):
    """TextArea with light auto-closing bracket/quote support."""

    def _on_key(self, event: events.Key) -> None:
        pairs = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
        if event.character in pairs:
            event.prevent_default()
            closing = pairs[event.character]
            self.insert(f"{event.character}{closing}")
            self.move_cursor_relative(columns=-1)


class PromptEditor(Screen):
    """Full-screen modal for composing a long prompt."""

    BINDINGS = [
        ("f9", "submit_prompt", "Submit Prompt"),
        ("escape", "cancel", "Cancel"),
    ]

    def compose(self):
        yield Static(
            "[bold cyan]F9[/bold cyan] Submit Prompt │ [bold cyan]Esc[/bold cyan] Cancel",
            id="prompt-cheat-sheet",
        )
        yield PromptTextArea(language="markdown", id="prompt-editor-area")

    def on_mount(self) -> None:
        self.query_one(PromptTextArea).focus()

    def action_submit_prompt(self) -> None:
        text = self.query_one(PromptTextArea).text.strip()
        self.dismiss(text)

    def action_cancel(self) -> None:
        self.dismiss(None)
