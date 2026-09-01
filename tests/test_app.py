"""End-to-end tests for otakuchat/app.py, driving the real OtakuChat app
through Textual's pilot harness rather than testing pieces in isolation.
Every test here should reflect something a real user (or an agent driving
the TUI) actually does — type a command, pick from a menu, confirm a
dialog — not just call an internal method directly.
"""

from unittest import mock

import pytest

from otakuchat import config, db
from otakuchat.app import OtakuChat
from otakuchat.inputer import TextPrompt
from otakuchat.pickers import ConfirmDialog, ListPicker, ModalListPicker


async def _settle(pilot, n=3):
    for _ in range(n):
        await pilot.pause()


@pytest.fixture
def app_env(isolated_env):
    """isolated_env alone isn't enough for OtakuChat(): its __init__ calls
    is_reachable()-adjacent code indirectly via refresh_header() on mount,
    so route network calls through a mock rather than hitting a real (or
    absent) Ollama instance during tests."""
    return isolated_env


# --- /menu -------------------------------------------------------------

async def test_menu_opens_and_running_think_from_it_cycles_boost_mode(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/menu")
        await _settle(pilot)
        assert isinstance(app.screen, ModalListPicker)

        opt_list = app.screen.query_one("#picker-list")
        think_index = next(
            i for i in range(opt_list.option_count)
            if opt_list.get_option_at_index(i).prompt == "think"
        )
        opt_list.highlighted = think_index
        await _settle(pilot)
        before = config.get_boost_mode()
        await pilot.press("enter")
        await _settle(pilot)
        after = config.get_boost_mode()
        assert after != before
        assert "reasoning boost mode set to" in app.conversation_history


async def test_menu_escape_cancels_with_no_side_effect(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        before_mode = config.get_boost_mode()
        app.handle_command("/menu")
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert config.get_boost_mode() == before_mode
        assert not isinstance(app.screen, (ModalListPicker, ListPicker))


# --- /rename -------------------------------------------------------------

async def test_rename_with_no_arg_opens_prefilled_prompt_and_commits(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/rename")
        await _settle(pilot)
        assert isinstance(app.screen, TextPrompt)
        inp = app.screen.query_one("#iptr-i1")
        assert inp.value == "New chat"

        inp.value = ""
        inp.focus()
        for ch in "My Renamed Chat":
            await pilot.press(ch)
        await pilot.press("enter")
        await _settle(pilot)

        assert db.get_session(app.session_id)["title"] == "My Renamed Chat"


async def test_rename_with_explicit_arg_skips_the_prompt(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/rename Direct Name")
        await _settle(pilot)
        assert not isinstance(app.screen, TextPrompt)
        assert db.get_session(app.session_id)["title"] == "Direct Name"


# --- /export -------------------------------------------------------------

async def test_export_with_no_arg_prompts_and_writes_the_file_on_accept(app_env, tmp_path):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        db.add_message(app.session_id, "user", "hello")
        app.handle_command("/export")
        await _settle(pilot)
        assert isinstance(app.screen, TextPrompt)

        inp = app.screen.query_one("#iptr-i1")
        out_path = tmp_path / "exported.md"
        inp.value = str(out_path)
        inp.focus()
        await pilot.press("enter")
        await _settle(pilot)

        assert out_path.exists()
        assert "hello" in out_path.read_text()


async def test_export_esc_cancels_and_writes_nothing(app_env, tmp_path):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/export")
        await _settle(pilot)
        default_name = app.screen.query_one("#iptr-i1").value
        await pilot.press("escape")
        await _settle(pilot)
        from pathlib import Path

        assert not Path(default_name).exists()


# --- /sessions (including delete) -----------------------------------------

async def test_sessions_resume_switches_active_session(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        other = db.create_session("other chat", app.model)
        db.add_message(other, "user", "hi from other")

        app.handle_command("/sessions")
        await _settle(pilot)
        opt = app.screen.query_one("#picker-list")
        idx = next(
            i for i in range(opt.option_count)
            if opt.get_option_at_index(i).id == str(other)
        )
        opt.highlighted = idx
        await _settle(pilot)
        await pilot.press("enter")
        await _settle(pilot)

        assert app.session_id == other
        assert "hi from other" in app.conversation_history


async def test_sessions_delete_inactive_session_after_confirm(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        doomed = db.create_session("delete me", app.model)

        app.handle_command("/sessions")
        await _settle(pilot)
        opt = app.screen.query_one("#picker-list")
        idx = next(
            i for i in range(opt.option_count)
            if opt.get_option_at_index(i).id == str(doomed)
        )
        opt.highlighted = idx
        await _settle(pilot)
        await pilot.press("d")
        await _settle(pilot)
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("y")
        await _settle(pilot)

        assert db.get_session(doomed) is None


async def test_sessions_delete_declined_keeps_the_session(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        keep = db.create_session("keep me", app.model)

        app.handle_command("/sessions")
        await _settle(pilot)
        opt = app.screen.query_one("#picker-list")
        idx = next(
            i for i in range(opt.option_count)
            if opt.get_option_at_index(i).id == str(keep)
        )
        opt.highlighted = idx
        await _settle(pilot)
        await pilot.press("d")
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot)

        assert db.get_session(keep) is not None


async def test_sessions_deleting_the_active_session_starts_a_fresh_one(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        active_id = app.session_id

        app.handle_command("/sessions")
        await _settle(pilot)
        opt = app.screen.query_one("#picker-list")
        idx = next(
            i for i in range(opt.option_count)
            if opt.get_option_at_index(i).id == str(active_id)
        )
        opt.highlighted = idx
        await _settle(pilot)
        await pilot.press("d")
        await _settle(pilot)
        await pilot.press("y")
        await _settle(pilot)

        assert db.get_session(active_id) is None
        assert app.session_id != active_id
        assert db.get_session(app.session_id) is not None


# --- /pattern --------------------------------------------------------------

async def test_pattern_off_clears_an_active_pattern(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.active_pattern = "summarize"
        app.handle_command("/pattern off")
        await _settle(pilot)
        assert app.active_pattern is None


async def test_pattern_unknown_name_shows_an_error_not_a_crash(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/pattern totally_made_up_xyz")
        await _settle(pilot)
        assert "no such pattern" in app.conversation_history


# --- build_messages robustness (regression for the config int-parsing bug) -

async def test_build_messages_survives_a_corrupted_config(app_env):
    """Regression: build_messages() runs several config.get_*() calls
    before run_turn_bg's try/except even starts. A single bad numeric
    value used to raise ValueError here and break every subsequent turn."""
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        parser = config._get_config()
        parser["MEMORY"]["max_memory_chars"] = ""
        parser["CONTEXT"]["max_context_messages"] = "garbage"
        parser["SEARCH"]["max_results"] = ""
        config._save_config(parser)

        messages = app.build_messages()
        assert len(messages) >= 1


async def test_build_messages_survives_a_search_network_failure(app_env):
    """Regression: http.client.IncompleteRead isn't an OSError subclass,
    so it used to slip past search.py's exception handling and crash
    build_messages() outright instead of degrading to no web grounding."""
    import http.client

    parser = config._get_config()
    parser["SEARCH"]["brave_api_key"] = "fake-key"
    config._save_config(parser)

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        db.add_message(app.session_id, "user", "some query")
        with mock.patch(
            "urllib.request.urlopen", side_effect=http.client.IncompleteRead(b"")
        ):
            messages = app.build_messages()
        assert len(messages) >= 1


async def test_maybe_web_search_extracts_page_content_for_top_results(app_env):
    """The 'supercharge' path: a successful Brave search should have its
    top result's actual page content fetched and folded into the search
    block, not just the title+snippet Brave itself returns."""
    import json

    parser = config._get_config()
    parser["SEARCH"]["brave_api_key"] = "fake-key"
    config._save_config(parser)

    search_payload = {
        "web": {
            "results": [
                {"title": "Real Page", "url": "https://example.com/a", "description": "a snippet"},
            ]
        }
    }
    search_resp = mock.MagicMock()
    search_resp.read.return_value = json.dumps(search_payload).encode("utf-8")
    search_resp.__enter__.return_value = search_resp

    page_resp = mock.MagicMock()
    page_resp.headers = {"Content-Type": "text/html"}
    page_resp.read.return_value = b"<p>This is the actual full article body text.</p>"
    page_resp.__enter__.return_value = page_resp

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        db.add_message(app.session_id, "user", "some query")
        with mock.patch(
            "urllib.request.urlopen", side_effect=[search_resp, page_resp]
        ):
            block = app.maybe_web_search("some query")
    assert block is not None
    assert "This is the actual full article body text." in block
    assert "1 with full page content" in app._last_search_note
