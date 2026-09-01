"""Tests for otakuchat/youtube.py — transcript fetch (via
youtube-transcript-api, the one deliberate non-stdlib dependency in this
project — see youtube.py's module docstring for why) + summarize/explain.

These hit the real network/YouTube for the id-extraction and mocked-fetch
paths; the actual fetch_transcript() call against YouTube itself is
exercised live in module smoke-testing during development (confirmed
working against api real videos), not re-verified on every test run here
to keep the suite offline-friendly and fast — everything below mocks
YouTubeTranscriptApi.fetch and ollama_client.chat_once.
"""
from unittest import mock

import pytest

from otakuchat import youtube


class _FakeSnippet:
    def __init__(self, text):
        self.text = text


class _FakeFetchedTranscript:
    def __init__(self, snippets):
        self.snippets = snippets


# --- extract_video_id --------------------------------------------------

def test_extract_video_id_from_bare_id():
    assert youtube.extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_from_watch_url():
    assert youtube.extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_from_watch_url_with_extra_params():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s&list=PLxyz"
    assert youtube.extract_video_id(url) == "dQw4w9WgXcQ"


def test_extract_video_id_from_youtu_be_short_link():
    assert youtube.extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_from_embed_url():
    assert youtube.extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_from_shorts_url():
    assert youtube.extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_raises_on_garbage():
    with pytest.raises(youtube.YouTubeError):
        youtube.extract_video_id("not a youtube url at all")


# --- fetch_transcript (mocked) ------------------------------------------

def test_fetch_transcript_joins_snippets(isolated_env):
    fake_transcript = _FakeFetchedTranscript([
        _FakeSnippet("Hello"), _FakeSnippet("world."), _FakeSnippet("  ")
    ])
    with mock.patch.object(youtube.YouTubeTranscriptApi, "fetch", return_value=fake_transcript):
        text = youtube.fetch_transcript("dQw4w9WgXcQ", use_cache=False)
    assert text == "Hello world."


def test_fetch_transcript_redacts_secrets(isolated_env):
    fake_transcript = _FakeFetchedTranscript([
        _FakeSnippet('api_key: "sk-ant-abcdefghijklmnopqrstuv123"')
    ])
    with mock.patch.object(youtube.YouTubeTranscriptApi, "fetch", return_value=fake_transcript):
        text = youtube.fetch_transcript("dQw4w9WgXcQ", use_cache=False)
    assert "sk-ant-abcdefghijklmnopqrstuv123" not in text


def test_fetch_transcript_raises_on_empty_result(isolated_env):
    fake_transcript = _FakeFetchedTranscript([_FakeSnippet("   ")])
    with mock.patch.object(youtube.YouTubeTranscriptApi, "fetch", return_value=fake_transcript):
        with pytest.raises(youtube.YouTubeError):
            youtube.fetch_transcript("dQw4w9WgXcQ", use_cache=False)


def test_fetch_transcript_wraps_api_exceptions(isolated_env):
    from youtube_transcript_api._errors import YouTubeTranscriptApiException

    with mock.patch.object(
        youtube.YouTubeTranscriptApi, "fetch",
        side_effect=YouTubeTranscriptApiException("no captions"),
    ):
        with pytest.raises(youtube.YouTubeError):
            youtube.fetch_transcript("dQw4w9WgXcQ", use_cache=False)


def test_fetch_transcript_uses_cache_on_second_call(isolated_env):
    fake_transcript = _FakeFetchedTranscript([_FakeSnippet("cached text")])
    with mock.patch.object(
        youtube.YouTubeTranscriptApi, "fetch", return_value=fake_transcript
    ) as mocked_fetch:
        first = youtube.fetch_transcript("dQw4w9WgXcQ", use_cache=True)
        second = youtube.fetch_transcript("dQw4w9WgXcQ", use_cache=True)
    assert first == second == "cached text"
    mocked_fetch.assert_called_once()


# --- summarize_transcript (mocked) ---------------------------------------

def test_summarize_transcript_calls_ollama_with_transcript(isolated_env):
    fake_transcript = _FakeFetchedTranscript([_FakeSnippet("This video is about testing.")])
    with mock.patch.object(youtube.YouTubeTranscriptApi, "fetch", return_value=fake_transcript):
        with mock.patch.object(youtube.ollama_client, "chat_once", return_value="A summary.") as mocked_chat:
            result = youtube.summarize_transcript("dQw4w9WgXcQ", "http://localhost:11434", "llama3.2:3b")
    assert result == "A summary."
    call_kwargs = mocked_chat.call_args
    sent_messages = call_kwargs.args[2] if len(call_kwargs.args) > 2 else call_kwargs.kwargs["messages"]
    assert "This video is about testing." in sent_messages[0]["content"]


def test_summarize_transcript_raises_youtube_error_on_ollama_failure(isolated_env):
    from otakuchat.ollama_client import OllamaError

    fake_transcript = _FakeFetchedTranscript([_FakeSnippet("content")])
    with mock.patch.object(youtube.YouTubeTranscriptApi, "fetch", return_value=fake_transcript):
        with mock.patch.object(youtube.ollama_client, "chat_once", side_effect=OllamaError("down")):
            with pytest.raises(youtube.YouTubeError):
                youtube.summarize_transcript("dQw4w9WgXcQ", "http://localhost:11434", "llama3.2:3b")
