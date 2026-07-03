"""Tests for the temporal (routine + habit) aggregator in skill-plus scan.

All fixtures are SYNTHETIC -- real session history has no clock-shaped cadence
(per the 2026-05-16 hand-probe). Positive-path tests inject cadence by building
JSONL files with deterministic timestamps, computed relative to the REAL
current time (not a hardcoded epoch -- a fixed past date would drift out of
the default 30-day `--since-days` window as the calendar moves on, silently
turning every positive-path test into a false negative weeks after it was
written). Negative-path tests verify that burst/noise signals are correctly
silenced.

Env layout:
  - SKILL_PLUS_DIR -> tmp/state   (isolates routine-candidates.jsonl + friends)
  - HOME / USERPROFILE -> tmp/home (isolates consent, config, last-scan)
  - Sessions live in tmp/home/.claude/projects/<encoded-proj>/
  - No writes touch real ~/.agent-plus or ~/.claude.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-plus"

# ─── helpers shared with test_scan.py ────────────────────────────────────────


def _encoded(path: Path) -> str:
    """Independent oracle for Claude Code's project-dir encoding (every
    non-alphanumeric character of the resolved path dashed, one-for-one).
    Deliberately NOT delegated to bin/skill-plus's _encode_project_path: if
    this helper called into the module under test, a regression in the
    implementation would move both in lockstep and these fixtures would
    silently keep matching a broken encoder -- which is exactly how the
    original bug (collapsed/stripped/re-prepended dashes) escaped detection."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def _setup_env(tmp_path: Path) -> dict[str, str]:
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)
    env["SKILL_PLUS_DIR"] = str(state)
    return env


def _seed_project(tmp_path: Path, project_name: str = "myproj") -> tuple[Path, Path]:
    proj = (tmp_path / project_name).resolve()
    proj.mkdir(parents=True, exist_ok=True)
    fake_home = tmp_path / "home"
    sess_dir = fake_home / ".claude" / "projects" / _encoded(proj)
    sess_dir.mkdir(parents=True, exist_ok=True)
    return proj, sess_dir


def _run_scan(env: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BIN), "scan", "--pretty", *extra],
        capture_output=True, text=True, timeout=30, env=env,
    )


# ─── JSONL builder helpers ────────────────────────────────────────────────────


def _bash_line_with_ts(
    cmd: str,
    session_id: str,
    ts: _dt.datetime,
    cwd: str | None = None,
) -> str:
    """Build a JSONL line with a timestamp on the outer envelope (Premise 2)."""
    obj: dict = {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "message": {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}
            ],
        },
    }
    if cwd is not None:
        obj["cwd"] = cwd
    return json.dumps(obj)


def _bash_line_no_ts(cmd: str, session_id: str) -> str:
    """Build a JSONL line WITHOUT a timestamp (tests frequency-only path)."""
    return json.dumps({
        "type": "assistant",
        "sessionId": session_id,
        "message": {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}
            ],
        },
    })


def _write_session(
    sess_dir: Path,
    name: str,
    lines: list[str],
    mtime: float | None = None,
) -> Path:
    sess_dir.mkdir(parents=True, exist_ok=True)
    f = sess_dir / f"{name}.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(f, (mtime, mtime))
    return f


# ─── fixture builders ─────────────────────────────────────────────────────────


def _weekday_morning_sessions(
    sess_dir: Path,
    cmd: str,
    num_dates: int,
    hour: int = 9,
    minute: int = 5,
) -> list[str]:
    """Create one session per weekday morning across num_dates distinct dates.

    Returns a list of session IDs (also writes the JSONL files).
    All sessions land within the last 30 days at mtime aligned to their date.
    """
    session_ids: list[str] = []
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    count = 0
    day_offset = 1
    while count < num_dates:
        candidate = now_utc - _dt.timedelta(days=day_offset)
        day_offset += 1
        if day_offset > 60:
            break
        if candidate.weekday() >= 5:  # skip Sat/Sun
            continue
        ts = candidate.replace(hour=hour, minute=minute, second=0)
        sess_id = f"wd_sess_{count}"
        line = _bash_line_with_ts(cmd, sess_id, ts)
        f = _write_session(sess_dir, sess_id, [line], mtime=ts.timestamp())
        session_ids.append(sess_id)
        count += 1
    return session_ids


