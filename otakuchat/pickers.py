from pathlib import Path
from typing import Iterable

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen, Screen
from textual.widgets import DirectoryTree, Label, OptionList
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

    def on_mount(self) -> None:
        opt_list = self.query_one("#picker-list", OptionList)
        if not self.options:
            opt_list.add_option(Option(self.empty_message, disabled=True))
            return
        for label, value in self.options:
            opt_list.add_option(Option(label, id=value))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ListPicker(_ListPickerBehavior, Screen):
    """Full-screen single-choice picker — /sessions, /pattern, /model.
    Enter dismisses with the value of the highlighted option, Esc
    dismisses with None."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Label(self.header, id="picker-header")
        yield OptionList(id="picker-list")


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
