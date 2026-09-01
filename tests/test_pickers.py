"""Tests for otakuchat/pickers.py — ListPicker, ModalListPicker,
SessionPicker, ConfirmDialog, FileBrowser.

test_selecting_an_option_in_session_picker_does_not_crash is a direct
regression test for a real, non-obvious Textual bug found while building
SessionPicker: naming-convention message handlers (on_option_list_option_
selected) are NOT single-dispatch overrides the way normal Python methods
are — Textual walks the whole MRO and invokes every class that defines the
handler directly, not just the most-derived one. SessionPicker used to
override that handler directly, so picking an option fired BOTH the mixin's
version (which called self.dismiss(...) once) AND SessionPicker's own
version (which called self.dismiss(...) again) — the second dismiss()
crashed trying to pop an already-popped screen. Fixed by routing through a
plain _handle_pick() method instead, which normal Python overriding
handles correctly.
"""
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList

from otakuchat.inputer import TextPrompt
from otakuchat.options import MENU_ITEMS
from otakuchat.pickers import ConfirmDialog, ListPicker, ModalListPicker, SessionPicker

# Real master.tcss, not a relative "master.tcss" (which Textual would look
# for next to this test file, not next to otakuchat/app.py) — without it,
# none of OtakuChat's layout CSS applies, including the popup-vs-full-
# screen distinction between ModalListPicker and ListPicker.
_MASTER_TCSS = Path(__file__).parent.parent / "otakuchat" / "master.tcss"


class _Host(App):
    CSS_PATH = str(_MASTER_TCSS)

    def compose(self) -> ComposeResult:
        yield from ()


async def _settle(pilot, n=3):
    for _ in range(n):
        await pilot.pause()


# --- ListPicker / ModalListPicker ------------------------------------------

async def test_list_picker_selecting_an_option_dismisses_with_its_value():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(
            ListPicker("pick one", [("Alpha", "a"), ("Beta", "b")]), results.append
        )
        await _settle(pilot)
        opt = app.screen.query_one("#picker-list", OptionList)
        opt.highlighted = 1
        await _settle(pilot)
        await pilot.press("enter")
        await _settle(pilot)
        assert results == ["b"]


async def test_list_picker_escape_dismisses_with_none():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(ListPicker("pick one", [("Alpha", "a")]), results.append)
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert results == [None]


async def test_list_picker_shows_empty_message_when_no_options():
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(ListPicker("pick one", [], "Nothing here."))
        await _settle(pilot)
        opt = app.screen.query_one("#picker-list", OptionList)
        assert opt.option_count == 1
        assert opt.get_option_at_index(0).disabled is True


async def test_modal_list_picker_renders_as_a_small_centered_popup():
    """/menu's picker should stay a dimmed popup, not a full-screen
    takeover like the other three pickers."""
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(ModalListPicker("Menu", MENU_ITEMS))
        await _settle(pilot)
        screen = app.screen
        assert isinstance(screen, ModalListPicker)
        assert screen._modal is True
        region = screen.query_one("#picker-c1").region
        assert region.width < 100
        assert region.height < 40


# --- SessionPicker (the multi-dispatch regression) -------------------------

async def test_selecting_an_option_in_session_picker_does_not_crash():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(
            SessionPicker("Sessions", [("#1 chat", "1"), ("#2 chat", "2")]),
            results.append,
        )
        await _settle(pilot)
        opt = app.screen.query_one("#picker-list", OptionList)
        opt.highlighted = 0
        await _settle(pilot)
        await pilot.press("enter")
        await _settle(pilot)
        # a single, correctly-shaped result — not a crash, not two dismisses
        assert results == [("resume", "1")]


async def test_session_picker_delete_binding_dismisses_a_delete_tuple():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(
            SessionPicker("Sessions", [("#1 chat", "1"), ("#2 chat", "2")]),
            results.append,
        )
        await _settle(pilot)
        opt = app.screen.query_one("#picker-list", OptionList)
        opt.highlighted = 1
        await _settle(pilot)
        await pilot.press("d")
        await _settle(pilot)
        assert results == [("delete", "2")]


async def test_session_picker_escape_still_dismisses_none():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(SessionPicker("Sessions", [("#1 chat", "1")]), results.append)
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert results == [None]


# --- ConfirmDialog -----------------------------------------------------------

async def test_confirm_dialog_y_confirms():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(ConfirmDialog("Are you sure?"), results.append)
        await _settle(pilot)
        await pilot.press("y")
        await _settle(pilot)
        assert results == [True]


async def test_confirm_dialog_n_declines():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(ConfirmDialog("Are you sure?"), results.append)
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        assert results == [False]


async def test_confirm_dialog_escape_also_declines():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(ConfirmDialog("Are you sure?"), results.append)
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert results == [False]


# --- TextPrompt (universal single-line input) -------------------------------

async def test_text_prompt_prefills_and_focuses_the_input():
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(TextPrompt("Rename", "current name"))
        await _settle(pilot)
        inp = app.screen.query_one("#iptr-i1", Input)
        assert inp.value == "current name"
        assert inp.has_focus


async def test_text_prompt_submit_dismisses_with_the_new_value():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(TextPrompt("Rename", "old"), results.append)
        await _settle(pilot)
        inp = app.screen.query_one("#iptr-i1", Input)
        inp.value = ""
        inp.focus()
        for ch in "new name":
            await pilot.press(ch)
        await pilot.press("enter")
        await _settle(pilot)
        assert results == ["new name"]


async def test_text_prompt_blank_submit_dismisses_none():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(TextPrompt("Rename", "old"), results.append)
        await _settle(pilot)
        inp = app.screen.query_one("#iptr-i1", Input)
        inp.value = ""
        inp.focus()
        await pilot.press("enter")
        await _settle(pilot)
        assert results == [None]


async def test_text_prompt_escape_dismisses_none():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(TextPrompt("Rename", "old"), results.append)
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert results == [None]
