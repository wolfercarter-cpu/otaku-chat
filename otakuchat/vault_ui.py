"""The /vault manager UI — a fuzzy-filterable list of everything under
vault/ and seed/, with Remove / Add to Seed / Wipe Vault actions.

Built on the same textual.fuzzy.Matcher search-as-you-type pattern as
pickers.py's _ListPickerBehavior (search box redirect-on-type, Tab
autocomplete, Up/Down focus handoff) — not a subclass of it, though,
since _ListPickerBehavior's contract is "pick exactly one option and
dismiss with its value" and this screen needs several actions (remove/
seed/wipe) plus an always-visible import box, not a single-pick flow.

Also hosts VaultImportPrompt, a small modal for /import's URL entry —
kept separate from VaultManager so /import can be typed directly
(`/import <url>`) without opening the full manager, matching every other
command in this app's `/cmd [optional args]` convention.
"""
from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Key
from textual.fuzzy import Matcher
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from . import vault


class VaultImportPrompt(ModalScreen[str]):
    """/import with no URL — a small popup asking for one. Enter submits,
    Esc cancels. Mirrors TextPrompt's contract but lives here rather than
    inputer.py since it's vault-specific (placeholder text, styling
    hook) and has exactly one caller."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(id="vault-import-c1"):
            yield Label("Import into vault — paste a git repo (.git), .zip, or file URL", id="vault-import-l1")
            yield Input(placeholder="https://github.com/user/repo.git", id="vault-import-i1")

    def on_mount(self) -> None:
        self.query_one("#vault-import-i1", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value if value else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class VaultManager(Screen):
    """/vault — browse, remove, seed, and wipe imported vault content.

    Layout: a search box + fuzzy-filtered OptionList of every vault/seed
    entry (root shown per-row so vault vs. seed is always visible), plus
    a row of action buttons that act on whatever's highlighted, plus an
    always-available import box at the bottom for pulling in new content
    without leaving the screen.
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("r", "remove_highlighted", "Remove"),
        ("w", "wipe_vault", "Wipe"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._entries: list[vault.VaultEntry] = []
        self._filtered: list[vault.VaultEntry] = []

    def compose(self) -> ComposeResult:
        yield Label("Vault — Enter or click to seed, r to remove, w to wipe, Esc to close", id="vault-header")
        with Vertical():
            yield Input(placeholder="Search vault + seed...", id="vault-search")
            yield OptionList(id="vault-list")
        yield Static("", id="vault-status")
        with Horizontal(id="vault-actions"):
            yield Button("Add to Seed", id="vault-b-seed")
            yield Button("Remove", id="vault-b-remove", variant="warning")
            yield Button("Wipe Vault", id="vault-b-wipe", variant="error")
            yield Button("Reindex", id="vault-b-reindex")
            yield Button("Import URL", id="vault-b-import")
            yield Button("Close", id="vault-b-close")

    def on_mount(self) -> None:
        self._reload_entries()

    # -- data / rendering ----------------------------------------------

    def _reload_entries(self, query: str = "") -> None:
        self._entries = vault.list_all_entries()
        self._render_options(self._entries, query)
        self._update_status()

    def _render_options(self, items: list[vault.VaultEntry], query: str = "") -> None:
        opt_list = self.query_one("#vault-list", OptionList)
        opt_list.clear_options()
        if not items:
            opt_list.add_option(Option("Vault is empty — use Import URL or drop files in the vault dir.", disabled=True))
            self._filtered = []
            return
        matcher = Matcher(query) if query else None
        for entry in items:
            label = f"[{entry.root}] {entry.relpath}" + ("" if entry.indexed else "  (not indexed — binary/unlisted ext)")
            prompt = matcher.highlight(label) if matcher else label
            opt_list.add_option(Option(prompt, id=f"{entry.root}\x00{entry.relpath}"))
        self._filtered = items
        opt_list.highlighted = 0

    def _apply_filter(self, query: str) -> None:
        query = query.strip()
        if not query:
            self._render_options(self._entries)
            return
        matcher = Matcher(query)
        scored = [
            (matcher.match(f"{e.root} {e.relpath}"), e) for e in self._entries
        ]
        ranked = [e for score, e in sorted(scored, key=lambda pair: pair[0], reverse=True) if score > 0]
        self._render_options(ranked, query=query)

    def _update_status(self) -> None:
        vault_n = len(vault.list_entries("vault"))
        seed_n = len(vault.list_entries("seed"))
        self.query_one("#vault-status", Static).update(
            f"{vault_n} in vault (wipeable)  •  {seed_n} in seed (protected)"
        )

    def _highlighted_entry(self) -> vault.VaultEntry | None:
        opt_list = self.query_one("#vault-list", OptionList)
        if opt_list.highlighted is None or not (0 <= opt_list.highlighted < len(self._filtered)):
            return None
        return self._filtered[opt_list.highlighted]

    # -- search-as-you-type (same pattern as pickers.py) -----------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "vault-search":
            self._apply_filter(event.value)

    def on_key(self, event: Key) -> None:
        try:
            search = self.query_one("#vault-search", Input)
            opt_list = self.query_one("#vault-list", OptionList)
        except NoMatches:
            return

        if event.key in ("r", "w", "escape"):
            return  # let the BINDINGS entries above fire normally

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

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Enter/click on a row = quick "Add to Seed" (the safe, additive
        # action) — matches the header hint ("Enter or click to seed").
        self._do_seed()

    # -- actions -----------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "vault-b-close":
            self.action_close()
        elif event.button.id == "vault-b-seed":
            self._do_seed()
        elif event.button.id == "vault-b-remove":
            self._do_remove()
        elif event.button.id == "vault-b-wipe":
            self._do_wipe()
        elif event.button.id == "vault-b-reindex":
            self._do_reindex()
        elif event.button.id == "vault-b-import":
            self.app.push_screen(VaultImportPrompt(), self._handle_import_url)

    def _do_seed(self) -> None:
        entry = self._highlighted_entry()
        if entry is None:
            return
        if entry.root == "seed":
            self.notify("Already in seed.", severity="warning")
            return
        if vault.add_to_seed(entry.relpath):
            self.notify(f"Seeded '{entry.relpath}' — survives Wipe Vault now.", severity="information")
            self._reload_entries()
        else:
            self.notify(f"Could not seed '{entry.relpath}'.", severity="error")

    def _do_remove(self) -> None:
        entry = self._highlighted_entry()
        if entry is None:
            return
        if vault.remove_entry(entry.root, entry.relpath):
            self.notify(f"Removed '{entry.relpath}' from {entry.root}.", severity="information")
            self._reload_entries()
        else:
            self.notify(f"Could not remove '{entry.relpath}'.", severity="error")

    def _do_wipe(self) -> None:
        n = vault.wipe_vault()
        self.notify(f"Wiped {n} file(s) from vault. Seed untouched.", severity="information")
        self._reload_entries()

    def _do_reindex(self) -> None:
        n = vault.reindex_all()
        self.notify(f"Reindexed {n} file(s).", severity="information")
        self._reload_entries()

    def action_remove_highlighted(self) -> None:
        self._do_remove()

    def action_wipe_vault(self) -> None:
        self._do_wipe()

    def action_close(self) -> None:
        self.dismiss()

    def _handle_import_url(self, url: str | None) -> None:
        if not url:
            return
        self.query_one("#vault-status", Static).update("Importing...")
        self._run_import(url)

    @work(thread=True)
    def _run_import(self, url: str) -> None:
        try:
            message = vault.import_url(url)
        except vault.ImportError_ as e:
            self.app.call_from_thread(self._import_failed, str(e))
            return
        self.app.call_from_thread(self._import_succeeded, message)

    def _import_succeeded(self, message: str) -> None:
        self.notify(message, title="Import complete", severity="information")
        self._reload_entries()

    def _import_failed(self, error: str) -> None:
        self.notify(error, title="Import failed", severity="error")
        self._update_status()
