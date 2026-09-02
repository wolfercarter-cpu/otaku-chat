"""Tests for otakuchat/extract.py — page-content extraction that
"supercharges" search.py's title+snippet results into real page text.
"""
from unittest import mock

import pytest

from otakuchat import db, extract


# --- html_to_text ------------------------------------------------------

def test_html_to_text_strips_script_and_style():
    html = "<html><head><style>.x{}</style></head><body><script>evil()</script><p>Hello world</p></body></html>"
    text = extract.html_to_text(html)
    assert "Hello world" in text
    assert "evil()" not in text
    assert ".x{}" not in text


def test_html_to_text_strips_nav_footer_aside():
    html = "<nav>Menu</nav><article><p>Real content</p></article><footer>Copyright</footer><aside>Ad</aside>"
    text = extract.html_to_text(html)
    assert "Real content" in text
    assert "Menu" not in text
    assert "Copyright" not in text
    assert "Ad" not in text


def test_html_to_text_separates_paragraphs_with_newlines():
    html = "<p>First paragraph.</p><p>Second paragraph.</p>"
    text = extract.html_to_text(html)
    lines = [line for line in text.split("\n") if line.strip()]
    assert "First paragraph." in lines
    assert "Second paragraph." in lines


def test_html_to_text_collapses_excess_whitespace():
    html = "<p>Too    many     spaces</p>"
    text = extract.html_to_text(html)
    assert "Too many spaces" in text


# --- extract_url (stdlib tier + cache) ----------------------------------

def test_extract_url_rejects_empty_url(isolated_env):
    with pytest.raises(extract.ExtractError):
        extract.extract_url("")


def test_extract_url_returns_clean_text_on_success(isolated_env):
    fake_resp = mock.MagicMock()
    fake_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
    fake_resp.read.return_value = b"<html><body><p>Some real article text.</p></body></html>"
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        text = extract.extract_url("https://example.com/a")
    assert "Some real article text." in text


def test_extract_url_truncates_to_max_chars(isolated_env):
    fake_resp = mock.MagicMock()
    fake_resp.headers = {"Content-Type": "text/html"}
    fake_resp.read.return_value = b"<p>" + b"x" * 5000 + b"</p>"
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        text = extract.extract_url("https://example.com/b", max_chars=100)
    assert len(text) == 100


def test_extract_url_raises_on_network_failure(isolated_env):
    with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
        with pytest.raises(extract.ExtractError):
            extract.extract_url("https://example.com/c")


def test_extract_url_redacts_secrets_before_returning(isolated_env):
    fake_resp = mock.MagicMock()
    fake_resp.headers = {"Content-Type": "text/html"}
    fake_resp.read.return_value = b"<p>my key is sk-ant-abcdefghijklmnop123456</p>"
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        text = extract.extract_url("https://example.com/d")
    assert "sk-ant-abcdefghijklmnop123456" not in text


def test_extract_url_uses_cache_on_second_call(isolated_env):
    fake_resp = mock.MagicMock()
    fake_resp.headers = {"Content-Type": "text/html"}
    fake_resp.read.return_value = b"<p>Cached content.</p>"
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp) as mocked:
        extract.extract_url("https://example.com/e")
        extract.extract_url("https://example.com/e")
    assert mocked.call_count == 1


def test_extract_url_skips_cache_when_use_cache_false(isolated_env):
    fake_resp = mock.MagicMock()
    fake_resp.headers = {"Content-Type": "text/html"}
    fake_resp.read.return_value = b"<p>Uncached content.</p>"
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp) as mocked:
        extract.extract_url("https://example.com/f", use_cache=False)
        extract.extract_url("https://example.com/f", use_cache=False)
    assert mocked.call_count == 2


def test_extract_url_ignores_non_html_content_type(isolated_env):
    fake_resp = mock.MagicMock()
    fake_resp.headers = {"Content-Type": "application/pdf"}
    fake_resp.read.return_value = b"%PDF-1.4 binary garbage"
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(extract.ExtractError):
            extract.extract_url("https://example.com/g")


# --- browser-tier gating (never touches playwright unless both conditions hold) ---

