"""Foundation smoke tests for skill-plus. Subcommand-specific tests live in
test_<subcommand>.py files added by their respective slices."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-plus"


def _load_bin_module():
    """The bin file has no .py extension; load it via SourceFileLoader so tests
    can introspect helper functions."""
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("skill_plus_bin", str(BIN))
    import importlib.util
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True, timeout=30, cwd=str(cwd) if cwd else None, env=e,
    )


def test_version_string():
    res = _run("--version")
    assert res.returncode == 0
    assert "skill-plus" in res.stdout


def test_help_when_no_command():
    res = _run()
    assert res.returncode == 2


def test_envelope_has_tool_meta():
    # Use a still-stubbed subcommand so the envelope smoke test stays
    # independent of any real handler's gating (e.g. scan's consent gate).
    res = _run("list", "--pretty")
    assert res.returncode == 0
    payload = json.loads(res.stdout)
    # Read version from plugin.json so this test doesn't break on every bump.
    plugin_json = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
    expected_version = json.loads(plugin_json.read_text(encoding="utf-8"))["version"]
    assert payload["tool"] == {"name": "skill-plus", "version": expected_version}


def test_subcommand_dispatch_falls_back_to_stub_when_module_missing(tmp_path: Path):
    # Foundation: every declared subcommand resolves to either a real handler
    # or the stub — never crashes.
    mod = _load_bin_module()
    for name in mod.SUBCOMMANDS:
        handler = mod._load_subcommand(name) or mod._stub(name)
        assert callable(handler), f"no handler resolvable for {name}"


def test_envelope_payload_path_offload(tmp_path: Path):
    out = tmp_path / "envelope.json"
    res = _run("list", "--output", str(out), "--pretty")
    assert res.returncode == 0
    summary = json.loads(res.stdout)
    assert summary["payloadPath"] == str(out)
    assert "payloadKeys" in summary
    assert "payloadShape" in summary
    assert out.exists()
    full = json.loads(out.read_text(encoding="utf-8"))
    assert full["tool"]["name"] == "skill-plus"


def test_storage_root_resolves_under_git(tmp_path: Path, monkeypatch):
    mod = _load_bin_module()
    # Make a git repo at tmp_path
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    monkeypatch.chdir(tmp_path)
    root, source = mod.project_state_root_with_source()
    assert source == "git"
    assert root == (tmp_path.resolve() / ".agent-plus" / "skill-plus")


def test_storage_root_env_override(tmp_path: Path, monkeypatch):
    mod = _load_bin_module()
    monkeypatch.setenv("SKILL_PLUS_DIR", str(tmp_path / "custom"))
    root, source = mod.project_state_root_with_source()
    assert source == "env"
    assert root == (tmp_path / "custom").resolve()


def test_scrub_text_redacts_known_secrets():
    mod = _load_bin_module()
    cases = [
        "ghp_" + "a" * 36,
        "sk-ant-" + "x" * 40,
        "Bearer abcdefghijklmnopqrstuvwxyz123456",
        "postgres://user:pw@host/db",
        "--token=abc123def456ghi789",
    ]
    for c in cases:
        assert "[REDACTED]" in mod.scrub_text(c), f"failed to scrub: {c}"


def test_encoded_cwd_for_matches_observed_format(tmp_path: Path):
    mod = _load_bin_module()
    # On Windows we expect the C:/dev/plans-agent-plus shape => C--dev-plans-agent-plus
    enc = mod.encoded_cwd_for(Path("C:/dev/plans-agent-plus"))
    assert enc.startswith("C--")
    assert "/" not in enc and ":" not in enc
