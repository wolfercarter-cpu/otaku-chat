"""The aggregation / self-boost reasoning layer.

This is the piece that makes an underpowered local model (llama3.2:3b,
qwen2.5-coder:7b, deepseek-r1, ...) punch above its weight — via TWO
strategies, chosen automatically per model:

1. Native thinking (preferred when available): some Ollama models
   (deepseek-r1, qwen3, gpt-oss, ...) support server-side chain-of-thought
   via `think: true`. When get_capabilities() reports this, we just ask
   for it directly — one call, the model reasons before answering, and we
   stream the reasoning into the UI as a collapsible/dim "thinking" block.
   Cheaper AND smarter than faking it externally.

2. External draft -> self-critique -> refine (fallback): for models with
   no native reasoning mode (llama3.2, qwen2.5-coder, ...), we simulate the
   same effect by running the model against itself three times with the
   same weights: draft, critique the draft, refine using the critique.

Both paths are adaptive, not a dumb on/off switch:
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
from .ollama_client import chat_once, chat_stream, get_capabilities
from . import strategies as strategy_lib

CODE_HINTS = re.compile(r"```|def |class |import |function |SELECT |sudo |systemctl |\{|\}|;\n")
STEP_HINTS = re.compile(r"\b(step|first|then|after that|finally|plan|design|architect|debug|refactor)\b", re.I)
CORRECTION_HINTS = re.compile(
    r"\b(no[,.]|nope|that'?s wrong|not right|incorrect|try again|actually[, ]|doesn'?t work|"
    r"didn'?t work|wrong answer|that's not it|redo|fix that)\b",
    re.I,
)

# Some Ollama model templates (chat-template quirks, not the native `thinking`
# API field) leak chain-of-thought as literal <think>...</think> tags inside
# `content` instead of the separate `thinking` field the API exposes for
# well-behaved models. Adapted from aider's reasoning_tags.py: strip these
# out of what the user sees, and surface the extracted content the same way
# native thinking is surfaced, so the visible answer is always just the
# answer regardless of which mechanism a given model/template uses.
_INLINE_THINK_TAGS = ("think", "thinking", "reasoning")
_INLINE_THINK_RE = re.compile(
    r"<(" + "|".join(_INLINE_THINK_TAGS) + r")>(.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)
_INLINE_THINK_UNCLOSED_RE = re.compile(
    r"<(" + "|".join(_INLINE_THINK_TAGS) + r")>(.*)$",
    re.IGNORECASE | re.DOTALL,
)


def split_inline_thinking(text: str) -> tuple[str, str]:
    """Extract any <think>/<thinking>/<reasoning> tagged content from a
    model's raw output. Returns (visible_content, extracted_thinking).

    Handles a still-open tag (mid-stream/truncated) by treating everything
    after the opening tag as thinking, since a real answer never starts
    mid-reasoning-block.
    """
    if "<" not in text:
        return text, ""

    thoughts = []

    def _collect(match: re.Match) -> str:
        thoughts.append(match.group(2).strip())
        return ""

    cleaned = _INLINE_THINK_RE.sub(_collect, text)

    unclosed = _INLINE_THINK_UNCLOSED_RE.search(cleaned)
    if unclosed:
        thoughts.append(unclosed.group(2).strip())
        cleaned = cleaned[: unclosed.start()]

    return cleaned.strip(), "\n\n".join(t for t in thoughts if t)


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
    from a boosted reasoning pass rather than a single fast shot."""
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
    strategy: str  # "none" | "native_thinking" | "draft_critique_refine"
    thinking: str = ""
    substrategy: str = ""  # Fabric-derived reasoning strategy name, e.g. "cot", "tot"


def run_turn(
    api_url: str,
    model: str,
    messages: list[dict],
    on_status: Callable[[str], None],
    on_token: Callable[[str], None],
    on_thinking: Callable[[str], None] | None = None,
) -> BoostResult:
    """Run one assistant turn, transparently boosting if warranted.

    messages: full running conversation ending in the latest user turn.
    on_status: fired with short progress notes ("(thinking harder...)").
    on_token: fired with streamed text chunks of the FINAL visible answer.
    on_thinking: fired with streamed native chain-of-thought chunks, when
      the model natively supports it and a boost is warranted.
    """
    user_prompt = messages[-1]["content"] if messages and messages[-1]["role"] == "user" else ""
    boosted = should_boost(model, user_prompt)
    start = time.time()
    options = config.get_generation_options()

    if not boosted:
        final = chat_stream(api_url, model, messages, on_token, options=options)
        latency = time.time() - start
        perf_id = db.record_perf(model, len(user_prompt), False, latency)
        content, leaked_thinking = split_inline_thinking(final.get("content", ""))
        return BoostResult(content, False, latency, perf_id, "none", thinking=leaked_thinking)

    caps = get_capabilities(api_url, model)

    # Auto-pick a Fabric-derived reasoning strategy for this prompt (cot,
    # tot, self-refine, ltm, cod, self-consistent, ...) and fold its short
    # instruction in as extra system guidance for whichever engine below
    # actually runs — this is what varies HOW the model thinks, while the
    # native-thinking-vs-draft/critique/refine choice below stays the
    # mechanism for WHETHER an extra reasoning pass happens at all.
    strat_name = strategy_lib.pick_strategy(user_prompt)
    strat = strategy_lib.get_strategy(strat_name)
    if strat and strat.get("prompt"):
        messages = list(messages) + [{"role": "system", "content": strat["prompt"]}]

    # --- Strategy 1: native server-side thinking (cheap, 1 call) ---
    if caps.get("thinking"):
        on_status(f"(thinking, {strat_name}...)")
        final = chat_stream(
            api_url, model, messages, on_token, on_thinking=on_thinking,
            think=True, options=options,
        )
        latency = time.time() - start
        perf_id = db.record_perf(model, len(user_prompt), True, latency)
        content, leaked_thinking = split_inline_thinking(final.get("content", ""))
        thinking = final.get("thinking", "") or leaked_thinking
        return BoostResult(
            content, True, latency, perf_id,
            "native_thinking", thinking=thinking, substrategy=strat_name,
        )

    # --- Strategy 2: draft -> critique -> refine, same model throughout ---
    on_status(f"(drafting, {strat_name}...)")
    draft = chat_once(api_url, model, messages, options=options)

    on_status("(reviewing draft...)")
    critique_messages = [
        {"role": "system", "content": CRITIQUE_SYSTEM},
        {
            "role": "user",
            "content": f"USER REQUEST:\n{user_prompt}\n\nDRAFT ANSWER:\n{draft}",
        },
    ]
    # Deliberately NOT passing the user's generation options to the
    # critique pass — it's an internal quality check, not a visible answer,
    # and benefits from staying close to the model's own defaults rather
    # than inheriting e.g. a high user-set temperature meant for creative
    # final answers.
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
        final_msg = chat_stream(api_url, model, refine_messages, on_token, options=options)
        final_content = final_msg.get("content", "") or draft

    latency = time.time() - start
    perf_id = db.record_perf(model, len(user_prompt), True, latency)
    final_content, leaked_thinking = split_inline_thinking(final_content)
    return BoostResult(
        final_content, True, latency, perf_id, "draft_critique_refine",
        thinking=leaked_thinking, substrategy=strat_name,
    )


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
