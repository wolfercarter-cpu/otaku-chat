"""Self-curating code-snippet library (SNIPPETS.md).

Same pattern as memory.py: the app periodically asks the model, in a
hidden structured side-call, to propose reusable snippets from recent
turns, then the app itself validates/dedupes/writes them — the model
never gets write access. See curation.py for the side-call/JSON-extraction
plumbing this shares with memory.py's self-curation pass.

Retrieval is always strictly relevance-gated (db.relevant_snippets) so a
snippet saved for one task never gets replayed into an unrelated turn —
see db._rank_by_relevance's fallback_to_recent=False. This is the
difference from memory.py's curated facts, which are general enough to
always include when the store is small; a snippet is only ever useful
when it actually matches what's being asked.
"""
import datetime

from . import config, curation, db
from .fileio import locked_atomic_write
from .redact import redact_secrets
from .threats import scan_for_threats

CURATION_SYSTEM = (
    "You extract reusable code snippets worth saving from a conversation for "
    "future reuse. A snippet is worth saving only if it's a general-purpose "
    "helper/pattern likely to come up again — not a one-off answer specific "
    "to this exact task. Respond with ONLY a JSON array of objects, each "
    '{"title": short unique name, "tags": comma-separated keywords, '
    '"language": e.g. python, "code": the snippet, "note": one-line note on '
    "when/why to use it}. If nothing is worth saving, respond with exactly: []"
)


def curate_from_turns(api_url: str, model: str, turns: list[dict]) -> list[str]:
    """Ask the model to propose reusable snippets from a slice of
    conversation. Returns the titles of snippets actually written/updated."""
    raw = curation.run_side_call(api_url, model, CURATION_SYSTEM, turns)
    if raw is None:
        return []

    max_code_chars = config.get_max_snippet_code_chars()
    added = []
    for item in curation.extract_json_array(raw):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        code = str(item.get("code", "")).strip()
        if not title or not code or len(code) > max_code_chars:
            continue
        tags = str(item.get("tags", "")).strip()
        language = str(item.get("language", "")).strip()
        note = str(item.get("note", "")).strip()
        code, _ = redact_secrets(code)  # mask a leaked secret before the threat scan sees it
        if scan_for_threats(f"{title} {tags} {note} {code}"):
            # A snippet's code/note is rendered verbatim into a future
            # system prompt — same reasoning as memory.py: don't let an
            # injection payload (e.g. a code comment engineered to look
            # like an instruction) get filed away as "reusable". Runs
            # AFTER redaction so a real secret gets masked-and-kept
            # rather than rejected outright (redact_secrets' masked form
            # never matches the injection patterns below).
            continue
        db.add_snippet(title, tags, language, code, note)
        added.append(title)

    db.prune_unused_snippets(keep=config.get_max_snippets_stored())
    return added


def render_snippets_block(query: str, max_items: int | None = None) -> str:
    """Render only the snippets actually relevant to `query` — never the
    whole library. Also mirrors the full library to SNIPPETS.md so it
    stays reviewable/hand-editable like MEMORY.md."""
    if db.count_snippets() == 0:
        return ""

    _mirror_to_disk()

    if not query or not query.strip():
        return ""
    matches = db.relevant_snippets(query, limit=max_items or config.get_snippets_results_per_turn())
    if not matches:
        return ""

    for m in matches:
        db.touch_snippet(m["id"])

    parts = []
    for m in matches:
        block = f"## {m['title']}\n```{m['language']}\n{m['code']}\n```"
        if m["note"]:
            block += f"\n{m['note']}"
        parts.append(block)
    return "## Relevant saved snippets\n\n" + "\n\n".join(parts) + "\n"


def _fmt_time(ts: float | None) -> str:
    if not ts:
        return "never"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _mirror_to_disk() -> None:
    all_snippets = db.list_snippets()
    lines = ["# Snippets", ""]
    for s in all_snippets:
        lines.append(f"## {s['title']}")
        lines.append(f"tags: {s['tags']}")
        lines.append(f"last_used: {_fmt_time(s['last_used'])}")
        lines.append("")
        lines.append(f"```{s['language']}")
        lines.append(s["code"])
        lines.append("```")
        if s["note"]:
            lines.append(s["note"])
        lines.append("")
        lines.append("---")
        lines.append("")
    if not all_snippets:
        lines.append("(Nothing here yet.)")
    try:
        locked_atomic_write(config.get_snippets_path(), "\n".join(lines))
    except OSError:
        pass


def maybe_curate(api_url: str, model: str, session_id: int, turns_since_last: int) -> list[str]:
    """Called after each assistant turn, same cadence as memory.maybe_curate
    (config.MEMORY.curation_interval_turns) — runs on the most recent slice
    of the conversation."""
    interval = config.get_curation_interval()
    if turns_since_last < interval:
        return []

    turns = curation.recent_turns_for_curation(session_id, interval)
    return curate_from_turns(api_url, model, turns)
