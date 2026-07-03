"""Minimal Ollama HTTP chat helper for eval tests (stdlib only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urljoin


def chat(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    timeout: float = 120.0,
) -> str:
    """POST /api/chat, non-streaming. Returns assistant message content or empty string."""
    base = base_url.rstrip("/") + "/"
    url = urljoin(base, "api/chat")
    payload = json.dumps(
        {"model": model, "messages": messages, "stream": False},
        separators=(",", ":"),
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"Ollama HTTP {e.code}: {detail[:500]}") from e
    data = json.loads(raw)
    msg = data.get("message") or {}
    return str(msg.get("content") or "").strip()


def ping(base_url: str, *, timeout: float = 5.0) -> bool:
    """GET /api/tags — True if server responds."""
    base = base_url.rstrip("/") + "/"
    url = urljoin(base, "api/tags")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False
