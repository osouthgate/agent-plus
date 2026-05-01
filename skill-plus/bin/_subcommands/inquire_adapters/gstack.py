"""gstack transcript adapter (stub).

TBD: gstack writes session data under `~/.gstack/projects/`, but the on-disk
format has not been pinned down for v0.17.0. This adapter is a no-op so
auto-discovery doesn't crash on the directory existing.

Replace `iter_tuples` with the real parser when the format is finalized.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Tuple


def iter_tuples(path: Path) -> Iterator[Tuple[str, str, str, str, dict]]:
    return
    yield  # pragma: no cover — generator stub
