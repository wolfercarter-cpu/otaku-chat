import os
from configparser import ConfigParser
from pathlib import Path

# XDG compliant config directory
CONFIG_DIR = Path.home() / ".config" / "otakuchat"
CONFIG_FILE = CONFIG_DIR / "config.ini"

# Default files
DEFAULT_SOUL_FILE = CONFIG_DIR / "SOUL.md"
DEFAULT_MEMORY_FILE = CONFIG_DIR / "MEMORY.md"

def _get_config():
    parser = ConfigParser(interpolation=None)
    parser.optionxform = str  # Prevent lowercase conversion

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if CONFIG_FILE.exists():
        parser.read(CONFIG_FILE)
    else:
        # 1. Setup defaults on first run if config.ini doesn't exist
        parser.add_section("GENERAL")
        parser["GENERAL"]["editor"] = os.environ.get("EDITOR", "nvim")
        
        parser.add_section("LLM")
        parser["LLM"]["model"] = "llama3.2"  # Easy to swap to qwen2.5-coder
        parser["LLM"]["api_url"] = "http://localhost:11434/api/chat"
        
        parser.add_section("PATHS")
        parser["PATHS"]["soul"] = str(DEFAULT_SOUL_FILE)
        parser["PATHS"]["memory"] = str(DEFAULT_MEMORY_FILE)

        with open(CONFIG_FILE, "w") as f:
            parser.write(f)
            
        # 2. Seed default SOUL and MEMORY files if they are missing
        if not DEFAULT_SOUL_FILE.exists():
            DEFAULT_SOUL_FILE.write_text("You are a pragmatic, direct assistant with a sharp technical voice.")
        if not DEFAULT_MEMORY_FILE.exists():
            DEFAULT_MEMORY_FILE.write_text("## Persistent Memory\n\n- Prefers concise terminal output.")

    return parser

def _save_config(parser):
    """Internal helper to write the file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        parser.write(f)

# --- GENERAL Settings ---

def save_editor(editor_name: str) -> None:
    config = _get_config()
    if not config.has_section("GENERAL"):
        config.add_section("GENERAL")
    config["GENERAL"]["editor"] = editor_name
    _save_config(config)

def get_editor() -> str:
    config = _get_config()
    return config.get("GENERAL", "editor", fallback=os.environ.get("EDITOR", "nvim"))

# --- LLM Settings ---

def save_model(model_name: str) -> None:
    config = _get_config()
    if not config.has_section("LLM"):
        config.add_section("LLM")
    config["LLM"]["model"] = model_name
    _save_config(config)

def get_model() -> str:
    config = _get_config()
    return config.get("LLM", "model", fallback="llama3.2")

def get_api_url() -> str:
    config = _get_config()
    return config.get("LLM", "api_url", fallback="http://localhost:11434/api/chat")

# --- PATH Settings ---

def get_soul_path() -> str:
    config = _get_config()
    return config.get("PATHS", "soul", fallback=str(DEFAULT_SOUL_FILE))

def get_memory_path() -> str:
    config = _get_config()
    return config.get("PATHS", "memory", fallback=str(DEFAULT_MEMORY_FILE))