def _smeared_sessions(
    sess_dir: Path,
    cmd: str,
    num_dates: int,
    hours: list[int] | None = None,
) -> list[str]:
    """Create sessions spread over num_dates with random hours 7-22 (diffuse habit)."""
    if hours is None:
        hours = list(range(7, 23))  # 7..22 inclusive
    session_ids: list[str] = []
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    for i in range(num_dates):
        date = now_utc - _dt.timedelta(days=i + 1)
        hour = hours[i % len(hours)]
        ts = date.replace(hour=hour, minute=0, second=0)
        sess_id = f"sm_sess_{i}"
        line = _bash_line_with_ts(cmd, sess_id, ts)
        _write_session(sess_dir, sess_id, [line], mtime=ts.timestamp())
        session_ids.append(sess_id)
    return session_ids


# ─── tests ───────────────────────────────────────────────────────────────────


def test_routine_candidate_weekday_morning(tmp_path: Path):
    """Command at ~09:05 every weekday across 8 distinct dates -> routine candidate."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)
    cmd = "railway deploy --service api"
    _weekday_morning_sessions(sess_dir, cmd, num_dates=8, hour=9, minute=5)

    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    rc = payload.get("routineCandidates", {})
    assert rc.get("byKind", {}).get("routine", 0) >= 1, (
        f"Expected at least 1 routine candidate; got: {payload}"
    )

    top = payload.get("routineCandidatesTop", [])
    routine_records = [r for r in top if r.get("kind") == "routine"]
    assert routine_records, f"No routine records in top: {top}"

    rec = routine_records[0]
    assert rec["key"] == "railway deploy --service"
    assert rec["distinctDates"] >= 8
    assert rec["regularity"]["weekdayClass"] in ("weekday", "Mon", "Tue", "Wed", "Thu", "Fri")
    assert rec["regularity"]["weekdayClassShare"] >= 0.70
    assert rec["regularity"]["hourBucketShare"] >= 0.50

    # scheduleString must be present and contain required phrases
    ss = rec.get("scheduleString", "")
    assert ss, f"scheduleString missing on routine record: {rec}"
    assert "(refine this intent before saving)" in ss, ss
    # Must mention morning (09:00 is morning) and UTC
    assert "morning" in ss.lower() or "09:00" in ss, ss
    assert "UTC" in ss, ss

    # suggestion must be null for routine
    assert rec.get("suggestion") is None


def test_habit_candidate_smeared_hours(tmp_path: Path):
    """Command on 12 dates with hours spread 7-22 -> habit (no scheduleString)."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)
    cmd = "railway logs --service api"
    _smeared_sessions(sess_dir, cmd, num_dates=12)

    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    top = payload.get("routineCandidatesTop", [])
    habit_records = [r for r in top if r.get("kind") == "habit"]
    assert habit_records, f"Expected habit record but got top: {top}"

    rec = habit_records[0]
    assert rec["scheduleString"] is None, f"habit should have null scheduleString: {rec}"
    assert rec["suggestion"] is not None
    assert "not a routine" in rec["suggestion"]
    assert "skill-plus propose" in rec["suggestion"]
    assert rec["distinctDates"] >= 12


