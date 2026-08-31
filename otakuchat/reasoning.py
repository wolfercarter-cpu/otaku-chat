"""The aggregation / self-boost reasoning layer.

This is the piece that makes an underpowered local model (llama3.2:3b,
qwen2.5-coder:7b, ...) punch above its weight: a draft -> self-critique ->
refine pass, run with the *same* local model, reserved for prompts that
actually look like they need it.

It's adaptive, not a dumb on/off switch:
  - a cheap heuristic estimates prompt "complexity" (length, code content,
    multi-step asks, question density)
  - mode=auto compares that score to a per-install, self-tuned threshold
  - after each turn we log (model, prompt size, boosted?, latency) to
    perf_stats; if the user's *next* message reads like a correction
    ("no that's wrong", "that's not right", "try again", ...) we treat the
    prior turn as negative feedback
  - periodically (memory.maybe_curate hook) we nudge the threshold down
    when un-boosted turns are drawing corrections, and nudge it up when
    boosted turns aren't buying anything (to stop wasting latency)

No shell involved anywhere in this file — it only ever calls the Ollama
chat API and writes to the local db/config.
"""
import re
import time
from dataclasses import dataclass
from typing import Callable

from . import config, db
from .ollama_client import chat_once, chat_stream

CODE_HINTS = re.compile(r"```|def |class |import |function |SELECT |sudo |systemctl |\{|\}|;\n")
STEP_HINTS = re.compile(r"\b(step|first|then|after that|finally|plan|design|architect|debug|refactor)\b", re.I)
CORRECTION_HINTS = re.compile(
    r"\b(no[,.]|nope|that'?s wrong|not right|incorrect|try again|actually[, ]|doesn'?t work|"
    r"didn'?t work|wrong answer|that's not it|redo|fix that)\b",
    re.I,
)

CRITIQUE_SYSTEM = (
    "You are a terse internal reviewer. You will be shown a user request and a draft "
    "answer to it from another instance of yourself. Find concrete mistakes, gaps, or "
    "weak spots ONLY. Do not restate the draft. Do not praise it. If it is already "
    "correct and complete, reply with exactly: OK\n"
    "Otherwise reply with a short bullet list of fixes needed."
)

REFINE_SYSTEM_SUFFIX = (
    "\n\nYou previously drafted an answer to the user's message and a reviewer found "
    "issues with it. Write the final, corrected answer to the user directly — do not "
    "mention the draft, the review, or this process. Just give the improved answer."
)


def complexity_score(prompt: str) -> int:
    """Cheap 0-ish..N heuristic score for how much a prompt likely benefits
    from a draft/critique/refine pass rather than a single fast shot."""
    score = len(prompt)
    if CODE_HINTS.search(prompt):
        score += 150
    step_hits = len(STEP_HINTS.findall(prompt))
    score += step_hits * 60
    score += prompt.count("?") * 20
    if len(prompt.splitlines()) > 3:
        score += 80
    return score


def looks_like_correction(text: str) -> bool:
    return bool(CORRECTION_HINTS.search(text.strip()[:120]))


def should_boost(model: str, prompt: str) -> bool:
    mode = config.get_boost_mode()
    if mode == "always":
        return True
    if mode == "off":
        return False
    threshold = config.get_complexity_threshold()
    return complexity_score(prompt) >= threshold


@dataclass
class BoostResult:
    content: str
    boosted: bool
    latency_s: float
    perf_id: int


def run_turn(
    api_url: str,
    model: str,
    messages: list[dict],
    on_status: Callable[[str], None],
    on_token: Callable[[str], None],
) -> BoostResult:
    """Run one assistant turn, transparently boosting if warranted.

    messages: full running conversation ending in the latest user turn.
    on_status: fired with short progress notes ("(thinking harder...)").
    on_token: fired with streamed text chunks of the FINAL visible answer.
    """
    user_prompt = messages[-1]["content"] if messages and messages[-1]["role"] == "user" else ""
    boosted = should_boost(model, user_prompt)
    start = time.time()

    if not boosted:
        final = chat_stream(api_url, model, messages, on_token)
        latency = time.time() - start
        perf_id = db.record_perf(model, len(user_prompt), False, latency)
        return BoostResult(final.get("content", ""), False, latency, perf_id)

    # --- Boosted path: draft -> critique -> refine, same model throughout ---
    on_status("(drafting...)")
    draft = chat_once(api_url, model, messages)

    on_status("(reviewing draft...)")
    critique_messages = [
        {"role": "system", "content": CRITIQUE_SYSTEM},
        {
            "role": "user",
            "content": f"USER REQUEST:\n{user_prompt}\n\nDRAFT ANSWER:\n{draft}",
        },
    ]
    critique = chat_once(api_url, model, critique_messages)

    if critique.strip().upper().startswith("OK"):
        # Draft already holds up — stream it out as-is (re-stream so the UI
        # gets that live-typing feel even though we already have the text).
        for ch in draft:
            on_token(ch)
        final_content = draft
    else:
        on_status("(refining...)")
        refine_messages = list(messages) + [
            {"role": "assistant", "content": draft},
            {
                "role": "user",
                "content": (
                    f"A reviewer found these issues with your draft:\n{critique}"
                    + REFINE_SYSTEM_SUFFIX
                ),
            },
        ]
        final_msg = chat_stream(api_url, model, refine_messages, on_token)
        final_content = final_msg.get("content", "") or draft

    latency = time.time() - start
    perf_id = db.record_perf(model, len(user_prompt), True, latency)
    return BoostResult(final_content, True, latency, perf_id)


def apply_implicit_feedback(prev_perf_id: int | None, new_user_message: str) -> None:
    """If the user's new message reads like a correction, mark the previous
    turn's perf record negative so the self-tuner can react to it."""
    if prev_perf_id is None:
        return
    if looks_like_correction(new_user_message):
        db.set_feedback(prev_perf_id, -1)
    else:
        db.set_feedback(prev_perf_id, 1)


def self_tune_threshold(model: str) -> str | None:
    """Nudge the complexity threshold based on recent perf/feedback history.

    - Un-boosted turns drawing corrections -> lower the threshold (boost more).
    - Boosted turns still drawing corrections, or boosted turns with no
      corrections happening broadly anyway -> raise it slightly (boost less,
      save latency) since the extra pass isn't clearly paying for itself.

    Returns a short human-readable note if it changed anything, else None.
    """
    rows = db.recent_perf(model, limit=40)
    if len(rows) < 6:
        return None

    threshold = config.get_complexity_threshold()
    unboosted_bad = sum(1 for r in rows if not r["boosted"] and r["feedback"] == -1)
    unboosted_total = sum(1 for r in rows if not r["boosted"])
    boosted_bad = sum(1 for r in rows if r["boosted"] and r["feedback"] == -1)
    boosted_total = sum(1 for r in rows if r["boosted"])

    unboosted_bad_rate = unboosted_bad / unboosted_total if unboosted_total else 0.0
    boosted_bad_rate = boosted_bad / boosted_total if boosted_total else 0.0

    step = 25
    note = None
    if unboosted_bad_rate > 0.25 and threshold > 60:
        threshold = max(60, threshold - step)
        note = f"Boosting more often for {model} (unboosted corrections were frequent)."
    elif boosted_total >= 4 and boosted_bad_rate <= unboosted_bad_rate and threshold < 900:
        threshold = min(900, threshold + step)
        note = f"Boosting less often for {model} (extra pass wasn't buying accuracy)."

    if note:
        config.save_complexity_threshold(threshold)
    return note
