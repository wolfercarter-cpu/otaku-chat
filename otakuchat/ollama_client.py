"""Thin client for the local Ollama HTTP API. No other providers — by design.

Uses urllib only (stdlib), matching the project's zero-dependency network
posture, but supports real token streaming via NDJSON line reads, plus
native `think` streaming for models that support it (deepseek-r1, qwen3,
gpt-oss, ...) — see get_capabilities().
"""
import json
import time
import urllib.request
import urllib.error
from typing import Callable, Iterator

DEFAULT_TIMEOUT = 120

_CAPS_CACHE: dict[str, tuple[float, dict]] = {}
_CAPS_TTL = 600  # seconds; models rarely change capability mid-session


class OllamaError(Exception):
    pass


def _base(api_url: str) -> str:
    return api_url.rstrip("/")


def list_models(api_url: str) -> list[dict]:
    url = f"{_base(api_url)}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("models", [])
    except (urllib.error.URLError, OSError) as e:
        raise OllamaError(f"Could not reach Ollama at {api_url}: {e}") from e


def is_reachable(api_url: str) -> bool:
    try:
        list_models(api_url)
        return True
    except OllamaError:
        return False


def get_capabilities(api_url: str, model: str) -> dict:
    """Query /api/show for a model's declared capabilities (thinking, tools,
    vision, ...), cached briefly per model to avoid a round-trip every turn.

    Returns e.g. {"thinking": True, "tools": True, "vision": False}.
    On any failure returns all-False rather than raising — callers treat
    this as a soft hint, never a hard requirement.
    """
    now = time.time()
    cached = _CAPS_CACHE.get(model)
    if cached and (now - cached[0]) < _CAPS_TTL:
        return cached[1]

    url = f"{_base(api_url)}/api/show"
    payload = {"model": model}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    result = {"thinking": False, "tools": False, "vision": False}
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        caps = info.get("capabilities", []) or []
        result = {
            "thinking": "thinking" in caps,
            "tools": "tools" in caps,
            "vision": "vision" in caps,
        }
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        pass

    _CAPS_CACHE[model] = (now, result)
    return result


def chat_stream(
    api_url: str,
    model: str,
    messages: list[dict],
    on_token: Callable[[str], None],
    on_thinking: Callable[[str], None] | None = None,
    think: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Stream a chat completion from Ollama, calling on_token per text chunk
    and on_thinking (if given) per native reasoning chunk.

    think=True asks Ollama for native chain-of-thought (deepseek-r1, qwen3,
    gpt-oss, ...) — only pass this for models get_capabilities() marks as
    thinking-capable; Ollama errors on models that don't support it.

    Returns the final assembled message dict (role/content/thinking/tool_calls).
    """
    url = f"{_base(api_url)}/api/chat"
    payload = {"model": model, "messages": messages, "stream": True}
    if think:
        payload["think"] = True
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )

    full_content = ""
    full_thinking = ""
    final_message: dict = {"role": "assistant", "content": ""}

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                chunk = json.loads(line)
                msg = chunk.get("message", {})
                think_piece = msg.get("thinking", "")
                if think_piece and on_thinking:
                    full_thinking += think_piece
                    on_thinking(think_piece)
                piece = msg.get("content", "")
                if piece:
                    full_content += piece
                    on_token(piece)
                if chunk.get("done"):
                    final_message = msg or final_message
                    final_message["content"] = full_content
                    if full_thinking:
                        final_message["thinking"] = full_thinking
    except (urllib.error.URLError, OSError) as e:
        raise OllamaError(f"Ollama request failed: {e}") from e
    except json.JSONDecodeError as e:
        raise OllamaError(f"Malformed response from Ollama: {e}") from e

    if "content" not in final_message:
        final_message["content"] = full_content
    return final_message


def chat_once(
    api_url: str,
    model: str,
    messages: list[dict],
    think: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Non-streaming single-shot chat call. Used by internal reasoning/curation
    passes where we just need the final text, not live UI updates."""
    url = f"{_base(api_url)}/api/chat"
    payload = {"model": model, "messages": messages, "stream": False}
    if think:
        payload["think"] = True
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result.get("message", {}).get("content", "").strip()
    except (urllib.error.URLError, OSError) as e:
        raise OllamaError(f"Ollama request failed: {e}") from e
