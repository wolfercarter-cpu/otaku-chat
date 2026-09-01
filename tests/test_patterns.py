"""Tests for otakuchat/patterns.py — the curated Fabric-style prompt
pattern library. Includes a regression test for a real path-traversal
vulnerability found during a stability audit: /pattern <name> fed a raw
chat-input string straight into a filesystem path with no containment
check, so `/pattern ../../../../etc/some_file` (or any .md file reachable
via ../ sequences) could read arbitrary files outside PATTERNS_DIR.
"""
import os

from otakuchat import patterns


def test_list_patterns_returns_the_bundled_library():
    names = patterns.list_patterns()
    assert len(names) > 5
    assert names == sorted(names)


def test_get_pattern_returns_text_for_a_real_pattern():
    names = patterns.list_patterns()
    text = patterns.get_pattern(names[0])
    assert text is not None
    assert len(text) > 0


def test_get_pattern_returns_none_for_a_missing_pattern():
    assert patterns.get_pattern("this_pattern_does_not_exist_xyz") is None


def test_describe_falls_back_to_the_name_when_pattern_missing():
    assert patterns.describe("missing_pattern_xyz") == "missing_pattern_xyz"


def test_describe_returns_a_short_line_for_a_real_pattern():
    names = patterns.list_patterns()
    desc = patterns.describe(names[0], max_chars=40)
    assert len(desc) <= 40


# --- path traversal regression -------------------------------------------

def test_get_pattern_blocks_traversal_to_a_real_file_outside_the_dir(tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("TOP SECRET DATA")
    traversal_name = os.path.relpath(
        str(secret)[: -len(".md")], start=str(patterns.PATTERNS_DIR)
    )
    assert patterns.get_pattern(traversal_name) is None


def test_get_pattern_rejects_forward_slash():
    assert patterns.get_pattern("sub/dir") is None


def test_get_pattern_rejects_backslash():
    assert patterns.get_pattern("sub\\dir") is None


def test_get_pattern_rejects_dot_and_dotdot():
    assert patterns.get_pattern(".") is None
    assert patterns.get_pattern("..") is None


def test_get_pattern_rejects_empty_name():
    assert patterns.get_pattern("") is None
