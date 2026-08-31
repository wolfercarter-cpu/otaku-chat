"""Self-curating persistent memory, modeled on Hermes's memory tool.

Difference from Hermes: the model never calls a "memory" tool. Instead the
app periodically asks the model (in a hidden, structured side-call) to
propose durable facts worth remembering from the recent conversation, then
the app itself validates/dedupes/writes them. The model has zero write
access of its own — it only ever sees the curated result show back up in
its next system prompt.
"""
import json
import re

from . import config, db
from .ollama_client import chat_once

CURATION_SYSTEM = (
    "You extract durable, reusable facts from a conversation for long-term memory. "
    "A durable fact is a stable preference, correction, identity detail, or environment "
    "fact that will still be true and useful in future unrelated conversations. "
    "Do NOT include one-off task details, or anything already obvious/trivial. "
    "Respond with ONLY a JSON array of short strings, each a single self-contained "
    "fact written in third person (e.g. \"User prefers dark themes\"). "
    "If there is nothing durable worth remembering, respond with exactly: []"
)


def _extract_json_array(text: str) -> list[str]:
    text = text.strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if isinstance(x, (str, int, float)) and str(x).strip()]


def curate_from_turns(api_url: str, model: str, turns: list[dict]) -> list[str]:
    """Ask the model to propose durable facts from a slice of conversation.
    Returns the list of NEW facts actually written to the store."""
    if not turns:
        return []

    transcript = "\n".join(
        f"{t['role'].upper()}: {t['content']}" for t in turns if t.get("content")
    )
    if not transcript.strip():
        return []

    messages = [
        {"role": "system", "content": CURATION_SYSTEM},
        {"role": "user", "content": transcript[-6000:]},
    ]
    try:
        raw = chat_once(api_url, model, messages)
    except Exception:
        return []

    candidates = _extract_json_array(raw)
    added = []
    for fact in candidates:
        fact = fact.strip().strip("-* ")
        if not fact or len(fact) > 300:
            continue
        if db.add_fact(fact):
            added.append(fact)

    # Keep the store bounded
    db.prune_oldest_facts(keep=200)
    return added


def render_memory_block(max_chars: int | None = None) -> str:
    """Render curated facts as a compact block for injection into the
    system prompt. Also mirrors to the on-disk MEMORY.md so /memory can
    show/edit it like a normal file."""
    max_chars = max_chars or config.get_max_memory_chars()
    facts = db.list_facts()
    if not facts:
        return ""

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

    all_msgs = db.get_messages(session_id)
    recent = all_msgs[-(interval * 2):]
    turns = [{"role": m["role"], "content": m["content"]} for m in recent if m["role"] in ("user", "assistant")]

    added = curate_from_turns(api_url, model, turns)
    render_memory_block()
    return added
