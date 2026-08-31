"""Self-curating persistent memory, modeled on Hermes's memory tool.

Difference from Hermes: the model never calls a "memory" tool. Instead the
app periodically asks the model (in a hidden, structured side-call) to
propose durable facts worth remembering from the recent conversation, then
the app itself validates/dedupes/writes them. The model has zero write
access of its own — it only ever sees the curated result show back up in
its next system prompt. See curation.py for the side-call/JSON-extraction
plumbing this shares with snippets.py's self-curation pass.
"""
from . import config, curation, db
from .redact import redact_secrets

# Below this many curated facts, always include the full store unranked
# (keeps the system prompt byte-stable for KV-cache reuse). Above it,
# render_memory_block ranks facts by relevance to the current query
# instead — see its docstring.
RELEVANCE_THRESHOLD = 20

CURATION_SYSTEM = (
    "You extract durable, reusable facts from a conversation for long-term memory. "
    "A durable fact is a stable preference, correction, identity detail, or environment "
    "fact that will still be true and useful in future unrelated conversations. "
    "Do NOT include one-off task details, or anything already obvious/trivial. "
    "Respond with ONLY a JSON array of short strings, each a single self-contained "
    "fact written in third person (e.g. \"User prefers dark themes\"). "
    "If there is nothing durable worth remembering, respond with exactly: []"
)


def curate_from_turns(api_url: str, model: str, turns: list[dict]) -> list[str]:
    """Ask the model to propose durable facts from a slice of conversation.
    Returns the list of NEW facts actually written to the store."""
    raw = curation.run_side_call(api_url, model, CURATION_SYSTEM, turns)
    if raw is None:
        return []

    added = []
    for item in curation.extract_json_array(raw):
        if not isinstance(item, (str, int, float)):
            continue
        fact = str(item).strip().strip("-* ")
        if not fact or len(fact) > 300:
            continue
        fact, _ = redact_secrets(fact)  # never let a leaked secret become a permanent fact
        if db.add_fact(fact):
            added.append(fact)

    # Keep the store bounded
    db.prune_oldest_facts(keep=200)
    return added


def render_memory_block(query: str | None = None, max_chars: int | None = None) -> str:
    """Render curated facts as a compact block for injection into the
    system prompt. Also mirrors to the on-disk MEMORY.md so /memory can
    show/edit it like a normal file.

    When the store is small, every fact is always included (unranked,
    insertion order) — this keeps the system prompt byte-stable turn to
    turn so Ollama can reuse its KV cache, matching Hermes's "prompt
    caching is sacred" rule. Once the fact count crosses
    RELEVANCE_THRESHOLD, dumping everything into every prompt stops
    scaling (irrelevant facts crowd the budget and dilute attention), so
    facts are instead ranked by Jaccard token overlap against `query`
    (db.relevant_facts, ported from hermes-agent's holographic memory
    provider) and only the top N are included. This deliberately trades
    cache-prefix stability for relevance once the store is big enough
    that relevance actually matters more than a stable prefix.
    """
    max_chars = max_chars or config.get_max_memory_chars()
    total = db.count_facts()
    if total == 0:
        return ""

    if query and total > RELEVANCE_THRESHOLD:
        facts = db.relevant_facts(query, limit=RELEVANCE_THRESHOLD)
    else:
        facts = db.list_facts()

    lines = [f"- {f}" for f in facts]
    block = "## Curated Memory\n\n" + "\n".join(lines) + "\n"
    if len(block) > max_chars:
        # Keep the most recent facts (tail) within budget
        while len(block) > max_chars and lines:
            lines.pop(0)
            block = "## Curated Memory\n\n" + "\n".join(lines) + "\n"

    try:
        with open(config.get_memory_path(), "w") as f:
            f.write(block)
    except OSError:
        pass

    return block


def maybe_curate(api_url: str, model: str, session_id: int, turns_since_last: int) -> list[str]:
    """Called after each assistant turn. Runs curation every N turns
    (config.MEMORY.curation_interval_turns) using only the most recent
    slice of the conversation, then re-renders the memory block."""
    interval = config.get_curation_interval()
    if turns_since_last < interval:
        return []

    turns = curation.recent_turns_for_curation(session_id, interval)
    added = curate_from_turns(api_url, model, turns)
    render_memory_block()
    return added
