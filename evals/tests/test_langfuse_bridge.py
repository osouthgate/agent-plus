"""Bridge eval: same project layout shows stack markers → `langfuse-remote` and session mining → scaffold candidate."""

from __future__ import annotations

import json
import os
import re
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


def test_langfuse_bridge_markers_and_scan_cluster(
    agent_plus_meta_bin_mod, tmp_path: Path, repo_root: Path
) -> None:
    """Committed `evals/fixtures/langfuse-bridge`: langfuse.yaml → suggest marketplace plugin;
    synthetic sessions → `skill-plus scan` surfaces `langfuse-remote …` Bash cluster."""
    bridge = repo_root / "evals" / "fixtures" / "langfuse-bridge"
    assert (bridge / "langfuse.yaml").is_file(), bridge
    fx_sess = bridge / "sessions"
    assert (fx_sess / "lf-s1.jsonl").is_file() and (fx_sess / "lf-s2.jsonl").is_file()

    names = [x["name"] for x in agent_plus_meta_bin_mod.detect_suggested_skills(bridge)]
    assert "langfuse-remote" in names

    proj = bridge.resolve()
    sess_dir = tmp_path / "home" / ".claude" / "projects" / _encoded(proj)
    sess_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fx_sess / "lf-s1.jsonl", sess_dir / "lf-s1.jsonl")
    shutil.copyfile(fx_sess / "lf-s2.jsonl", sess_dir / "lf-s2.jsonl")

    assert BIN.is_file(), BIN
    state = tmp_path / "skill-plus-bridge-state"
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
    keys = [c["key"] for c in payload["candidates"]]
    assert "langfuse-remote health --json" in keys
    cand = next(c for c in payload["candidates"] if c["key"] == "langfuse-remote health --json")
    assert cand["count"] == 5
    assert sorted(cand["sessions"]) == ["lf-s1", "lf-s2"]