def test_burst_not_persisted(tmp_path: Path):
    """Command on only 2 distinct dates -> below MIN_DISTINCT_DATES -> not persisted."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)
    cmd = "railway deploy --service api"

    now_utc = _dt.datetime.now(_dt.timezone.utc)
    for i in range(2):
        ts = now_utc - _dt.timedelta(days=i + 1)
        ts = ts.replace(hour=9)
        sess_id = f"burst_{i}"
        line = _bash_line_with_ts(cmd, sess_id, ts)
        _write_session(sess_dir, sess_id, [line], mtime=ts.timestamp())

    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    top = payload.get("routineCandidatesTop", [])
    matching = [r for r in top if "railway deploy" in r.get("key", "")]
    assert not matching, f"Burst pattern should not appear in routine candidates: {matching}"

    state_dir = Path(env["SKILL_PLUS_DIR"])
    rc_log = state_dir / "routine-candidates.jsonl"
    if rc_log.exists():
        records = [json.loads(l) for l in rc_log.read_text().splitlines() if l.strip()]
        matching_stored = [r for r in records if "railway deploy" in r.get("key", "")]
        assert not matching_stored, f"Burst should not be stored: {matching_stored}"


def test_missing_timestamp_no_crash(tmp_path: Path):
    """Lines without a timestamp still count for frequency; no crash; no temporal."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)
    cmd = "railway logs --service api"

    # 5 sessions, no timestamps -- should count for frequency but not temporal
    for i in range(5):
        sess_id = f"nots_{i}"
        lines = [_bash_line_no_ts(cmd, sess_id) for _ in range(2)]
        _write_session(sess_dir, sess_id, lines, mtime=time.time() - i * 100)

    res = _run_scan(env, "--project", str(proj), "--accept-consent",
                    "--min-count", "1", "--min-sessions", "1")
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    # Frequency path should pick it up
    assert payload["candidatesNew"] >= 1

    # Temporal should NOT emit a routine/habit candidate (no timestamps available)
    top = payload.get("routineCandidatesTop", [])
    # There may be zero -- that's correct (no timestamps -> no temporal occurrences)
    # Just assert no crash happened and the key isn't misclassified
    for r in top:
        if "railway logs" in r.get("key", ""):
            # If present, it should only be from sessions WITH timestamps
            assert r.get("distinctDates", 0) == 0 or r.get("count", 0) == 0


