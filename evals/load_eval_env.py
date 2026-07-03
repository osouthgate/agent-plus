"""Load optional local env for eval tooling (Ollama vars). Stdlib only."""

from __future__ import annotations

import os
from pathlib import Path


_OLLAMA_REL = Path("evals") / "ollama.env"


def load_ollama_env(repo_root: Path | None = None) -> Path | None:
    """
    If ``evals/ollama.env`` exists under repo_root, parse KEY=value lines into os.environ.

    Does not override keys already set in the process environment.
    Returns the path if the file existed and was read, else None.
    """
    root = repo_root or Path(__file__).resolve().parent.parent
    path = root / _OLLAMA_REL
    if not path.is_file():
        return None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, rest = line.partition("=")
        key = key.strip()
        if not key.startswith("OLLAMA_"):
            continue
        val = rest.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key not in os.environ:
            os.environ[key] = val
    return path
