"""Tests for skill-plus feedback subcommand (slice 3.7)."""
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


# ─── helpers ─────────────────────────────────────────────────────────────────


def _encoded(path: Path) -> str:
    """Independent oracle for Claude Code's project-dir encoding (every
    non-alphanumeric character of the resolved path dashed, one-for-one).
    Deliberately NOT delegated to bin/skill-plus's _encode_project_path --
    see test_scan.py's copy of this helper for why. This file had the same
    stale (pre-v0.19.7-hotfix) encoding duplicated, which is why
    feedback's session-mining fixtures (stream2, discoverability) never
    caught the bug either."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def _load_bin_module():
    """Load bin/skill-plus as a module so tests can call helpers like
    session_files_for_project() directly instead of only via subprocess."""
    from importlib.machinery import SourceFileLoader
    import importlib.util
    loader = SourceFileLoader("skill_plus_bin_feedback", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _setup_env(tmp_path: Path) -> dict[str, str]:
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    env = os.environ.copy()
    # Don't inherit a real override from the outer environment -- stream 1
    # must resolve against the fake home unless a test sets this itself.
    env.pop("SKILL_FEEDBACK_DIR", None)
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)
    env["SKILL_PLUS_DIR"] = str(state)
    return env


def _run_feedback(env: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BIN), "feedback", "--pretty", *extra],
        capture_output=True, text=True, timeout=30, env=env,
    )


def _grant_consent(env: dict[str, str], project: Path) -> None:
    consent_dir = Path(env["HOME"]) / ".agent-plus" / "skill-plus"
    consent_dir.mkdir(parents=True, exist_ok=True)
    consent = consent_dir / "consent.json"
    data = {"projects": {str(project.resolve()): {"grantedAt": "2026-01-01T00:00:00Z",
                                                  "source": "test"}}}
    consent.write_text(json.dumps(data), encoding="utf-8")


def _seed_project(tmp_path: Path, name: str = "myproj") -> tuple[Path, Path]:
    proj = (tmp_path / name).resolve()
    proj.mkdir(parents=True, exist_ok=True)
    fake_home = tmp_path / "home"
    sess_dir = fake_home / ".claude" / "projects" / _encoded(proj)
    sess_dir.mkdir(parents=True, exist_ok=True)
    return proj, sess_dir


def _now_iso(offset_seconds: int = 0) -> str:
    t = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=offset_seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_stream1(tmp_path: Path, skill: str, entries: list[dict]) -> Path:
    """Write ratings where the skill-feedback CLI writes them since v0.19.4:
    the user-global store under the fake home (`_setup_env` points HOME /
    USERPROFILE at `tmp_path / "home"`). Previously seeded the project-local
    dir, which stopped being read when stream 1 moved to global resolution."""
    fb_dir = tmp_path / "home" / ".agent-plus" / "skill-feedback"
    fb_dir.mkdir(parents=True, exist_ok=True)
    p = fb_dir / f"{skill}.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return p


def _bash_line(cmd: str, session_id: str = "s1") -> str:
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


def _write_session(sess_dir: Path, name: str, lines: list[str]) -> Path:
    f = sess_dir / f"{name}.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


# ─── tests ───────────────────────────────────────────────────────────────────


def test_stream1_aggregation(tmp_path: Path):
    proj, _ = _seed_project(tmp_path)
    _seed_stream1(tmp_path, "foo", [
        {"ts": _now_iso(-60), "skill": "foo", "rating": 5, "outcome": "success",
         "friction": "Too verbose"},
        {"ts": _now_iso(-30), "skill": "foo", "rating": 3, "outcome": "partial",
         "friction": "too verbose"},
        {"ts": _now_iso(-10), "skill": "foo", "rating": 2, "outcome": "failure",
         "friction": "Crashed on edge case"},
    ])
    env = _setup_env(tmp_path)
    res = _run_feedback(env, "--project", str(proj))
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    skills = {s["skill"]: s for s in payload["skills"]}
    assert "foo" in skills
    s1 = skills["foo"]["stream1"]
    assert s1["count"] == 3
    assert s1["ratingsHistogram"] == {"1": 0, "2": 1, "3": 1, "4": 0, "5": 1}
    assert s1["outcomesHistogram"] == {"success": 1, "partial": 1, "failure": 1}
    assert s1["meanRating"] == round((5 + 3 + 2) / 3, 2)
    # frictionLabels lowercased and merged
    labels = {f["label"]: f["count"] for f in s1["frictionLabels"]}
    assert labels.get("too verbose") == 2
    assert labels.get("crashed on edge case") == 1


def test_stream2_fallback_rate(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    # 5 invocations of repo-analyze, each followed by a `find` fallback.
    lines = []
    for i in range(5):
        lines.append(_bash_line("agent-plus repo-analyze --root .", "s1"))
        lines.append(_bash_line(f"find . -name '*.py' -{i}", "s1"))
        if i < 4:
            lines.append(_bash_line("grep foo bar", "s1"))
    _write_session(sess_dir, "s1", lines)
    env = _setup_env(tmp_path)
    _grant_consent(env, proj)
    res = _run_feedback(env, "--project", str(proj))
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    skills = {s["skill"]: s for s in payload["skills"]}
    assert "repo-analyze" in skills
    s2 = skills["repo-analyze"]["stream2"]
    assert s2["invocations"] == 5
    # All 5 invocations have a `find` within next 10 calls
    assert s2["fallbackCount"] == 5
    assert s2["fallbackRate"] == 1.0


def test_skill_filter(tmp_path: Path):
    proj, _ = _seed_project(tmp_path)
    _seed_stream1(tmp_path, "foo", [
        {"ts": _now_iso(-10), "skill": "foo", "rating": 4, "outcome": "success"},
    ])
    _seed_stream1(tmp_path, "bar", [
        {"ts": _now_iso(-10), "skill": "bar", "rating": 1, "outcome": "failure"},
    ])
    env = _setup_env(tmp_path)
    res = _run_feedback(env, "--project", str(proj), "--skill", "foo")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    names = [s["skill"] for s in payload["skills"]]
    assert names == ["foo"]


def test_stream2_skipped_without_consent(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    _write_session(sess_dir, "s1", [_bash_line("agent-plus repo-analyze")])
    env = _setup_env(tmp_path)  # no consent granted
    res = _run_feedback(env, "--project", str(proj))
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["stream2Source"]["status"] == "skipped"
    assert payload["stream2Source"]["reason"] == "no_consent"
    # No stream2 rows surfaced
    for row in payload["skills"]:
        assert "stream2" not in row


def test_discoverability_gap(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    # 6 raw `git diff` calls, zero diff-summary invocations
    lines = [_bash_line(f"git diff HEAD~{i}", "s1") for i in range(6)]
    _write_session(sess_dir, "s1", lines)
    env = _setup_env(tmp_path)
    _grant_consent(env, proj)
    res = _run_feedback(env, "--project", str(proj))
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    skills = {s["skill"]: s for s in payload["skills"]}
    assert "diff-summary" in skills
    disc = skills["diff-summary"].get("discoverability")
    assert disc is not None
    assert disc["signal"] == "discoverability_gap"
    assert disc["manualPatternCount"] >= 6
    assert disc["pluginInvocations"] == 0


def test_since_days_zero_returns_no_entries(tmp_path: Path):
    proj, _ = _seed_project(tmp_path)
    _seed_stream1(tmp_path, "foo", [
        {"ts": _now_iso(-60), "skill": "foo", "rating": 5, "outcome": "success"},
    ])
    env = _setup_env(tmp_path)
    res = _run_feedback(env, "--project", str(proj), "--since-days", "0")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    # foo file exists but window is empty -> "no_entries_in_window" stub or 0 count
    skills = {s["skill"]: s for s in payload["skills"]}
    if "foo" in skills:
        s1 = skills["foo"].get("stream1", {})
        assert s1.get("count", 0) == 0


def test_malformed_jsonl_line_tolerated(tmp_path: Path):
    # Seeds via the SKILL_FEEDBACK_DIR override (the other resolution tier;
    # the rest of the stream-1 tests exercise the default home-store tier).
    proj, _ = _seed_project(tmp_path)
    fb_dir = tmp_path / "override-store"
    fb_dir.mkdir(parents=True, exist_ok=True)
    p = fb_dir / "foo.jsonl"
    valid1 = json.dumps({"ts": _now_iso(-10), "skill": "foo", "rating": 4,
                         "outcome": "success"})
    valid2 = json.dumps({"ts": _now_iso(-5), "skill": "foo", "rating": 5,
                         "outcome": "success"})
    p.write_text(f"{valid1}\n{{not valid json,,,\n{valid2}\n", encoding="utf-8")
    env = _setup_env(tmp_path)
    env["SKILL_FEEDBACK_DIR"] = str(fb_dir)
    res = _run_feedback(env, "--project", str(proj))
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    skills = {s["skill"]: s for s in payload["skills"]}
    assert skills["foo"]["stream1"]["count"] == 2


def test_stream1_reads_user_global_not_project_local(tmp_path: Path):
    """Regression test for the v0.19.4 global-path migration, reader side.

    skill-feedback has written to ~/.agent-plus/skill-feedback/ since
    v0.19.4, but feedback.py's stream 1 kept reading
    <project>/.agent-plus/skill-feedback/ -- a dir that no longer receives
    writes -- so explicit ratings silently vanished from the join. Assert
    the fix: a rating in the user-global store IS aggregated; a rating in
    the legacy project-local dir is NOT (reading both would double-count
    hand-merged legacy data); stream1Source reports the global store."""
    proj, _ = _seed_project(tmp_path)
    env = _setup_env(tmp_path)

    global_dir = tmp_path / "home" / ".agent-plus" / "skill-feedback"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "globalskill.jsonl").write_text(
        json.dumps({"ts": _now_iso(-10), "skill": "globalskill",
                    "rating": 4, "outcome": "success"}) + "\n",
        encoding="utf-8",
    )

    legacy_dir = proj / ".agent-plus" / "skill-feedback"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "legacyskill.jsonl").write_text(
        json.dumps({"ts": _now_iso(-10), "skill": "legacyskill",
                    "rating": 1, "outcome": "failure"}) + "\n",
        encoding="utf-8",
    )

    res = _run_feedback(env, "--project", str(proj))
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    skills = {s["skill"]: s for s in payload["skills"]}

    assert "globalskill" in skills, payload
    assert skills["globalskill"]["stream1"]["count"] == 1
    assert "legacyskill" not in skills, (
        "project-local store must no longer be read: " + str(payload)
    )
    assert Path(payload["stream1Source"]).resolve() == global_dir.resolve()


def test_session_files_for_project_resolves_fixture(tmp_path: Path, monkeypatch):
    """Regression test for the v0.19.7 encoded_cwd_for hotfix, feedback side.

    feedback.py's stream2 (session-mining) depends on
    session_files_for_project(), which depends on encoded_cwd_for(). Before
    the fix, encoded_cwd_for() produced a directory name that never matched
    what Claude Code (or this fixture, seeded via the independent _encoded()
    oracle above) actually creates on disk, so session_files_for_project()
    returned [] even though the session file was sitting right there --
    silent data loss with no error surfaced anywhere.
    """
    proj, sess_dir = _seed_project(tmp_path)
    session_file = _write_session(sess_dir, "s1", [_bash_line("echo hi")])
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    mod = _load_bin_module()
    files = mod.session_files_for_project(proj)
    assert files == [session_file]
