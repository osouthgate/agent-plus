"""Tests for the skill-plus friction lens (scan -> blocks.jsonl;
propose --kind blocks). Mirrors test_scan.py / test_routine_scan.py fixture
conventions.

Positive paths use synthetic injected blocks; the negative/zero path uses
ordinary sessions (organic history has no blocks unless something refused).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-plus"

_AUTO = "denied by the Claude Code auto mode classifier"
_REJECT = "The user doesn't want to proceed with this tool use."


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
    # Same literals as test_scan.py / test_foundation.py's pin, checked here
    # against the raw spec regex (no .resolve() involved, so safe on any OS)
    # to guard this file's fixture helper specifically -- this is the helper
    # whose drift let the original bug ship undetected.
    pattern = r"[^A-Za-z0-9]"
    assert re.sub(pattern, "-", "C:\\dev\\patchboard") == "C--dev-patchboard"
    assert re.sub(pattern, "-", "/Users/bob/foo") == "-Users-bob-foo"
    assert re.sub(pattern, "-", "C:\\dev\\foo.bar") == "C--dev-foo-bar"


def _bash_line(cmd: str, sid: str = "s1", ts: str = "2026-05-10T09:00:00Z") -> str:
    return json.dumps({
        "type": "assistant", "sessionId": sid, "timestamp": ts,
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]},
    })


def _use_line(tool: str, inp: dict, uid: str, sid: str = "s1",
              ts: str = "2026-05-10T09:00:00Z") -> str:
    return json.dumps({
        "type": "assistant", "sessionId": sid, "timestamp": ts,
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": uid, "name": tool, "input": inp}]},
    })


def _result_line(uid: str, text: str, sid: str = "s1",
                  ts: str = "2026-05-10T09:00:01Z") -> str:
    return json.dumps({
        "type": "user", "sessionId": sid, "timestamp": ts,
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": uid,
             "content": [{"type": "text", "text": text}]}]},
    })


def _block_pair(tool: str, inp: dict, reason_text: str, uid: str,
                sid: str = "s1") -> list[str]:
    return [_use_line(tool, inp, uid, sid), _result_line(uid, reason_text, sid)]


def _write_session(sess_dir: Path, name: str, lines: list[str]) -> Path:
    sess_dir.mkdir(parents=True, exist_ok=True)
    f = sess_dir / f"{name}.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


def _setup_env(tmp_path: Path) -> dict[str, str]:
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)
    env["SKILL_PLUS_DIR"] = str(tmp_path / "state")
    return env


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    proj = (tmp_path / "myproj").resolve()
    proj.mkdir(parents=True, exist_ok=True)
    sess_dir = tmp_path / "home" / ".claude" / "projects" / _encoded(proj)
    sess_dir.mkdir(parents=True, exist_ok=True)
    return proj, sess_dir


def _scan(env: dict[str, str], proj: Path) -> dict:
    r = subprocess.run(
        [sys.executable, str(BIN), "scan", "--pretty",
         "--project", str(proj), "--accept-consent"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert r.returncode == 0, r.stderr or r.stdout
    return json.loads(r.stdout)


def _propose_blocks(env: dict[str, str], proj: Path, *extra: str) -> dict:
    # cwd is pinned to proj.parent (== tmp_path: has home/, myproj/, state/
    # but no .claude/) so _load_settings's cwd-scoped settings.json lookup
    # can never pick up a REAL ambient .claude/settings.json from wherever
    # the test runner happens to be invoked from (e.g. the agent-plus repo
    # root itself has one). Mirrors test_routine_boomerang.py's cwd=tmp_path
    # isolation for the same reason.
    r = subprocess.run(
        [sys.executable, str(BIN), "propose", "--pretty",
         "--kind", "blocks", "--project", str(proj), *extra],
        capture_output=True, text=True, timeout=30, env=env,
        cwd=str(proj.parent),
    )
    assert r.returncode == 0, r.stderr or r.stdout
    return json.loads(r.stdout)


def _write_settings(env: dict[str, str], allow: list[str]) -> None:
    """Write ~/.claude/settings.json (fake HOME) with permissions.allow."""
    home = Path(env["HOME"])
    d = home / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text(
        json.dumps({"permissions": {"allow": allow}}), encoding="utf-8")


# ─── tests ───────────────────────────────────────────────────────────────────


def test_zero_path_no_blocks(tmp_path: Path):
    """Ordinary session, nothing refused -> empty friction signal, no crash."""
    proj, sd = _seed(tmp_path)
    _write_session(sd, "s1", [_bash_line("railway logs --service api") for _ in range(3)])
    p = _scan(_setup_env(tmp_path), proj)
    assert p["blocks"]["total"] == 0
    assert p["blocks"]["byClass"] == {}
    assert p["blocksTop"] == []


def test_auto_block_joined_and_classified(tmp_path: Path):
    proj, sd = _seed(tmp_path)
    reason = (f"{_AUTO}. Reason: Creating a new Linear ticket "
              f"(External System Writes) the user never asked for.")
    _write_session(sd, "s1", _block_pair(
        "Bash", {"command": "railway logs --service api"}, reason, "u1"))
    p = _scan(_setup_env(tmp_path), proj)
    assert p["blocks"]["byClass"].get("AUTO_MODE_CLASSIFIER") == 1
    rec = p["blocksTop"][0]
    assert rec["class"] == "AUTO_MODE_CLASSIFIER"
    assert rec["signature"] == "Bash: railway"
    assert "External System Writes" in rec["categories"]
    assert rec["configFixable"] is False
    assert rec["signatureTokens"][:2] == ["railway", "logs"]


def test_user_rejected_is_config_fixable(tmp_path: Path):
    proj, sd = _seed(tmp_path)
    _write_session(sd, "s1", _block_pair(
        "Bash", {"command": "supabase db push"}, _REJECT, "u1"))
    p = _scan(_setup_env(tmp_path), proj)
    rec = p["blocksTop"][0]
    assert rec["class"] == "USER_REJECTED_PROMPT"
    assert rec["configFixable"] is True


def test_secret_scrubbed_from_reason(tmp_path: Path):
    proj, sd = _seed(tmp_path)
    leak = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    reason = f"{_AUTO}. Reason: ran curl --token={leak} against the API."
    _write_session(sd, "s1", _block_pair(
        "Bash", {"command": "curl https://x"}, reason, "u1"))
    p = _scan(_setup_env(tmp_path), proj)
    blob = json.dumps(p)
    assert leak not in blob
    assert "[REDACTED]" in json.dumps(p["blocksTop"][0]["sampleReasons"])


def test_cd_compound_flagged_proven_ineffective(tmp_path: Path):
    proj, sd = _seed(tmp_path)
    _write_session(sd, "s1", _block_pair(
        "Bash", {"command": "cd /foo && pnpm test"}, _AUTO, "u1"))
    p = _scan(_setup_env(tmp_path), proj)
    rec = p["blocksTop"][0]
    assert rec["signature"] == "Bash: cd"
    assert rec["habitNoteProvenIneffective"] is True


def test_category_parser_not_polluted_by_code_fragments(tmp_path: Path):
    """The anchored allowlist must NOT scrape `msg.get("content")` etc.
    into fake categories (the standalone CAT_RE bug)."""
    proj, sd = _seed(tmp_path)
    reason = (f"{_AUTO}. Reason: Force-push to remote (Git Destructive); "
              f'the transcript line had msg.get("content") and (toks) in it.')
    _write_session(sd, "s1", _block_pair(
        "Bash", {"command": "git push --force"}, reason, "u1"))
    p = _scan(_setup_env(tmp_path), proj)
    cats = p["blocksTop"][0]["categories"]
    assert cats == ["Git Destructive"]


def test_non_bash_tool_input_never_echoed(tmp_path: Path):
    """mcp save_* / Edit inputs carry payloads -- signature is the bare tool
    name, and no input content leaks into the envelope."""
    proj, sd = _seed(tmp_path)
    secret_body = "sk-ant-SECRETSECRETSECRETSECRETSECRET"
    _write_session(sd, "s1", _block_pair(
        "mcp__plugin_linear_linear__save_issue",
        {"title": "x", "description": secret_body}, _AUTO, "u1"))
    p = _scan(_setup_env(tmp_path), proj)
    rec = p["blocksTop"][0]
    assert rec["signature"] == "mcp__plugin_linear_linear__save_issue"
    assert secret_body not in json.dumps(p)


def test_merge_accumulates_across_scans(tmp_path: Path):
    proj, sd = _seed(tmp_path)
    env = _setup_env(tmp_path)
    _write_session(sd, "s1", _block_pair(
        "Bash", {"command": "supabase db push"}, _REJECT, "u1"))
    _scan(env, proj)
    _write_session(sd, "s2", _block_pair(
        "Bash", {"command": "supabase db push"}, _REJECT, "u2", "s2"))
    p = _scan(env, proj)
    rec = next(r for r in p["blocksTop"] if r["signature"] == "Bash: supabase")
    assert rec["count"] == 2
    assert len(rec["sessions"]) == 2


def test_propose_blocks_envelope(tmp_path: Path):
    proj, sd = _seed(tmp_path)
    env = _setup_env(tmp_path)
    _write_session(sd, "s1", (
        _block_pair("Bash", {"command": "supabase db push"}, _REJECT, "u1")
        + _block_pair("Bash", {"command": "railway logs"},
                      f"{_AUTO}. Reason: Production Reads not authorized.", "u2")
    ))
    _scan(env, proj)
    p = _propose_blocks(env, proj)
    assert p["kind"] == "blocks"
    assert p["frictionBoost"] == []
    assert p["byClass"].get("USER_REJECTED_PROMPT") == 1
    assert p["byClass"].get("AUTO_MODE_CLASSIFIER") == 1
    assert "Bash: supabase" in p["bySignature"]
    counts = [c["count"] for c in p["candidates"]]
    assert counts == sorted(counts, reverse=True)
    # coverage cross-reference: no settings files -> all not_covered,
    # the declined supabase prompt is an actionable gap.
    assert p["byCoverage"].get("USER_REJECTED_PROMPT|not_covered") == 1
    assert p["byCoverage"].get("AUTO_MODE_CLASSIFIER|not_covered") == 1
    assert "Bash: supabase" in p["actionableAllowlistGaps"]
    assert all(c["coverage"] == "not_covered" for c in p["candidates"])


def test_propose_blocks_empty_store(tmp_path: Path):
    proj, _ = _seed(tmp_path)
    p = _propose_blocks(_setup_env(tmp_path), proj)
    assert p["candidates"] == []
    assert p["frictionBoost"] == []
    assert p["byCoverage"] == {}
    assert p["actionableAllowlistGaps"] == {}
    assert "note" in p


def test_propose_blocks_coverage_routes_allowlisted(tmp_path: Path):
    """A USER_REJECTED block on an ALREADY-allowlisted tool is NOT an
    actionable gap; an AUTO veto on an allowlisted tool is the headline
    proof that an allowlist cannot fix classifier vetoes."""
    proj, sd = _seed(tmp_path)
    env = _setup_env(tmp_path)
    _write_settings(env, ["Bash(supabase:*)"])
    _write_session(sd, "s1", (
        _block_pair("Bash", {"command": "supabase db push"}, _REJECT, "u1")
        + _block_pair("Bash", {"command": "supabase db reset"},
                      f"{_AUTO}. Reason: Production Reads not authorized.", "u2")
    ))
    _scan(env, proj)
    p = _propose_blocks(env, proj)
    assert p["byCoverage"].get("USER_REJECTED_PROMPT|already_allowlisted") == 1
    assert p["byCoverage"].get("AUTO_MODE_CLASSIFIER|already_allowlisted") == 1
    # covered -> NOT re-proposed as an allowlist gap
    assert "Bash: supabase" not in p["actionableAllowlistGaps"]
    assert p["settingsCrossReferenced"]  # non-empty list of source files


def test_propose_blocks_no_settings_flag(tmp_path: Path):
    """--no-settings disables the cross-reference even when a settings
    file exists: coverage falls back to not_covered, sources -> None."""
    proj, sd = _seed(tmp_path)
    env = _setup_env(tmp_path)
    _write_settings(env, ["Bash(supabase:*)"])
    _write_session(sd, "s1", _block_pair(
        "Bash", {"command": "supabase db push"}, _REJECT, "u1"))
    _scan(env, proj)
    p = _propose_blocks(env, proj, "--no-settings")
    assert p["settingsCrossReferenced"] is None
    assert p["byCoverage"].get("USER_REJECTED_PROMPT|not_covered") == 1
    assert "Bash: supabase" in p["actionableAllowlistGaps"]


# ─── redaction: on-disk rescrub (mirrors test_scan.py / test_routine_scan.py) ─


def test_rescrub_removes_previously_leaked_token_from_blocks_log_on_rewrite(tmp_path: Path):
    """Regression guard for the friction lens: a token that leaked into
    blocks.jsonl BEFORE a redaction-pattern gap was fixed must be scrubbed on
    the next scan's full rewrite, even though this scan's friction pass never
    recomputes that record (zero sessions this run, so blocks_agg never
    touches its id) -- it is carried forward straight off disk by
    _read_existing() and only a rescrub-on-write pass can clean it. Mirrors
    test_routine_scan.py's
    test_rescrub_removes_previously_leaked_token_from_routine_log_on_rewrite
    for routine-candidates.jsonl. (The friction lens's original design
    predates this hardening -- added here to bring blocks.jsonl to parity
    with candidates.jsonl / routine-candidates.jsonl.)"""
    proj, sd = _seed(tmp_path)
    env = _setup_env(tmp_path)
    state_dir = Path(env["SKILL_PLUS_DIR"])
    state_dir.mkdir(parents=True, exist_ok=True)

    fake_hash = "f" * 40
    leaked_reason = f'curl -s -H "Authorization: Bearer 9|{fake_hash}" https://example.test'
    leaked_record = {
        "id": "leakedblock001",
        "tool": "Bash",
        "class": "USER_REJECTED_PROMPT",
        "signature": "Bash: curl",
        "signatureTokens": ["curl", "-s", "-H", "https://example.test"],
        "count": 3,
        "categories": [],
        "sampleReasons": [leaked_reason],
        "projects": [str(proj)],
        "sessions": ["old1", "old2"],
        "byMonth": {"2026-01": 3},
        "configFixable": True,
        "habitNoteProvenIneffective": False,
        "firstSeen": "2026-01-01T00:00:00Z",
        "lastSeen": "2026-01-01T00:00:00Z",
        "scannedAt": "2026-01-01T00:00:00Z",
    }
    (state_dir / "blocks.jsonl").write_text(
        json.dumps(leaked_record) + "\n", encoding="utf-8"
    )

    _scan(env, proj)

    on_disk = (state_dir / "blocks.jsonl").read_text(encoding="utf-8")
    assert "9|" not in on_disk
    assert fake_hash not in on_disk
    assert "[REDACTED]" in on_disk
    rec = json.loads(on_disk.strip().splitlines()[0])
    assert rec["id"] == "leakedblock001"  # record preserved, just scrubbed