def test_browser_fallback_not_attempted_when_disabled_in_config(isolated_env):
    fake_resp = mock.MagicMock()
    fake_resp.headers = {"Content-Type": "text/html"}
    fake_resp.read.return_value = b""  # thin content: would trigger fallback if enabled
    fake_resp.__enter__.return_value = fake_resp
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        with mock.patch("otakuchat.extract._has_playwright") as has_pw:
            with pytest.raises(extract.ExtractError):
                extract.extract_url("https://example.com/h")
            has_pw.assert_not_called()


def test_browser_fallback_used_when_enabled_and_stdlib_thin(isolated_env):
    from otakuchat import config

    parser = config._get_config()
    parser["EXTRACT"]["use_browser_fallback"] = "true"
    config._save_config(parser)

    thin_resp = mock.MagicMock()
    thin_resp.headers = {"Content-Type": "text/html"}
    thin_resp.read.return_value = b"<div id='root'></div>"
    thin_resp.__enter__.return_value = thin_resp

    with mock.patch("urllib.request.urlopen", return_value=thin_resp):
        with mock.patch("otakuchat.extract._has_playwright", return_value=True):
            with mock.patch(
                "otakuchat.extract._fetch_with_browser",
                return_value=("<html><body><p>Rendered by JS, plenty of real text here.</p></body></html>", None),
            ) as fetch_browser:
                text = extract.extract_url("https://example.com/i")
    fetch_browser.assert_called_once()
    assert "Rendered by JS" in text


def test_browser_fallback_never_imported_when_not_installed(isolated_env):
    """_has_playwright() must return False, not raise, when playwright
    isn't installed — the whole point of the lazy-import gate."""
    with mock.patch.dict("sys.modules", {"playwright": None}):
        assert extract._has_playwright() is False


# --- extract_top_results / format_extract_block -------------------------

def test_extract_top_results_limits_to_top_n(isolated_env):
    from otakuchat import config

    parser = config._get_config()
    parser["EXTRACT"]["top_n"] = "1"
    config._save_config(parser)

    results = [
        {"title": "A", "url": "https://a.com", "description": "da"},
        {"title": "B", "url": "https://b.com", "description": "db"},
    ]
    with mock.patch("otakuchat.extract.extract_url", return_value="body text"):
        enriched = extract.extract_top_results(results)
    assert len(enriched) == 1
    assert enriched[0]["excerpt"] == "body text"


def test_extract_top_results_degrades_gracefully_on_per_url_failure(isolated_env):
    results = [{"title": "A", "url": "https://a.com", "description": "da"}]
    with mock.patch("otakuchat.extract.extract_url", side_effect=extract.ExtractError("boom")):
        enriched = extract.extract_top_results(results)
    assert enriched[0]["excerpt"] == ""
    assert enriched[0]["title"] == "A"  # original result data preserved


def test_extract_top_results_fetches_concurrently_not_sequentially(isolated_env):
    """Regression/perf test: extract_top_results used to fetch each URL
    one at a time, so N results each blocking up to EXTRACT.timeout_s cost
    up to N * timeout_s wall time. It must now run them through a
    ThreadPoolExecutor so 3 slow (artificially delayed) fetches finish in
    close to one fetch's time, not three."""
    import time

    from otakuchat import config

    parser = config._get_config()
    parser["EXTRACT"]["top_n"] = "3"
    config._save_config(parser)

    def _slow_extract(url, *args, **kwargs):
        time.sleep(0.3)
        return f"content for {url}"

    results = [
        {"title": "A", "url": "https://a.com", "description": "da"},
        {"title": "B", "url": "https://b.com", "description": "db"},
        {"title": "C", "url": "https://c.com", "description": "dc"},
    ]
    with mock.patch("otakuchat.extract.extract_url", side_effect=_slow_extract):
        t0 = time.perf_counter()
        enriched = extract.extract_top_results(results)
        elapsed = time.perf_counter() - t0

    assert len(enriched) == 3
    assert all(r["excerpt"] for r in enriched)
    # 3 sequential 0.3s fetches would take ~0.9s; concurrent should land
    # well under that (generous ceiling to avoid CI flakiness).
    assert elapsed < 0.7, f"extraction took {elapsed:.2f}s — looks sequential, not concurrent"


# --- summarize_excerpt / extract_top_results summarize gating -----------

