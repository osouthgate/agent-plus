"""Tests for skill-plus opportunities."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-plus"


def _encoded(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def _run(*args: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True, timeout=30, cwd=str(cwd), env=env,
    )


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    state = tmp_path / "state"
    feedback = tmp_path / "feedback"
    home.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    feedback.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["SKILL_PLUS_DIR"] = str(state)
    env["SKILL_FEEDBACK_DIR"] = str(feedback)
    return env


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _bash_line(cmd: str, session_id: str) -> str:
    return json.dumps({
        "type": "assistant",
        "sessionId": session_id,
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": cmd}},
        ]},
    })


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    return root


def test_empty_opportunities_report(repo: Path, tmp_path: Path):
    env = _env(tmp_path)
    res = _run("opportunities", "--project", str(repo), "--pretty", cwd=repo, env=env)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["tool"]["name"] == "skill-plus"
    assert payload["summary"]["total"] == 0
    assert payload["opportunities"] == []
    assert "run more sessions" in payload["note"]


def test_opportunities_combines_skill_cadence_friction_and_feedback(repo: Path, tmp_path: Path):
    env = _env(tmp_path)
    state = Path(env["SKILL_PLUS_DIR"])
    feedback = Path(env["SKILL_FEEDBACK_DIR"])

    _write_jsonl(state / "candidates.jsonl", [{
        "id": "skill111",
        "key": "railway logs --service",
        "count": 7,
        "sessions": ["s1", "s2"],
        "lastSeen": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }])
    _write_jsonl(state / "routine-candidates.jsonl", [{
        "id": "routine222",
        "kind": "routine",
        "key": "railway deploy --service",
        "count": 9,
        "distinctDates": 8,
        "regularity": {"weekdayClass": "weekday"},
        "scheduleString": "On weekday mornings around 09:00 UTC, run railway deploy",
    }])
    _write_jsonl(state / "blocks.jsonl", [{
        "id": "block333",
        "signature": "Bash: supabase",
        "class": "USER_REJECTED_PROMPT",
        "count": 4,
        "configFixable": True,
    }])
    _write_jsonl(feedback / "repo-analyze.jsonl", [{
        "ts": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None, microsecond=0).isoformat(),
        "rating": 2,
        "outcome": "confusing",
        "friction": "unclear output",
    }])

    res = _run("opportunities", "--project", str(repo), "--pretty", cwd=repo, env=env)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    kinds = {o["kind"] for o in payload["opportunities"]}
    assert {"skill", "routine", "friction", "feedback"} <= kinds
    assert payload["summary"]["byKind"]["skill"] == 1
    commands = [o["command"] for o in payload["opportunities"]]
    assert any(c.startswith("skill-plus scaffold railway-logs") for c in commands)
    assert any(c == "skill-plus propose --kind routine --pretty" for c in commands)
    assert payload["summary"]["topAction"]


def test_run_scan_refreshes_candidates_before_reporting(repo: Path, tmp_path: Path):
    env = _env(tmp_path)
    sess_dir = Path(env["HOME"]) / ".claude" / "projects" / _encoded(repo)
    _write_lines(sess_dir / "s1.jsonl", [_bash_line("railway logs --service api", "s1") for _ in range(3)])
    _write_lines(sess_dir / "s2.jsonl", [_bash_line("railway logs --service api", "s2") for _ in range(2)])

    res = _run(
        "opportunities", "--project", str(repo), "--run-scan",
        "--accept-consent", "--pretty", cwd=repo, env=env,
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["scan"]["ran"] is True
    assert payload["scan"]["ok"] is True
    assert payload["scan"]["summary"]["sessionsScanned"] == 2
    assert payload["summary"]["byKind"]["skill"] >= 1
    assert any(o["evidence"]["key"] == "railway logs --service" for o in payload["opportunities"])


def test_run_scan_failure_surfaces_clean_error(repo: Path, tmp_path: Path):
    env = _env(tmp_path)
    res = _run("opportunities", "--project", str(repo), "--run-scan", "--pretty", cwd=repo, env=env)
    assert res.returncode == 2
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "scan_failed"
    assert payload["scan"]["returnCode"] == 2
    assert payload["scan"]["payload"]["error"] == "consent_required"
