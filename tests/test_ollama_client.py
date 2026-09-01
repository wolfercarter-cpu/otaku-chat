"""Tests for otakuchat/ollama_client.py — the local Ollama HTTP client.

Includes the same http.client.HTTPException regression as test_search.py:
a mid-response disconnect isn't a subclass of OSError, so it used to slip
past the exception handling in list_models/chat_stream/chat_once/
get_capabilities and propagate raw instead of becoming a clean OllamaError
(or, for get_capabilities, the documented all-False fallback).
"""
import http.client
from unittest import mock

import pytest

from otakuchat import ollama_client as oc


def test_list_models_wraps_a_url_error():
    with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
        with pytest.raises(oc.OllamaError):
            oc.list_models("http://x")


def test_list_models_wraps_a_mid_stream_incomplete_read():
    with mock.patch("urllib.request.urlopen", side_effect=http.client.IncompleteRead(b"")):
        with pytest.raises(oc.OllamaError):
            oc.list_models("http://x")


def test_is_reachable_is_false_on_failure():
    with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
        assert oc.is_reachable("http://x") is False


def test_is_reachable_is_true_on_success():
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = b'{"models": []}'
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        assert oc.is_reachable("http://x") is True


def test_get_capabilities_never_raises_on_incomplete_read():
    with mock.patch("urllib.request.urlopen", side_effect=http.client.IncompleteRead(b"")):
        caps = oc.get_capabilities("http://x", "some-model")
    assert caps == {"thinking": False, "tools": False, "vision": False}


def test_get_capabilities_never_raises_on_malformed_json():
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = b"not json"
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        caps = oc.get_capabilities("http://x", "another-model")
    assert caps == {"thinking": False, "tools": False, "vision": False}


def test_get_capabilities_parses_real_capabilities():
    import json

    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = json.dumps(
        {"capabilities": ["thinking", "tools"]}
    ).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        caps = oc.get_capabilities("http://x", "capable-model")
    assert caps == {"thinking": True, "tools": True, "vision": False}


def test_get_capabilities_is_cached_per_model():
    import json

    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = json.dumps({"capabilities": ["vision"]}).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp) as urlopen:
        oc.get_capabilities("http://x", "cached-model")
        oc.get_capabilities("http://x", "cached-model")
    assert urlopen.call_count == 1


def test_chat_once_wraps_network_failure():
    with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
        with pytest.raises(oc.OllamaError):
            oc.chat_once("http://x", "model", [{"role": "user", "content": "hi"}])


def test_chat_once_returns_stripped_content():
    import json

    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = json.dumps(
        {"message": {"content": "  hello  "}}
    ).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        result = oc.chat_once("http://x", "model", [{"role": "user", "content": "hi"}])
    assert result == "hello"
