"""Tests for otakuchat/widgets.py — the chat input box and the shared
HistoryRecallMixin, using Textual's own pilot test harness (App.run_test).

Two of these are direct regression tests for real, previously-shipped bugs
in this exact file: "typing did nothing" (a plain, non-async _on_key
override silently shadowed the base widget's own async _on_key) and "chat
box squeezed to 0 visible rows" (a box-sizing/height miscalculation left
no room for content once a border was added).
"""
from textual.app import App, ComposeResult

from otakuchat.widgets import ChatInput, HistoryInput


class _ChatInputHost(App):
    def compose(self) -> ComposeResult:
        yield ChatInput(id="ci")


async def test_typing_actually_inserts_characters():
    """Regression: ChatInput._on_key used to be a plain `def` that shadowed
    TextArea's own async _on_key entirely, so typing did nothing for any
    key that wasn't Up/Down."""
    app = _ChatInputHost()
    async with app.run_test() as pilot:
        ci = app.query_one(ChatInput)
        ci.focus()
        for ch in "hello world":
            await pilot.press(ch)
        assert ci.text == "hello world"


async def test_backspace_still_works():
    app = _ChatInputHost()
    async with app.run_test() as pilot:
        ci = app.query_one(ChatInput)
        ci.focus()
        for ch in "hello":
            await pilot.press(ch)
        await pilot.press("backspace")
        assert ci.text == "hell"


async def test_box_is_visible_for_a_single_line_not_squeezed_to_zero():
    """Regression: box-sizing: border-box counted the top+bottom border
    rows against the requested content height, leaving 0 rows for text."""
    app = _ChatInputHost()
    async with app.run_test(size=(80, 30)) as pilot:
        ci = app.query_one(ChatInput)
        await pilot.pause()
        assert ci.content_size.height >= 1
        assert ci.region.size.height >= 3  # 1 content row + 2 border rows


async def test_box_grows_with_shift_enter_and_caps_at_max_lines():
    app = _ChatInputHost()
    async with app.run_test(size=(80, 30)) as pilot:
        ci = app.query_one(ChatInput)
        ci.focus()
        await pilot.pause()

        for _ in range(3):
            await pilot.press("shift+enter")
        await pilot.pause()
        assert ci.document.line_count == 4

        for _ in range(20):
            await pilot.press("shift+enter")
        await pilot.pause()
        assert ci.content_size.height == ChatInput.MAX_LINES


async def test_enter_posts_submitted_with_the_typed_text():
    """ChatInput itself only posts Submitted on Enter — clearing the box
    afterward is app.py's job (on_chat_input_submitted calls event.input
    .clear()), so a bare host with no such handler should NOT clear it."""
    app = _ChatInputHost()
    async with app.run_test() as pilot:
        ci = app.query_one(ChatInput)
        ci.focus()
        for ch in "hi there":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        assert ci.text == "hi there"  # unclear, since nothing handled Submitted


async def test_clear_empties_the_box_and_resets_height():
    app = _ChatInputHost()
    async with app.run_test(size=(80, 30)) as pilot:
        ci = app.query_one(ChatInput)
        ci.focus()
        for ch in "hi there":
            await pilot.press(ch)
        ci.clear()
        await pilot.pause()
        assert ci.text == ""
        assert ci.content_size.height == 1


async def test_region_width_does_not_overflow_the_screen():
    """Regression: an earlier fix for the box-sizing bug (switching to
    box-sizing: content-box) accidentally broke width instead, pushing the
    input 4 columns past the edge of a 100-column terminal."""
    app = _ChatInputHost()
    async with app.run_test(size=(100, 30)) as pilot:
        ci = app.query_one(ChatInput)
        await pilot.pause()
        assert ci.region.size.width <= 100


class _HistoryHost(App):
    def compose(self) -> ComposeResult:
        yield HistoryInput(id="hi")


async def test_history_recall_walks_up_and_down_and_preserves_the_draft(isolated_env):
    from otakuchat import db

    db.add_input_history("first")
    db.add_input_history("second")

    app = _HistoryHost()
    async with app.run_test() as pilot:
        hi = app.query_one(HistoryInput)
        hi.focus()
        for ch in "draft":
            await pilot.press(ch)

        await pilot.press("up")
        assert hi.value == "second"
        await pilot.press("up")
        assert hi.value == "first"
        await pilot.press("up")  # already at oldest, should stay put
        assert hi.value == "first"

        await pilot.press("down")
        assert hi.value == "second"
        await pilot.press("down")
        assert hi.value == "draft"


async def test_can_still_type_normally_after_a_recall_round_trip(isolated_env):
    from otakuchat import db

    db.add_input_history("past entry")

    app = _HistoryHost()
    async with app.run_test() as pilot:
        hi = app.query_one(HistoryInput)
        hi.focus()
        await pilot.press("up")
        await pilot.press("down")
        for ch in "!":
            await pilot.press(ch)
        assert hi.value == "!"
