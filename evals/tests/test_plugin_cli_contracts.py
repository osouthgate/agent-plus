"""Subprocess JSON contracts for plugins not yet exercised elsewhere in evals."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_py(bin_path: Path, args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    e = {**os.environ, **env} if env is not None else None
    return subprocess.run(
        [sys.executable, str(bin_path), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=e,
    )


def _git_init(cwd: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(cwd), check=True)
    subprocess.run(["git", "config", "user.email", "eval@test.local"], cwd=str(cwd), check=True)
    subprocess.run(["git", "config", "user.name", "eval"], cwd=str(cwd), check=True)


def test_skill_feedback_log_and_report_json(plugin_bin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILL_FEEDBACK_DIR", str(tmp_path))
    fb = plugin_bin("skill-feedback")
    r_log = _run_py(
        fb,
        ["log", "evalsmoke", "--rating", "4", "--outcome", "success"],
        cwd=tmp_path,
    )
    assert r_log.returncode == 0, r_log.stderr
    log_payload = json.loads(r_log.stdout)
    assert log_payload["tool"]["name"] == "skill-feedback"
    assert log_payload["tool"].get("version")

    r_rep = _run_py(fb, ["report"], cwd=tmp_path)
    assert r_rep.returncode == 0, r_rep.stderr
    rep = json.loads(r_rep.stdout)
    assert rep["tool"]["name"] == "skill-feedback"
    assert rep.get("total_entries", 0) >= 1
    assert isinstance(rep.get("skills"), list)
    assert rep["skills"], "expected one skill summary after log"


def test_skill_plus_propose_json_with_candidates(plugin_bin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git_init(tmp_path)
    state = tmp_path / ".agent-plus" / "skill-plus"
    state.mkdir(parents=True)
    monkeypatch.setenv("SKILL_PLUS_DIR", str(state))
    row = {
        "id": "eval-e1",
        "key": "kubectl get pods",
        "count": 5,
        "sessions": 2,
        "lastSeen": "2026-04-29T12:00:00Z",
    }
    (state / "candidates.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    sp = plugin_bin("skill-plus")
    r = _run_py(sp, ["propose", "--json", "--limit", "5"], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["tool"]["name"] == "skill-plus"
    assert data["tool"].get("version")
    assert data.get("candidatesTotal", 0) >= 1
    cands = data.get("candidates")
    assert isinstance(cands, list) and cands
    c0 = cands[0]
    for k in ("id", "score", "proposedSkillName"):
        assert k in c0, f"missing {k} in {c0!r}"


def test_skill_plus_scaffold_writes_project_skill(plugin_bin, tmp_path: Path) -> None:
    _git_init(tmp_path)
    name = "eval-scaffold-smoke"
    sp = plugin_bin("skill-plus")
    r = _run_py(
        sp,
        [
            "scaffold",
            name,
            "--description",
            "Eval contract smoke skill.",
            "--when-to-use",
            "When pytest runs eval/scaffold contract tests.",
            "--killer-command",
            "eval-smoke --json",
            "--do-not-use-for",
            "Anything outside the eval harness",
        ],
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["tool"]["name"] == "skill-plus"
    assert data.get("ok") is True
    assert data.get("name") == name
    skill_dir = tmp_path / ".claude" / "skills" / name
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "bin" / name).is_file()
