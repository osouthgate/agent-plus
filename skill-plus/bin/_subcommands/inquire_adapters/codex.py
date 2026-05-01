"""codex (OpenAI Codex CLI) transcript adapter (stub).

TBD on contact. Codex sessions live under `~/.codex/sessions/` when present.
Stub returns no tuples so auto-discovery is safe.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Tuple


def iter_tuples(path: Path) -> Iterator[Tuple[str, str, str, str, dict]]:
    return
    yield  # pragma: no cover
