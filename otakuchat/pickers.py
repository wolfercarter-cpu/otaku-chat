from pathlib import Path
from typing import Iterable

from textual.screen import Screen
from textual.app import ComposeResult
from textual.widgets import DirectoryTree, Label, OptionList
from textual.widgets.option_list import Option

from . import db, patterns


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


class PatternPicker(Screen):
    """Pick a curated Fabric-style prompt pattern to apply to the next message."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self):
        yield Label("Patterns — Enter to apply, Esc to cancel", id="pattern-header")
        yield OptionList(id="pattern-list")

    def on_mount(self) -> None:
        opt_list = self.query_one("#pattern-list", OptionList)
        self.names = patterns.list_patterns()
        if not self.names:
            opt_list.add_option(Option("No patterns installed.", disabled=True))
            return
        for name in self.names:
            desc = patterns.describe(name, max_chars=60)
            opt_list.add_option(Option(f"{name} — {desc}", id=name))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SessionBrowser(Screen):
    """Pick a past session to resume."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self):
        yield Label("Sessions — Enter to resume, Esc to cancel", id="sess-header")
        yield OptionList(id="session-list")

    def on_mount(self) -> None:
        opt_list = self.query_one("#session-list", OptionList)
        self.sessions = db.list_sessions()
        if not self.sessions:
            opt_list.add_option(Option("No sessions yet.", disabled=True))
            return
        for row in self.sessions:
            label = f"#{row['id']}  {row['title']}  [{row['model']}]"
            opt_list.add_option(Option(label, id=str(row["id"])))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not self.sessions or event.option.id is None:
            return
        self.dismiss(int(event.option.id))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ModelPicker(Screen):
    """Pick an installed Ollama model."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, models: list[str], current: str):
        super().__init__()
        self.models = models
        self.current = current

    def compose(self):
        yield Label("Select a model — Esc to cancel", id="model-header")
        yield OptionList(id="model-list")

    def on_mount(self) -> None:
        opt_list = self.query_one("#model-list", OptionList)
        if not self.models:
            opt_list.add_option(Option("No models found. Is Ollama running?", disabled=True))
            return
        for name in self.models:
            marker = " (active)" if name == self.current else ""
            opt_list.add_option(Option(f"{name}{marker}", id=name))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
