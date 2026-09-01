"""Tests for otakuchat/db.py, particularly:

- _rank_by_relevance's strict-gating mode (topic_links/snippets never fall
  back to an arbitrary recent item for an unrelated query, unlike curated
  memory facts which intentionally do)
- the stopword-list fix: an un-stopworded "what" used to false-positive
  match two completely unrelated topics purely because both were phrased
  as questions
- ON DELETE CASCADE actually removing messages/compaction when a session
  is deleted
- add_snippet's supersede-in-place behavior (never accumulate duplicates
  for the same title)
"""
import pytest

from otakuchat import db


@pytest.fixture(autouse=True)
def _fresh_db(isolated_env):
    pass


# --- curated memory facts (fallback_to_recent=True) -----------------------

def test_relevant_facts_falls_back_to_recency_when_nothing_matches():
    for i in range(25):
        db.add_fact(f"filler fact number {i}")
    result = db.relevant_facts("completely unrelated query about spaceships", limit=3)
    assert len(result) == 3  # recency fallback, never empty for memory facts


def test_relevant_facts_returns_everything_when_store_is_small():
    db.add_fact("User prefers dark themes")
    db.add_fact("User is a Python developer")
    assert set(db.relevant_facts("anything", limit=12)) == {
        "User prefers dark themes", "User is a Python developer",
    }


def test_add_fact_rejects_exact_duplicate():
    assert db.add_fact("a fact") is True
    assert db.add_fact("a fact") is False
    assert db.count_facts() == 1


def test_prune_oldest_facts_keeps_only_the_newest():
    for i in range(10):
        db.add_fact(f"fact {i}")
    db.prune_oldest_facts(keep=3)
    remaining = db.list_facts()
    assert remaining == ["fact 7", "fact 8", "fact 9"]


# --- topic links / snippets (fallback_to_recent=False) --------------------

def test_relevant_topic_links_returns_nothing_for_an_unrelated_query():
    db.add_topic_link("ollama capabilities", "https://ollama.com/library", "model tags")
    assert db.relevant_topic_links("banana bread recipe") == []


def test_relevant_topic_links_matches_a_related_query():
    db.add_topic_link("ollama capabilities", "https://ollama.com/library", "model tags")
    matches = db.relevant_topic_links("tell me about ollama capabilities")
    assert len(matches) == 1
    assert matches[0]["url"] == "https://ollama.com/library"


def test_relevant_topic_links_stays_empty_even_with_a_tiny_store():
    """The strict-gating mode must never fall back to 'dump everything',
    even when the store is far below the requested limit."""
    db.add_topic_link("topic one", "https://example.com/1")
    assert db.relevant_topic_links("something totally unrelated", limit=10) == []


def test_stopword_regression_what_does_not_false_positive_match():
    """Regression test for a real bug found during a stability audit: two
    completely unrelated topics used to match purely because both were
    phrased as questions containing the word 'what' — 'what' wasn't in the
    stopword list, so it counted as genuine topical overlap."""
    db.add_topic_link(
        "what ollama models support tool calling?",
        "https://ollama.com/docs/api",
        "chat/show endpoints",
    )
    assert db.relevant_topic_links("what's a good recipe for banana bread?") == []


def test_relevant_snippets_strictly_gated():
    db.add_snippet("regex helper", "python,regex", "python", "import re", "note")
    assert db.relevant_snippets("what's the weather today") == []
    matches = db.relevant_snippets("I need a python regex helper")
    assert len(matches) == 1
    assert matches[0]["title"] == "regex helper"


def test_add_snippet_supersedes_in_place_instead_of_duplicating():
    assert db.add_snippet("atomic write", "python", "python", "code v1", "n1") is True
    assert db.add_snippet("atomic write", "python,updated", "python", "code v2", "n2") is False
    assert db.count_snippets() == 1
    row = db.list_snippets()[0]
    assert row["code"] == "code v2"
    assert row["tags"] == "python,updated"


def test_add_topic_link_rejects_exact_topic_url_duplicate():
    assert db.add_topic_link("topic", "https://example.com") is True
    assert db.add_topic_link("topic", "https://example.com") is False
    assert db.count_topic_links() == 1


def test_prune_unused_snippets_keeps_most_recently_used():
    db.add_snippet("old unused", "tag", "py", "code", "")
    db.add_snippet("recently used", "tag", "py", "code", "")
    row = db.list_snippets()[1]
    db.touch_snippet(row["id"])

    db.prune_unused_snippets(keep=1)
    remaining = db.list_snippets()
    assert len(remaining) == 1
    assert remaining[0]["title"] == "recently used"


# --- sessions / cascade delete ---------------------------------------------

def test_delete_session_cascades_messages_and_compaction():
    sid = db.create_session("test", "model")
    db.add_message(sid, "user", "hello")
    db.add_message(sid, "assistant", "hi")
    db.save_compaction(sid, 1, "a summary")

    db.delete_session(sid)

    assert db.get_session(sid) is None
    assert db.get_messages(sid) == []
    assert db.get_compaction(sid) is None


def test_delete_session_leaves_other_sessions_untouched():
    keep = db.create_session("keep me", "model")
    doomed = db.create_session("delete me", "model")
    db.add_message(keep, "user", "hello")

    db.delete_session(doomed)

    assert db.get_session(keep) is not None
    assert len(db.get_messages(keep)) == 1


def test_rename_session_updates_title():
    sid = db.create_session("old title", "model")
    db.rename_session(sid, "new title")
    assert db.get_session(sid)["title"] == "new title"


# --- input history ----------------------------------------------------------

def test_input_history_skips_immediate_duplicate():
    db.add_input_history("hello")
    db.add_input_history("hello")
    assert db.get_input_history() == ["hello"]


def test_input_history_keeps_non_consecutive_repeats():
    db.add_input_history("a")
    db.add_input_history("b")
    db.add_input_history("a")
    assert db.get_input_history() == ["a", "b", "a"]
