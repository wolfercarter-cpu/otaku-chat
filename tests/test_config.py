"""Tests for otakuchat/config.py, specifically _get_int()'s fallback
behavior. Found during a stability audit: every numeric config getter used
to call bare int(_get(...)) with no error handling. Since /config
explicitly opens config.ini in $EDITOR for hand-editing, one typo or blank
value anywhere would raise ValueError from inside build_messages() —
which runs before app.py's per-turn try/except even starts — breaking
every subsequent chat turn until the user noticed and fixed config.ini by
hand.
"""
from otakuchat import config

ALL_INT_SETTINGS = [
    ("BOOST", "complexity_threshold", config.get_complexity_threshold, 220),
    ("MEMORY", "curation_interval_turns", config.get_curation_interval, 8),
    ("MEMORY", "max_memory_chars", config.get_max_memory_chars, 6000),
    ("CONTEXT", "max_context_messages", config.get_max_context_messages, 30),
    ("CONTEXT", "protect_tail_messages", config.get_protect_tail_messages, 12),
    ("SEARCH", "max_results", config.get_search_max_results, 5),
    ("FACTS", "max_links_stored", config.get_max_links_stored, 200),
    ("FACTS", "results_per_turn", config.get_facts_results_per_turn, 2),
    ("SNIPPETS", "max_snippets_stored", config.get_max_snippets_stored, 100),
    ("SNIPPETS", "max_code_chars", config.get_max_snippet_code_chars, 4000),
    ("SNIPPETS", "results_per_turn", config.get_snippets_results_per_turn, 2),
]


def test_get_int_returns_default_for_a_missing_section(isolated_env):
    assert config._get_int("NOPE", "nope", 42) == 42


def test_defaults_apply_on_a_fresh_config(isolated_env):
    for section, key, getter, default in ALL_INT_SETTINGS:
        assert getter() == default, f"{section}.{key} should default to {default}"


def test_every_numeric_getter_survives_a_blank_value(isolated_env):
    parser = config._get_config()
    for section, key, _getter, _default in ALL_INT_SETTINGS:
        parser[section][key] = ""
    config._save_config(parser)

    for section, key, getter, default in ALL_INT_SETTINGS:
        assert getter() == default, f"{section}.{key} blank should fall back to {default}"


def test_every_numeric_getter_survives_a_garbage_value(isolated_env):
    parser = config._get_config()
    for section, key, _getter, _default in ALL_INT_SETTINGS:
        parser[section][key] = "not-a-number"
    config._save_config(parser)

    for section, key, getter, default in ALL_INT_SETTINGS:
        assert getter() == default, f"{section}.{key} garbage should fall back to {default}"


def test_a_valid_value_is_still_read_correctly(isolated_env):
    parser = config._get_config()
    parser["CONTEXT"]["max_context_messages"] = "99"
    config._save_config(parser)
    assert config.get_max_context_messages() == 99


def test_generation_options_still_typed_correctly(isolated_env):
    parser = config._get_config()
    parser["GENERATION"]["temperature"] = "0.7"
    parser["GENERATION"]["seed"] = "42"
    config._save_config(parser)

    options = config.get_generation_options()
    assert options["temperature"] == 0.7
    assert options["seed"] == 42


def test_generation_options_omits_blank_fields_entirely(isolated_env):
    assert config.get_generation_options() == {}


def test_generation_options_survives_garbage_values(isolated_env):
    parser = config._get_config()
    parser["GENERATION"]["temperature"] = "hot"
    config._save_config(parser)
    # already-existing behavior: garbage silently omitted, not defaulted,
    # since a bad temperature should fall back to Ollama's own default
    # rather than the app guessing one (see get_generation_options docstring)
    assert "temperature" not in config.get_generation_options()


def test_isolated_env_does_not_touch_the_real_home_directory(isolated_env, monkeypatch):
    config_dir, data_dir = isolated_env
    assert str(config_dir).startswith("/tmp") or "pytest" in str(config_dir) or "tmp" in str(config_dir).lower()
    assert config.CONFIG_DIR == config_dir
    assert config.DB_FILE == data_dir / "otakuchat.db"
