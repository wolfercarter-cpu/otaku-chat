"""Lightweight secret redaction, adapted from Hermes's agent/redact.py.

Purpose here is narrower than Hermes's full redactor (no log/terminal
integration — we have no shell tool): protect the two places raw text
enters *persistent* storage or repeated model context —
memory curation (facts get replayed into the system prompt forever) and
/add file ingestion (arbitrary file content joins the conversation).

Not a security boundary against a determined attacker — a best-effort
net so a pasted API key doesn't quietly become part of curated memory.
"""
import re

# Trimmed down from Hermes's much larger vendor-prefix list — kept to the
# common/high-signal ones rather than trying to track every provider.
_PREFIX_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{10,}",            # OpenAI / Anthropic (sk-ant-*)
    r"ghp_[A-Za-z0-9]{10,}",             # GitHub PAT (classic)
    r"github_pat_[A-Za-z0-9_]{10,}",     # GitHub PAT (fine-grained)
    r"gho_[A-Za-z0-9]{10,}",             # GitHub OAuth token
    r"xox[baprs]-[A-Za-z0-9-]{10,}",     # Slack tokens
    r"AIza[A-Za-z0-9_-]{30,}",           # Google API keys
    r"AKIA[A-Z0-9]{16}",                 # AWS Access Key ID
    r"sk_live_[A-Za-z0-9]{10,}",         # Stripe secret key (live)
    r"hf_[A-Za-z0-9]{10,}",              # HuggingFace token
    r"npm_[A-Za-z0-9]{10,}",             # npm access token
    r"gsk_[A-Za-z0-9]{10,}",             # Groq Cloud API key
    r"glpat-[A-Za-z0-9_\-]{10,}",        # GitLab personal access token
]
_PREFIX_RE = re.compile("|".join(_PREFIX_PATTERNS))

# Generic "key/token/secret/password: <value>" patterns for anything not
# matching a known vendor prefix.
_GENERIC_KV_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|passwd|token|auth)\s*[:=]\s*"
    r"[\"']?([A-Za-z0-9_\-\.\/+=]{8,})[\"']?"
)


def _mask(token: str) -> str:
    if len(token) <= 18:
        return "***REDACTED***"
    return f"{token[:6]}...{token[-4:]}"


def redact_secrets(text: str) -> tuple[str, int]:
    """Redact likely secrets from text. Returns (redacted_text, count)."""
    if not text:
        return text, 0

    count = 0

    def _sub_prefix(m: re.Match) -> str:
        nonlocal count
        count += 1
        return _mask(m.group(0))

    text = _PREFIX_RE.sub(_sub_prefix, text)

    def _sub_kv(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f"{m.group(1)}={_mask(m.group(2))}"

    text = _GENERIC_KV_RE.sub(_sub_kv, text)

    return text, count
