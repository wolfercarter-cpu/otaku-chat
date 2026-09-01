"""Tests for otakuchat/redact.py — best-effort secret redaction applied to
/add file ingestion and memory/facts/snippets curation, so a pasted API
key never becomes a permanent curated fact or a persisted attachment."""
from otakuchat.redact import redact_secrets


def test_redacts_a_known_vendor_prefix():
    text, count = redact_secrets("my key is sk-ant-abcdefghijklmnopqrstuvwxyz")
    assert count == 1
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz" not in text


def test_redacts_a_github_token():
    text, count = redact_secrets("token: ghp_abcdefghijklmnopqrstuvwxyz")
    assert count >= 1
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in text


def test_redacts_a_generic_key_value_pair():
    text, count = redact_secrets("api_key: abcdef1234567890")
    assert count == 1
    assert "abcdef1234567890" not in text
    assert "api_key" in text  # the label itself isn't secret, only the value


def test_leaves_ordinary_text_completely_untouched():
    original = "just a normal sentence about python packaging"
    text, count = redact_secrets(original)
    assert count == 0
    assert text == original


def test_empty_text_is_a_no_op():
    assert redact_secrets("") == ("", 0)


def test_short_token_is_fully_masked_not_partially_shown():
    text, count = redact_secrets("ghp_abcdefghij")
    assert count == 1
    assert "***REDACTED***" in text


def test_long_token_shows_a_partial_mask():
    long_token = "sk-" + "a" * 40
    text, count = redact_secrets(f"key={long_token}")
    assert count >= 1
    assert long_token not in text
    # partial mask keeps a short prefix/suffix for a long token
    assert text != "key=***REDACTED***"
