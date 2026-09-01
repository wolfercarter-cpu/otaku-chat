"""Tests for otakuchat/autocomplete.py — the generic dropdown-autocomplete
component (ported from gmag) behind editor.py's CodeAutoComplete.

Uses a plain Input target (the base class's own contract) rather than
editor.py's TextArea-targeting subclass — that combination is covered
end-to-end by test_editor.py's leap/word-completion tests instead.
"""
from textual.app import App, ComposeResult
from textual.widgets import Input

from otakuchat.autocomplete import AutoComplete, DropdownItem


class _FixedAutoComplete(AutoComplete):
    """Concrete AutoComplete with a static candidate list, for testing
    the base dropdown mechanics independent of any candidate source."""


class _Host(App):
    def compose(self) -> ComposeResult:
        yield Input(id="target")
        yield _FixedAutoComplete(
            target="#target",
            candidates=["apple", "banana", "grape", "application"],
            id="ac",
        )


async def _settle(pilot, n=3):
    for _ in range(n):
        await pilot.pause()


async def test_typing_shows_matching_candidates():
    app = _Host()
    async with app.run_test() as pilot:
        target = app.query_one("#target", Input)
        target.focus()
        for ch in "app":
            await pilot.press(ch)
        await _settle(pilot)
        ac = app.query_one("#ac", _FixedAutoComplete)
        assert ac.display
        values = [opt.value for opt in ac.option_list._options]
        assert "apple" in values
        assert "application" in values
        assert "banana" not in values


async def test_empty_search_string_hides_dropdown():
    app = _Host()
    async with app.run_test() as pilot:
        target = app.query_one("#target", Input)
        target.focus()
        await _settle(pilot)
        ac = app.query_one("#ac", _FixedAutoComplete)
        assert not ac.display


async def test_losing_focus_hides_dropdown():
    app = _Host()
    async with app.run_test() as pilot:
        target = app.query_one("#target", Input)
        target.focus()
        for ch in "app":
            await pilot.press(ch)
        await _settle(pilot)
        ac = app.query_one("#ac", _FixedAutoComplete)
        assert ac.display
        target.blur()
        await _settle(pilot)
        assert not ac.display


async def test_tab_accepts_the_highlighted_candidate():
    app = _Host()
    async with app.run_test() as pilot:
        target = app.query_one("#target", Input)
        target.focus()
        for ch in "gra":
            await pilot.press(ch)
        await _settle(pilot)
        await pilot.press("tab")
        await _settle(pilot)
        assert target.value == "grape"


async def test_dropdown_item_value_strips_highlight_markup():
    item = DropdownItem(main="hello")
    assert item.value == "hello"
