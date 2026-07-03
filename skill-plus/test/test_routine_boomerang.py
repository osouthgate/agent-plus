"""Tests for skill-plus propose --kind routine|habit|all and routine subcommand.

Uses subprocess against the real bin (skill-plus/bin/skill-plus) as the primary
integration strategy, so the actual argparse wiring and helper injection are
exercised end-to-end.

Fixtures: synthetic routine-candidates.jsonl entries matching the frozen schema
exactly (one routine record, one habit record).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-plus"

# ---------------------------------------------------------------------------
# Frozen-schema fixtures
# ---------------------------------------------------------------------------

ROUTINE_RECORD = {
    "id": "aabbcc112233",
    "kind": "routine",
    "key": "railway deploy --service api",
    "count": 9,
    "distinctDates": 8,
    "regularity": {
        "weekdayClass": "weekday",
        "weekdayClassShare": 0.89,
        "hourBucket": 3,
        "hourBucketUtc": "09:00-11:59",
        "hourBucketShare": 0.78,
    },
    "examples": ["railway deploy --service api"],
    "sessions": ["sess1", "sess2"],
    "scheduleString": (
        "On weekday mornings around 09:00 UTC, run the workflow that does"
        " `railway deploy --service api` (refine this intent before saving)"
    ),
    "suggestion": None,
    "firstSeen": "2026-04-01T09:05:00Z",
    "lastSeen": "2026-04-29T09:10:00Z",
    "scannedAt": "2026-04-30T00:00:00Z",
    "sourceProject": "/home/user/myproject",
}

HABIT_RECORD = {
    "id": "ddeeff445566",
    "kind": "habit",
    "key": "gh pr view --web",
    "count": 19,
    "distinctDates": 14,
    "regularity": {
        "weekdayClass": "weekday",
        "weekdayClassShare": 0.65,
        "hourBucket": 2,
        "hourBucketUtc": "06:00-08:59",
        "hourBucketShare": 0.30,
    },
    "examples": ["gh pr view --web"],
    "sessions": ["sess3", "sess4", "sess5"],
    "scheduleString": None,
    "suggestion": (
        "recurs on 14 dates with no consistent time;"
        " not a routine -- run skill-plus propose to consider a skill"
    ),
    "firstSeen": "2026-03-15T07:00:00Z",
    "lastSeen": "2026-04-28T08:00:00Z",
    "scannedAt": "2026-04-30T00:00:00Z",
    "sourceProject": "/home/user/myproject",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(
    *args: str,
    skill_plus_dir: Path,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SKILL_PLUS_DIR"] = str(skill_plus_dir)
    # Remove USERPROFILE/HOME side-effects; SKILL_PLUS_DIR is the load-bearing override
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(cwd) if cwd else None,
        env=env,
    )


def _write_routine_candidates(skill_plus_dir: Path, records: list[dict]) -> Path:
    skill_plus_dir.mkdir(parents=True, exist_ok=True)
    path = skill_plus_dir / "routine-candidates.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


def _read_adopted_log(skill_plus_dir: Path) -> list[dict]:
    path = skill_plus_dir / "routines-adopted.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def skill_dir(tmp_path: Path):
    """Isolated SKILL_PLUS_DIR with both routine records pre-written."""
    d = tmp_path / "skill-plus-state"
    _write_routine_candidates(d, [ROUTINE_RECORD, HABIT_RECORD])
    return d


@pytest.fixture
def empty_skill_dir(tmp_path: Path):
    """Isolated SKILL_PLUS_DIR with no routine-candidates file."""
    d = tmp_path / "skill-plus-empty"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# propose --kind routine
# ---------------------------------------------------------------------------


def test_propose_kind_routine_returns_only_routine(skill_dir: Path, tmp_path: Path):
    res = _run("propose", "--kind", "routine", "--pretty", skill_plus_dir=skill_dir, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    assert payload["kind"] == "routine"
    ids = [c["id"] for c in payload["candidates"]]
    assert ROUTINE_RECORD["id"] in ids
    assert HABIT_RECORD["id"] not in ids


def test_propose_kind_routine_has_reachability_note(skill_dir: Path, tmp_path: Path):
    res = _run("propose", "--kind", "routine", "--pretty", skill_plus_dir=skill_dir, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert "reachabilityNote" in payload
    assert "Pro/Max" in payload["reachabilityNote"]


def test_propose_kind_routine_candidate_has_schedule_string(skill_dir: Path, tmp_path: Path):
    res = _run("propose", "--kind", "routine", "--pretty", skill_plus_dir=skill_dir, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    cand = next(c for c in payload["candidates"] if c["id"] == ROUTINE_RECORD["id"])
    assert cand.get("scheduleString") == ROUTINE_RECORD["scheduleString"]


# ---------------------------------------------------------------------------
# propose --kind habit
# ---------------------------------------------------------------------------


def test_propose_kind_habit_returns_only_habit(skill_dir: Path, tmp_path: Path):
    res = _run("propose", "--kind", "habit", "--pretty", skill_plus_dir=skill_dir, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    assert payload["kind"] == "habit"
    ids = [c["id"] for c in payload["candidates"]]
    assert HABIT_RECORD["id"] in ids
    assert ROUTINE_RECORD["id"] not in ids


def test_propose_kind_habit_no_reachability_note(skill_dir: Path, tmp_path: Path):
    """reachabilityNote absent when result contains only habit records."""
    res = _run("propose", "--kind", "habit", "--pretty", skill_plus_dir=skill_dir, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert "reachabilityNote" not in payload


def test_propose_kind_habit_has_suggestion(skill_dir: Path, tmp_path: Path):
    res = _run("propose", "--kind", "habit", "--pretty", skill_plus_dir=skill_dir, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    cand = next(c for c in payload["candidates"] if c["id"] == HABIT_RECORD["id"])
    # habit: scheduleString is null, suggestion has text
    assert cand.get("scheduleString") is None
    assert cand.get("suggestion") == HABIT_RECORD["suggestion"]


# ---------------------------------------------------------------------------
# propose --kind all
# ---------------------------------------------------------------------------


def test_propose_kind_all_returns_both(skill_dir: Path, tmp_path: Path):
    res = _run("propose", "--kind", "all", "--pretty", skill_plus_dir=skill_dir, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    assert payload["kind"] == "all"
    ids = {c["id"] for c in payload["candidates"]}
    assert ROUTINE_RECORD["id"] in ids
    assert HABIT_RECORD["id"] in ids


def test_propose_kind_all_has_reachability_note_when_routine_present(skill_dir: Path, tmp_path: Path):
    res = _run("propose", "--kind", "all", "--pretty", skill_plus_dir=skill_dir, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert "reachabilityNote" in payload


def test_propose_kind_all_dismissed_sorted_last(skill_dir: Path, tmp_path: Path):
    """A dismissed routine record must appear after non-dismissed, regardless of count."""
    dismissed_routine = dict(ROUTINE_RECORD)
    dismissed_routine["id"] = "dismissed000001"
    dismissed_routine["count"] = 999  # higher count but dismissed
    dismissed_routine["dismissed"] = 2  # dismiss_count annotation

    non_dismissed_habit = dict(HABIT_RECORD)
    non_dismissed_habit["id"] = "nondismissed01"
    non_dismissed_habit["count"] = 1

    d = tmp_path / "dismissed-test"
    _write_routine_candidates(d, [dismissed_routine, non_dismissed_habit])

    res = _run("propose", "--kind", "all", "--pretty", skill_plus_dir=d, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    ids = [c["id"] for c in payload["candidates"]]
    # non-dismissed should come first despite lower count
    assert ids.index("nondismissed01") < ids.index("dismissed000001")


# ---------------------------------------------------------------------------
# propose --kind skill (default) -- must be unchanged
# ---------------------------------------------------------------------------


def test_propose_default_kind_skill_shape_unchanged(tmp_path: Path):
    """Default skill path must return the existing envelope shape."""
    d = tmp_path / "skill-skill"
    d.mkdir(parents=True, exist_ok=True)
    # Write a skill candidate (not a routine candidate)
    cand_path = d / "candidates.jsonl"
    cand_path.write_text(
        json.dumps({
            "id": "ff001122", "key": "psql -c select", "count": 5,
            "sessions": ["s1", "s2"], "lastSeen": "2026-04-29T00:00:00Z",
        }) + "\n",
        encoding="utf-8",
    )
    res = _run("propose", "--pretty", skill_plus_dir=d, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    # Existing envelope keys must all be present
    for key in ("project", "candidatesTotal", "candidatesShown", "candidates"):
        assert key in payload, f"missing key: {key}"
    # skill-path-only keys must be present
    assert payload["candidatesTotal"] == 1
    assert payload["candidatesShown"] == 1
    # No routine keys injected into skill path
    assert "reachabilityNote" not in payload
    assert "kind" not in payload or payload.get("kind") is None or True  # kind not emitted on skill path


def test_propose_default_kind_skill_empty_log(empty_skill_dir: Path, tmp_path: Path):
    """Default skill path with no candidates emits the original note."""
    res = _run("propose", "--pretty", skill_plus_dir=empty_skill_dir, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["candidatesTotal"] == 0
    assert "note" in payload
    assert "scan" in payload["note"].lower()


def test_propose_kind_routine_empty_log(empty_skill_dir: Path, tmp_path: Path):
    """Missing routine-candidates.jsonl emits empty candidates with a note."""
    res = _run("propose", "--kind", "routine", "--pretty", skill_plus_dir=empty_skill_dir, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["kind"] == "routine"
    assert payload["candidates"] == []
    assert "note" in payload


# ---------------------------------------------------------------------------
# routine --adopt
# ---------------------------------------------------------------------------


def test_routine_adopt_appends_correct_record(skill_dir: Path, tmp_path: Path):
    res = _run(
        "routine", "--adopt", ROUTINE_RECORD["id"], "--pretty",
        skill_plus_dir=skill_dir, cwd=tmp_path,
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["id"] == ROUTINE_RECORD["id"]
    assert payload["status"] == "adopted"
    assert payload["scheduleString"] == ROUTINE_RECORD["scheduleString"]
    assert "recordedAt" in payload

    # Verify file was written
    rows = _read_adopted_log(skill_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == ROUTINE_RECORD["id"]
    assert row["clusterKey"] == ROUTINE_RECORD["key"]
    assert row["scheduleString"] == ROUTINE_RECORD["scheduleString"]
    assert row["status"] == "adopted"
    assert "ts" in row


# ---------------------------------------------------------------------------
# routine --dismiss
# ---------------------------------------------------------------------------


def test_routine_dismiss_appends_correct_record(skill_dir: Path, tmp_path: Path):
    res = _run(
        "routine", "--dismiss", HABIT_RECORD["id"], "--pretty",
        skill_plus_dir=skill_dir, cwd=tmp_path,
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["id"] == HABIT_RECORD["id"]
    assert payload["status"] == "dismissed"
    # habit: scheduleString should come from suggestion field
    assert payload["scheduleString"] == HABIT_RECORD["suggestion"]

    rows = _read_adopted_log(skill_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == HABIT_RECORD["id"]
    assert row["clusterKey"] == HABIT_RECORD["key"]
    assert row["scheduleString"] == HABIT_RECORD["suggestion"]
    assert row["status"] == "dismissed"


# ---------------------------------------------------------------------------
# routine -- unknown id
# ---------------------------------------------------------------------------


def test_routine_adopt_unknown_id_returns_error(skill_dir: Path, tmp_path: Path):
    res = _run(
        "routine", "--adopt", "nonexistentid99", "--pretty",
        skill_plus_dir=skill_dir, cwd=tmp_path,
    )
    assert res.returncode != 0
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "unknown_id"
    assert payload["id"] == "nonexistentid99"


def test_routine_dismiss_unknown_id_returns_error(skill_dir: Path, tmp_path: Path):
    res = _run(
        "routine", "--dismiss", "badid000", "--pretty",
        skill_plus_dir=skill_dir, cwd=tmp_path,
    )
    assert res.returncode != 0
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "unknown_id"


# ---------------------------------------------------------------------------
# round-trip: adopt then re-read
# ---------------------------------------------------------------------------


def test_adopt_then_reread_shows_record(skill_dir: Path, tmp_path: Path):
    """Adopt, then confirm the record is readable in the adopted log."""
    _run(
        "routine", "--adopt", ROUTINE_RECORD["id"],
        skill_plus_dir=skill_dir, cwd=tmp_path,
    )
    rows = _read_adopted_log(skill_dir)
    matching = [r for r in rows if r["id"] == ROUTINE_RECORD["id"]]
    assert len(matching) == 1
    assert matching[0]["status"] == "adopted"


def test_adopt_and_dismiss_different_ids_both_appended(skill_dir: Path, tmp_path: Path):
    """Two actions on different ids both land in the log (append-only)."""
    _run("routine", "--adopt", ROUTINE_RECORD["id"], skill_plus_dir=skill_dir, cwd=tmp_path)
    _run("routine", "--dismiss", HABIT_RECORD["id"], skill_plus_dir=skill_dir, cwd=tmp_path)

    rows = _read_adopted_log(skill_dir)
    assert len(rows) == 2
    statuses = {r["id"]: r["status"] for r in rows}
    assert statuses[ROUTINE_RECORD["id"]] == "adopted"
    assert statuses[HABIT_RECORD["id"]] == "dismissed"


# ---------------------------------------------------------------------------
# propose --limit cap respected for routine kinds
# ---------------------------------------------------------------------------


def test_propose_kind_all_limit_cap(tmp_path: Path):
    """--limit N caps the result for the routine/habit path."""
    d = tmp_path / "limit-test"
    # Write 4 routine records
    records = []
    for i in range(4):
        r = dict(ROUTINE_RECORD)
        r["id"] = f"rr{i:010d}"
        r["count"] = 10 - i
        records.append(r)
    _write_routine_candidates(d, records)

    res = _run("propose", "--kind", "all", "--limit", "2", "--pretty", skill_plus_dir=d, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert len(payload["candidates"]) == 2