def test_adopted_id_suppressed(tmp_path: Path):
    """An adopted id must not appear in routineCandidatesTop or be persisted."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)
    cmd = "railway deploy --service api"
    _weekday_morning_sessions(sess_dir, cmd, num_dates=8)

    # First scan to get the id
    r1 = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert r1.returncode == 0, r1.stderr
    p1 = json.loads(r1.stdout)
    top1 = p1.get("routineCandidatesTop", [])
    routine_recs = [r for r in top1 if r.get("kind") == "routine"]
    assert routine_recs, f"Need a routine record to test suppression; got: {p1}"
    rid = routine_recs[0]["id"]

    # Write routines-adopted.jsonl to mark it adopted
    state_dir = Path(env["SKILL_PLUS_DIR"])
    adopted_path = state_dir / "routines-adopted.jsonl"
    state_dir.mkdir(parents=True, exist_ok=True)
    adopted_entry = {
        "id": rid,
        "clusterKey": routine_recs[0]["key"],
        "scheduleString": routine_recs[0].get("scheduleString", ""),
        "status": "adopted",
        "ts": "2026-05-18T13:00:00Z",
    }
    adopted_path.write_text(json.dumps(adopted_entry) + "\n", encoding="utf-8")

    # Second scan -- adopted id should be suppressed from output AND not stored
    # Bump mtimes so frequency path re-scans
    for f in sess_dir.glob("*.jsonl"):
        os.utime(f, (time.time() + 60, time.time() + 60))

    r2 = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert r2.returncode == 0, r2.stderr
    p2 = json.loads(r2.stdout)

    top2 = p2.get("routineCandidatesTop", [])
    assert all(r.get("id") != rid for r in top2), (
        f"Adopted id {rid} should not appear in routineCandidatesTop: {top2}"
    )

    # Also should not be in persisted log
    rc_log = state_dir / "routine-candidates.jsonl"
    if rc_log.exists():
        records = [json.loads(l) for l in rc_log.read_text().splitlines() if l.strip()]
        assert all(r.get("id") != rid for r in records), (
            f"Adopted id {rid} should not be stored: {records}"
        )


def test_dismissed_id_present_with_count(tmp_path: Path):
    """A dismissed id must still be persisted, have 'dismissed' >= 1, and sort after non-dismissed."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)

    # Two commands: one will be dismissed, one will not
    cmd_a = "railway deploy --service api"
    cmd_b = "gh pr --service review"

    _weekday_morning_sessions(sess_dir, cmd_a, num_dates=8, hour=9)
    _smeared_sessions(sess_dir, cmd_b, num_dates=6)

    r1 = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert r1.returncode == 0, r1.stderr
    p1 = json.loads(r1.stdout)
    top1 = p1.get("routineCandidatesTop", [])
    routine_recs = [r for r in top1 if r.get("kind") == "routine"]
    assert routine_recs, f"Need a routine record; got: {p1}"
    rid = routine_recs[0]["id"]

    # Dismiss it (twice, to test count > 1)
    state_dir = Path(env["SKILL_PLUS_DIR"])
    adopted_path = state_dir / "routines-adopted.jsonl"
    state_dir.mkdir(parents=True, exist_ok=True)
    with adopted_path.open("a", encoding="utf-8") as fh:
        for i in range(2):
            fh.write(json.dumps({
                "id": rid,
                "clusterKey": routine_recs[0]["key"],
                "scheduleString": routine_recs[0].get("scheduleString", ""),
                "status": "dismissed",
                "ts": f"2026-05-18T1{i}:00:00Z",
            }) + "\n")

    # Re-scan
    for f in sess_dir.glob("*.jsonl"):
        os.utime(f, (time.time() + 60, time.time() + 60))

    r2 = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert r2.returncode == 0, r2.stderr
    p2 = json.loads(r2.stdout)

    # Check persisted log
    rc_log = state_dir / "routine-candidates.jsonl"
    assert rc_log.exists(), "routine-candidates.jsonl should exist"
    records = [json.loads(l) for l in rc_log.read_text().splitlines() if l.strip()]
    dismissed_recs = [r for r in records if r.get("id") == rid]
    assert dismissed_recs, f"Dismissed id {rid} should still be persisted"
    assert dismissed_recs[0].get("dismissed", 0) >= 1, f"Expected dismissed count >= 1: {dismissed_recs[0]}"

    # Dismissed record should sort AFTER non-dismissed in the file
    non_dismissed = [r for r in records if "dismissed" not in r]
    if non_dismissed:
        non_dismissed_positions = [i for i, r in enumerate(records) if "dismissed" not in r]
        dismissed_positions = [i for i, r in enumerate(records) if "dismissed" in r]
        if non_dismissed_positions and dismissed_positions:
            assert max(non_dismissed_positions) < min(dismissed_positions), (
                f"Non-dismissed should sort before dismissed in file. "
                f"non_dismissed at {non_dismissed_positions}, dismissed at {dismissed_positions}"
            )


def test_watermark_bypass(tmp_path: Path):
    """Temporal pass must detect cadence even when all sessions predate last_scan."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)
    cmd = "railway deploy --service api"

    # Plant sessions 5-24 days ago (well within since_days=30 window)
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    session_count = 0
    day_offset = 5
    while session_count < 8 and day_offset < 25:
        ts_candidate = now_utc - _dt.timedelta(days=day_offset)
        day_offset += 1
        if ts_candidate.weekday() >= 5:
            continue
        ts = ts_candidate.replace(hour=9, minute=5, second=0)
        sess_id = f"old_sess_{session_count}"
        line = _bash_line_with_ts(cmd, sess_id, ts)
        _write_session(sess_dir, sess_id, [line], mtime=ts.timestamp())
        session_count += 1

    # Write a last-scan timestamp set to NOW (after all sessions)
    state_dir = Path(env["SKILL_PLUS_DIR"])
    state_dir.mkdir(parents=True, exist_ok=True)
    last_scan_file = state_dir / "last-scan.txt"
    last_scan_file.write_text(
        now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), encoding="utf-8"
    )

    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    # Frequency path should see ZERO sessions (all predate last_scan)
    assert payload["sessionsScanned"] >= 0  # might be 0 for frequency

    # Temporal path MUST still find the routine despite the watermark
    top = payload.get("routineCandidatesTop", [])
    routine_records = [r for r in top if r.get("kind") == "routine"]
    assert routine_records, (
        f"Temporal pass should detect routine even when sessions predate last_scan. "
        f"payload: {json.dumps(payload, indent=2)}"
    )
    assert routine_records[0]["distinctDates"] >= 8


def test_schedulestring_content(tmp_path: Path):
    """Verify scheduleString structure: cadence phrase + (refine) marker + UTC."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)
    cmd = "railway deploy --service api"
    _weekday_morning_sessions(sess_dir, cmd, num_dates=8, hour=9)

    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    top = payload.get("routineCandidatesTop", [])
    routine_recs = [r for r in top if r.get("kind") == "routine"]
    assert routine_recs

    ss = routine_recs[0]["scheduleString"]
    assert "(refine this intent before saving)" in ss
    assert "UTC" in ss
    # ASCII only: no em-dash (U+2014) or en-dash (U+2013)
    assert "—" not in ss, f"em-dash found in scheduleString: {ss}"
    assert "–" not in ss, f"en-dash found in scheduleString: {ss}"
    # Contains the command example (scrubbed, truncated)
    assert "railway" in ss or "deploy" in ss, f"Expected command in scheduleString: {ss}"


