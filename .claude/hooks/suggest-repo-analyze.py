#!/usr/bin/env python3
"""UserPromptSubmit hook: suggest repo-analyze if not yet run in this repo.

Injected into user projects by `agent-plus-meta init`. Prints a one-line hint
to stdout (which Claude Code inserts into Claude's context) when the repo
hasn't been scanned yet. Silent when the stamp exists or when the prompt
starts with / (slash command).
"""
from __future__ import annotations
import os
import re
import subprocess
import sys
from pathlib import Path


def _msys_to_windows(p: str) -> str:
    """Convert MSYS/Git-Bash paths like /c/dev/foo -> C:/dev/foo on Windows."""
    if sys.platform == "win32":
        m = re.match(r"^/([a-zA-Z])(/.*)$", p)
        if m:
            return m.group(1).upper() + ":" + m.group(2)
    return p


def _git_toplevel(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(_msys_to_windows(out.stdout.strip()))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def main() -> int:
    prompt = os.environ.get("CLAUDE_USER_PROMPT", "")
    if prompt.startswith("/"):
        return 0
    cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())).resolve()
    git_root = _git_toplevel(cwd)
    if git_root is None:
        return 0
    stamp = git_root / ".agent-plus" / "repo-analyze.stamp"
    if stamp.exists():
        return 0
    print("[agent-plus] This repo hasn't been scanned yet. "
          "Run /repo-analyze:repo-analyze for full context before starting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
