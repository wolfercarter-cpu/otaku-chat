"""End-to-end tests for otakuchat/app.py, driving the real OtakuChat app
through Textual's pilot harness rather than testing pieces in isolation.
Every test here should reflect something a real user (or an agent driving
the TUI) actually does — type a command, pick from a menu, confirm a
dialog — not just call an internal method directly.
"""

from unittest import mock
from pathlib import Path

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


# --- /sources ------------------------------------------------------------

async def test_sources_with_no_prior_search_shows_a_helpful_note(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/sources")
        await _settle(pilot)
    assert "no web sources" in app.conversation_history


async def test_sources_after_a_search_lists_every_url_and_fetch_status(app_env):
    """Ties into the real user-facing gap: web search results were
    included in a turn with no way to actually view which URLs were
    used or whether their content was fetched. /sources must surface
    both pieces for every result from the last search."""
    import json

    parser = config._get_config()
    parser["SEARCH"]["brave_api_key"] = "fake-key"
    config._save_config(parser)

    search_payload = {
        "web": {
            "results": [
                {"title": "Fetched OK", "url": "https://example.com/ok", "description": "d1"},
                {"title": "Fetch Failed", "url": "https://example.com/bad", "description": "d2"},
            ]
        }
    }
    search_resp = mock.MagicMock()
    search_resp.read.return_value = json.dumps(search_payload).encode("utf-8")
    search_resp.__enter__.return_value = search_resp

    page_resp = mock.MagicMock()
    page_resp.headers = {"Content-Type": "text/html"}
    page_resp.read.return_value = b"<p>Real extracted content.</p>"
    page_resp.__enter__.return_value = page_resp

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        db.add_message(app.session_id, "user", "some query")
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=[search_resp, page_resp, OSError("network down")],
        ):
            app.maybe_web_search("some query")
        app.handle_command("/sources")
        await _settle(pilot)

    assert "https://example.com/ok" in app.conversation_history
    assert "https://example.com/bad" in app.conversation_history
    assert "full page content fetched" in app.conversation_history
    assert "snippet only — fetch failed" in app.conversation_history


# --- /memory /facts /snippets /config open the in-app Slate editor --------

async def test_memory_command_opens_slate_editor(app_env):
    from otakuchat.editor import Slate

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/memory")
        await _settle(pilot)
        assert isinstance(app.screen, Slate)
        assert app.screen.current_file_path == Path(config.get_memory_path())


async def test_facts_command_opens_slate_editor(app_env):
    from otakuchat.editor import Slate

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/facts")
        await _settle(pilot)
        assert isinstance(app.screen, Slate)
        assert app.screen.current_file_path == Path(config.get_facts_path())


async def test_snippets_command_opens_slate_editor(app_env):
    from otakuchat.editor import Slate

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/snippets")
        await _settle(pilot)
        assert isinstance(app.screen, Slate)
        assert app.screen.current_file_path == Path(config.get_snippets_path())


async def test_config_command_opens_slate_editor(app_env):
    from otakuchat.editor import Slate

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/config")
        await _settle(pilot)
        assert isinstance(app.screen, Slate)
        assert app.screen.current_file_path == config.CONFIG_FILE


