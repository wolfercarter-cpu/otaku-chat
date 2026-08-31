from textual.screen import Screen
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

from . import db


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


class RenameSession(ModalScreen[str]):
    """Rename the current session (oterm-style modal)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, old_name: str) -> None:
        super().__init__()
        self.old_name = old_name

    def compose(self) -> ComposeResult:
        with Container(id="rename-container"):
            yield Label("Rename session — Enter to save, Esc to cancel", id="rename-header")
            yield Input(id="rename-input", value=self.old_name)

    def on_mount(self) -> None:
        self.query_one("#rename-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(event.value.strip())
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
