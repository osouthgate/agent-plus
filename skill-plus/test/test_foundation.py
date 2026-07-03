"""Foundation smoke tests for skill-plus. Subcommand-specific tests live in
test_<subcommand>.py files added by their respective slices."""
from __future__ import annotations

import json
import os
import re
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


def test_encode_project_path_pins_real_world_examples():
    # Regression for the v0.19.7 hotfix: Claude Code names project dirs by
    # replacing EACH non-alphanumeric character of the resolved cwd with its
    # own dash -- no collapsing of runs, no stripping, no re-prepending "C--".
    # The old implementation dashed only [\\/:], collapsed dash runs, stripped,
    # then re-prepended "C--", which produced C--C-dev-patchboard (a directory
    # that does not exist) instead of C--dev-patchboard, so skill-plus scan
    # silently found 0 sessions on Windows. _encode_project_path is a pure
    # string function (no filesystem/platform path semantics involved), so
    # these literals are safe to assert on any OS.
    mod = _load_bin_module()
    assert mod._encode_project_path("C:\\dev\\plans-agent-plus") == "C--dev-plans-agent-plus"
    # Field-report repro from the bug report.
    assert mod._encode_project_path("C:\\dev\\patchboard") == "C--dev-patchboard"
    assert mod._encode_project_path("/Users/bob/foo") == "-Users-bob-foo"
    # Dotted worktree name: guards against a narrower fix that only widens
    # the character class to [\\/:.] instead of "everything non-alphanumeric".
    assert mod._encode_project_path("C:\\dev\\foo.bar") == "C--dev-foo-bar"


def test_encoded_cwd_for_matches_observed_format(tmp_path: Path):
    # Integration test: encoded_cwd_for() resolves the given path and feeds
    # it through _encode_project_path(). The expected value is derived here
    # independently (not by calling mod._encode_project_path itself) so this
    # isn't tautological -- it pins the same one-line spec regex Claude Code
    # is observed to use.
    mod = _load_bin_module()
    expected = re.sub(r"[^A-Za-z0-9]", "-", str(tmp_path.resolve()))
    assert mod.encoded_cwd_for(tmp_path) == expected
    enc = mod.encoded_cwd_for(tmp_path)
    assert "/" not in enc and ":" not in enc and "\\" not in enc


# ── MSYS path normalisation (Windows path audit launch gate) ──────────────────
#
# `git rev-parse --show-toplevel` under Git Bash/MSYS returns `/c/dev/foo`;
# Path("/c/dev/foo") on Windows resolves to C:\c\dev\foo (a real past incident
# wrote state to C:\c\dev\Tinker-Tailor\.agent-plus). Mirrors the coverage
# style used for skill-feedback's identical helper.


def test_msys_to_windows_converts_drive_path_on_win32(monkeypatch):
    mod = _load_bin_module()
    monkeypatch.setattr(mod.sys, "platform", "win32")
    assert mod._msys_to_windows("/c/dev/foo") == "C:/dev/foo"
    # Drive letter is upcased; rest of the path is untouched.
    assert mod._msys_to_windows("/d/Work/Repo") == "D:/Work/Repo"


def test_msys_to_windows_posix_path_passthrough(monkeypatch):
    mod = _load_bin_module()
    # Even on win32, a multi-letter first component is not a drive letter.
    monkeypatch.setattr(mod.sys, "platform", "win32")
    assert mod._msys_to_windows("/home/user/x") == "/home/user/x"
    # On POSIX platforms the helper is a strict no-op, drive-shaped or not.
    monkeypatch.setattr(mod.sys, "platform", "linux")
    assert mod._msys_to_windows("/c/dev/foo") == "/c/dev/foo"
    assert mod._msys_to_windows("/home/user/x") == "/home/user/x"


def test_msys_to_windows_plain_windows_form_unchanged(monkeypatch):
    mod = _load_bin_module()
    monkeypatch.setattr(mod.sys, "platform", "win32")
    assert mod._msys_to_windows("C:/dev/foo") == "C:/dev/foo"
    assert mod._msys_to_windows("C:\\dev\\foo") == "C:\\dev\\foo"


def test_git_toplevel_normalises_msys_output(monkeypatch):
    # The wiring, not just the helper: _git_toplevel() must route git's stdout
    # through _msys_to_windows before building the Path.
    mod = _load_bin_module()
    monkeypatch.setattr(mod.sys, "platform", "win32")

    class _FakeProc:
        returncode = 0
        stdout = "/c/dev/foo\n"
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _FakeProc())
    assert mod._git_toplevel() == Path("C:/dev/foo")
