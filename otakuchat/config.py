"""XDG-compliant configuration for OtakuChat."""
import os
from configparser import ConfigParser
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "otakuchat"
CONFIG_FILE = CONFIG_DIR / "config.ini"
DATA_DIR = Path.home() / ".local" / "share" / "otakuchat"
DB_FILE = DATA_DIR / "otakuchat.db"

DEFAULT_SOUL_FILE = CONFIG_DIR / "SOUL.md"
DEFAULT_MEMORY_FILE = CONFIG_DIR / "MEMORY.md"
DEFAULT_FACTS_FILE = CONFIG_DIR / "FACTS.md"
DEFAULT_SNIPPETS_FILE = CONFIG_DIR / "SNIPPETS.md"
DEFAULT_VAULT_DIR = DATA_DIR / "vault"
DEFAULT_SEED_DIR = DATA_DIR / "seed"
DEFAULT_FUNCTIONS_FILE = CONFIG_DIR / "FUNCTIONS.py"

DEFAULTS = {
    "GENERAL": {
        "editor": os.environ.get("EDITOR", "nvim"),
    },
    "LLM": {
        "model": "qwen2.5-coder:7b",
        "api_url": "http://localhost:11434",
    },
    "PATHS": {
        "soul": str(DEFAULT_SOUL_FILE),
        "memory": str(DEFAULT_MEMORY_FILE),
        "facts": str(DEFAULT_FACTS_FILE),
        "snippets": str(DEFAULT_SNIPPETS_FILE),
        "functions": str(DEFAULT_FUNCTIONS_FILE),
    },
    "BOOST": {
        # auto | always | off — auto self-tunes per model from perf stats
        "mode": "auto",
        # rolling threshold (chars) above which a prompt is considered "complex"
        # enough to warrant a boost pass when mode=auto. Self-adjusted over time.
        "complexity_threshold": "220",
    },
    "GENERATION": {
        # Ollama /api/chat "options" — leave any value blank to omit it and
        # use Ollama's own model default. Ported from oterm's chat_edit.py
        # per-chat parameter modal, flattened into config.ini instead of a
        # new UI/command (this app tries to add as few commands as possible;
        # editing config.ini via the existing /config command already covers it).
        "temperature": "",
        "top_p": "",
        "max_tokens": "",
        "seed": "",
    },
    "MEMORY": {
        "curation_interval_turns": "8",
        "max_memory_chars": "6000",
        # cap on stored curated facts before the oldest get pruned
        "max_facts_stored": "200",
    },
    "CONTEXT": {
        # once a session's transmitted history exceeds this many messages,
        # compact the middle into a summary (Hermes-style trajectory
        # compression) instead of resending an ever-growing transcript
        "max_context_messages": "30",
        # always send the most recent N raw messages uncompressed
        "protect_tail_messages": "12",
    },
    "SEARCH": {
        # Brave Web Search API key. Leave blank to disable web search
        # entirely — no key means no network call, ever.
        "brave_api_key": "",
        "max_results": "5",
    },
    "FACTS": {
        "max_links_stored": "200",
        # how many search-result URLs get filed per query, and how many
        # bookmarks can surface into a single turn
        "results_per_turn": "2",
    },
    "SNIPPETS": {
        "max_snippets_stored": "100",
        "max_code_chars": "4000",
        "results_per_turn": "2",
    },
    "EXTRACT": {
        # how many of the top search results actually get their page
        # content fetched (rest fall back to title+snippet only)
        "top_n": "2",
        # per-result extracted text is truncated to this many chars before
        # entering the system prompt
        "max_chars": "3000",
        "timeout_s": "10",
        # cache a fetched page's extracted text for this long before a
        # repeat request re-fetches it
        "cache_ttl_hours": "24",
        # opt-in: retry a JS-thin stdlib extraction through headless
        # Chromium via playwright, if installed (uv sync --extra browser).
        # Never touches playwright at all when false/uninstalled.
        "use_browser_fallback": "false",
    },
    "VAULT": {
        # vault dirs: "vault" (wipeable dump) and "seed" (survives-wipe
        # whitelist) — see otakuchat/vault.py. Paths default under
        # DATA_DIR (~/.local/share/otakuchat/vault, .../seed).
        "vault_path": str(DEFAULT_VAULT_DIR),
        "seed_path": str(DEFAULT_SEED_DIR),
        # only files with these extensions are indexed for retrieval
        # (binaries land on disk fine via /import, just never chunked/
        # searched — we can't usefully embed them with token-overlap
        # ranking); comma-separated, matched case-insensitively.
        "indexed_extensions": ".md,.txt,.py,.js,.ts,.json,.yaml,.yml,.toml,.ini,.sh,.go,.rs,.java,.c,.cpp,.h,.html,.css,.rst,.csv",
        # a single indexed file larger than this many chars is truncated
        # before being stored for retrieval (full file still lands on
        # disk untouched; this only bounds what a turn can inject)
        "max_file_index_chars": "20000",
        # how many vault chunks can surface into a single turn
        "results_per_turn": "3",
        # per-chunk text is truncated to this many chars before entering
        # the system prompt
        "max_chunk_chars": "1500",
        # git clone / zip download / single-file download timeout
        "import_timeout_s": "60",
    },
    "YOUTUBE": {
        # a fetched transcript longer than this is truncated before being
        # handed to the model (full transcript is what got cached; this
        # only bounds what a single summarize/explain call sends)
        "max_transcript_chars": "20000",
    },
    "FAAS": {
        # wall-clock seconds a single model-requested function call is
        # allowed to run in its subprocess before being killed
        "call_timeout_s": "15",
    },
}


