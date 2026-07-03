#!/usr/bin/env python3
"""Append one timing snapshot (JSON line) for eval CLIs + optional pytest. Stdlib only.

Writes to evals/benchmarks/history.jsonl (gitignored). Each line includes git HEAD so runs
are comparable across commits without committing noisy rolling logs to the repo."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HISTORY = ROOT / "evals" / "benchmarks" / "history.jsonl"
RA = ROOT / "repo-analyze" / "bin" / "repo-analyze"
DS = ROOT / "diff-summary" / "bin" / "diff-summary"
FIXTURES = ("osdb", "rainshift", "tinker-tailor")


def _git_head(root: Path) -> tuple[str, str]:
    def _one(args: list[str]) -> str:
        r = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if r.returncode != 0:
            return ""
        return r.stdout.strip()

    full = _one(["rev-parse", "HEAD"])
    short = _one(["rev-parse", "--short", "HEAD"])
    return full, short


def _time_repo_analyze_ms(fixture: str) -> float:
    fp = ROOT / "evals" / "fixtures" / fixture
    t0 = time.perf_counter()
    r = subprocess.run(
        [sys.executable, str(RA), "--path", str(fp), "--json"],
        cwd=str(ROOT),
        capture_output=True,
        timeout=120,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if r.returncode != 0:
        return -1.0
    return round(elapsed_ms, 2)


def _time_diff_summary_ms(fixture: str) -> float:
    fp = ROOT / "evals" / "fixtures" / fixture
    t0 = time.perf_counter()
    r = subprocess.run(
        [sys.executable, str(DS), "--path", ".", "--range", "HEAD~1..HEAD", "--json"],
        cwd=str(fp),
        capture_output=True,
        timeout=120,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if r.returncode != 0:
        return -1.0
    return round(elapsed_ms, 2)


def _time_pytest_evals_ms() -> float:
    t0 = time.perf_counter()
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "evals/tests/", "-q"],
        cwd=str(ROOT),
        capture_output=True,
        timeout=600,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if r.returncode != 0:
        return -1.0
    return round(elapsed_ms, 2)


def main() -> int:
    ap = argparse.ArgumentParser(description="Record eval benchmark timings with git revision.")
    ap.add_argument(
        "--no-pytest",
        action="store_true",
        help="Skip timing the full evals pytest suite.",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=HISTORY,
        help=f"JSONL file to append (default: {HISTORY})",
    )
    args = ap.parse_args()

    if not RA.is_file():
        print("repo-analyze bin missing", file=sys.stderr)
        return 1
    if not DS.is_file():
        print("diff-summary bin missing", file=sys.stderr)
        return 1

    commit, commit_short = _git_head(ROOT)
    timings: dict[str, float | dict[str, float]] = {}
    for fx in FIXTURES:
        timings[f"repo_analyze_{fx}_ms"] = _time_repo_analyze_ms(fx)
    for fx in FIXTURES:
        timings[f"diff_summary_{fx}_ms"] = _time_diff_summary_ms(fx)
    if not args.no_pytest:
        timings["pytest_evals_ms"] = _time_pytest_evals_ms()

    row = {
        "recordedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": commit or None,
        "commitShort": commit_short or None,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "timingsMs": timings,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")

    print(json.dumps(row, indent=2))
    print(f"\nAppended to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
