"""Tests for otakuchat/editor.py — the in-app Slate editor that replaced
the old $EDITOR subprocess + App.suspend() flow for /memory, /facts,
/snippets, and /config.
"""
from pathlib import Path

from textual.app import App, ComposeResult

from otakuchat.editor import EXTENSION_LANGUAGE_MAP, LeapInput, Slate, SlateTextArea


class _Host(App):
    def compose(self) -> ComposeResult:
        yield from ()


async def _settle(pilot, n=3):
    for _ in range(n):
        await pilot.pause()


# --- opening an existing file --------------------------------------------

async def test_opening_an_existing_file_loads_its_text(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("hello from disk")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(Slate(str(f)))
        await _settle(pilot)
        editor = app.screen.query_one("#editor", SlateTextArea)
        assert editor.text == "hello from disk"


async def test_opening_a_missing_path_starts_with_empty_editor(tmp_path):
    f = tmp_path / "new_file.py"
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(Slate(str(f)))
        await _settle(pilot)
        editor = app.screen.query_one("#editor", SlateTextArea)
        assert editor.text == ""


async def test_language_detected_from_extension(tmp_path):
    f = tmp_path / "script.py"
    f.write_text("x = 1")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(Slate(str(f)))
        await _settle(pilot)
        editor = app.screen.query_one("#editor", SlateTextArea)
        assert editor.language == "python"


async def test_unknown_extension_falls_back_to_none_language(tmp_path):
    f = tmp_path / "data.xyz123"
    f.write_text("whatever")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(Slate(str(f)))
        await _settle(pilot)
        editor = app.screen.query_one("#editor", SlateTextArea)
        assert editor.language is None


def test_extension_map_covers_config_ini():
    # config.ini has no dedicated tree-sitter grammar; mapped to toml as
    # the closest fit for key=value/[section] syntax coloring.
    assert EXTENSION_LANGUAGE_MAP[".ini"] == "toml"


# --- editing + saving ------------------------------------------------------

async def test_ctrl_s_saves_editor_text_to_disk(tmp_path):
    f = tmp_path / "out.txt"
    f.write_text("original")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(Slate(str(f)))
        await _settle(pilot)
        editor = app.screen.query_one("#editor", SlateTextArea)
        editor.focus()
        editor.text = "edited content"
        await pilot.press("ctrl+s")
        await _settle(pilot)
        assert f.read_text() == "edited content"


async def test_creating_a_new_file_via_save(tmp_path):
    f = tmp_path / "brand_new.md"
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(Slate(str(f)))
        await _settle(pilot)
        editor = app.screen.query_one("#editor", SlateTextArea)
        editor.focus()
        editor.text = "# New file"
        await pilot.press("ctrl+s")
        await _settle(pilot)
        assert f.exists()
        assert f.read_text() == "# New file"


# --- close / dismiss --------------------------------------------------------

async def test_escape_dismisses_the_editor_and_fires_callback(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("content")
    results = []
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(Slate(str(f)), results.append)
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert results == [None]
        assert not isinstance(app.screen, Slate)


# --- auto-closing brackets ---------------------------------------------------

async def test_typing_open_paren_auto_closes_it(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(Slate(str(f)))
        await _settle(pilot)
        editor = app.screen.query_one("#editor", SlateTextArea)
        editor.focus()
        await pilot.press("(")
        await _settle(pilot)
        assert editor.text == "()"
        # cursor lands between the pair, not after the closing paren
        row, col = editor.cursor_location
        assert col == 1


# --- soft wrap toggle --------------------------------------------------------

async def test_f9_toggles_soft_wrap(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(Slate(str(f)))
        await _settle(pilot)
        editor = app.screen.query_one("#editor", SlateTextArea)
        initial = editor.soft_wrap
        await pilot.press("f9")
        await _settle(pilot)
        assert editor.soft_wrap != initial


# --- LeapInput: jump-to-text and Tab word completion ------------------------

async def test_leap_enter_jumps_cursor_to_next_occurrence(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("alpha\nbeta\ngamma\nbeta_again")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(Slate(str(f)))
        await _settle(pilot)
        leap = app.screen.query_one("#leap-box", LeapInput)
        leap.focus()
        for ch in "beta":
            await pilot.press(ch)
        await _settle(pilot)
        editor = app.screen.query_one("#editor", SlateTextArea)
        row, col = editor.cursor_location
        assert (row, col) == (1, 0)  # start of "beta" on line 1


async def test_leap_tab_completes_from_document_words(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("def calculate_total():\n    pass")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(Slate(str(f)))
        await _settle(pilot)
        leap = app.screen.query_one("#leap-box", LeapInput)
        leap.focus()
        for ch in "calc":
            await pilot.press(ch)
        await _settle(pilot)
        await pilot.press("tab")
        await _settle(pilot)
        assert leap.value == "calculate_total"


async def test_leap_enter_returns_focus_to_editor_and_clears_box(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("target_word here")
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(Slate(str(f)))
        await _settle(pilot)
        leap = app.screen.query_one("#leap-box", LeapInput)
        leap.focus()
        for ch in "target":
            await pilot.press(ch)
        await _settle(pilot)
        await pilot.press("enter")
        await _settle(pilot)
        editor = app.screen.query_one("#editor", SlateTextArea)
        assert editor.has_focus
        assert leap.value == ""
