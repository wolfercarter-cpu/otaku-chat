"""Optional Brave Web Search client.

Entirely inert unless config.get_brave_api_key() is non-empty — no key, no
network call, no behavior change. Uses urllib only (stdlib), matching the
zero-dependency network posture of ollama_client.py.
"""
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_TIMEOUT = 10


class SearchError(Exception):
    pass


def search_web(
    query: str, api_key: str, max_results: int = 5, timeout: int = DEFAULT_TIMEOUT
) -> list[dict]:
    """Query Brave's Web Search API. Returns a list of
    {"title", "url", "description"} dicts, most relevant first.

    Raises SearchError on any network/API failure — callers treat web
    search as best-effort and should swallow this rather than break the
    chat turn (see app.py OtakuChat.maybe_web_search).
    """
    params = urllib.parse.urlencode({"q": query, "count": max_results})
    req = urllib.request.Request(
        f"{SEARCH_URL}?{params}",
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        raise SearchError(f"Brave search request failed: {e}") from e
    except json.JSONDecodeError as e:
        raise SearchError(f"Malformed response from Brave search: {e}") from e

    web_results = (data.get("web") or {}).get("results") or []
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "description": item.get("description", ""),
        }
        for item in web_results[:max_results]
    ]


def format_results(query: str, results: list[dict]) -> str:
    """Render results as plain text for a system message — no markdown link
    syntax the model might mangle into something that looks clickable but
    isn't."""
    if not results:
        return ""
    lines = [f'Web search results for "{query}":']
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']} — {r['url']}\n   {r['description']}")
    return "\n".join(lines)
