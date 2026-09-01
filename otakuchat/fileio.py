"""Shared file-locking + atomic-write helper for otaku-chat's on-disk
mirror files (MEMORY.md, FACTS.md, SNIPPETS.md, config.ini) and the
Slate editor's saves to those same paths.

Adapted (heavily trimmed) from Hermes-agent's memory_tool.py file-locking
pattern: a sidecar `.lock` file guarded with fcntl (POSIX) or msvcrt
(Windows), and the write itself goes to a temp file first with a final
os.replace() so a crash or a second writer mid-write can never leave a
torn/partial file on disk.

Otaku-chat's concurrency exposure is narrower than Hermes's (no tool-call
based writes, no multi-session RPC), but it's real: the periodic
self-curation side-call runs on a background worker thread
(app.py's @work(thread=True)) and can write MEMORY.md/FACTS.md/SNIPPETS.md
at the same moment the user has that same file open for hand-editing in
the Slate editor (or a second otaku-chat process is running against the
same ~/.local/share/otakuchat). Without this, one writer's save could
silently clobber the other's.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

msvcrt = None
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        pass


@contextmanager
def file_lock(path: str | Path) -> Iterator[None]:
    """Acquire an exclusive lock scoped to `path`, via a sidecar `.lock`
    file so the target itself can still be atomically replaced under the
    lock. No-op (no serialization) on a platform with neither fcntl nor
    msvcrt available — best-effort, matching this module's non-adversarial
    threat model."""
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if fcntl is None and msvcrt is None:
        yield
        return

    fd = open(lock_path, "a+", encoding="utf-8")
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        if fcntl:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        elif msvcrt:
            try:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        fd.close()


def locked_atomic_write(path: str | Path, text: str) -> None:
    """Write `text` to `path` under file_lock(), via a temp file +
    os.replace() so a concurrent reader never sees a partial write and a
    crash mid-write can't corrupt the target. Raises OSError on failure —
    callers decide whether that's fatal (Slate's explicit Ctrl+S save) or
    best-effort (the periodic mirror-to-disk renders in memory.py/
    facts.py/snippets.py, which already wrap this in try/except OSError)."""
    path = Path(path)
    with file_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