def _get_config() -> ConfigParser:
    parser = ConfigParser(interpolation=None)
    parser.optionxform = str  # preserve case

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Unlike SOUL/MEMORY/FACTS/SNIPPETS above, vault/seed are plain
    # directories a user drops content into directly (via /import or by
    # hand) — always ensured to exist, not just on a fresh install, so
    # enabling the feature on an existing ~/.local/share/otakuchat still
    # gets working directories immediately.
    DEFAULT_VAULT_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_SEED_DIR.mkdir(parents=True, exist_ok=True)

    if CONFIG_FILE.exists():
        parser.read(CONFIG_FILE)
        changed = False
        for section, values in DEFAULTS.items():
            if not parser.has_section(section):
                parser.add_section(section)
                changed = True
            for key, val in values.items():
                if not parser.has_option(section, key):
                    parser[section][key] = val
                    changed = True
        if changed:
            _save_config(parser)
    else:
        for section, values in DEFAULTS.items():
            parser.add_section(section)
            for key, val in values.items():
                parser[section][key] = val
        _save_config(parser)

        if not DEFAULT_SOUL_FILE.exists():
            DEFAULT_SOUL_FILE.write_text(
                "You are OtakuChat, a sharp, pragmatic local AI chat companion. "
                "You run entirely on local Ollama models. You are direct, technically "
                "competent, and a little playful. You have no shell access — you are "
                "for conversation and thinking, not system execution.\n"
            )
        if not DEFAULT_MEMORY_FILE.exists():
            DEFAULT_MEMORY_FILE.write_text(
                "## Curated Memory\n\n"
                "(OtakuChat writes durable facts about the user and itself here "
                "as conversations happen. Nothing here yet.)\n"
            )
        if not DEFAULT_FACTS_FILE.exists():
            DEFAULT_FACTS_FILE.write_text(
                "# Facts\n\n"
                "(OtakuChat bookmarks URLs a web search actually returned for a "
                "topic here, grouped by topic, once web search is configured "
                "and a topic comes up. Nothing here yet.)\n"
            )
        if not DEFAULT_SNIPPETS_FILE.exists():
            DEFAULT_SNIPPETS_FILE.write_text(
                "# Snippets\n\n"
                "(OtakuChat curates reusable code snippets here as "
                "conversations happen. Nothing here yet.)\n"
            )

    return parser


def _save_config(parser: ConfigParser) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    from io import StringIO

    from .fileio import locked_atomic_write

    buf = StringIO()
    parser.write(buf)
    locked_atomic_write(CONFIG_FILE, buf.getvalue())


def _set(section: str, key: str, value: str) -> None:
    parser = _get_config()
    if not parser.has_section(section):
        parser.add_section(section)
    parser[section][key] = value
    _save_config(parser)


def _get(section: str, key: str, fallback: str) -> str:
    return _get_config().get(section, key, fallback=fallback)


