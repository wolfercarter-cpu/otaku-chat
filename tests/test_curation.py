"""Tests for otakuchat/curation.py — the shared side-call/JSON-extraction
plumbing behind memory.py's and snippets.py's self-curation passes."""
from unittest import mock

from otakuchat import curation


def test_extract_json_array_parses_a_clean_array():
    assert curation.extract_json_array('["a", "b"]') == ["a", "b"]


def test_extract_json_array_pulls_the_array_out_of_surrounding_prose():
    text = 'Sure, here you go:\n["fact one", "fact two"]\nHope that helps!'
    assert curation.extract_json_array(text) == ["fact one", "fact two"]


def test_extract_json_array_returns_empty_on_a_json_object_not_a_list():
    assert curation.extract_json_array('{"not": "a list"}') == []


def test_extract_json_array_returns_empty_on_non_json_garbage():
    assert curation.extract_json_array("not json at all") == []


def test_extract_json_array_returns_empty_for_the_documented_empty_response():
    assert curation.extract_json_array("[]") == []


def test_run_side_call_returns_none_for_no_turns():
    assert curation.run_side_call("http://x", "model", "sys", []) is None


def test_run_side_call_returns_none_for_turns_with_no_content():
    turns = [{"role": "user", "content": ""}]
    assert curation.run_side_call("http://x", "model", "sys", turns) is None


def test_run_side_call_returns_none_when_the_model_call_raises():
    with mock.patch("otakuchat.curation.chat_once", side_effect=RuntimeError("boom")):
        result = curation.run_side_call(
            "http://x", "model", "sys", [{"role": "user", "content": "hi"}]
        )
    assert result is None


def test_run_side_call_returns_the_raw_text_on_success():
    with mock.patch("otakuchat.curation.chat_once", return_value="raw response"):
        result = curation.run_side_call(
            "http://x", "model", "sys", [{"role": "user", "content": "hi"}]
        )
    assert result == "raw response"


def test_recent_turns_for_curation_filters_to_user_and_assistant(isolated_env):
    from otakuchat import db

    sid = db.create_session("s", "model")
    db.add_message(sid, "user", "hello")
    db.add_message(sid, "assistant", "hi there")

    turns = curation.recent_turns_for_curation(sid, interval=4)
    assert turns == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
