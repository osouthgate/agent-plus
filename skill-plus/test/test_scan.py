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
    """Independent oracle for Claude Code's project-dir encoding (every
    non-alphanumeric character of the resolved path dashed, one-for-one).
    Deliberately NOT delegated to bin/skill-plus's _encode_project_path: if
    this helper called into the module under test, a regression in the
    implementation would move both in lockstep and these fixtures would
    silently keep matching a broken encoder -- which is exactly how the
    original bug (collapsed/stripped/re-prepended dashes) escaped detection."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def test_encoded_helper_pins_real_world_examples():
    # Same literals as test_foundation.py's _encode_project_path pin, checked
    # here against the raw spec regex (no .resolve() involved, so safe on any
    # OS) to guard this file's fixture helper specifically -- this is the
    # helper whose drift let the original bug ship undetected.
    pattern = r"[^A-Za-z0-9]"
    assert re.sub(pattern, "-", "C:\\dev\\patchboard") == "C--dev-patchboard"
    assert re.sub(pattern, "-", "/Users/bob/foo") == "-Users-bob-foo"
    assert re.sub(pattern, "-", "C:\\dev\\foo.bar") == "C--dev-foo-bar"


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


def test_scan_finds_sessions_under_correctly_encoded_project_dir(tmp_path: Path):
    """Regression test for the v0.19.7 encoded_cwd_for hotfix.

    Claude Code names ~/.claude/projects/<slug> by dashing every
    non-alphanumeric character of the resolved cwd, one dash per character,
    with no collapsing/stripping/re-prepending. The pre-fix implementation
    produced a different (nonexistent) directory name, so scan silently
    reported sessionsScanned == 0 on every real Windows project. This test
    seeds the correctly-encoded directory (via _encoded(), an oracle kept
    independent of bin/skill-plus so it can't drift in lockstep with a
    regression) and asserts scan actually discovers the sessions there.
    MUST fail if encoded_cwd_for regresses to the old collapse/strip/
    re-prepend behavior.
    """
    proj, sess_dir = _seed_project(tmp_path, "hotfixproj")
    cmd = "railway logs --service api"
    _write_session(sess_dir, "s1", [_bash_line(cmd, "s1") for _ in range(2)])
    _write_session(sess_dir, "s2", [_bash_line(cmd, "s2") for _ in range(2)])
    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["sessionsScanned"] >= 1
    assert payload["sessionsScanned"] == 2
    assert payload["candidatesNew"] >= 1


# ─── redaction: on-disk rescrub (v0.19.7 hotfix, part 2, Task 3) ──────────────


def _state_dir(env: dict[str, str]) -> Path:
    """Where candidates.jsonl / last-scan.txt actually land, resolved the
    same way project_state_root_with_source() resolves SKILL_PLUS_DIR --
    so reading files back can't drift from where the tool actually wrote."""
    return Path(env["SKILL_PLUS_DIR"]).expanduser().resolve()


