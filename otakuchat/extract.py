"""Web page content extraction — the "supercharge" layer on top of
search.py's Brave results.

search.py gets you titles + two-line snippets. This module fetches the
actual page for the top few results and turns it into clean plain text,
so the model grounds on real page content instead of a snippet. Two
tiers, both optional and independently gated:

1. **stdlib tier** (always available): urllib + html.parser only, zero
   new dependencies — matches the zero-dependency network posture the
   rest of the app already holds to (ollama_client.py, search.py).
   Handles the overwhelming majority of docs/blogs/wikis/news sites.

2. **browser tier** (opt-in, lazy-imported): if the `playwright` extra is
   installed (`uv sync --extra browser` / `pip install otakuchat[browser]`)
   AND `EXTRACT.use_browser_fallback = true`, a JS-heavy page that yields
   too little text via the stdlib tier gets re-fetched through a headless
   Chromium page instead — ported from aider's `aider/scrape.py` Scraper,
   trimmed to the read-only path (no pandoc, no SSL-bypass option, no CLI
   entrypoint) since we only ever need markdown-adjacent plain text, not a
   faithful HTML→Markdown conversion. Import is inside the function so an
   otakuchat install with no `browser` extra never touches `playwright` at
   all — same lazy-check pattern as aider's `has_playwright()`.

Every extracted page is cached in sqlite (see db.py's `url_extract_cache`
table) for EXTRACT.cache_ttl_hours so a repeated question about the same
URL doesn't refetch, and every extracted page is run through
redact_secrets before it can ever reach a system prompt or persistent
store — a scraped page is no more trusted than a pasted /add file.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser

from . import config, db
from .redact import redact_secrets

DEFAULT_TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (compatible; OtakuChat/0.1; +https://github.com/wolfercarter-cpu/otaku-chat)"

# Below this many extracted characters, the stdlib tier is considered to
# have likely hit a JS-rendered shell (empty <div id="root"> etc.) rather
# than real content — worth a browser-tier retry if one is available.
_THIN_CONTENT_CHARS = 200

_SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "section", "article",
}


class ExtractError(Exception):
    pass


class _TextExtractor(HTMLParser):
    """Minimal HTML->text: strips script/style/nav/footer/aside content,
    keeps everything else as text, and inserts a newline at block-level
    tag boundaries so paragraphs don't run together. Deliberately far
    simpler than BeautifulSoup — good enough for "give the model readable
    page text", not for faithful layout reconstruction."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data)

    def text(self) -> str:
        raw = " ".join(self._chunks)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.text()


def _fetch_httpx_free(url: str, timeout: int) -> tuple[str | None, str | None]:
    """stdlib-only page fetch (urllib), matching search.py/ollama_client.py's
    existing network posture. Returns (html_or_none, error_or_none)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type and content_type:
                return None, f"unsupported content-type: {content_type}"
            raw = resp.read(2_000_000)  # cap read at 2MB, plenty for article text
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].strip() or "utf-8"
            try:
                return raw.decode(charset, errors="replace"), None
            except LookupError:
                return raw.decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return None, str(e)


def _has_playwright() -> bool:
    """Cheap availability check — mirrors aider's Scraper.check_env(), but
    only checks importability (never launches a browser here; that cost is
    paid lazily inside _fetch_with_browser, only for pages that actually
    need it)."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def _fetch_with_browser(url: str, timeout: int) -> tuple[str | None, str | None]:
    """Headless-Chromium fetch for JS-rendered pages. Ported from aider's
    aider/scrape.py:Scraper.scrape_with_playwright, trimmed to what
    otakuchat needs: no pandoc/markdown conversion (html_to_text handles
    that uniformly for both tiers), no SSL-bypass knob, synchronous API
    only. Only ever called when EXTRACT.use_browser_fallback is on AND the
    stdlib tier already came back thin."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "playwright not installed"

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as e:
                return None, f"could not launch chromium: {e}"
            try:
                page = browser.new_context().new_page()
                page.set_extra_http_headers({"User-Agent": USER_AGENT})
                try:
                    page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                except PlaywrightTimeoutError:
                    pass  # scrape whatever rendered so far, same as aider
                except PlaywrightError as e:
                    return None, f"navigation error: {e}"
                try:
                    return page.content(), None
                except PlaywrightError as e:
                    return None, f"content error: {e}"
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001 — playwright/OS-level failures, never crash a turn
        return None, str(e)


def extract_url(url: str, max_chars: int | None = None, use_cache: bool = True) -> str:
    """Fetch `url` and return clean plain text, truncated to `max_chars`.

    Cache-first (see db.get_cached_extract / EXTRACT.cache_ttl_hours).
    Tries the stdlib tier first; if EXTRACT.use_browser_fallback is on,
    playwright is installed, and the stdlib result looks thin (likely a
    JS-only shell), retries through a headless browser. Raises
    ExtractError if every available path fails — callers treat this as
    best-effort, same posture as search.SearchError.
    """
    max_chars = max_chars if max_chars is not None else config.get_extract_max_chars()
    url = url.strip()
    if not url:
        raise ExtractError("empty url")

    if use_cache:
        cached = db.get_cached_extract(url, ttl_seconds=config.get_extract_cache_ttl_hours() * 3600)
        if cached is not None:
            return cached[:max_chars]

    timeout = config.get_extract_timeout()
    html, err = _fetch_httpx_free(url, timeout)
    text = html_to_text(html) if html else ""

    if len(text) < _THIN_CONTENT_CHARS and config.get_extract_use_browser_fallback() and _has_playwright():
        browser_html, browser_err = _fetch_with_browser(url, timeout)
        if browser_html:
            browser_text = html_to_text(browser_html)
            if len(browser_text) > len(text):
                text, err = browser_text, None

    if not text:
        raise ExtractError(err or f"no extractable content from {url}")

    text, _ = redact_secrets(text)  # a scraped page is untrusted input, same as /add
    if use_cache:
        db.set_cached_extract(url, text)
    return text[:max_chars]


def format_extract_block(results: list[dict]) -> str:
    """Render extracted-page results as a plain-text system message block.

    `results` items: {"title", "url", "description", "excerpt"} — excerpt
    may be empty if extraction failed for that URL (still shows the search
    snippet so failure degrades gracefully instead of dropping the result).
    """
    if not results:
        return ""
    lines = ["Web page content (fetched and extracted for grounding):"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n{i}. {r['title']} — {r['url']}")
        if r.get("excerpt"):
            lines.append(r["excerpt"])
        elif r.get("description"):
            lines.append(f"   (page fetch failed, snippet only) {r['description']}")
    return "\n".join(lines)


def extract_top_results(results: list[dict], top_n: int | None = None) -> list[dict]:
    """Extract page content for the top `top_n` search results. Best-effort
    per URL — a failed extraction just omits `excerpt` rather than dropping
    the whole result, so one bad page doesn't cost the other results."""
    top_n = top_n if top_n is not None else config.get_extract_top_n()
    enriched = []
    for r in results[:top_n]:
        item = dict(r)
        try:
            item["excerpt"] = extract_url(r["url"])
        except ExtractError:
            item["excerpt"] = ""
        enriched.append(item)
    return enriched
