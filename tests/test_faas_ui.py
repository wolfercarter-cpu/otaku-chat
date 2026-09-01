"""Tests for otakuchat/faas_ui.py — FunctionCallConfirm (the Yes/No safety
gate every call goes through), FunctionCallResult, and build_functions_menu.
"""
from pathlib import Path

from textual.app import App, ComposeResult

from otakuchat import faas
from otakuchat.faas_ui import FunctionCallConfirm, FunctionCallResult, build_functions_menu
from otakuchat.pickers import ListPicker

_MASTER_TCSS = Path(__file__).parent.parent / "otakuchat" / "master.tcss"


class _Host(App):
    CSS_PATH = str(_MASTER_TCSS)

    def compose(self) -> ComposeResult:
        yield from ()


async def _settle(pilot, n=3):
    for _ in range(n):
        await pilot.pause()


# --- FunctionCallConfirm ----------------------------------------------

async def test_confirm_shows_function_name_and_args():
    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(FunctionCallConfirm("greet", {"name": "World"}, source="model"))
        await _settle(pilot)
        from textual.widgets import Label
        text = app.screen.query_one("#faas-confirm-l2", Label).render().plain
        assert "greet" in str(text)
        assert "World" in str(text)


async def test_confirm_y_key_dismisses_true():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(FunctionCallConfirm("greet", {}), results.append)
        await _settle(pilot)
        await pilot.press("y")
        await _settle(pilot)
        assert results == [True]


async def test_confirm_n_key_dismisses_false():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(FunctionCallConfirm("greet", {}), results.append)
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)
        assert results == [False]


async def test_confirm_escape_dismisses_false():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(FunctionCallConfirm("greet", {}), results.append)
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert results == [False]


async def test_confirm_shows_different_wording_for_model_vs_manual():
    from textual.widgets import Label

    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(FunctionCallConfirm("greet", {}, source="model"))
        await _settle(pilot)
        model_text = str(app.screen.query_one("#faas-confirm-l1", Label).render().plain)
        app.pop_screen()
        await _settle(pilot)

        app.push_screen(FunctionCallConfirm("greet", {}, source="manual"))
        await _settle(pilot)
        manual_text = str(app.screen.query_one("#faas-confirm-l1", Label).render().plain)

    assert "model" in model_text.lower()
    assert "model" not in manual_text.lower()


# --- FunctionCallResult -------------------------------------------------

async def test_result_shows_success():
    from textual.widgets import Label

    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(FunctionCallResult("greet", True, "Hello!"))
        await _settle(pilot)
        text = str(app.screen.query_one("#faas-result-l1", Label).render().plain)
        assert "succeeded" in text
        assert "greet" in text


async def test_result_shows_failure():
    from textual.widgets import Label

    app = _Host()
    async with app.run_test() as pilot:
        app.push_screen(FunctionCallResult("greet", False, "boom"))
        await _settle(pilot)
        text = str(app.screen.query_one("#faas-result-l1", Label).render().plain)
        assert "failed" in text


async def test_result_enter_closes():
    app = _Host()
    async with app.run_test() as pilot:
        results = []
        app.push_screen(FunctionCallResult("greet", True, "ok"), results.append)
        await _settle(pilot)
        await pilot.press("enter")
        await _settle(pilot)
        assert results == [None]


# --- build_functions_menu ------------------------------------------------

def test_build_functions_menu_includes_manage_entry(isolated_env):
    picker = build_functions_menu()
    assert isinstance(picker, ListPicker)
    values = [v for _, v in picker.options]
    assert "__manage__" in values


def test_build_functions_menu_lists_defined_functions(isolated_env):
    faas.functions_path().parent.mkdir(parents=True, exist_ok=True)
    faas.functions_path().write_text("def my_func(x):\n    return x\n")
    picker = build_functions_menu()
    values = [v for _, v in picker.options]
    assert "my_func" in values


def test_build_functions_menu_empty_message_when_no_functions(isolated_env):
    faas.functions_path().parent.mkdir(parents=True, exist_ok=True)
    faas.functions_path().write_text("# nothing here\n")
    picker = build_functions_menu()
    values = [v for _, v in picker.options]
    assert values == ["__manage__"]
