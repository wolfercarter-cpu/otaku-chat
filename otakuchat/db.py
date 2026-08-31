"""SQLite-backed store: sessions, messages, curated memory, and per-model
performance stats used to self-tune the reasoning booster.

Everything here is app-owned I/O — the model never gets a tool to touch
this database directly. It only ever sees curated text handed to it in
the system prompt.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    boosted INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

CREATE TABLE IF NOT EXISTS perf_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    prompt_chars INTEGER NOT NULL,
    boosted INTEGER NOT NULL,
    latency_s REAL NOT NULL,
    -- crude self-reported quality signal: -1 (user reacted negatively /
    -- retried), 0 (neutral), +1 (user moved on / positive) — updated
    -- heuristically, see reasoning.py
    feedback INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- Sessions ---------------------------------------------------------

def create_session(title: str, model: str) -> int:
    now = time.time()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (title, model, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (title, model, now, now),
        )
        return cur.lastrowid


def touch_session(session_id: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (time.time(), session_id)
        )


def list_sessions(limit: int = 25) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()


def get_session(session_id: int) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()


def rename_session(session_id: int, title: str) -> None:
    with db() as conn:
        conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))


# --- Messages -----------------------------------------------------------

def add_message(session_id: int, role: str, content: str, boosted: bool = False) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, boosted, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, int(boosted), time.time()),
        )
    touch_session(session_id)
    return cur.lastrowid


def get_messages(session_id: int) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)
        ).fetchall()


# --- Performance stats (fuel for the self-tuning booster) ---------------

def record_perf(model: str, prompt_chars: int, boosted: bool, latency_s: float) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO perf_stats (model, prompt_chars, boosted, latency_s, feedback, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (model, prompt_chars, int(boosted), latency_s, time.time()),
        )
        return cur.lastrowid


def set_feedback(perf_id: int, feedback: int) -> None:
    with db() as conn:
        conn.execute("UPDATE perf_stats SET feedback = ? WHERE id = ?", (feedback, perf_id))


def recent_perf(model: str, limit: int = 40) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM perf_stats WHERE model = ? ORDER BY id DESC LIMIT ?",
            (model, limit),
        ).fetchall()


# --- Curated memory facts ------------------------------------------------

def add_fact(fact: str) -> bool:
    """Insert a fact if new. Returns True if it was actually added."""
    fact = fact.strip()
    if not fact:
        return False
    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO memory_facts (fact, created_at) VALUES (?, ?)",
                (fact, time.time()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def list_facts() -> list[str]:
    with db() as conn:
        rows = conn.execute("SELECT fact FROM memory_facts ORDER BY id ASC").fetchall()
    return [r["fact"] for r in rows]


def remove_fact(fact: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM memory_facts WHERE fact = ?", (fact,))


def prune_oldest_facts(keep: int) -> None:
    with db() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM memory_facts ORDER BY id ASC")]
        if len(ids) > keep:
            to_remove = ids[: len(ids) - keep]
            conn.executemany(
                "DELETE FROM memory_facts WHERE id = ?", [(i,) for i in to_remove]
            )
