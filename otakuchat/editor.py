"""Slate — an in-app text editor, replacing the old $EDITOR subprocess.

Ported from Otakumafia's gmag project (gmag/slate.py): a syntax-highlighted
TextArea with auto-closing brackets/quotes, a "leap" input for jump-to-text
search plus Tab word-completion, and a document-word autocomplete dropdown
(otakuchat/autocomplete.py) — all inside the same Textual process instead
of shelling out to $EDITOR and suspending the TUI.

Differences from gmag's version:
- No CSS_PATH of its own — otaku-chat has one master.tcss for the whole
  app (see app.py's CSS_PATH), so #editor/#leap-box/#cheat_sheet styling
  lives there instead, in the app's otaku-purple palette rather than
  gmag's own darkviolet/cyan theme.
- gmag pushes Slate from a file-manager screen with real disk files
  always in play. Here it's pushed over OtakuChat's chat screen for four
  specific files (MEMORY.md, FACTS.md, SNIPPETS.md, config.ini) — same
  contract (edit, Ctrl+S saves to current_file_path, Esc closes), no
  behavior change needed for that difference.
"""
import re
from pathlib import Path

from rich.style import Style
from textual import events
from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.events import Key
from textual.screen import Screen
from textual.widgets import Input, Static, TextArea
from textual.widgets.text_area import TextAreaTheme

from .autocomplete import AutoComplete, DropdownItem, TargetState

# otaku-purple palette (#8b5cf6 purple, #a855f7 violet, #06b6d4 cyan) —
# see master.tcss's header/prompt colors for the same three used elsewhere.
otaku_editor_theme = TextAreaTheme(
    name="otaku-editor",
    cursor_style=Style(color="white", bgcolor="#8b5cf6"),
    cursor_line_style=Style(bgcolor="#2a1f3d"),
    syntax_styles={
        "keyword": Style(color="#a855f7", bold=True),
        "string": Style(color="#06b6d4"),
        "function": Style(color="#a855f7"),
        "number": Style(color="#06b6d4"),
        "comment": Style(color="#8b5cf6", italic=True),
    },
)

EXTENSION_LANGUAGE_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".html": "html", ".css": "css", ".json": "json",
    ".md": "markdown", ".yml": "yaml", ".yaml": "yaml",
    ".toml": "toml", ".sh": "bash", ".c": "c",
    ".cpp": "cpp", ".rs": "rust", ".go": "go", ".java": "java",
    ".ini": "toml",  # config.ini has no dedicated tree-sitter grammar; toml is close enough for coloring key=value/[section]
}


class LeapInput(Input):
    """Jump-to-text search box: Tab completes a word straight from the
    editor's own document text (not the autocomplete dropdown's fuzzy
    ranking — a plain shortest-match prefix search), Enter jumps the
    cursor to the next occurrence of what's typed and returns focus to
    the editor."""

    def on_key(self, event: Key) -> None:
        if event.key == "tab":
            event.prevent_default()
            event.stop()

            if not self.value:
                return

            try:
                text_area = self.screen.query_one("#editor", SlateTextArea)
            except NoMatches:
                return
            words = set(re.findall(r"\b\w+\b", text_area.text))
            search_val = self.value.lower()
            matches = [w for w in words if w.lower().startswith(search_val)]

            if matches:
                matches.sort(key=len)
                self.value = matches[0]
                self.cursor_position = len(self.value)


class SlateTextArea(TextArea):
    """TextArea subclass adding auto-closing brackets/quotes, and giving
    the autocomplete dropdown first look at navigation/accept keys before
    TextArea's own default action (inserting a literal newline/tab) runs.

    Also reclaims Escape: stock TextArea intercepts it itself (moves
    focus to the next widget instead of letting it bubble) whenever
    tab_behavior="indent" (the code_editor() default), which silently
    swallowed Slate's own escape->close_editor binding — Escape would
    just shift focus around inside the editor screen instead of closing
    it. Left un-prevented here, it now reaches Slate's BINDINGS as
    expected.
    """

    def _on_key(self, event: Key) -> None:
        # Textual dispatches _on_key at every class in the MRO that defines
        # it, so TextArea's own _on_key (the real typing/newline/etc.
        # handling) still runs after this returns — no need to call it
        # ourselves. What IS needed is letting the autocomplete dropdown
        # react to Enter/Tab/Up/Down *before* TextArea's default action —
        # its usual message-signal notification only fires after the
        # target already processed the key, too late to stop a literal
        # newline/tab landing when accepting a suggestion.
        if event.key == "escape":
            # Stock TextArea's own _on_key ALSO runs (separately, via the
            # same MRO dispatch) unless we mark the event done here —
            # and it intercepts escape itself whenever tab_behavior is
            # "indent" (code_editor()'s default), shifting focus to the
            # next widget instead of letting it bubble to Slate's
            # escape->close_editor binding. Stop it here and drive the
            # close directly so Escape always closes the editor.
            event.prevent_default()
            event.stop()
            if isinstance(self.screen, Slate):
                self.screen.action_close_editor()
            return
        try:
            autocomplete = self.screen.query_one(CodeAutoComplete)
        except NoMatches:
            autocomplete = None
        if autocomplete is not None:
            autocomplete._handle_key(event)
            if event._no_default_action:
                return

        pairs = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
        if event.character in pairs:
            event.prevent_default()
            closing = pairs[event.character]
            self.insert(f"{event.character}{closing}")
            self.move_cursor_relative(columns=-1)