def test_candidates_file_on_disk_has_no_token_material(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    fake_hash = "d" * 40
    cmd = f'curl -s -H "Authorization: Bearer 3|{fake_hash}" https://example.test --service api'
    _write_session(sess_dir, "s1", [_bash_line(cmd, "s1") for _ in range(3)])
    _write_session(sess_dir, "s2", [_bash_line(cmd, "s2") for _ in range(2)])
    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stdout + res.stderr

    candidates_file = _state_dir(env) / "candidates.jsonl"
    assert candidates_file.exists()
    on_disk = candidates_file.read_text(encoding="utf-8")
    assert "3|" not in on_disk
    assert fake_hash not in on_disk
    assert "[REDACTED]" in on_disk


def test_rescrub_removes_previously_leaked_token_on_rewrite(tmp_path: Path):
    """Regression guard for Task 3: a token that leaked into candidates.jsonl
    BEFORE the redaction-pattern gap was fixed must be scrubbed on the next
    rewrite, even though this scan's own clustering never touches that
    pre-existing record (no matching new sessions at all)."""
    proj, sess_dir = _seed_project(tmp_path)
    env = _setup_env(tmp_path, proj)
    state = _state_dir(env)
    state.mkdir(parents=True, exist_ok=True)
    fake_hash = "e" * 40
    leaked_cmd = f'curl -s -H "Authorization: Bearer 5|{fake_hash}" https://example.test'
    leaked_record = {
        "id": "deadbeef1234",
        "key": "curl -s -H",
        "count": 3,
        "sessions": ["old1", "old2"],
        "examples": [leaked_cmd],
        "firstSeen": "2026-01-01T00:00:00Z",
        "lastSeen": "2026-01-01T00:00:00Z",
        "scannedAt": "2026-01-01T00:00:00Z",
        "sourceProject": str(proj),
    }
    (state / "candidates.jsonl").write_text(json.dumps(leaked_record) + "\n", encoding="utf-8")

    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stdout + res.stderr

    on_disk = (state / "candidates.jsonl").read_text(encoding="utf-8")
    assert "5|" not in on_disk
    assert fake_hash not in on_disk
    assert "[REDACTED]" in on_disk
    rec = json.loads(on_disk.strip().splitlines()[0])
    assert rec["id"] == "deadbeef1234"  # record preserved, just scrubbed


# ─── cursor semantics (Task 4) ─────────────────────────────────────────────────


def test_zero_session_scan_leaves_watermark_unchanged_and_sets_zero_reason(tmp_path: Path):
    # Don't create the encoded session dir at all -- deterministic
    # project_dir_missing with no mtime-cutoff ambiguity.
    proj = (tmp_path / "myproj").resolve()
    proj.mkdir(parents=True, exist_ok=True)
    env = _setup_env(tmp_path, proj)
    state = _state_dir(env)
    state.mkdir(parents=True, exist_ok=True)
    sentinel = "2020-01-01T00:00:00Z"
    (state / "last-scan.txt").write_text(sentinel, encoding="utf-8")

    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["sessionsScanned"] == 0
    assert payload["zeroReason"] == "project_dir_missing"
    assert payload["diagnostics"]["projectDirExists"] is False

    assert (state / "last-scan.txt").read_text(encoding="utf-8") == sentinel


def test_nonzero_session_scan_advances_watermark(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    cmd = "railway logs --service api"
    _write_session(sess_dir, "s1", [_bash_line(cmd, "s1") for _ in range(3)])
    _write_session(sess_dir, "s2", [_bash_line(cmd, "s2") for _ in range(2)])
    env = _setup_env(tmp_path, proj)
    state = _state_dir(env)
    state.mkdir(parents=True, exist_ok=True)
    sentinel = "2020-01-01T00:00:00Z"
    (state / "last-scan.txt").write_text(sentinel, encoding="utf-8")

    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["sessionsScanned"] == 2
    assert payload["zeroReason"] is None
    assert payload["diagnostics"]["projectDirExists"] is True
    assert payload["diagnostics"]["rawSessionFiles"] == 2

    new_watermark = (state / "last-scan.txt").read_text(encoding="utf-8")
    assert new_watermark != sentinel


def test_zero_reason_all_before_cutoff(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    cmd = "railway logs --service api"
    old_mtime = time.time() - (400 * 86400)  # ~400 days: outside the default 30-day window
    _write_session(sess_dir, "s1", [_bash_line(cmd, "s1")], mtime=old_mtime)
    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["sessionsScanned"] == 0
    assert payload["zeroReason"] == "all_before_cutoff"


def test_zero_reason_filtered_by_caps(tmp_path: Path):
    proj, sess_dir = _seed_project(tmp_path)
    cmd = "railway logs --service api"
    _write_session(sess_dir, "s1", [_bash_line(cmd, "s1")])
    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent", "--max-sessions", "0")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["sessionsScanned"] == 0
    assert payload["zeroReason"] == "filtered_by_caps"


# ─── self-diagnosis canary (Task 5) ────────────────────────────────────────────


def test_zero_sessions_with_other_projects_present_adds_hint(tmp_path: Path):
    proj = (tmp_path / "myproj").resolve()
    proj.mkdir(parents=True, exist_ok=True)
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    # Two unrelated project dirs WITH history under the same projects root,
    # but neither is this project's own slug -- simulates a slug mismatch.
    for i in range(2):
        other_dir = fake_home / ".claude" / "projects" / f"some-other-encoded-slug-{i}"
        other_dir.mkdir(parents=True, exist_ok=True)
        (other_dir / "sess1.jsonl").write_text(_bash_line("echo hi") + "\n", encoding="utf-8")

    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["sessionsScanned"] == 0
    assert payload["zeroReason"] == "project_dir_missing"
    assert "hint" in payload
    assert payload["diagnostics"]["slug"] in payload["hint"]
    assert "2 other" in payload["hint"]
    assert "--since-days N" in payload["hint"]


def test_hint_absent_when_no_other_projects_exist(tmp_path: Path):
    """Canary control: 0 sessions but NO other project dirs present -- no
    hint (nothing suggests a slug mismatch, so don't guess at one)."""
    proj = (tmp_path / "myproj").resolve()
    proj.mkdir(parents=True, exist_ok=True)
    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["sessionsScanned"] == 0
    assert "hint" not in payload


def test_hint_absent_when_other_project_dirs_are_empty(tmp_path: Path):
    """Sibling project dirs exist under ~/.claude/projects/ but hold no
    .jsonl files -- an empty dir isn't "history", so no hint should fire
    even though other dirs are present. zeroReason is still set, since the
    scan itself still found 0 sessions for this project's own slug."""
    proj = (tmp_path / "myproj").resolve()
    proj.mkdir(parents=True, exist_ok=True)
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    # Two unrelated project dirs that exist but contain no session files.
    for i in range(2):
        other_dir = fake_home / ".claude" / "projects" / f"some-other-encoded-slug-{i}"
        other_dir.mkdir(parents=True, exist_ok=True)

    env = _setup_env(tmp_path, proj)
    res = _run_scan(env, "--project", str(proj), "--accept-consent")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["sessionsScanned"] == 0
    assert payload["zeroReason"] == "project_dir_missing"
    assert "hint" not in payload