def _get_int(section: str, key: str, default: int) -> int:
    """Like _get, but for integer settings — falls back to `default` if the
    value is missing, blank, or not a valid integer (e.g. the user
    hand-editing config.ini via /config left a typo or blank value) rather
    than raising and breaking every subsequent chat turn. build_messages()
    calls several of these unconditionally before app.py's try/except
    around a turn even starts, so a bad value here can't be allowed to
    propagate as an exception."""
    raw = _get(section, key, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


# --- GENERAL ---
def get_editor() -> str:
    return _get("GENERAL", "editor", os.environ.get("EDITOR", "nvim"))


def save_editor(editor_name: str) -> None:
    _set("GENERAL", "editor", editor_name)


# --- LLM ---
def get_model() -> str:
    return _get("LLM", "model", "qwen2.5-coder:7b")


def save_model(model_name: str) -> None:
    _set("LLM", "model", model_name)


def get_api_url() -> str:
    return _get("LLM", "api_url", "http://localhost:11434")


# --- PATHS ---
def get_soul_path() -> str:
    return _get("PATHS", "soul", str(DEFAULT_SOUL_FILE))


def get_memory_path() -> str:
    return _get("PATHS", "memory", str(DEFAULT_MEMORY_FILE))


def get_facts_path() -> str:
    return _get("PATHS", "facts", str(DEFAULT_FACTS_FILE))


def get_snippets_path() -> str:
    return _get("PATHS", "snippets", str(DEFAULT_SNIPPETS_FILE))


def get_functions_path() -> str:
    return _get("PATHS", "functions", str(DEFAULT_FUNCTIONS_FILE))


# --- BOOST (self-curating reasoning aggregation layer) ---
def get_boost_mode() -> str:
    return _get("BOOST", "mode", "auto")


def save_boost_mode(mode: str) -> None:
    _set("BOOST", "mode", mode)


def get_complexity_threshold() -> int:
    return _get_int("BOOST", "complexity_threshold", 220)


def save_complexity_threshold(value: int) -> None:
    _set("BOOST", "complexity_threshold", str(value))


# --- GENERATION (Ollama /api/chat "options") ---
def get_generation_options() -> dict:
    """Return only the options the user has actually set (non-blank in
    config.ini), typed and ready to pass straight through as Ollama's
    `options` dict. Blank/unset fields are omitted entirely so Ollama
    falls back to the model's own defaults rather than us guessing one."""
    parser = _get_config()
    if not parser.has_section("GENERATION"):
        return {}

    options: dict = {}
    raw_temp = parser.get("GENERATION", "temperature", fallback="").strip()
    if raw_temp:
        try:
            options["temperature"] = float(raw_temp)
        except ValueError:
            pass
    raw_top_p = parser.get("GENERATION", "top_p", fallback="").strip()
    if raw_top_p:
        try:
            options["top_p"] = float(raw_top_p)
        except ValueError:
            pass
    raw_max_tokens = parser.get("GENERATION", "max_tokens", fallback="").strip()
    if raw_max_tokens:
        try:
            options["num_predict"] = int(raw_max_tokens)
        except ValueError:
            pass
    raw_seed = parser.get("GENERATION", "seed", fallback="").strip()
    if raw_seed:
        try:
            options["seed"] = int(raw_seed)
        except ValueError:
            pass
    return options


# --- MEMORY ---
def get_curation_interval() -> int:
    return _get_int("MEMORY", "curation_interval_turns", 8)


def get_max_memory_chars() -> int:
    return _get_int("MEMORY", "max_memory_chars", 6000)


def get_max_memory_facts_stored() -> int:
    return _get_int("MEMORY", "max_facts_stored", 200)


# --- CONTEXT (trajectory compression) ---
def get_max_context_messages() -> int:
    return _get_int("CONTEXT", "max_context_messages", 30)


def get_protect_tail_messages() -> int:
    return _get_int("CONTEXT", "protect_tail_messages", 12)


# --- SEARCH (Brave Web Search grounding) ---
def get_brave_api_key() -> str:
    return _get("SEARCH", "brave_api_key", "").strip()


def get_search_max_results() -> int:
    return _get_int("SEARCH", "max_results", 5)


# --- FACTS (topic -> URL bookmarks, fed by web search) ---
def get_max_links_stored() -> int:
    return _get_int("FACTS", "max_links_stored", 200)


def get_facts_results_per_turn() -> int:
    return _get_int("FACTS", "results_per_turn", 2)


# --- SNIPPETS (self-curating code-snippet library) ---
def get_max_snippets_stored() -> int:
    return _get_int("SNIPPETS", "max_snippets_stored", 100)


def get_max_snippet_code_chars() -> int:
    return _get_int("SNIPPETS", "max_code_chars", 4000)


def get_snippets_results_per_turn() -> int:
    return _get_int("SNIPPETS", "results_per_turn", 2)


# --- EXTRACT (page-content extraction for search grounding) ---
def get_extract_top_n() -> int:
    return _get_int("EXTRACT", "top_n", 2)


def get_extract_max_chars() -> int:
    return _get_int("EXTRACT", "max_chars", 3000)


def get_extract_timeout() -> int:
    return _get_int("EXTRACT", "timeout_s", 10)


def get_extract_cache_ttl_hours() -> int:
    return _get_int("EXTRACT", "cache_ttl_hours", 24)


def get_extract_use_browser_fallback() -> bool:
    return _get("EXTRACT", "use_browser_fallback", "false").strip().lower() in ("1", "true", "yes", "on")


# --- VAULT (dump/seed RAG-style import directories) ---
def get_vault_path() -> str:
    return _get("VAULT", "vault_path", str(DEFAULT_VAULT_DIR))


def get_seed_path() -> str:
    return _get("VAULT", "seed_path", str(DEFAULT_SEED_DIR))


def get_vault_indexed_extensions() -> set[str]:
    raw = _get("VAULT", "indexed_extensions", DEFAULTS["VAULT"]["indexed_extensions"])
    return {ext.strip().lower() for ext in raw.split(",") if ext.strip()}


def get_vault_max_file_index_chars() -> int:
    return _get_int("VAULT", "max_file_index_chars", 20000)


def get_vault_results_per_turn() -> int:
    return _get_int("VAULT", "results_per_turn", 3)


def get_vault_max_chunk_chars() -> int:
    return _get_int("VAULT", "max_chunk_chars", 1500)


def get_vault_import_timeout() -> int:
    return _get_int("VAULT", "import_timeout_s", 60)


def get_youtube_max_transcript_chars() -> int:
    return _get_int("YOUTUBE", "max_transcript_chars", 20000)


def get_faas_call_timeout() -> int:
    return _get_int("FAAS", "call_timeout_s", 15)