class CodeAutoComplete(AutoComplete):
    """A specialized AutoComplete that targets a 2D TextArea instead of a
    1D Input — completion candidates are every unique word already
    present in the document, plus a small set of common keywords."""

    @property
    def target(self) -> SlateTextArea:
        return self.screen.query_one(self._target, SlateTextArea)

    def _get_target_state(self) -> TargetState:
        target = self.target
        row, col = target.cursor_location
        idx = target.document.get_index_from_location((row, col))
        return TargetState(text=target.text, cursor_position=idx)

    def get_search_string(self, target_state: TargetState) -> str:
        text_before_cursor = target_state.text[: target_state.cursor_position]
        match = re.search(r"\w+$", text_before_cursor)
        return match.group(0) if match else ""

    def apply_completion(self, value: str, state: TargetState) -> None:
        target = self.target
        search_string = self.get_search_string(state)

        if search_string:
            for _ in range(len(search_string)):
                target.action_delete_left()

        target.insert(value)

    def _align_to_target(self) -> None:
        from textual.geometry import Offset, Region, Spacing

        target = self.target
        row, col = target.cursor_location

        x = int(target.region.x + col - target.scroll_x)
        y = int(target.region.y + row - target.scroll_y + 1)

        dropdown = self.option_list
        width, height = dropdown.outer_size

        x, y, _w, _h = Region(x, y, width, height).constrain(
            "inside", "none", Spacing.all(0), self.screen.scrollable_content_region
        )
        self.absolute_offset = Offset(x, y)

    def _listen_to_messages(self, event: events.Event) -> None:
        super()._listen_to_messages(event)
        if isinstance(event, TextArea.Changed) or isinstance(event, TextArea.SelectionChanged):
            self._handle_target_update()

    def get_candidates(self, target_state: TargetState) -> list[DropdownItem]:
        """Every unique word already in the document, plus a small base
        set of common keywords so completion isn't limited to what's
        already been typed once."""
        document_words = set(re.findall(r"\b[a-zA-Z_]\w*\b", target_state.text))

        base_keywords = {
            "import", "def", "class", "print", "return", "self",
            "yield", "try", "except", "True", "False", "None",
            "if", "elif", "else", "for", "while", "with", "as",
        }

        all_candidates = document_words.union(base_keywords)
        return [DropdownItem(main=word) for word in sorted(all_candidates)]


class Slate(Screen[None]):
    """A full-screen text editor pushed over OtakuChat's chat screen —
    replaces subprocess.run([$EDITOR, path]) + App.suspend() for
    /memory, /facts, /snippets, and /config."""

    BINDINGS = [
        ("ctrl+s", "save_file", "Save"),
        ("ctrl+l", "focus_leap", "Leap"),
        ("f9", "toggle_soft_wrap", "Toggle Wrap"),
        ("escape", "close_editor", "Close"),
    ]

    def __init__(self, file_path: str | Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.current_file_path = Path(file_path)

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold]Ctrl+S[/bold] Save  │  [bold]Ctrl+L[/bold] Leap  │  "
            "[bold]F9[/bold] Wrap  │  [bold]Esc[/bold] Close",
            id="editor-cheat-sheet",
        )
        yield LeapInput(placeholder="Leap -> (Tab completes, Enter jumps + returns)", id="leap-box")
        yield SlateTextArea.code_editor(language="python", theme="monokai", id="editor")
        yield CodeAutoComplete(target="#editor", id="editor-autocomplete")

    def on_mount(self) -> None:
        editor = self.query_one("#editor", SlateTextArea)
        editor.register_theme(otaku_editor_theme)
        editor.theme = "otaku-editor"

        suffix = self.current_file_path.suffix.lower()
        editor.language = EXTENSION_LANGUAGE_MAP.get(suffix)

        try:
            is_existing_file = self.current_file_path.exists() and self.current_file_path.is_file()
        except OSError as e:
            self.notify(f"Can't access {self.current_file_path.name}: {e}", title="File Error", severity="error")
            return

        if is_existing_file:
            try:
                text = self.current_file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                self.notify(f"Can't open {self.current_file_path.name}: {e}", title="File Error", severity="error")
                return
            editor.load_text(text)
            lang_display = editor.language or "plain text"
            self.notify(f"Opened {self.current_file_path.name} ({lang_display})", title="File Loaded")
        else:
            self.notify(f"Ready to create new file: {self.current_file_path.name}", title="New File")

        editor.focus()

    def action_close_editor(self) -> None:
        """Dismisses the editor screen, returning to the chat and firing
        any callback the caller passed to push_screen (used for the
        post-close UI notes/config-reload in app.py)."""
        self.dismiss(None)

    def action_focus_leap(self) -> None:
        self.query_one("#leap-box", LeapInput).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "leap-box":
            return
        search_string = event.value
        if not search_string:
            return

        text_area = self.query_one("#editor", SlateTextArea)
        row, col = text_area.cursor_location
        current_index = text_area.document.get_index_from_location((row, col))

        next_index = text_area.text.find(search_string, current_index)
        if next_index == -1:
            next_index = text_area.text.find(search_string)

        if next_index != -1:
            new_location = text_area.document.get_location_from_index(next_index)
            text_area.move_cursor(new_location)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "leap-box":
            return
        self.query_one("#editor", SlateTextArea).focus()
        event.input.value = ""

    def action_save_file(self) -> None:
        editor = self.query_one("#editor", SlateTextArea)
        try:
            self.current_file_path.write_text(editor.text, encoding="utf-8")
        except OSError as e:
            self.notify(f"Could not save {self.current_file_path.name}: {e}", title="Save Error", severity="error")
            return
        self.notify(f"Successfully saved to {self.current_file_path.name}!", title="Saved", severity="information")

    def action_toggle_soft_wrap(self) -> None:
        editor = self.query_one("#editor", SlateTextArea)
        editor.soft_wrap = not editor.soft_wrap
        status = "enabled" if editor.soft_wrap else "disabled"
        self.notify(f"Soft wrapping is now {status}.", title="Settings")
