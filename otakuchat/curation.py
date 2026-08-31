"""Shared infrastructure behind OtakuChat's self-curating stores.

memory.py, facts.py, and snippets.py each maintain a store the model has
no write access to — the app periodically makes a hidden side-call asking
the model to propose additions, then validates/dedupes/writes the result
itself. This module holds the pieces that were identical across all of
them (transcript building, the side-call itself, and pulling a JSON array
out of a model response that may have wrapped it in prose) so each store
module only has to own what's actually specific to it: what a good
addition looks like, and how to render one back into a prompt.
"""
import json
import re

from . import db
from .ollama_client import chat_once


def extract_json_array(text: str) -> list:
    """Pull the first top-level JSON array out of a model response, even if
    the model wrapped it in prose it was told not to add. Returns [] on
    anything that isn't parseable JSON or isn't a list — callers validate/
    cast individual items themselves (facts want strings, snippets want
    dicts), so this stays agnostic about item shape."""
    text = text.strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def run_side_call(
    api_url: str, model: str, system_prompt: str, turns: list[dict],
    max_transcript_chars: int = 6000,
) -> str | None:
    """Ask the model a structured question about recent conversation turns,
    hidden from the visible chat. Returns the raw response text, or None if
    there's nothing worth asking about or the call fails — curation is
    always best-effort and must never break a real chat turn."""
    if not turns:
        return None

    transcript = "\n".join(
        f"{t['role'].upper()}: {t['content']}" for t in turns if t.get("content")
    )
    if not transcript.strip():
        return None

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": transcript[-max_transcript_chars:]},
    ]
    try:
        return chat_once(api_url, model, messages)
    except Exception:
        return None


def recent_turns_for_curation(session_id: int, interval: int) -> list[dict]:
    """The last `interval * 2` messages of a session, reshaped for a
    side-call transcript. Shared window so every periodic curation pass
    (memory, snippets) looks at the same slice of recent conversation."""
    all_msgs = db.get_messages(session_id)
    recent = all_msgs[-(interval * 2):]
    return [
        {"role": m["role"], "content": m["content"]}
        for m in recent if m["role"] in ("user", "assistant")
    ]
