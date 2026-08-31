from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

# (label shown in the menu, slash command it actually runs) — kept as pairs
# rather than deriving one from the other since a couple of labels read
# better short than their real command name (e.g. "name" for /rename).
MENU_ITEMS = [
    ("help", "/help"),
    ("model", "/model"),
    ("memory", "/memory"),
    ("facts", "/facts"),
    ("snippets", "/snippets"),
    ("config", "/config"),
    ("new", "/new"),
    ("sessions", "/sessions"),
    ("name", "/rename"),
    ("export", "/export"),
    ("add", "/add"),
    ("think", "/think"),
    ("pattern", "/pattern"),
    ("pattern off", "/pattern off"),
    ("prompt", "/prompt"),
    ("quit", "/quit"),
]


class OptionSelector(ModalScreen[str]):
    """/menu — every slash command in one picker, so you don't have to
    remember them. Enter runs the highlighted command exactly as if it had
    been typed (including opening any picker it normally opens, e.g.
    "sessions" -> /sessions -> SessionBrowser); Esc cancels.

    No CSS_PATH here — like every other Screen in this app, it's styled by
    selectors in the app-level master.tcss (OtakuChat.CSS_PATH), not its
    own stylesheet.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(id="sel-c1"):
            yield Label("Menu — Enter to run, Esc to cancel", id="menu-header")
            yield OptionList(id="menu-list")

    def on_mount(self) -> None:
        opt_list = self.query_one("#menu-list", OptionList)
        for label, command in MENU_ITEMS:
            opt_list.add_option(Option(label, id=command))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
