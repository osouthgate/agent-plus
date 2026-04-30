"""Tests for skill-plus propose subcommand."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-plus"


def _run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True, timeout=30,
        cwd=str(cwd) if cwd else None, env=e,
    )


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(path), check=True)


def _write_candidates(repo: Path, rows: list[dict]) -> Path:
    state = repo / ".agent-plus" / "skill-plus"
    state.mkdir(parents=True, exist_ok=True)
    p = state / "candidates.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return p


@pytest.fixture
def repo(tmp_path: Path, monkeypatch):
    _git_init(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_empty_log_clean_envelope(repo: Path):
    res = _run("propose", "--pretty", cwd=repo)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["tool"]["name"] == "skill-plus"
    assert payload["candidates"] == []
    assert payload["candidatesTotal"] == 0
    assert "note" in payload
    assert "scan first" in payload["note"]


def test_envelope_shape(repo: Path):
    _write_candidates(repo, [
        {"id": "a1", "key": "psql -c", "count": 5, "sessions": 2, "lastSeen": "2026-04-29T00:00:00Z"},
    ])
    res = _run("propose", "--pretty", cwd=repo)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    for k in ("tool", "project", "candidatesTotal", "candidatesShown", "candidates"):
        assert k in payload, f"missing {k}"
    assert payload["candidatesShown"] == 1
    c = payload["candidates"][0]
    for k in ("score", "daysSinceLastSeen", "proposedSkillName", "existing", "kind"):
        assert k in c


def test_ranking_orders_by_score(repo: Path):
    # high-count old, low-count fresh, mid both
    rows = [
        {"id": "old", "key": "old cmd", "count": 100, "sessions": 1, "lastSeen": "2020-01-01T00:00:00Z"},
        {"id": "fresh", "key": "fresh cmd", "count": 4, "sessions": 1, "lastSeen": "2026-04-30T00:00:00Z"},
        {"id": "mid", "key": "mid cmd", "count": 8, "sessions": 4, "lastSeen": "2026-04-25T00:00:00Z"},
    ]
    _write_candidates(repo, rows)
    res = _run("propose", "--pretty", cwd=repo)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    ids = [c["id"] for c in payload["candidates"]]
    # old has score 100 + 0.5 + 0 = 100.5 → highest
    # mid has score 8 + 2 + recency_boost (depends on now)
    # fresh has score 4 + 0.5 + ~7 ≈ 11.5
    assert ids[0] == "old"
    # mid vs fresh: mid score ≈ 8 + 2 + recency, fresh ≈ 4 + 0.5 + ~7 ≈ 11.5
    # mid recency ≈ 7 - days_since(2026-04-25). Today is 2026-04-30 → 5 days → boost=2 → mid ≈ 12
    # mid wins narrowly.
    assert set(ids) == {"old", "fresh", "mid"}


def test_limit_caps_results(repo: Path):
    rows = [
        {"id": f"c{i}", "key": f"cmd{i}", "count": 10 - i, "sessions": 1, "lastSeen": "2026-04-29T00:00:00Z"}
        for i in range(5)
    ]
    _write_candidates(repo, rows)
    res = _run("propose", "--limit", "2", "--pretty", cwd=repo)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["candidatesShown"] == 2
    assert len(payload["candidates"]) == 2
    assert payload["candidatesTotal"] == 5


def test_limit_below_one_treated_as_default(repo: Path):
    rows = [
        {"id": f"c{i}", "key": f"cmd{i}", "count": 10 - i, "sessions": 1, "lastSeen": "2026-04-29T00:00:00Z"}
        for i in range(12)
    ]
    _write_candidates(repo, rows)
    res = _run("propose", "--limit", "0", "--pretty", cwd=repo)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["candidatesShown"] == 10


def test_proposed_skill_name_derivation(repo: Path):
    rows = [
        {"id": "abcdef123", "key": "railway logs --service api", "count": 5, "sessions": 1, "lastSeen": "2026-04-29T00:00:00Z"},
        {"id": "psql01", "key": "psql -c 'select 1'", "count": 5, "sessions": 1, "lastSeen": "2026-04-29T00:00:00Z"},
        {"id": "junk-only", "key": "--all-flags --here", "count": 5, "sessions": 1, "lastSeen": "2026-04-29T00:00:00Z"},
    ]
    _write_candidates(repo, rows)
    res = _run("propose", "--pretty", cwd=repo)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    by_id = {c["id"]: c for c in payload["candidates"]}
    assert by_id["abcdef123"]["proposedSkillName"] == "railway-logs"
    assert by_id["psql01"]["proposedSkillName"] == "psql"
    assert by_id["junk-only"]["proposedSkillName"].startswith("skill-")


def test_existing_skill_flips_to_enhance(repo: Path):
    skill_dir = repo / ".claude" / "skills" / "railway-logs"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("placeholder", encoding="utf-8")

    _write_candidates(repo, [
        {"id": "x1", "key": "railway logs --service api", "count": 5, "sessions": 1, "lastSeen": "2026-04-29T00:00:00Z"},
        {"id": "x2", "key": "psql -c", "count": 5, "sessions": 1, "lastSeen": "2026-04-29T00:00:00Z"},
    ])
    res = _run("propose", "--pretty", cwd=repo)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    by_id = {c["id"]: c for c in payload["candidates"]}
    assert by_id["x1"]["existing"] is True
    assert by_id["x1"]["kind"] == "enhance"
    assert by_id["x2"]["existing"] is False
    assert by_id["x2"]["kind"] == "new"


def test_handles_sessions_as_list(repo: Path):
    """`sessions` may be either an int count or a list of session ids."""
    _write_candidates(repo, [
        {"id": "a", "key": "foo", "count": 3, "sessions": ["s1", "s2", "s3"], "lastSeen": "2026-04-29T00:00:00Z"},
    ])
    res = _run("propose", "--pretty", cwd=repo)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    c = payload["candidates"][0]
    # 3 + 0.5*3 + recency ≈ 4.5 + ~6
    assert c["score"] >= 4.5
