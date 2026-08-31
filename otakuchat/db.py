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
from typing import Iterator

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
    -- JSON array of base64-encoded image bytes, Ollama's own multimodal
    -- message format (see ollama_client.py). NULL/empty for normal turns.
    images TEXT,
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

-- FACTS.md backing store: URLs a web search actually returned for a topic,
-- so a recurring question can be pointed at a known-good source instead of
-- re-searching cold. See otakuchat/facts.py.
CREATE TABLE IF NOT EXISTS topic_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    url TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    last_used REAL,
    UNIQUE(topic, url)
);

-- SNIPPETS.md backing store: reusable code the model curates from
-- conversation, same self-curation pattern as memory_facts. See
-- otakuchat/snippets.py.
CREATE TABLE IF NOT EXISTS snippets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL UNIQUE,
    tags TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    code TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    last_used REAL
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
        # Lightweight migration for DBs created before the `images` column
        # existed — CREATE TABLE IF NOT EXISTS above won't add it to an
        # already-existing messages table.
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
        if "images" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN images TEXT")


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

def add_message(
    session_id: int, role: str, content: str, boosted: bool = False,
    images: list[str] | None = None,
) -> int:
    images_json = json.dumps(images) if images else None
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, boosted, images, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content, int(boosted), images_json, time.time()),
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
    "her their our as by from "
    # wh-question / functional words — topics and queries stored verbatim
    # (facts.py, snippets.py) are often phrased as questions, so without
    # these a single shared "what"/"how" was enough to false-positive
    # match two otherwise unrelated topics (see db.relevant_topic_links).
    "what who how when where why which whom whose do does did doing "
    "can could would will shall should might must if so than then there "
    "here about into onto out up down over under again further once all "
    "any both each few more most other some such no nor only own same too "
    "very just because while during before after above below between "
    "am s t d m re ve ll don isn aren wasn weren hasn haven hadn wouldn "
    "couldn shouldn won ain".split()
)


def _tokenize(text: str) -> set[str]:
    # len(w) > 1 drops lone contraction remnants regex splits out around
    # apostrophes (e.g. "what's" -> "what", "s") that _STOPWORDS above
    # doesn't already special-case.
    return {
        w for w in re.findall(r"[a-z0-9]+", text.lower())
        if len(w) > 1 and w not in _STOPWORDS
    }


def _rank_by_relevance(
    query: str, items: list, text_of, limit: int, fallback_to_recent: bool = True,
) -> list:
    """Shared Jaccard token-overlap ranking — adapted (numpy/FTS5/trust-score
    machinery stripped out) from hermes-agent's holographic memory provider
    (plugins/memory/holographic/retrieval.py). Used by relevant_facts,
    relevant_topic_links, and relevant_snippets so there's one ranking
    implementation instead of one per store.

    fallback_to_recent=True (memory facts): degrade to the most-recent
    `limit` items when the query has no usable tokens or nothing scores
    above zero, so retrieval degrades to "recency", never to nothing —
    appropriate for facts, which are meant to always be present-ish.

    fallback_to_recent=False (topic links, snippets): return [] instead in
    that case. These are topic-specific reference material, not general
    facts about the user — an unrelated query should surface nothing
    rather than an arbitrary recent bookmark/snippet getting replayed into
    a turn it has nothing to do with.
    """
    if not items:
        return []
    if fallback_to_recent and len(items) <= limit:
        return items

    query_tokens = _tokenize(query)
    if not query_tokens:
        return items[-limit:] if fallback_to_recent else []

    scored = []
    for item in items:
        item_tokens = _tokenize(text_of(item))
        if not item_tokens:
            continue
        overlap = query_tokens & item_tokens
        union = query_tokens | item_tokens
        jaccard = len(overlap) / len(union) if union else 0.0
        if jaccard > 0:
            scored.append((jaccard, item))

    if not scored:
        return items[-limit:] if fallback_to_recent else []

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