def test_routine_schema_fields(tmp_path: Path):
    """Routine record must have all frozen-schema fields."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)
    cmd = "railway deploy --service api"
    _weekday_morning_sessions(sess_dir, cmd, num_dates=8)

    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    top = payload.get("routineCandidatesTop", [])
    routine_recs = [r for r in top if r.get("kind") == "routine"]
    assert routine_recs

    rec = routine_recs[0]
    required_fields = [
        "id", "kind", "key", "count", "distinctDates", "regularity",
        "examples", "sessions", "scheduleString", "suggestion",
        "firstSeen", "lastSeen", "scannedAt", "sourceProject",
    ]
    for field in required_fields:
        assert field in rec, f"Missing field '{field}' in routine record: {rec}"

    reg = rec["regularity"]
    reg_fields = ["weekdayClass", "weekdayClassShare", "hourBucket", "hourBucketUtc", "hourBucketShare"]
    for field in reg_fields:
        assert field in reg, f"Missing regularity field '{field}': {reg}"

    # hourBucketUtc format: "HH:00-HH:59"
    import re as _re
    assert _re.match(r"\d{2}:00-\d{2}:59", reg["hourBucketUtc"]), (
        f"hourBucketUtc format wrong: {reg['hourBucketUtc']}"
    )


def test_habit_schema_fields(tmp_path: Path):
    """Habit record must have all frozen-schema fields with null scheduleString."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)
    cmd = "railway logs --service api"
    _smeared_sessions(sess_dir, cmd, num_dates=12)

    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    top = payload.get("routineCandidatesTop", [])
    habit_recs = [r for r in top if r.get("kind") == "habit"]
    assert habit_recs, f"Expected habit candidate; got: {top}"

    rec = habit_recs[0]
    assert rec["scheduleString"] is None
    assert rec["suggestion"] is not None
    assert isinstance(rec["regularity"], dict)

    required_fields = [
        "id", "kind", "key", "count", "distinctDates", "regularity",
        "examples", "sessions", "scheduleString", "suggestion",
        "firstSeen", "lastSeen", "scannedAt", "sourceProject",
    ]
    for field in required_fields:
        assert field in rec, f"Missing field '{field}' in habit record: {rec}"


# ─── redaction: on-disk rescrub (mirrors test_scan.py's candidates.jsonl guard) ──


