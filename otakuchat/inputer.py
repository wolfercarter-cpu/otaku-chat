from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class TextPrompt(ModalScreen[str]):
    """Universal single-line text input modal — ask the user for a short
    string (a session rename, an export filename, ...) instead of every
    call site needing its own one-off Screen subclass. Enter submits
    (blank submits None, treated the same as cancel), Esc cancels.

    No CSS_PATH — styled by selectors in the app-level master.tcss, same
    as every other Screen here.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, initial_value: str = "", placeholder: str = "") -> None:
        super().__init__()
        self.prompt = prompt
        self.initial_value = initial_value
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Container(id="iptr-c1"):
            yield Label(self.prompt, id="iptr-l1")
            yield Input(value=self.initial_value, placeholder=self.placeholder, id="iptr-i1")

    def on_mount(self) -> None:
        self.query_one("#iptr-i1", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value if value else None)

    def action_cancel(self) -> None:
        self.dismiss(None)
