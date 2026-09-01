from pathlib import Path
from typing import Iterable

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.css.query import NoMatches
from textual.events import Key
from textual.fuzzy import Matcher
from textual.screen import ModalScreen, Screen
from textual.widgets import DirectoryTree, Input, Label, OptionList
from textual.widgets.option_list import Option


class FilteredDirectoryTree(DirectoryTree):
    """A DirectoryTree that hides dotfiles/dotdirs (.git, .cache, ...)."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if not path.name.startswith(".")]


class FileBrowser(Screen):
    """Browse the filesystem and pick a file to /add — click a file or
    press Enter on it to attach it, Esc to cancel. Replaces having to type
    a path by hand."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, start_path: str | None = None) -> None:
        super().__init__()
        self.start_path = start_path or str(Path.cwd())

    def compose(self) -> ComposeResult:
        yield Label("Pick a file to attach — Esc to cancel", id="file-header")
        yield FilteredDirectoryTree(self.start_path, id="file-tree")

    def on_mount(self) -> None:
        self.query_one("#file-tree", FilteredDirectoryTree).focus()

    def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        self.dismiss(str(event.path))

    def action_cancel(self) -> None:
        self.dismiss(None)


class _ListPickerBehavior:
    """Shared behavior for ListPicker (full-screen) and ModalListPicker
    (small centered popup) — same options/header/empty-message contract,
    different presentation chrome. Same reasoning as widgets.py's
    HistoryRecallMixin: the two pickers differ only in visual chrome
    (Screen vs. ModalScreen isn't just CSS — ModalScreen also changes
    binding precedence and dims what's behind it), so the actual picking
    logic lives once here instead of twice.

    Also owns fuzzy search-as-you-type: a hidden `#picker-search` Input
    filters `#picker-list` live via textual.fuzzy.Matcher (the same
    matcher Textual's own command palette uses), with the highlight
    ranking's match spans shown inline. Typing while the OptionList has
    focus jumps focus to the search box instead of doing nothing (a bare
    OptionList has no letter-key behavior at all); Up/Down from the
    search box hands focus back to the list to browse results with the
    keyboard. A subclass with its own single-key bindings (SessionPicker's
    `d` to delete) is respected — those keys are never intercepted for
    the filter, so the binding still fires normally.
    """

    def __init__(
        self,
        header: str,
        options: list[tuple[str, str]],
        empty_message: str = "Nothing to pick.",
    ) -> None:
        super().__init__()
        self.header = header
        self.options = options
        self.empty_message = empty_message
        self._filtered: list[tuple[str, str]] = options

    def _reserved_keys(self) -> set[str]:
        """Single-key bindings this screen (or a subclass, e.g.
        SessionPicker's 'd') already owns — never redirected into the
        search box, so the binding keeps firing normally."""
        reserved: set[str] = set()
        for binding in getattr(self, "BINDINGS", []):
            key = binding[0] if isinstance(binding, tuple) else binding.key
            reserved.update(key.split(","))
        return reserved

    def on_mount(self) -> None:
        self._render_options(self.options)
        if not self.options:
            try:
                self.query_one("#picker-search", Input).display = False
            except NoMatches:
                pass

    def _render_options(self, items: list[tuple[str, str]], query: str = "") -> None:
        opt_list = self.query_one("#picker-list", OptionList)
        opt_list.clear_options()
        if not self.options:
            opt_list.add_option(Option(self.empty_message, disabled=True))
            self._filtered = []
            return
        for label, value in items:
            prompt = Matcher(query).highlight(label) if query else label
            opt_list.add_option(Option(prompt, id=value))
        self._filtered = items
        if items:
            opt_list.highlighted = 0

    def _apply_filter(self, query: str) -> None:
        query = query.strip()
        if not query:
            self._render_options(self.options)
            return
        matcher = Matcher(query)
        scored = [
            (matcher.match(label), (label, value)) for label, value in self.options
        ]
        ranked = [item for score, item in sorted(scored, key=lambda pair: pair[0], reverse=True) if score > 0]
        self._render_options(ranked, query=query)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "picker-search":
            self._apply_filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the search box picks the currently highlighted option —
        same effect as pressing Enter with the OptionList itself focused,
        so search-then-Enter is a single fluid motion without an extra
        Tab/Down to move focus first."""
        if event.input.id != "picker-search":
            return
        try:
            opt_list = self.query_one("#picker-list", OptionList)
        except NoMatches:
            return
        if opt_list.highlighted is None or not (0 <= opt_list.highlighted < len(self._filtered)):
            return
        _, value = self._filtered[opt_list.highlighted]
        self._handle_pick(value)

    def on_key(self, event: Key) -> None:
        """Smart focus routing: typing on the list jumps to search;
        Up/Down from search hands back to the list; Tab autocompletes
        the search box to the top fuzzy match."""
        try:
            search = self.query_one("#picker-search", Input)
            opt_list = self.query_one("#picker-list", OptionList)
        except NoMatches:
            return
        if not search.display:
            return  # empty-options case: no search box to route into

        if event.key in self._reserved_keys():
            return  # let a subclass binding (e.g. SessionPicker's 'd') fire normally

        if event.is_printable and opt_list.has_focus:
            search.focus()
            if event.character:
                search.value += event.character
                search.cursor_position = len(search.value)
            event.stop()
        elif event.key == "down" and search.has_focus:
            opt_list.focus()
            opt_list.action_cursor_down()
            event.stop()
        elif event.key == "up" and search.has_focus:
            opt_list.focus()
            opt_list.action_cursor_up()
            event.stop()
        elif event.key == "tab" and search.has_focus and self._filtered:
            top_label = self._filtered[0][0]
            search.value = top_label
            search.cursor_position = len(top_label)
            event.stop()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Routes through a plain method rather than being overridden
        # directly in subclasses (see SessionPicker): Textual's naming-
        # convention message handlers aren't single-dispatch overrides —
        # it walks the whole MRO and invokes every class that defines
        # on_option_list_option_selected directly, not just the most
        # derived one, so a subclass "override" of this exact method name
        # would fire ALONGSIDE this one, not instead of it.
        if event.option.id is not None:
            self._handle_pick(event.option.id)

    def _handle_pick(self, value: str) -> None:
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ListPicker(_ListPickerBehavior, Screen):
    """Full-screen single-choice picker — /sessions, /pattern, /model.
    Enter dismisses with the value of the highlighted option, Esc
    dismisses with None."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Label(self.header, id="picker-header")
        with Vertical():
            yield OptionList(id="picker-list")
            yield Input(placeholder="Search...", id="picker-search")


