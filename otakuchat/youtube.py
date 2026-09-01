"""YouTube transcript fetch + explain, ported from the idea in
inspiration/pytube (a pure-Python YouTube downloader) but pointed at
transcripts instead of video streams — pytube itself has no transcript
API, and neither approach can stay pure-stdlib: YouTube's caption
endpoint requires a PoToken (a proof-of-origin token minted at runtime
by real player JS) as of 2026, and even routing the request through a
real headless-Chromium page (otakuchat/extract.py's existing browser
tier) doesn't clear it — Chromium's `navigator.webdriver=True`
automation fingerprint gets read server-side and the caption endpoint
silently 200s with an empty body for any detected automated browser
(confirmed by direct testing, not assumption).

So this is the one deliberate exception to the project's zero-dependency
network posture: `youtube-transcript-api` is a real, actively-maintained
dependency (see pyproject.toml) specifically because it tracks YouTube's
anti-bot changes for us — reimplementing that arms race by hand here
would be a losing, high-maintenance bet.

Everything downstream of the fetch (id extraction, caching, redaction,
the explain/summarize prompt) stays plain Python + this project's own
ollama_client — no other new dependencies.
"""
from __future__ import annotations

import re
import urllib.parse

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import YouTubeTranscriptApiException

from . import config, db, ollama_client
from .redact import redact_secrets

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Cached transcripts share url_extract_cache with extract.py's web-page
# cache (otakuchat/db.py) — same shape (key -> content, TTL-checked), so
# a synthetic "youtube:{video_id}" cache key avoids a whole new table for
# what's really the same "fetched text, don't refetch too often" need.
_CACHE_KEY_PREFIX = "youtube:"


class YouTubeError(Exception):
    pass


def extract_video_id(url_or_id: str) -> str:
    """Accept a full watch/share/shorts/embed URL or a bare 11-char video
    ID and return the bare ID. Raises YouTubeError if nothing usable is
    found — callers treat this the same as any other best-effort failure
    (notify(), never crash a turn)."""
    candidate = url_or_id.strip()
    if _ID_RE.fullmatch(candidate):
        return candidate

    parsed = urllib.parse.urlparse(candidate)
    if "youtu.be" in parsed.netloc:
        vid = parsed.path.lstrip("/")
        if _ID_RE.fullmatch(vid):
            return vid

    qs = urllib.parse.parse_qs(parsed.query)
    if "v" in qs and _ID_RE.fullmatch(qs["v"][0]):
        return qs["v"][0]

    m = re.search(r"/(?:embed|shorts|live)/([A-Za-z0-9_-]{11})", parsed.path)
    if m:
        return m.group(1)

    raise YouTubeError(f"could not extract a video ID from '{url_or_id}'")


def fetch_transcript(video_id_or_url: str, languages: tuple[str, ...] = ("en",), use_cache: bool = True) -> str:
    """Fetch a video's transcript as plain text (timestamps dropped —
    otakuchat only ever needs the words for summarizing/explaining, not
    a subtitle file). Cached in db.url_extract_cache under a synthetic
    key so a repeat question about the same video doesn't re-hit
    YouTube. Raises YouTubeError on any failure (no captions available,
    video unavailable/private, YouTube-side blocking, ...)."""
    video_id = extract_video_id(video_id_or_url)
    cache_key = f"{_CACHE_KEY_PREFIX}{video_id}"

    if use_cache:
        cached = db.get_cached_extract(cache_key, ttl_seconds=config.get_extract_cache_ttl_hours() * 3600)
        if cached is not None:
            return cached

    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=list(languages))
    except YouTubeTranscriptApiException as e:
        raise YouTubeError(str(e)) from e

    text = " ".join(snippet.text.strip() for snippet in fetched.snippets if snippet.text.strip())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise YouTubeError(f"transcript for {video_id} came back empty")

    text, _ = redact_secrets(text)  # a transcript is untrusted third-party text, same as /add
    if use_cache:
        db.set_cached_extract(cache_key, text)
    return text


def summarize_transcript(
    video_id_or_url: str,
    api_url: str,
    model: str,
    instruction: str = "Summarize this video transcript clearly and concisely.",
) -> str:
    """Fetch a transcript and ask the active Ollama model to summarize or
    explain it — a single non-streaming call (ollama_client.chat_once),
    same pattern as memory.py's/facts.py's internal curation passes: this
    never touches the visible conversation, it's a side-call whose result
    the caller decides what to do with (e.g. append to the chat as a
    system note, or hand back as /youtube's direct answer).

    `instruction` lets a caller ask something more specific than a bare
    summary (\"explain the main argument\", \"list every tool mentioned\",
    ...) against the same transcript without a second fetch."""
    transcript = fetch_transcript(video_id_or_url)
    max_chars = config.get_youtube_max_transcript_chars()
    transcript = transcript[:max_chars]

    messages = [
        {
            "role": "user",
            "content": f"{instruction}\n\nTranscript:\n{transcript}",
        }
    ]
    try:
        return ollama_client.chat_once(api_url, model, messages)
    except ollama_client.OllamaError as e:
        raise YouTubeError(f"model call failed: {e}") from e
