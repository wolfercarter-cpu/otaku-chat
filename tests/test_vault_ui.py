"""Tests for otakuchat/vault_ui.py — VaultManager (the /vault fuzzy
browse/remove/seed/wipe screen) and VaultImportPrompt (/import's URL
entry modal).
"""
from pathlib import Path
from unittest import mock

from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList

from otakuchat import vault
from otakuchat.vault_ui import VaultImportPrompt, VaultManager

_MASTER_TCSS = Path(__file__).parent.parent / "otakuchat" / "master.tcss"


class _Host(App):
    CSS_PATH = str(_MASTER_TCSS)

    def compose(self) -> ComposeResult:
        yield from ()


async def _settle(pilot, n=3):
    for _ in range(n):
        await pilot.pause()


def _write_vault_file(relpath: str, content: str) -> Path:
    p = vault.vault_dir() / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


# --- VaultManager: listing / empty state ------------------------------------

async def test_manager_shows_empty_message_when_vault_is_empty(isolated_env):
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        opt = app.screen.query_one("#vault-list", OptionList)
        assert opt.option_count == 1
        assert opt.get_option_at_index(0).disabled is True


async def test_manager_lists_vault_and_seed_entries(isolated_env):
    _write_vault_file("doc.md", "content")
    (vault.seed_dir() / "keep.md").parent.mkdir(parents=True, exist_ok=True)
    (vault.seed_dir() / "keep.md").write_text("content")

    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        opt = app.screen.query_one("#vault-list", OptionList)
        assert opt.option_count == 2


# --- fuzzy search-as-you-type (same pattern as pickers.py) -----------------

async def test_typing_while_list_focused_redirects_into_search(isolated_env):
    _write_vault_file("alpha.md", "a")
    _write_vault_file("beta.md", "b")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        opt = app.screen.query_one("#vault-list", OptionList)
        opt.focus()
        await _settle(pilot)
        await pilot.press("b")
        await _settle(pilot)
        search = app.screen.query_one("#vault-search", Input)
        assert search.value == "b"
        assert search.has_focus


async def test_search_filters_by_fuzzy_match(isolated_env):
    _write_vault_file("alpha.md", "a")
    _write_vault_file("beta.md", "b")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        search = app.screen.query_one("#vault-search", Input)
        search.focus()
        for ch in "bet":
            await pilot.press(ch)
        await _settle(pilot)
        opt = app.screen.query_one("#vault-list", OptionList)
        assert opt.option_count == 1


# --- actions: seed / remove / wipe ------------------------------------------

async def test_seed_button_copies_highlighted_entry(isolated_env):
    _write_vault_file("keep_me.md", "important")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        opt = app.screen.query_one("#vault-list", OptionList)
        opt.highlighted = 0
        await _settle(pilot)
        await pilot.click("#vault-b-seed")
        await _settle(pilot)
        assert (vault.seed_dir() / "keep_me.md").exists()


async def test_enter_on_a_row_seeds_it(isolated_env):
    """Header hint: 'Enter or click to seed' — Enter on the highlighted
    row is the quick/safe path to Add to Seed."""
    _write_vault_file("quick.md", "content")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        opt = app.screen.query_one("#vault-list", OptionList)
        opt.focus()
        opt.highlighted = 0
        await _settle(pilot)
        await pilot.press("enter")
        await _settle(pilot)
        assert (vault.seed_dir() / "quick.md").exists()


async def test_remove_button_deletes_highlighted_entry(isolated_env):
    _write_vault_file("delete_me.md", "content")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        opt = app.screen.query_one("#vault-list", OptionList)
        opt.highlighted = 0
        await _settle(pilot)
        await pilot.click("#vault-b-remove")
        await _settle(pilot)
        assert not (vault.vault_dir() / "delete_me.md").exists()


async def test_r_key_removes_highlighted_entry(isolated_env):
    _write_vault_file("target.md", "content")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        opt = app.screen.query_one("#vault-list", OptionList)
        opt.focus()
        opt.highlighted = 0
        await _settle(pilot)
        await pilot.press("r")
        await _settle(pilot)
        assert not (vault.vault_dir() / "target.md").exists()


async def test_wipe_button_clears_vault_but_not_seed(isolated_env):
    _write_vault_file("temp.md", "gone")
    (vault.seed_dir()).mkdir(parents=True, exist_ok=True)
    (vault.seed_dir() / "permanent.md").write_text("stays")

    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        await pilot.click("#vault-b-wipe")
        await _settle(pilot)
        assert not (vault.vault_dir() / "temp.md").exists()
        assert (vault.seed_dir() / "permanent.md").exists()


async def test_w_key_wipes_vault(isolated_env):
    _write_vault_file("temp.md", "gone")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        opt = app.screen.query_one("#vault-list", OptionList)
        opt.focus()
        await _settle(pilot)
        await pilot.press("w")
        await _settle(pilot)
        assert not (vault.vault_dir() / "temp.md").exists()


async def test_seeded_entry_survives_wipe_end_to_end(isolated_env):
    """The whole point of the feature, driven through the real UI: seed
    something, wipe, confirm it survived."""
    _write_vault_file("valuable.md", "keep this")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        opt = app.screen.query_one("#vault-list", OptionList)
        opt.highlighted = 0
        await _settle(pilot)
        await pilot.click("#vault-b-seed")
        await _settle(pilot)
        await pilot.click("#vault-b-wipe")
        await _settle(pilot)
        assert not (vault.vault_dir() / "valuable.md").exists()
        assert (vault.seed_dir() / "valuable.md").exists()


# --- close ------------------------------------------------------------------

async def test_escape_closes_the_manager(isolated_env):
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert not isinstance(app.screen, VaultManager)


async def test_close_button_closes_the_manager(isolated_env):
    app = _Host()
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        await pilot.click("#vault-b-close")
        await _settle(pilot)
        assert not isinstance(app.screen, VaultManager)


# --- import via the manager's Import URL button -----------------------------

async def test_import_button_opens_prompt_and_imports(isolated_env):
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultManager())
        await _settle(pilot)
        await pilot.click("#vault-b-import")
        await _settle(pilot)
        assert isinstance(app.screen, VaultImportPrompt)

        fake_resp = mock.MagicMock()
        fake_resp.read.return_value = b"# content"
        fake_resp.__enter__.return_value = fake_resp
        with mock.patch("urllib.request.urlopen", return_value=fake_resp):
            search = app.screen.query_one("#vault-import-i1", Input)
            search.focus()
            for ch in "https://example.com/doc.md":
                await pilot.press(ch)
            await pilot.press("enter")
            await _settle(pilot, n=5)

        assert (vault.vault_dir() / "doc.md").exists()


# --- VaultImportPrompt standalone -------------------------------------------

async def test_import_prompt_escape_dismisses_none():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(VaultImportPrompt(), results.append)
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert results == [None]


async def test_import_prompt_focuses_input_on_mount():
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(VaultImportPrompt())
        await _settle(pilot)
        inp = app.screen.query_one("#vault-import-i1", Input)
        assert inp.has_focus
