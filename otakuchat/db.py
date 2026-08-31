"""SQLite-backed store: sessions, messages, curated memory, and per-model
performance stats used to self-tune the reasoning booster.

Everything here is app-owned I/O — the model never gets a tool to touch
this database directly. It only ever sees curated text handed to it in
the system prompt.
"""
import json
import re
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

CREATE TABLE IF NOT EXISTS session_compaction (
    session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    -- id of the last raw message folded into `summary` (exclusive of
    -- everything after it, which is sent to the model uncompressed)
    covered_through_message_id INTEGER NOT NULL,
    summary TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS input_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
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


def count_facts() -> int:
    with db() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM memory_facts").fetchone()
    return row["n"] if row else 0


_STOPWORDS = frozenset(
    "a an the is are was were be been being of in on at to for with and or "
    "but not this that these those it its i you he she they we my your his "
    "her their our as by from".split()
)


def _tokenize(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9]+", text.lower())
        if w and w not in _STOPWORDS
    }


def relevant_facts(query: str, limit: int = 12) -> list[str]:
    """Rank curated facts by Jaccard token overlap with `query` instead of
    always returning the whole store — adapted (numpy/FTS5/trust-score
    machinery stripped out) from hermes-agent's holographic memory
    provider (plugins/memory/holographic/retrieval.py). otaku-chat's fact
    count is capped small (prune_oldest_facts) so a plain Python pass over
    every stored fact per turn is cheap; no virtual FTS5 table needed.

    Falls back to the most-recent `limit` facts if the query has no usable
    tokens (empty/short input) or nothing scores above zero — so relevant
    memory retrieval degrades to "recency", never to nothing.
    """
    facts = list_facts()
    if len(facts) <= limit:
        return facts

    query_tokens = _tokenize(query)
    if not query_tokens:
        return facts[-limit:]

    scored = []
    for fact in facts:
        fact_tokens = _tokenize(fact)
        if not fact_tokens:
            continue
        overlap = query_tokens & fact_tokens
        union = query_tokens | fact_tokens
        jaccard = len(overlap) / len(union) if union else 0.0
        if jaccard > 0:
            scored.append((jaccard, fact))

    if not scored:
        return facts[-limit:]

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [fact for _, fact in scored[:limit]]


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


# --- Session compaction (trajectory compression cache) -------------------

def get_compaction(session_id: int) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM session_compaction WHERE session_id = ?", (session_id,)
        ).fetchone()


def save_compaction(session_id: int, covered_through_message_id: int, summary: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO session_compaction (session_id, covered_through_message_id, summary, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "covered_through_message_id = excluded.covered_through_message_id, "
            "summary = excluded.summary, updated_at = excluded.updated_at",
            (session_id, covered_through_message_id, summary, time.time()),
        )


# --- Input history (up/down recall across all sessions, aider-style) -----

def add_input_history(text: str, limit: int = 500) -> None:
    text = text.strip()
    if not text:
        return
    with db() as conn:
        # Skip immediate duplicate of the very last entry
        last = conn.execute(
            "SELECT text FROM input_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last and last["text"] == text:
            return
        conn.execute(
            "INSERT INTO input_history (text, created_at) VALUES (?, ?)",
            (text, time.time()),
        )
        conn.execute(
            "DELETE FROM input_history WHERE id NOT IN "
            "(SELECT id FROM input_history ORDER BY id DESC LIMIT ?)",
            (limit,),
        )


def get_input_history(limit: int = 500) -> list[str]:
    """Oldest-first list of past inputs, ready for up/down recall."""
    with db() as conn:
        rows = conn.execute(
            "SELECT text FROM input_history ORDER BY id ASC LIMIT ?", (limit,)
        ).fetchall()
    return [r["text"] for r in rows]
