"""Tests for otakuchat/search.py — the optional Brave Web Search client.

Includes a regression test for a real gap found during a stability audit:
a mid-response disconnect (http.client.IncompleteRead, which is NOT a
subclass of OSError) used to slip past `except (URLError, OSError)` and
propagate as a raw exception instead of the expected SearchError. This
mattered specifically because app.py's maybe_web_search only catches
search.SearchError (not a broad Exception), and runs before app.py's
per-turn try/except even starts — an unwrapped exception here would have
crashed the background worker outright.
"""
import http.client
from unittest import mock

import pytest

from otakuchat import search


def test_search_web_wraps_a_url_error():
    with mock.patch("urllib.request.urlopen", side_effect=OSError("network down")):
        with pytest.raises(search.SearchError):
            search.search_web("query", "fake-key")


def test_search_web_wraps_a_mid_stream_incomplete_read():
    with mock.patch("urllib.request.urlopen", side_effect=http.client.IncompleteRead(b"")):
        with pytest.raises(search.SearchError):
            search.search_web("query", "fake-key")


def test_search_web_wraps_malformed_json():
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = b"not json"
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(search.SearchError):
            search.search_web("query", "fake-key")


def test_search_web_returns_parsed_results_on_success():
    payload = {
        "web": {
            "results": [
                {"title": "T1", "url": "https://x", "description": "D1"},
                {"title": "T2", "url": "https://y", "description": "D2"},
            ]
        }
    }
    import json

    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = json.dumps(payload).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        results = search.search_web("query", "fake-key", max_results=5)
    assert results == [
        {"title": "T1", "url": "https://x", "description": "D1"},
        {"title": "T2", "url": "https://y", "description": "D2"},
    ]


def test_format_results_empty_list_returns_empty_string():
    assert search.format_results("query", []) == ""


def test_format_results_includes_query_and_entries():
    results = [{"title": "T", "url": "https://x", "description": "D"}]
    text = search.format_results("my query", results)
    assert "my query" in text
    assert "https://x" in text
    assert "T" in text
