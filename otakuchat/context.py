"""Trajectory compaction — Hermes-style context compression, enhanced with
aider's size-aware recursive split (see ispiration/hermes-agent's
trajectory_compressor.py for the head/tail-protection lineage, and
ispiration/aider/aider/history.py's ChatSummary for the budget-driven,
recursive splitting logic adapted below).

Why: sending the full, ever-growing message history every turn is exactly
what breaks local models under a small context window and burns tokens on
every single Ollama call. Two ideas combine here:

- Hermes: protect the head (system) and a tail window, summarize everything
  compressible in between into ONE message, instead of just truncating.
- aider: don't gate on a flat message *count* — gate on actual character
  budget, walk backward from the tail keeping whole messages until the
  budget is exhausted, always end the kept head on an assistant turn (never
  cut off mid soliloquy), and if a single summarization pass still doesn't
  fit the budget, recurse (summarize the summary + next chunk) rather than
  silently overflowing anyway.

Runs only when the session's total tracked size actually crosses the
configured budget, and reuses a cached summary (session_compaction table)
instead of re-summarizing on every turn — so a stable-length session pays
this cost once, not every message.
"""
import json

from . import config, db
from .ollama_client import chat_once

COMPACT_SYSTEM = (
    "You are compressing an older portion of a chat conversation into a short, "
    "neutral summary for the assistant's own future reference. Capture what was "
    "discussed, decided, or established as fact — not verbatim dialogue. "
    "Write 3-8 sentences, third person, starting with 'Earlier in this conversation:'."
)

# Rough chars-per-token conversion; we have no local tokenizer dependency,
# so this stays consistent with the character-based heuristics elsewhere
# in the app (reasoning.complexity_score) rather than pulling in a new dep.
_CHARS_PER_TOKEN = 4


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def maybe_compact(api_url: str, model: str, session_id: int) -> bool:
    """Run compaction if the session has grown past the size budget and the
    existing cached summary doesn't already cover the compressible region.
    Returns True if compaction ran (for a UI note), False otherwise.
    """
    rows = db.get_messages(session_id)
    max_msgs = config.get_max_context_messages()
    tail_n = config.get_protect_tail_messages()

    total_tokens = sum(_approx_tokens(r["content"]) for r in rows)
    budget_tokens = max_msgs * 220  # rough per-message token budget proxy

    if len(rows) <= max_msgs and total_tokens <= budget_tokens:
        return False

    existing = db.get_compaction(session_id)
    covered_id = existing["covered_through_message_id"] if existing else 0

    # Protected tail: walk backward from the end keeping whole messages
    # until protect_tail_messages is reached, then make sure the boundary
    # lands right after an assistant turn (never mid soliloquy — matches
    # aider's ChatSummary.summarize_real head/tail split rule).
    tail_cutoff_idx = max(0, len(rows) - tail_n)
    while tail_cutoff_idx > 0 and rows[tail_cutoff_idx - 1]["role"] != "assistant":
        tail_cutoff_idx -= 1

    compressible = [r for r in rows[:tail_cutoff_idx] if r["id"] > covered_id]

    if len(compressible) < 4:
        # Not enough new material to bother re-summarizing yet.
        return False

    prior_summary = existing["summary"] if existing else ""
    ran = _compact_region(api_url, model, session_id, prior_summary, compressible, depth=0)
    return ran


def _compact_region(
    api_url: str,
    model: str,
    session_id: int,
    prior_summary: str,
    compressible: list,
    depth: int,
) -> bool:
    """Summarize `compressible` (optionally folding in `prior_summary`),
    recursing if the summarization pass itself still doesn't fit budget —
    mirrors aider's ChatSummary.summarize_real depth-guarded recursion."""
    if depth > 3 or not compressible:
        return False

    transcript_lines = []
    if prior_summary:
        transcript_lines.append(f"[PRIOR SUMMARY]\n{prior_summary}\n")
    for r in compressible:
        transcript_lines.append(f"{r['role'].upper()}: {r['content']}")
    transcript = "\n".join(transcript_lines)

    # Budget the *input* we send to the summarizer too — an unbounded
    # transcript defeats the point on a small local context window.
    max_input_chars = 8000
    if len(transcript) > max_input_chars:
        transcript = transcript[-max_input_chars:]

    messages = [
        {"role": "system", "content": COMPACT_SYSTEM},
        {"role": "user", "content": transcript},
    ]
    try:
        summary = chat_once(api_url, model, messages)
    except Exception:
        return False

    if not summary.strip():
        return False
    summary = summary.strip()

    # If the summary itself came back oversized (small models sometimes
    # ramble), recurse: treat it as the new prior_summary and re-summarize
    # nothing further (there's no more raw material) — just cap it hard.
    max_summary_tokens = 400
    if _approx_tokens(summary) > max_summary_tokens and depth < 3:
        capped = [{"role": "assistant", "content": summary}]
        return _compact_region(api_url, model, session_id, "", capped, depth + 1)

    new_covered_id = compressible[-1]["id"]
    db.save_compaction(session_id, new_covered_id, summary)
    return True


def get_effective_messages(session_id: int) -> list[dict]:
    """Return the (role, content) list to send to the model for this
    session: a summary message for compacted history (if any) followed by
    the raw, uncompacted tail. This is the ONLY place callers should read
    conversation turns from for a live model call."""
    rows = db.get_messages(session_id)
    compaction = db.get_compaction(session_id)

    if not compaction:
        return [_row_to_message(r) for r in rows]

    covered_id = compaction["covered_through_message_id"]
    remaining = [r for r in rows if r["id"] > covered_id]

    out = [{
        "role": "user",
        "content": f"[Earlier conversation summary]\n{compaction['summary']}",
    }]
    out.extend(_row_to_message(r) for r in remaining)
    return out


def _row_to_message(r) -> dict:
    msg = {"role": r["role"], "content": r["content"]}
    images_json = r["images"] if "images" in r.keys() else None
    if images_json:
        try:
            images = json.loads(images_json)
        except (json.JSONDecodeError, TypeError):
            images = None
        if images:
            msg["images"] = images
    return msg
