from dataclasses import dataclass

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Input, Static, TextArea


class HistoryInput(Input):
    """Input with aider-style persistent up/down history recall.

    Kept around for the small modal screens (rename, session name, etc.)
    that still want a plain single-line Input. The main chat box uses
    ChatInput below.

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


class ChatInput(TextArea):
    """The main chat box: an auto-growing TextArea, adapted from oterm's
    PostableTextArea (oterm/app/widgets/prompt.py).

    - Enter submits (posts ChatInput.Submitted)
    - Shift+Enter / Ctrl+M inserts a literal newline — real multi-line
      composition without needing the separate /prompt modal for most cases
    - Grows with content up to MAX_LINES, then scrolls
    - Persistent Up/Down history recall (aider-style, db-backed), but only
      while the box holds a single line — once you're composing multiple
      lines, Up/Down navigate within the text like a normal editor instead
      of hijacking cursor movement.
    """

    MAX_LINES = 8

    BINDINGS = TextArea.BINDINGS + [
        Binding("enter", "submit", "submit", show=True, key_display=None, priority=True),
        Binding(
            "shift+enter", "newline", "newline",
            show=True, key_display=None, priority=True, id="newline",
        ),
        Binding("ctrl+m", "newline", "newline", show=False, key_display=None, priority=True),
    ]

    @dataclass
    class Submitted(Message):
        input: "ChatInput"
        value: str

        @property
        def control(self) -> "ChatInput":
            return self.input

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.show_line_numbers = False
        self.soft_wrap = True
        self._history: list[str] = []
        self._history_index: int = 0
        self._draft: str = ""
        self._loaded = False

    def on_mount(self) -> None:
        self._resize_to_content()

    def _resize_to_content(self) -> None:
        line_count = max(self.wrapped_document.height, 1)
        self.styles.height = min(line_count, self.MAX_LINES)

    def _on_key(self, event: events.Key) -> None:
        # Only intercept Up/Down for history recall when the box is a
        # single line — otherwise let TextArea move the cursor normally.
        if event.key in ("up", "down") and self.document.line_count == 1:
            self._ensure_history_loaded()
            if event.key == "up":
                self._recall_up()
            else:
                self._recall_down()
            event.prevent_default()
            event.stop()

    def _ensure_history_loaded(self) -> None:
        if self._loaded:
            return
        from . import db

        self._history = db.get_input_history()
        self._history_index = len(self._history)
        self._loaded = True

    def reload_history(self) -> None:
        self._loaded = False
        self._ensure_history_loaded()

    def _recall_up(self) -> None:
        if not self._history:
            return
        if self._history_index == len(self._history):
            self._draft = self.text
        if self._history_index > 0:
            self._history_index -= 1
            self.text = self._history[self._history_index]
            self.move_cursor(self.document.end)

    def _recall_down(self) -> None:
        if not self._history:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.text = self._history[self._history_index]
        elif self._history_index == len(self._history) - 1:
            self._history_index += 1
            self.text = self._draft
        else:
            return
        self.move_cursor(self.document.end)

    def action_submit(self) -> None:
        self.post_message(ChatInput.Submitted(self, self.text))

    def action_newline(self) -> None:
        self.insert("\n")

    def clear(self) -> None:
        self.text = ""
        self._resize_to_content()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._resize_to_content()


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