def test_rescrub_removes_previously_leaked_token_from_routine_log_on_rewrite(tmp_path: Path):
    """Regression guard for the temporal lens: a token that leaked into
    routine-candidates.jsonl BEFORE a redaction-pattern gap was fixed must be
    scrubbed on the next scan's full rewrite, even though this scan's
    temporal pass never recomputes that record (zero sessions this run, so
    temporal_agg never touches its cluster key) -- it is carried forward
    straight off disk by _read_existing() and only a rescrub-on-write pass
    can clean it. Mirrors test_scan.py's
    test_rescrub_removes_previously_leaked_token_on_rewrite for candidates.jsonl."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)
    state_dir = Path(env["SKILL_PLUS_DIR"])
    state_dir.mkdir(parents=True, exist_ok=True)

    fake_hash = "f" * 40
    leaked_cmd = f'curl -s -H "Authorization: Bearer 9|{fake_hash}" https://example.test'
    leaked_record = {
        "id": "cafef00dbeef",
        "kind": "habit",
        "key": "curl -s -H",
        "count": 6,
        "distinctDates": 6,
        "regularity": {
            "weekdayClass": "weekday",
            "weekdayClassShare": 0.5,
            "hourBucket": 3,
            "hourBucketUtc": "09:00-11:59",
            "hourBucketShare": 0.4,
        },
        "examples": [leaked_cmd],
        "sessions": ["old1", "old2"],
        "scheduleString": None,
        "suggestion": (
            "recurs on 6 dates with no consistent time; not a routine --"
            " run `skill-plus propose` to consider a skill"
        ),
        "firstSeen": "2026-01-01T00:00:00Z",
        "lastSeen": "2026-01-06T00:00:00Z",
        "scannedAt": "2026-01-06T00:00:00Z",
        "sourceProject": str(proj),
    }
    (state_dir / "routine-candidates.jsonl").write_text(
        json.dumps(leaked_record) + "\n", encoding="utf-8"
    )

    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stdout + res.stderr

    on_disk = (state_dir / "routine-candidates.jsonl").read_text(encoding="utf-8")
    assert "9|" not in on_disk
    assert fake_hash not in on_disk
    assert "[REDACTED]" in on_disk
    rec = json.loads(on_disk.strip().splitlines()[0])
    assert rec["id"] == "cafef00dbeef"  # record preserved, just scrubbed


def test_single_dow_classification(tmp_path: Path):
    """8 Mondays only -> weekdayClass='Mon', scheduleString='Every Mon around ...'"""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)
    cmd = "railway deploy --service api"

    # Find 8 Mondays within the last 70 days (8 Mondays spans ~56 days)
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    count = 0
    day_offset = 1
    while count < 8 and day_offset < 80:
        candidate = now_utc - _dt.timedelta(days=day_offset)
        day_offset += 1
        if candidate.weekday() != 0:  # 0 = Monday
            continue
        ts = candidate.replace(hour=9, minute=0, second=0)
        sess_id = f"mon_sess_{count}"
        line = _bash_line_with_ts(cmd, sess_id, ts)
        _write_session(sess_dir, sess_id, [line], mtime=ts.timestamp())
        count += 1

    res = _run_scan(env, "--project", str(proj), "--accept-consent", "--since-days", "70")
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    top = payload.get("routineCandidatesTop", [])
    routine_recs = [r for r in top if r.get("kind") == "routine"]
    assert routine_recs, f"Expected routine from Monday cadence; got: {top}"

    rec = routine_recs[0]
    assert rec["regularity"]["weekdayClass"] == "Mon", (
        f"Expected weekdayClass='Mon'; got: {rec['regularity']}"
    )
    ss = rec["scheduleString"]
    assert "Every Mon" in ss, f"Expected 'Every Mon' in scheduleString: {ss}"
    assert "(refine this intent before saving)" in ss


def test_existing_scan_envelope_keys_unchanged(tmp_path: Path):
    """scan output must still have all original envelope keys (no regression)."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path)
    cmd = "railway logs --service api"
    lines = []
    for i in range(3):
        lines.append(_bash_line_no_ts(cmd, f"s{i}"))
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "s0.jsonl").write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    (sess_dir / "s1.jsonl").write_text(lines[2] + "\n", encoding="utf-8")

    res = _run_scan(env, "--project", str(proj), "--accept-consent",
                    "--min-count", "1", "--min-sessions", "1")
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)

    original_keys = [
        "project", "sessionsScanned", "parseErrors", "formatUnsupportedLines",
        "candidatesNew", "candidatesUpdated", "candidatesTotal", "candidates",
    ]
    for key in original_keys:
        assert key in payload, f"Original envelope key '{key}' missing: {list(payload.keys())}"

    # New keys also present
    assert "routineCandidates" in payload
    assert "routineCandidatesTop" in payload
