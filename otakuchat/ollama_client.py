"""Thin client for the local Ollama HTTP API. No other providers — by design.

Uses urllib only (stdlib), matching the project's zero-dependency network
posture, but supports real token streaming via NDJSON line reads.
"""
import json
import urllib.request
import urllib.error
from typing import Callable, Iterator

DEFAULT_TIMEOUT = 120


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


def chat_stream(
    api_url: str,
    model: str,
    messages: list[dict],
    on_token: Callable[[str], None],
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Stream a chat completion from Ollama, calling on_token per text chunk.

    Returns the final assembled message dict (role/content/tool_calls if any).
    """
    url = f"{_base(api_url)}/api/chat"
    payload = {"model": model, "messages": messages, "stream": True}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )

    full_content = ""
    final_message: dict = {"role": "assistant", "content": ""}

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                chunk = json.loads(line)
                msg = chunk.get("message", {})
                piece = msg.get("content", "")
                if piece:
                    full_content += piece
                    on_token(piece)
                if chunk.get("done"):
                    final_message = msg or final_message
                    final_message["content"] = full_content
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
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Non-streaming single-shot chat call. Used by internal reasoning/curation
    passes where we just need the final text, not live UI updates."""
    url = f"{_base(api_url)}/api/chat"
    payload = {"model": model, "messages": messages, "stream": False}
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