class ModalListPicker(_ListPickerBehavior, ModalScreen[str]):
    """Small centered popup single-choice picker — /menu. Same behavior as
    ListPicker, just rendered as a dimmed overlay with a bordered box
    instead of taking over the whole screen — /menu's list is short enough
    (currently 16 items) that a full-screen takeover isn't warranted the
    way it is for potentially-long session/pattern/model lists."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(id="picker-c1"):
            yield Label(self.header, id="picker-header")
            yield OptionList(id="picker-list")
            yield Input(placeholder="Search...", id="picker-search")


class SessionPicker(ListPicker):
    """/sessions — a ListPicker with one addition: 'd' on the highlighted
    row deletes that session instead of resuming it. Deleting a session is
    the one genuinely irreversible action in this app (permanently drops
    its messages too, via ON DELETE CASCADE — see db.delete_session), so
    this dismisses ("resume"|"delete", id) instead of a bare id, letting
    the caller route a delete through a confirm step before it happens
    rather than acting on it directly from here."""

    BINDINGS = ListPicker.BINDINGS + [("d", "delete_highlighted", "Delete")]

    def _handle_pick(self, value: str) -> None:
        self.dismiss(("resume", value))

    def action_delete_highlighted(self) -> None:
        opt_list = self.query_one("#picker-list", OptionList)
        if opt_list.highlighted is None:
            return
        option = opt_list.get_option_at_index(opt_list.highlighted)
        if option.id is not None:
            self.dismiss(("delete", option.id))


class ConfirmDialog(ModalScreen[bool]):
    """Universal yes/no confirmation modal, for the rare action in this app
    that's actually destructive/irreversible (currently: deleting a
    session). y or Enter confirms, n or Esc declines — always dismisses a
    bool, never None, since declining IS the answer here."""

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "No"),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Container(id="confirm-c1"):
            yield Label(self.prompt, id="confirm-l1")
            yield Label("[y]es / [n]o / Esc", id="confirm-hint")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