def test_summarize_excerpt_returns_condensed_text_on_success(isolated_env):
    with mock.patch("otakuchat.extract.chat_once", return_value="Condensed summary."):
        result = extract.summarize_excerpt("http://x", "model", "a long raw excerpt " * 50, "some query")
    assert result == "Condensed summary."


def test_summarize_excerpt_falls_back_to_raw_on_ollama_error(isolated_env):
    from otakuchat.ollama_client import OllamaError

    with mock.patch("otakuchat.extract.chat_once", side_effect=OllamaError("down")):
        result = extract.summarize_excerpt("http://x", "model", "the raw excerpt text", "some query")
    assert result == "the raw excerpt text"


def test_summarize_excerpt_falls_back_to_raw_on_empty_response(isolated_env):
    with mock.patch("otakuchat.extract.chat_once", return_value=""):
        result = extract.summarize_excerpt("http://x", "model", "the raw excerpt text", "some query")
    assert result == "the raw excerpt text"


def test_extract_top_results_skips_summarize_when_config_off(isolated_env):
    results = [{"title": "A", "url": "https://a.com", "description": "da"}]
    with mock.patch("otakuchat.extract.extract_url", return_value="raw excerpt"):
        with mock.patch("otakuchat.extract.chat_once") as chat_mock:
            enriched = extract.extract_top_results(
                results, api_url="http://x", model="m", query="q"
            )
    chat_mock.assert_not_called()
    assert enriched[0]["excerpt"] == "raw excerpt"


def test_extract_top_results_summarizes_when_config_on(isolated_env):
    from otakuchat import config

    parser = config._get_config()
    parser["EXTRACT"]["use_summarize"] = "true"
    config._save_config(parser)

    results = [{"title": "A", "url": "https://a.com", "description": "da"}]
    with mock.patch("otakuchat.extract.extract_url", return_value="raw excerpt"):
        with mock.patch("otakuchat.extract.chat_once", return_value="condensed") as chat_mock:
            enriched = extract.extract_top_results(
                results, api_url="http://x", model="m", query="q"
            )
    chat_mock.assert_called_once()
    assert enriched[0]["excerpt"] == "condensed"


def test_extract_top_results_summarize_off_when_no_model_given(isolated_env):
    """use_summarize=true alone isn't enough — api_url/model must also be
    passed (app.py always passes them; this guards direct callers)."""
    from otakuchat import config

    parser = config._get_config()
    parser["EXTRACT"]["use_summarize"] = "true"
    config._save_config(parser)

    results = [{"title": "A", "url": "https://a.com", "description": "da"}]
    with mock.patch("otakuchat.extract.extract_url", return_value="raw excerpt"):
        with mock.patch("otakuchat.extract.chat_once") as chat_mock:
            enriched = extract.extract_top_results(results)
    chat_mock.assert_not_called()
    assert enriched[0]["excerpt"] == "raw excerpt"


def test_format_extract_block_empty_results_returns_empty_string():
    assert extract.format_extract_block([]) == ""


def test_format_extract_block_includes_excerpt_when_present():
    results = [{"title": "T", "url": "https://x", "description": "D", "excerpt": "Full page text here."}]
    text = extract.format_extract_block(results)
    assert "Full page text here." in text
    assert "https://x" in text


def test_format_extract_block_falls_back_to_snippet_when_excerpt_empty():
    results = [{"title": "T", "url": "https://x", "description": "D", "excerpt": ""}]
    text = extract.format_extract_block(results)
    assert "page fetch failed" in text
    assert "D" in text


# --- db cache layer -------------------------------------------------------

def test_db_extract_cache_roundtrip(isolated_env):
    db.set_cached_extract("https://x.com", "hello")
    assert db.get_cached_extract("https://x.com", ttl_seconds=3600) == "hello"


def test_db_extract_cache_expires_past_ttl(isolated_env):
    db.set_cached_extract("https://x.com", "hello")
    assert db.get_cached_extract("https://x.com", ttl_seconds=0) is None


def test_db_extract_cache_miss_returns_none(isolated_env):
    assert db.get_cached_extract("https://never-cached.com", ttl_seconds=3600) is None


def test_db_prune_stale_extract_cache(isolated_env):
    db.set_cached_extract("https://x.com", "hello")
    db.prune_stale_extract_cache(ttl_seconds=0)
    assert db.get_cached_extract("https://x.com", ttl_seconds=3600) is None
