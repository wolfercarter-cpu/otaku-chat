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
    },
    "CONTEXT": {
        # once a session's transmitted history exceeds this many messages,
        # compact the middle into a summary (Hermes-style trajectory
        # compression) instead of resending an ever-growing transcript
        "max_context_messages": "30",
        # always send the most recent N raw messages uncompressed
        "protect_tail_messages": "12",
    },
}


def _get_config() -> ConfigParser:
    parser = ConfigParser(interpolation=None)
    parser.optionxform = str  # preserve case

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

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

    return parser


def _save_config(parser: ConfigParser) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        parser.write(f)


def _set(section: str, key: str, value: str) -> None:
    parser = _get_config()
    if not parser.has_section(section):
        parser.add_section(section)
    parser[section][key] = value
    _save_config(parser)


def _get(section: str, key: str, fallback: str) -> str:
    return _get_config().get(section, key, fallback=fallback)


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


# --- BOOST (self-curating reasoning aggregation layer) ---
def get_boost_mode() -> str:
    return _get("BOOST", "mode", "auto")


def save_boost_mode(mode: str) -> None:
    _set("BOOST", "mode", mode)


def get_complexity_threshold() -> int:
    return int(_get("BOOST", "complexity_threshold", "220"))


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
    return int(_get("MEMORY", "curation_interval_turns", "8"))


def get_max_memory_chars() -> int:
    return int(_get("MEMORY", "max_memory_chars", "6000"))


# --- CONTEXT (trajectory compression) ---
def get_max_context_messages() -> int:
    return int(_get("CONTEXT", "max_context_messages", "30"))


def get_protect_tail_messages() -> int:
    return int(_get("CONTEXT", "protect_tail_messages", "12"))
