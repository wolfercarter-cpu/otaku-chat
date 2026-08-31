"""Trajectory compaction — Hermes-style context compression, adapted for a
local chat harness (see ispiration/hermes-agent/trajectory_compressor.py
and app.py's old compress_trajectory_bg for the lineage).

Why: sending the full, ever-growing message history every turn is exactly
what breaks local models under a small context window and burns tokens on
every single Ollama call. Hermes's fix — protect the head (system) and a
tail window, summarize everything compressible in between into ONE
message — is exactly right for us too, just simplified since we have no
tool-call turns to preserve.

Runs only when the session's message count actually crosses the
configured threshold, and reuses a cached summary (session_compaction
table) instead of re-summarizing on every turn — so a stable-length
session pays this cost once, not every message.
"""
from . import config, db
from .ollama_client import chat_once

COMPACT_SYSTEM = (
    "You are compressing an older portion of a chat conversation into a short, "
    "neutral summary for the assistant's own future reference. Capture what was "
    "discussed, decided, or established as fact — not verbatim dialogue. "
    "Write 3-8 sentences, third person, starting with 'Earlier in this conversation:'."
)


def maybe_compact(api_url: str, model: str, session_id: int) -> bool:
    """Run compaction if the session has grown past the threshold and the
    existing cached summary doesn't already cover the compressible region.
    Returns True if compaction ran (for a UI note), False otherwise.
    """
    rows = db.get_messages(session_id)
    max_msgs = config.get_max_context_messages()
    tail_n = config.get_protect_tail_messages()

    if len(rows) <= max_msgs:
        return False

    existing = db.get_compaction(session_id)
    covered_id = existing["covered_through_message_id"] if existing else 0

    # Compressible region: everything except the protected tail, and not
    # already covered by a prior summary.
    tail_cutoff_idx = max(0, len(rows) - tail_n)
    compressible = [r for r in rows[:tail_cutoff_idx] if r["id"] > covered_id]

    if len(compressible) < 4:
        # Not enough new material to bother re-summarizing yet.
        return False

    prior_summary = existing["summary"] if existing else ""
    transcript_lines = []
    if prior_summary:
        transcript_lines.append(f"[PRIOR SUMMARY]\n{prior_summary}\n")
    for r in compressible:
        transcript_lines.append(f"{r['role'].upper()}: {r['content']}")
    transcript = "\n".join(transcript_lines)[-8000:]

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

    new_covered_id = compressible[-1]["id"]
    db.save_compaction(session_id, new_covered_id, summary.strip())
    return True


def get_effective_messages(session_id: int) -> list[dict]:
    """Return the (role, content) list to send to the model for this
    session: a summary message for compacted history (if any) followed by
    the raw, uncompacted tail. This is the ONLY place callers should read
    conversation turns from for a live model call."""
    rows = db.get_messages(session_id)
    compaction = db.get_compaction(session_id)

    if not compaction:
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    covered_id = compaction["covered_through_message_id"]
    remaining = [r for r in rows if r["id"] > covered_id]

    out = [{
        "role": "user",
        "content": f"[Earlier conversation summary]\n{compaction['summary']}",
    }]
    out.extend({"role": r["role"], "content": r["content"]} for r in remaining)
    return out