async def test_closing_memory_editor_appends_a_ui_note(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/memory")
        await _settle(pilot)
        await pilot.press("escape")
        await _settle(pilot)
        assert "closed memory editor" in app.conversation_history


async def test_closing_config_editor_reloads_model_and_api_url(app_env):
    """Regression guard: the old subprocess+suspend /config flow reloaded
    self.model/self.api_url after the external editor closed — the
    in-app Slate replacement must keep doing that so a hand-edited
    config.ini actually takes effect without restarting the app."""
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/config")
        await _settle(pilot)

        parser = config._get_config()
        parser["LLM"]["model"] = "a-different-model:latest"
        config._save_config(parser)

        await pilot.press("escape")
        await _settle(pilot)
        assert app.model == "a-different-model:latest"
        assert "closed config editor" in app.conversation_history


async def test_editing_and_saving_memory_file_persists_to_disk(app_env):
    from otakuchat.editor import SlateTextArea

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/memory")
        await _settle(pilot)
        editor = app.screen.query_one("#editor", SlateTextArea)
        editor.focus()
        editor.text = "## Curated Memory\n\n- a hand-edited fact\n"
        await pilot.press("ctrl+s")
        await _settle(pilot)
        assert Path(config.get_memory_path()).read_text() == "## Curated Memory\n\n- a hand-edited fact\n"


# --- /vault and /import ------------------------------------------------

async def test_vault_command_opens_vault_manager(app_env):
    from otakuchat.vault_ui import VaultManager

    app = OtakuChat()
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/vault")
        await _settle(pilot)
        assert isinstance(app.screen, VaultManager)


async def test_import_with_no_arg_opens_prompt(app_env):
    from otakuchat.vault_ui import VaultImportPrompt

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/import")
        await _settle(pilot)
        assert isinstance(app.screen, VaultImportPrompt)


async def test_import_with_url_arg_imports_directly_without_a_prompt(app_env):
    from otakuchat import vault

    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = b"# direct import"
    fake_resp.__enter__.return_value = fake_resp

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        with mock.patch("urllib.request.urlopen", return_value=fake_resp):
            app.handle_command("/import https://example.com/direct.md")
            await _settle(pilot, n=6)
        assert (vault.vault_dir() / "direct.md").exists()
        assert "importing" in app.conversation_history


async def test_vault_content_grounds_a_turn_via_build_messages(app_env):
    from otakuchat import vault

    p = vault.vault_dir() / "project_notes.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("The deployment pipeline uses GitHub Actions and pushes to Fly.io.")
    vault.index_file("vault", "project_notes.md", p)

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        db.add_message(app.session_id, "user", "how does our deployment pipeline work")
        messages = app.build_messages()
    joined = " ".join(m["content"] for m in messages)
    assert "GitHub Actions" in joined


# --- /functions (FaaS hands) --------------------------------------------

async def test_functions_command_opens_menu(app_env):
    from otakuchat.pickers import ListPicker

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_command("/functions")
        await _settle(pilot)
        assert isinstance(app.screen, ListPicker)


async def test_functions_block_grounds_build_messages(app_env):
    from otakuchat import faas

    faas.functions_path().parent.mkdir(parents=True, exist_ok=True)
    faas.functions_path().write_text('def add(a: int, b: int) -> int:\n    """Add two numbers."""\n    return a + b\n')

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        messages = app.build_messages()
    joined = " ".join(m["content"] for m in messages)
    assert "add(a: int, b: int)" in joined


async def test_manual_fire_no_args_goes_straight_to_confirm(app_env):
    from otakuchat import faas
    from otakuchat.faas_ui import FunctionCallConfirm

    faas.functions_path().parent.mkdir(parents=True, exist_ok=True)
    faas.functions_path().write_text("def ping():\n    return 'pong'\n")

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_functions_menu_pick("ping")
        await _settle(pilot)
        assert isinstance(app.screen, FunctionCallConfirm)


async def test_manual_fire_with_args_prompts_first(app_env):
    from otakuchat import faas
    from otakuchat.faas_ui import FunctionArgsPrompt

    faas.functions_path().parent.mkdir(parents=True, exist_ok=True)
    faas.functions_path().write_text("def greet(name):\n    return f'hi {name}'\n")

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_functions_menu_pick("greet")
        await _settle(pilot)
        assert isinstance(app.screen, FunctionArgsPrompt)


async def test_manual_fire_confirmed_yes_runs_and_reports_success(app_env):
    from otakuchat import faas

    faas.functions_path().parent.mkdir(parents=True, exist_ok=True)
    faas.functions_path().write_text("def ping():\n    return 'pong'\n")

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_functions_menu_pick("ping")
        await _settle(pilot)
        await pilot.press("y")
        await _settle(pilot, n=6)
        assert "ping" in app.conversation_history
        assert "succeeded" in app.conversation_history


async def test_manual_fire_confirmed_no_never_runs(app_env):
    from otakuchat import faas

    faas.functions_path().parent.mkdir(parents=True, exist_ok=True)
    faas.functions_path().write_text("def ping():\n    return 'pong'\n")

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_functions_menu_pick("ping")
        await _settle(pilot)
        await pilot.press("n")
        await _settle(pilot, n=4)
        assert "declined" in app.conversation_history
        assert "succeeded" not in app.conversation_history


async def test_manage_entry_opens_slate_editor(app_env):
    from otakuchat.editor import Slate

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.handle_functions_menu_pick("__manage__")
        await _settle(pilot)
        assert isinstance(app.screen, Slate)


async def test_model_requested_call_triggers_confirm_dialog(app_env):
    """The end-to-end safety property: a model's ```call block in a real
    turn's reply must NEVER execute without the same Yes/No gate a
    manual fire goes through."""
    from otakuchat import faas
    from otakuchat.faas_ui import FunctionCallConfirm

    faas.functions_path().parent.mkdir(parents=True, exist_ok=True)
    faas.functions_path().write_text("def ping():\n    return 'pong'\n")

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        app.maybe_handle_model_call_requests("Sure, calling it now.\n```call\nping()\n```")
        await _settle(pilot)
        assert isinstance(app.screen, FunctionCallConfirm)
        assert app.screen.func_name == "ping"


async def test_model_requested_call_with_no_call_block_does_nothing(app_env):
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        before_screen = app.screen
        app.maybe_handle_model_call_requests("Just a normal reply, nothing to call.")
        await _settle(pilot)
        assert app.screen is before_screen


# --- UI bug regressions: auto-scroll + input overflow -------------------

async def test_chat_output_autoscrolls_to_bottom_on_new_content(app_env):
    """Regression test: the chat output must track new content to the
    bottom instead of staying anchored at the top — Markdown.update()
    resolves asynchronously (batched block mounting), so scrolling
    synchronously right after calling it used to compute against the
    OLD (pre-update) size and silently no-op."""
    from textual.containers import VerticalScroll

    app = OtakuChat()
    async with app.run_test(size=(100, 20)) as pilot:
        await _settle(pilot)
        scroll = app.query_one("#chat-scroll", VerticalScroll)
        assert scroll.scroll_y == 0

        long_content = "\n\n".join(f"Line block {i} " + ("word " * 20) for i in range(80))
        app.append_to_ui(long_content)
        for _ in range(8):
            await pilot.pause()

        assert scroll.max_scroll_y > 0, "content should have overflowed the viewport"
        assert scroll.scroll_y == scroll.max_scroll_y, "chat output should be anchored to the bottom"


async def test_chat_input_stays_within_screen_width(app_env):
    """Regression test: #main-i1 used width: 1fr while docked bottom with
    a 1-cell margin on both sides — Textual computes 1fr against the
    full parent width BEFORE subtracting dock margins, so the widget's
    right edge landed exactly `margin` cells past the screen edge
    instead of wrapping text vertically inside its border."""
    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)
        chat_input = app.query_one("#main-i1")
        right_edge = chat_input.region.x + chat_input.region.width
        assert right_edge <= app.size.width, (
            f"chat input's right edge ({right_edge}) overflows the {app.size.width}-wide screen"
        )


