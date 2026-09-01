"""Tests for otakuchat/fileio.py — the file-lock + atomic-write helper
shared by memory.py/facts.py/snippets.py's mirror writes, config.py's
config.ini save, and editor.py's Slate save.
"""
from pathlib import Path

from otakuchat.fileio import file_lock, locked_atomic_write


def test_locked_atomic_write_creates_file_with_content(tmp_path):
    target = tmp_path / "out.txt"
    locked_atomic_write(target, "hello world")
    assert target.read_text() == "hello world"


def test_locked_atomic_write_overwrites_existing_content(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("old content")
    locked_atomic_write(target, "new content")
    assert target.read_text() == "new content"


def test_locked_atomic_write_creates_parent_dirs(tmp_path):
    target = tmp_path / "nested" / "dir" / "out.txt"
    locked_atomic_write(target, "content")
    assert target.read_text() == "content"


def test_locked_atomic_write_leaves_no_temp_file_behind(tmp_path):
    target = tmp_path / "out.txt"
    locked_atomic_write(target, "content")
    remaining = {p.name for p in tmp_path.iterdir()}
    # the .lock sidecar is expected/persistent; the mkstemp .tmp scratch
    # file must not survive the write
    assert remaining == {"out.txt", "out.txt.lock"}
    assert not any(name.endswith(".tmp") for name in remaining)


def test_locked_atomic_write_leaves_no_lock_file_visible_as_content(tmp_path):
    target = tmp_path / "out.txt"
    locked_atomic_write(target, "content")
    lock_path = Path(str(target) + ".lock")
    # the lock sidecar may exist (it's reused across calls), but must
    # never be mistaken for real content
    assert target.read_text() == "content"
    if lock_path.exists():
        # the lock file itself should never contain the written text
        assert lock_path.read_text() != "content"


def test_file_lock_is_reentrant_safe_sequential_calls(tmp_path):
    target = tmp_path / "out.txt"
    with file_lock(target):
        pass
    with file_lock(target):
        pass  # a second sequential acquire must not deadlock


def test_concurrent_writes_do_not_corrupt_the_file(tmp_path):
    """Two writers racing for the same file: the lock serializes them so
    the final content is one writer's full text, never an interleaved
    mix of both."""
    import threading

    target = tmp_path / "out.txt"
    text_a = "A" * 5000
    text_b = "B" * 5000

    def write_a():
        locked_atomic_write(target, text_a)

    def write_b():
        locked_atomic_write(target, text_b)

    t1 = threading.Thread(target=write_a)
    t2 = threading.Thread(target=write_b)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    final = target.read_text()
    assert final == text_a or final == text_b
