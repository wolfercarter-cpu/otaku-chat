"""Pattern library — curated prompt templates you can apply to your next
message instead of hand-writing a system instruction every time.

Adapted from Fabric (danielmiessler/fabric)'s `data/patterns/`: each pattern
is a plain markdown system prompt (`patterns/<name>.md`) originally designed
to be piped stdin content through. Fabric ships 256 of these; otaku-chat
carries a curated ~26-pattern subset chosen for general chat/dev use
(summarize, extract_wisdom, review_code, explain_code, translate, ...) to
keep the library small and legible rather than a 256-entry wall.

No shell involved: patterns are just static markdown read from disk and
folded into a single turn's system role alongside the normal system prompt.
"""
from pathlib import Path

PATTERNS_DIR = Path(__file__).parent / "patterns"


def list_patterns() -> list[str]:
    """Names of all available patterns, sorted."""
    if not PATTERNS_DIR.is_dir():
        return []
    return sorted(p.stem for p in PATTERNS_DIR.glob("*.md"))


def get_pattern(name: str) -> str | None:
    """Return a pattern's system-prompt text, or None if it doesn't exist."""
    path = PATTERNS_DIR / f"{name}.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def describe(name: str, max_chars: int = 100) -> str:
    """A short one-line description for a picker list, pulled from the
    pattern's own IDENTITY/PURPOSE header when present."""
    text = get_pattern(name) or ""
    for line in text.splitlines():
        line = line.strip("# ").strip()
        if line and "IDENTITY" not in line.upper() and "PURPOSE" not in line.upper():
            return line[:max_chars]
    return name