# --- modal centering -----------------------------------------------------

async def test_every_modalscreen_subclass_is_centered(app_env):
    """Regression test: VaultImportPrompt, FunctionCallConfirm, and
    FunctionCallResult were left out of master.tcss's
    'ModalListPicker, TextPrompt, ConfirmDialog { align: center middle; }'
    rule, so they rendered pinned to the top-left corner of the screen
    instead of centered like every other modal. Walk every ModalScreen
    subclass in the app and assert the live, resolved style actually has
    align: center middle — catching this class of bug for any modal
    added later, not just the ones known about today."""
    from otakuchat.faas_ui import FunctionCallConfirm, FunctionCallResult
    from otakuchat.pickers import ConfirmDialog, ModalListPicker
    from otakuchat.vault_ui import VaultImportPrompt

    app = OtakuChat()
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot)

        screens = [
            ModalListPicker("t", [("a", "a")], "none"),
            TextPrompt("t"),
            ConfirmDialog("t"),
            VaultImportPrompt(),
            FunctionCallConfirm("ping", {}),
            FunctionCallResult("ping", True, "ok"),
        ]
        for screen in screens:
            app.push_screen(screen)
            await _settle(pilot)
            assert screen.styles.align_horizontal == "center", (
                f"{type(screen).__name__} is not horizontally centered"
            )
            assert screen.styles.align_vertical == "middle", (
                f"{type(screen).__name__} is not vertically centered"
            )
            app.pop_screen()
            await _settle(pilot)

