"""Self-curating topic -> URL bookmark store (FACTS.md).

No LLM side-call needed here, unlike memory.py/snippets.py: it's fed
directly by the existing Brave web-search grounding (search.py) — whenever
a turn's search actually returns results, the query itself becomes the
topic and the top few result URLs get filed away, deduped by (topic, url).

Retrieval is always strictly relevance-gated (db.relevant_topic_links) so
a bookmark saved for one topic never gets replayed into an unrelated
turn — see db._rank_by_relevance's fallback_to_recent=False.
"""
from . import config, db
from .fileio import locked_atomic_write
from .threats import scan_for_threats


def curate_from_search(topic: str, results: list[dict]) -> list[str]:
    """Called right after a successful web search (see app.py
    OtakuChat.maybe_web_search). Files the top few result URLs under
    `topic` (the search query, as-is) and returns the URLs actually newly
    added (skips exact (topic, url) dupes).

    A search result's description is the least trusted text in this app
    — it's raw third-party web content that flows straight into a
    permanent, always-replayed bookmark file. Scanned for injection
    patterns before it's ever stored; a flagged result is skipped
    entirely (not partially stored with the description blanked) since a
    bookmark with no description is a URL with no useful context."""
    topic = topic.strip()
    if not topic or not results:
        return []

    save_top_n = config.get_facts_results_per_turn()
    added = []
    for r in results[:save_top_n]:
        url = (r.get("url") or "").strip()
        description = (r.get("description") or "").strip()
        if not url:
            continue
        if scan_for_threats(f"{topic} {description}"):
            continue
        if db.add_topic_link(topic, url, description):
            added.append(url)

    if added:
        db.prune_oldest_topic_links(keep=config.get_max_links_stored())
    return added


def render_facts_block(query: str, max_items: int | None = None) -> str:
    """Render only the bookmarks actually relevant to `query` — never the
    whole store. Also mirrors the full store to FACTS.md so it stays
    reviewable/hand-editable like MEMORY.md."""
    if db.count_topic_links() == 0:
        return ""

    _mirror_to_disk()

    if not query or not query.strip():
        return ""
    matches = db.relevant_topic_links(query, limit=max_items or config.get_facts_results_per_turn())
    if not matches:
        return ""

    for m in matches:
        db.touch_topic_link(m["id"])

    lines = [f"- [{m['topic']}] {m['url']} — {m['description']}" for m in matches]
    return "## Relevant bookmarked sources\n\n" + "\n".join(lines) + "\n"


def _mirror_to_disk() -> None:
    links = db.list_topic_links()
    lines = ["# Facts", ""]
    current_topic = None
    for link in links:
        if link["topic"] != current_topic:
            current_topic = link["topic"]
            lines.append(f"## {current_topic}")
        lines.append(f"- {link['url']} — {link['description']}")
    if not links:
        lines.append("(Nothing here yet.)")
    try:
        locked_atomic_write(config.get_facts_path(), "\n".join(lines) + "\n")
    except OSError:
        pass
