"""Shared fixtures for OtakuChat's test suite.

Every test that touches config.py or db.py should depend on `isolated_env`
(directly or via another fixture that depends on it). It redirects
CONFIG_DIR/DATA_DIR/DB_FILE and the default SOUL/MEMORY/FACTS/SNIPPETS
paths into a throwaway pytest tmp_path — without this, running the suite
would read, write, and clobber a real user's actual ~/.config/otakuchat
and ~/.local/share/otakuchat (sessions, curated memory, config.ini). This
was a real problem during manual testing this project's own history: test
sessions and config edits repeatedly leaked into the real store and had to
be cleaned up by hand.
"""
import pytest

from otakuchat import config, db, ollama_client


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Point every on-disk path OtakuChat uses at tmp_path, then init a
    fresh db. Yields the (config_dir, data_dir) pair in case a test wants
    to inspect files directly (e.g. verifying FACTS.md/SNIPPETS.md mirror
    content)."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"

    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "CONFIG_FILE", config_dir / "config.ini")
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DB_FILE", data_dir / "otakuchat.db")
    monkeypatch.setattr(config, "DEFAULT_SOUL_FILE", config_dir / "SOUL.md")
    monkeypatch.setattr(config, "DEFAULT_MEMORY_FILE", config_dir / "MEMORY.md")
    monkeypatch.setattr(config, "DEFAULT_FACTS_FILE", config_dir / "FACTS.md")
    monkeypatch.setattr(config, "DEFAULT_SNIPPETS_FILE", config_dir / "SNIPPETS.md")

    # DEFAULTS is a plain dict built once at import time from the ORIGINAL
    # path constants above — patching the constants alone doesn't change
    # the strings already baked into DEFAULTS["PATHS"], so a fresh-install
    # config.ini would still get the real paths written into it. Patch the
    # nested dict values too.
    monkeypatch.setitem(config.DEFAULTS["PATHS"], "soul", str(config_dir / "SOUL.md"))
    monkeypatch.setitem(config.DEFAULTS["PATHS"], "memory", str(config_dir / "MEMORY.md"))
    monkeypatch.setitem(config.DEFAULTS["PATHS"], "facts", str(config_dir / "FACTS.md"))
    monkeypatch.setitem(config.DEFAULTS["PATHS"], "snippets", str(config_dir / "SNIPPETS.md"))

    db.init_db()
    yield config_dir, data_dir


@pytest.fixture(autouse=True)
def _clear_ollama_caps_cache():
    """get_capabilities() caches per-model results in a module-level dict
    for 10 minutes — without clearing it, one test's mocked response can
    leak into another test asking about the same model name."""
    ollama_client._CAPS_CACHE.clear()
    yield
    ollama_client._CAPS_CACHE.clear()