def relevant_facts(query: str, limit: int = 12) -> list[str]:
    """Rank curated facts by relevance to `query` instead of always
    returning the whole store. See render_memory_block's docstring
    (memory.py) for why this only kicks in above RELEVANCE_THRESHOLD."""
    return _rank_by_relevance(query, list_facts(), lambda f: f, limit)


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


# --- Topic -> URL bookmarks (FACTS.md backing store) ---------------------

def add_topic_link(topic: str, url: str, description: str = "") -> bool:
    """Insert a (topic, url) pair if new. Returns True if actually added —
    False on an exact dupe (UNIQUE(topic, url))."""
    topic = topic.strip()
    url = url.strip()
    if not topic or not url:
        return False
    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO topic_links (topic, url, description, created_at) "
                "VALUES (?, ?, ?, ?)",
                (topic, url, description.strip(), time.time()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def list_topic_links() -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM topic_links ORDER BY topic ASC, id ASC"
        ).fetchall()


def count_topic_links() -> int:
    with db() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM topic_links").fetchone()
    return row["n"] if row else 0


def relevant_topic_links(query: str, limit: int = 3) -> list[sqlite3.Row]:
    """Strictly relevance-gated (see _rank_by_relevance's
    fallback_to_recent=False) — an unrelated query gets nothing back
    rather than an arbitrary bookmark."""
    links = list_topic_links()
    return _rank_by_relevance(
        query, links, lambda r: f"{r['topic']} {r['description']}", limit,
        fallback_to_recent=False,
    )


def touch_topic_link(link_id: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE topic_links SET last_used = ? WHERE id = ?", (time.time(), link_id)
        )


def prune_oldest_topic_links(keep: int) -> None:
    with db() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM topic_links ORDER BY id ASC")]
        if len(ids) > keep:
            to_remove = ids[: len(ids) - keep]
            conn.executemany(
                "DELETE FROM topic_links WHERE id = ?", [(i,) for i in to_remove]
            )


# --- Code snippets (SNIPPETS.md backing store) ----------------------------

def add_snippet(title: str, tags: str, language: str, code: str, note: str = "") -> bool:
    """Insert a new snippet, or update one in place if `title` already
    exists — supersede rather than accumulate near-duplicates (see
    SNIPPETS.md's curation policy). Returns True only for a brand-new
    title."""
    title = title.strip()
    code = code.strip()
    if not title or not code:
        return False
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM snippets WHERE title = ?", (title,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE snippets SET tags = ?, language = ?, code = ?, note = ? "
                "WHERE id = ?",
                (tags.strip(), language.strip(), code, note.strip(), existing["id"]),
            )
            return False
        conn.execute(
            "INSERT INTO snippets (title, tags, language, code, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title, tags.strip(), language.strip(), code, note.strip(), time.time()),
        )
        return True


def list_snippets() -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute("SELECT * FROM snippets ORDER BY id ASC").fetchall()


def count_snippets() -> int:
    with db() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM snippets").fetchone()
    return row["n"] if row else 0


def relevant_snippets(query: str, limit: int = 2) -> list[sqlite3.Row]:
    """Strictly relevance-gated, same reasoning as relevant_topic_links —
    a snippet saved months ago for an unrelated task should never get
    replayed into today's turn just because the store isn't empty."""
    snippets = list_snippets()
    return _rank_by_relevance(
        query, snippets, lambda r: f"{r['title']} {r['tags']}", limit,
        fallback_to_recent=False,
    )


def touch_snippet(snippet_id: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE snippets SET last_used = ? WHERE id = ?", (time.time(), snippet_id)
        )


def prune_unused_snippets(keep: int) -> None:
    """Drop the least-recently-used snippets once the store exceeds `keep`
    — ranked by last_used (falling back to created_at for ones never
    surfaced), not insertion order, so snippets that keep proving useful
    survive over ones that don't."""
    with db() as conn:
        ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM snippets ORDER BY COALESCE(last_used, created_at) ASC"
            )
        ]
        if len(ids) > keep:
            to_remove = ids[: len(ids) - keep]
            conn.executemany("DELETE FROM snippets WHERE id = ?", [(i,) for i in to_remove])
