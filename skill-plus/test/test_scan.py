"""Tests for skill-plus scan subcommand (slice 3.2)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-plus"


# ─── helpers ─────────────────────────────────────────────────────────────────


def _encoded(path: Path) -> str:
    s = str(path.resolve())
    s = re.sub(r"[\\/:]", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return f"C--{s}" if not s.startswith("C--") else s


def _bash_line(cmd: str, session_id: str = "sess1") -> str:
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


def _write_session(proj_dir: Path, name: str, lines: list[str], mtime: float | None = None) -> Path:
    proj_dir.mkdir(parents=True, exist_ok=True)
    f = proj_dir / f"{name}.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(f, (mtime, mtime))
    return f


def _setup_env(tmp_path: Path, project_path: Path) -> dict[str, str]:
    """Build an env that redirects HOME/USERPROFILE to tmp and points
    SKILL_PLUS_DIR at a project-state directory under tmp."""
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)
    env["SKILL_PLUS_DIR"] = str(state)
    return env


def _run_scan(env: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BIN), "scan", "--pretty", *extra],
        capture_output=True, text=True, timeout=30, env=env,
    )


def _seed_project(tmp_path: Path, project_name: str = "myproj") -> tuple[Path, Path]:
    """Create a fake project path and an encoded session dir under fake HOME."""
    proj = (tmp_path / project_name).resolve()
    proj.mkdir(parents=True, exist_ok=True)
    fake_home = tmp_path / "home"
    sess_dir = fake_home / ".claude" / "projects" / _encoded(proj)
    sess_dir.mkdir(parents=True, exist_ok=True)
    return proj, sess_dir


# ─── tests ───────────────────────────────────────────────────────────────────


def test_consent_gate_blocks_without_flag(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    _write_session(sess_dir, "s1", [_bash_line("railway logs --service api")])
    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj))
    assert res.returncode == 2, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "consent_required"
    assert payload["needsConsentFor"] == str(proj.resolve())


def test_happy_path_clusters_repeated_invocations(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    cmd = "railway logs --service api"
    _write_session(sess_dir, "s1", [_bash_line(cmd, "s1") for _ in range(3)])
    _write_session(sess_dir, "s2", [_bash_line(cmd, "s2") for _ in range(2)])
    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["sessionsScanned"] == 2
    assert payload["candidatesNew"] >= 1
    keys = [c["key"] for c in payload["candidates"]]
    assert "railway logs --service" in keys
    cand = next(c for c in payload["candidates"] if c["key"] == "railway logs --service")
    assert cand["count"] == 5
    assert sorted(cand["sessions"]) == ["s1", "s2"]


def test_denylist_skips_git_status(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    _write_session(sess_dir, "s1", [_bash_line("git status", "s1") for _ in range(6)])
    _write_session(sess_dir, "s2", [_bash_line("git status", "s2") for _ in range(6)])
    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0
    payload = json.loads(res.stdout)
    assert payload["candidatesNew"] == 0
    assert payload["candidates"] == []


def test_allowlist_overrides_denylist(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    cmd = "git status --service foo"
    _write_session(sess_dir, "s1", [_bash_line(cmd, "s1") for _ in range(3)])
    _write_session(sess_dir, "s2", [_bash_line(cmd, "s2") for _ in range(2)])
    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0
    payload = json.loads(res.stdout)
    assert payload["candidatesNew"] == 1


def test_redaction_in_examples(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    secret = "Bearer abcdefghijklmnopqrstuvwxyz1234567890"
    cmd = f"curl --service api -H 'Authorization: {secret}'"
    _write_session(sess_dir, "s1", [_bash_line(cmd, "s1") for _ in range(3)])
    _write_session(sess_dir, "s2", [_bash_line(cmd, "s2") for _ in range(2)])
    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0
    payload = json.loads(res.stdout)
    assert payload["candidates"], payload
    examples = payload["candidates"][0]["examples"]
    blob = " ".join(examples)
    assert "[REDACTED]" in blob
    assert secret not in blob


def test_dedupe_on_second_run(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    cmd = "railway logs --service api"
    _write_session(sess_dir, "s1", [_bash_line(cmd, "s1") for _ in range(3)])
    _write_session(sess_dir, "s2", [_bash_line(cmd, "s2") for _ in range(2)])
    env = _setup_env(tmp_path, proj)
    r1 = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert r1.returncode == 0
    p1 = json.loads(r1.stdout)
    assert p1["candidatesNew"] >= 1
    # Second run: same files. Bump mtime so the watermark doesn't drop them.
    now = time.time() + 5
    for f in sess_dir.glob("*.jsonl"):
        os.utime(f, (now, now))
    r2 = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert r2.returncode == 0
    p2 = json.loads(r2.stdout)
    assert p2["candidatesUpdated"] >= 1
    assert p2["candidatesNew"] == 0
    assert p2["candidatesTotal"] == p1["candidatesTotal"]


def test_malformed_jsonl_line_tolerated(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    cmd = "railway logs --service api"
    bad = "{not json,,,"
    lines = [bad] + [_bash_line(cmd, "s1") for _ in range(3)]
    _write_session(sess_dir, "s1", lines)
    _write_session(sess_dir, "s2", [_bash_line(cmd, "s2") for _ in range(2)])
    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0
    payload = json.loads(res.stdout)
    assert payload["parseErrors"] == 1
    assert payload["candidatesNew"] >= 1


def test_max_sessions_cap(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    cmd = "railway logs --service api"
    _write_session(sess_dir, "old", [_bash_line(cmd)], mtime=time.time() - 1000)
    _write_session(sess_dir, "newest", [_bash_line(cmd)], mtime=time.time())
    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent",
                    "--max-sessions", "1", "--min-count", "1", "--min-sessions", "1")
    assert res.returncode == 0
    payload = json.loads(res.stdout)
    assert payload["sessionsScanned"] == 1
    cand = payload["candidates"][0]
    assert cand["sessions"] == ["newest"]
