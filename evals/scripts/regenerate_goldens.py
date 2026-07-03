#!/usr/bin/env python3
"""Rewrite evals/golden/*-repo-analyze.json from current fixtures. Stdlib only."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "repo-analyze" / "bin" / "repo-analyze"


def main() -> int:
    for name in ("osdb", "rainshift", "tinker-tailor"):
        fp = ROOT / "evals" / "fixtures" / name
        r = subprocess.run(
            [sys.executable, str(BIN), "--path", str(fp), "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
            return r.returncode or 1
        out = ROOT / "evals" / "golden" / f"{name}-repo-analyze.json"
        # Validate JSON round-trip
        json.loads(r.stdout)
        out.write_text(r.stdout, encoding="utf-8")
        print("wrote", out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
