"""Lightweight prompt-injection / exfiltration pattern scanning.

Adapted (trimmed hard) from Hermes-agent's tools/threat_patterns.py. Our
threat surface is much narrower than Hermes's: otaku-chat has no shell
tool, no skills install, no C2/Brainworm-style persistence concern — the
only way untrusted text reaches a permanent, replayed-forever system
prompt block here is via memory.py/snippets.py's self-curation side-calls
(model summarizes recent conversation -> app writes the result) and
facts.py's search-result descriptions (Brave API text). So this module
keeps only the patterns relevant to THAT surface: classic instruction-
override injection, system-prompt leak attempts, and secret-exfiltration
phrasing — dropped everything about SSH backdoors, C2 frameworks, agent
config file mutation, and env-var unsetting, since none of that can
happen here (no shell, no file-write tool, no skills).

Not a security boundary against a determined attacker (same caveat as
redact.py) — a best-effort net so a poisoned conversation or a malicious
search result can't quietly become a permanent, always-injected memory
entry.
"""
import re
import unicodedata

# Bounded filler between key attack words — mirrors Hermes's own
# reasoning: unbounded `(?:\w+\s+)*` can backtrack heavily on adversarial
# near-misses, a handful of filler words is enough for the intended
# obfuscation bypasses.
_FILLER = r"(?:\w+\s+){0,8}"

_PATTERNS: list[tuple[str, str]] = [
    (rf"ignore\s+{_FILLER}(previous|all|above|prior)\s+{_FILLER}instructions", "prompt_injection"),
    (r"system\s+prompt\s+override", "sys_prompt_override"),
    (rf"disregard\s+{_FILLER}(your|all|any)\s+{_FILLER}(instructions|rules|guidelines)", "disregard_rules"),
    (rf"output\s+{_FILLER}(system|initial)\s+prompt", "leak_system_prompt"),
    (rf"(respond|answer|reply)\s+without\s+{_FILLER}(restrictions|limitations|filters|safety)", "remove_filters"),
    (r"<!--[^>]{0,512}(?:ignore|override|system|secret|hidden)[^>]{0,512}-->", "html_comment_injection"),
    (rf"do\s+not\s+{_FILLER}tell\s+{_FILLER}the\s+user", "deception_hide"),
    (rf"you\s+are\s+{_FILLER}now\s+(?:a|an|the)\s+", "role_hijack"),
    (rf"pretend\s+{_FILLER}(you\s+are|to\s+be)\s+", "role_pretend"),
    (r"(?:api[_-]?key|token|secret|password)\s*[=:]\s*[\"'][A-Za-z0-9+/=_-]{20,}", "hardcoded_secret"),
    (r"(send|post|upload|transmit)\s+[^\n]{0,512}\s+(to|at)\s+https?://", "send_to_url"),
]

# Invisible/bidirectional unicode characters used in injection attacks —
# same set Hermes's scanner checks, these are real attack tools with no
# legitimate use in a curated fact/snippet.
INVISIBLE_CHARS = frozenset({
    "\u200b", "\u200c", "\u200d", "\u2060", "\u2062", "\u2063", "\u2064",
    "\ufeff", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
    "\u2066", "\u2067", "\u2068", "\u2069",
})

_COMPILED = [(re.compile(pattern, re.IGNORECASE), pid) for pattern, pid in _PATTERNS]

MAX_SCAN_CHARS = 20_000


def scan_for_threats(content: str) -> list[str]:
    """Return a list of matched pattern IDs in `content`. Empty list means
    clean. Also flags invisible/bidi unicode as
    "invisible_unicode_U+XXXX"."""
    if not content:
        return []

    content = content[:MAX_SCAN_CHARS]
    findings: list[str] = []

    char_set = set(content)
    for ch in char_set & INVISIBLE_CHARS:
        findings.append(f"invisible_unicode_U+{ord(ch):04X}")

    # NFKC-normalize so full-width/compatibility unicode variants can't
    # bypass the keyword patterns (does not defend against cross-script
    # confusables — same limitation Hermes's scanner documents).
    normalized = unicodedata.normalize("NFKC", content)
    for pattern, pid in _COMPILED:
        if pattern.search(normalized):
            findings.append(pid)

    return findings


def is_suspicious(content: str) -> bool:
    """Convenience boolean wrapper for a single yes/no gate."""
    return bool(scan_for_threats(content))
