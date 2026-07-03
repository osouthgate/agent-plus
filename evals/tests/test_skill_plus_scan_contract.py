"""Hermetic subprocess contract for `skill-plus scan` using committed JSONL fixtures.

Duplicates encoding/session layout from `skill-plus/test/test_scan.py` so evals do not
import the skill-plus bin as a library."""

from __future__ import annotations

import json
import os
import re
import time
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[2] / "skill-plus" / "bin" / "skill-plus"


def _encoded(path: Path) -> str:
    """Independent oracle for Claude Code's project-dir encoding (every
    non-alphanumeric character of the resolved path dashed, one-for-one).
    Deliberately NOT delegated to bin/skill-plus's _encode_project_path: if
    this helper called into the module under test, a regression in the
    implementation would move both in lockstep and these fixtures would
    silently keep matching a broken encoder -- which is exactly how the
    original bug (collapsed/stripped/re-prepended dashes) escaped detection."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def _fake_projects_env(tmp_path: Path, skill_state: Path) -> dict[str, str]:
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)
    env["SKILL_PLUS_DIR"] = str(skill_state)
    return env


def test_skill_plus_scan_fixture_railway_cluster(tmp_path: Path, repo_root: Path):
    """Committed fixtures → scan emits envelope + railway cluster (matches plugin happy-path test)."""
    assert BIN.is_file(), BIN
    fx = repo_root / "evals" / "fixtures" / "skill-plus-scan"
    assert (fx / "s1.jsonl").is_file() and (fx / "s2.jsonl").is_file()

    proj = (tmp_path / "scan-fixture-proj").resolve()
    proj.mkdir(parents=True, exist_ok=True)
    sess_dir = tmp_path / "home" / ".claude" / "projects" / _encoded(proj)
    sess_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fx / "s1.jsonl", sess_dir / "s1.jsonl")
    shutil.copyfile(fx / "s2.jsonl", sess_dir / "s2.jsonl")

    state = tmp_path / "skill-plus-state"
    env = _fake_projects_env(tmp_path, state)
    res = subprocess.run(
        [
            sys.executable,
            str(BIN),
            "scan",
            "--project",
            str(proj),
            "--accept-consent",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload.get("tool", {}).get("name") == "skill-plus"
    assert payload["sessionsScanned"] == 2
    assert payload.get("candidatesNew", 0) >= 1
    keys = [c["key"] for c in payload["candidates"]]
    assert "railway logs --service" in keys
    cand = next(c for c in payload["candidates"] if c["key"] == "railway logs --service")
    assert cand["count"] == 5
    assert sorted(cand["sessions"]) == ["s1", "s2"]

    cand_path = state / "candidates.jsonl"
    assert cand_path.is_file()
    lines = [ln for ln in cand_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines, "candidates.jsonl should persist mined rows"
    rec = json.loads(lines[0])
    assert "id" in rec and "examples" in rec


def test_skill_plus_scan_fixture_langfuse_remote_cluster(tmp_path: Path, repo_root: Path):
    """Same clustering rules as railway — repeated Bash must use the CLI binary name as the
    first token (not `python …`), matching how marketplace wrappers like langfuse-remote are used."""
    assert BIN.is_file(), BIN
    fx = repo_root / "evals" / "fixtures" / "skill-plus-scan"
    assert (fx / "lf-s1.jsonl").is_file() and (fx / "lf-s2.jsonl").is_file()

    proj = (tmp_path / "scan-langfuse-proj").resolve()
    proj.mkdir(parents=True, exist_ok=True)
    sess_dir = tmp_path / "home" / ".claude" / "projects" / _encoded(proj)
    sess_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fx / "lf-s1.jsonl", sess_dir / "lf-s1.jsonl")
    shutil.copyfile(fx / "lf-s2.jsonl", sess_dir / "lf-s2.jsonl")

    state = tmp_path / "skill-plus-state-langfuse"
    env = _fake_projects_env(tmp_path, state)
    res = subprocess.run(
        [
            sys.executable,
            str(BIN),
            "scan",
            "--project",
            str(proj),
            "--accept-consent",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload.get("tool", {}).get("name") == "skill-plus"
    assert payload["sessionsScanned"] == 2
    assert payload.get("candidatesNew", 0) >= 1
    keys = [c["key"] for c in payload["candidates"]]
    assert "langfuse-remote health --json" in keys
    cand = next(c for c in payload["candidates"] if c["key"] == "langfuse-remote health --json")
    assert cand["count"] == 5
    assert sorted(cand["sessions"]) == ["lf-s1", "lf-s2"]


def test_skill_plus_scan_second_pass_new_cluster(tmp_path: Path, repo_root: Path):
    """Two-phase scan: first pass matches marketing 'session loop' (railway only); add
    'later' session files, bump mtimes, second pass surfaces a new cluster (kubectl)
    while merging state (see `skill-plus/test/test_scan.py::test_dedupe_on_second_run`)."""
    assert BIN.is_file(), BIN
    fx = repo_root / "evals" / "fixtures" / "skill-plus-scan"
    for name in ("s1.jsonl", "s2.jsonl", "day2-a.jsonl", "day2-b.jsonl"):
        assert (fx / name).is_file(), fx / name

    proj = (tmp_path / "scan-phased-proj").resolve()
    proj.mkdir(parents=True, exist_ok=True)
    sess_dir = tmp_path / "home" / ".claude" / "projects" / _encoded(proj)
    sess_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fx / "s1.jsonl", sess_dir / "s1.jsonl")
    shutil.copyfile(fx / "s2.jsonl", sess_dir / "s2.jsonl")

    state = tmp_path / "skill-plus-state"
    env = _fake_projects_env(tmp_path, state)

    r1 = subprocess.run(
        [sys.executable, str(BIN), "scan", "--project", str(proj), "--accept-consent"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert r1.returncode == 0, r1.stdout + r1.stderr
    p1 = json.loads(r1.stdout)
    assert p1["candidatesNew"] >= 1
    keys1 = {c["key"] for c in p1["candidates"]}
    assert "railway logs --service" in keys1

    shutil.copyfile(fx / "day2-a.jsonl", sess_dir / "day2-a.jsonl")
    shutil.copyfile(fx / "day2-b.jsonl", sess_dir / "day2-b.jsonl")
    now = time.time() + 5
    for f in sess_dir.glob("*.jsonl"):
        os.utime(f, (now, now))

    r2 = subprocess.run(
        [sys.executable, str(BIN), "scan", "--project", str(proj), "--accept-consent"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert r2.returncode == 0, r2.stdout + r2.stderr
    p2 = json.loads(r2.stdout)
    keys2 = {c["key"] for c in p2["candidates"]}
    assert "kubectl get pods" in keys2
    assert p2["candidatesNew"] >= 1
    assert p2["candidatesUpdated"] >= 1
