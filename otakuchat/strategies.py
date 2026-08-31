"""Reasoning strategy library — short, battle-tested "how to think about
this" instructions, adapted from Fabric (danielmiessler/fabric)'s
`data/strategies/`: chain-of-thought, tree-of-thought, self-refine,
reflexion, self-consistency, chain-of-draft, atom-of-thought,
least-to-most, and a plain "standard" no-op baseline.

reasoning.py's boost layer picks ONE of these automatically per prompt
(heuristic, not user-selectable — see should_boost()/pick_strategy()) and
folds its `prompt` text in as extra guidance for the boosted pass, instead
of always running the same fixed draft->critique->refine dance regardless
of what kind of ask it is.
"""
import json
import re
from pathlib import Path

STRATEGIES_DIR = Path(__file__).parent / "strategies"

_CODE_HINTS = re.compile(r"```|def |class |import |function |SELECT |sudo |systemctl |\{|\}|;\n")
_DESIGN_HINTS = re.compile(r"\b(design|architect|approach|options?|alternative|compare|trade-?off)\b", re.I)
_STEP_HINTS = re.compile(r"\b(step|first|then|after that|finally|plan)\b", re.I)
_BRIEF_HINTS = re.compile(r"\b(tl;?dr|briefly|one line|short answer|quick)\b", re.I)

_cache: dict[str, dict] | None = None


def _load_all() -> dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    strategies = {}
    if STRATEGIES_DIR.is_dir():
        for path in STRATEGIES_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            strategies[path.stem] = data
    _cache = strategies
    return strategies


def get_strategy(name: str) -> dict | None:
    return _load_all().get(name)


def pick_strategy(prompt: str) -> str:
    """Cheap heuristic mapping a prompt to the Fabric reasoning strategy
    that best fits its shape. Falls back to 'self-refine' (closest to
    otaku-chat's original hardcoded draft->critique->refine behavior)
    when nothing more specific matches."""
    available = _load_all()
    if not available:
        return "standard"

    if _BRIEF_HINTS.search(prompt) and "cod" in available:
        return "cod"  # chain-of-draft: minimal steps, fast
    if _DESIGN_HINTS.search(prompt) and "tot" in available:
        return "tot"  # tree-of-thought: multiple approaches, pick best
    if _CODE_HINTS.search(prompt) and "self-refine" in available:
        return "self-refine"  # draft + critique + fix, good for code
    if len(_STEP_HINTS.findall(prompt)) >= 2 and "ltm" in available:
        return "ltm"  # least-to-most: break into ordered sub-problems
    if prompt.count("?") >= 2 and "self-consistent" in available:
        return "self-consistent"  # multiple reasoning paths, pick consistent
    if "cot" in available:
        return "cot"  # default: plain chain-of-thought
    return next(iter(available), "standard")
