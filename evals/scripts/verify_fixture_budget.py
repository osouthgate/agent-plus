#!/usr/bin/env python3
"""Fail if evals/fixtures/* look like accidental full repo copies (public-agent-plus guard). Stdlib only."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "evals" / "fixtures"
# osdb is always synthetic (tiny). rainshift / tinker-tailor may be real repo copies.
# Real-repo copies (node_modules / .claude / binaries excluded) are ~300-2000 files / up to 30 MB.
MAX_FILES_SYNTHETIC = 300
MAX_BYTES_SYNTHETIC = 6 * 1024 * 1024
MAX_FILES_REAL = 3000
MAX_BYTES_REAL = 50 * 1024 * 1024
EXPECTED = ("osdb", "rainshift", "tinker-tailor")


def _walk_stats(d: Path) -> tuple[int, int]:
    nfiles = 0
    nbytes = 0
    for dirpath, _dirnames, filenames in os.walk(d):
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                nbytes += p.stat().st_size
            except OSError:
                pass
            nfiles += 1
    return nfiles, nbytes


def main() -> int:
    if not FIX.is_dir():
        print(f"skip: no {FIX}", file=sys.stderr)
        return 0
    bad = False
    for name in EXPECTED:
        d = FIX / name
        if not d.is_dir():
            print(f"FAIL: missing fixture dir {d} — run evals/scripts/bootstrap_fixtures.py", file=sys.stderr)
            bad = True
            continue
        source_marker = d / ".fixture-source"
        is_real = source_marker.is_file() and source_marker.read_text().startswith("real:")
        max_files = MAX_FILES_REAL if is_real else MAX_FILES_SYNTHETIC
        max_bytes = MAX_BYTES_REAL if is_real else MAX_BYTES_SYNTHETIC
        source_label = "real-repo copy" if is_real else "synthetic"
        nfiles, nbytes = _walk_stats(d)
        if nfiles > max_files or nbytes > max_bytes:
            print(
                f"FAIL {name} ({source_label}): files={nfiles} (max {max_files}), "
                f"bytes={nbytes} (max {max_bytes}). "
                "Fixture too large — check that node_modules / .claude / binaries are excluded.",
                file=sys.stderr,
            )
            bad = True
        else:
            print(f"OK {name} ({source_label}): files={nfiles}, bytes={nbytes}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
