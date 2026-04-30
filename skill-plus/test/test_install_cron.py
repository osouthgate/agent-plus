"""Tests for skill-plus install-cron subcommand (slice 3.6).

Never invokes crontab or schtasks for real — subprocess.run is stubbed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-plus"
MODULE_PATH = Path(__file__).resolve().parent.parent / "bin" / "_subcommands" / "install_cron.py"


def _load_bin_module():
    from importlib.machinery import SourceFileLoader
    import importlib.util
    loader = SourceFileLoader("skill_plus_bin", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _load_install_cron(state_root: Path, consent_p: Path):
    """Load install_cron.py with the same helper injection pattern bin/skill-plus uses,
    but with overrides pointed at tmp_path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_skill_plus_install_cron_test", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    bin_mod = _load_bin_module()

    def _project_state_root():
        return state_root

    def _grant_consent_for(project_path, source="install-cron"):
        consent_p.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if consent_p.exists():
            existing = json.loads(consent_p.read_text(encoding="utf-8"))
        projects = existing.setdefault("projects", {})
        projects[str(Path(project_path).resolve())] = {"source": source}
        consent_p.write_text(json.dumps(existing), encoding="utf-8")

    mod.__dict__.update({
        "project_state_root": _project_state_root,
        "_git_toplevel": lambda: None,
        "grant_consent_for": _grant_consent_for,
        "_ensure_dir": bin_mod._ensure_dir,
        "_now_iso": bin_mod._now_iso,
        "consent_path": lambda: consent_p,
    })
    spec.loader.exec_module(mod)
    return mod


def _args(**kw):
    defaults = {
        "project": None,
        "frequency": "weekly",
        "print_only": False,
        "uninstall": False,
    }
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


# ─── platform branch tests ────────────────────────────────────────────────────


def test_posix_print_only_weekly(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    state = tmp_path / "state"
    consent = tmp_path / "consent.json"
    mod = _load_install_cron(state, consent)

    captured = {}
    def emit_fn(p):
        captured["payload"] = p

    proj = tmp_path / "myproj"
    proj.mkdir()
    rc = mod.run(_args(project=str(proj), print_only=True, frequency="weekly"), emit_fn)
    assert rc == 0
    p = captured["payload"]
    assert p["ok"] is True
    assert p["platform"] == "posix"
    assert p["action"] == "print-only"
    # Weekly cron expression
    assert p["entry"].startswith("# skill-plus auto-installed for ")
    assert "0 3 * * 0" in p["entry"]
    assert "scan --accept-consent --project" in p["entry"]
    # consent NOT granted in print-only
    assert not consent.exists()


def test_posix_print_only_daily_cron_expression(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    captured = {}
    proj = tmp_path / "p"
    proj.mkdir()
    mod.run(_args(project=str(proj), print_only=True, frequency="daily"),
            lambda x: captured.setdefault("p", x))
    assert "0 3 * * *" in captured["p"]["entry"]


def test_windows_print_only_weekly(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    captured = {}
    proj = tmp_path / "winproj"
    proj.mkdir()
    rc = mod.run(_args(project=str(proj), print_only=True, frequency="weekly"),
                 lambda x: captured.setdefault("p", x))
    assert rc == 0
    p = captured["p"]
    assert p["platform"] == "windows"
    assert p["action"] == "print-only"
    assert isinstance(p["entry"], list)
    assert "schtasks" in p["entry"][0]
    assert "/create" in p["entry"]
    assert "/sc" in p["entry"] and "weekly" in p["entry"]
    assert "/d" in p["entry"] and "SUN" in p["entry"]
    assert "/st" in p["entry"] and "03:00" in p["entry"]
    assert "/f" in p["entry"]


def test_windows_print_only_daily(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    captured = {}
    proj = tmp_path / "winproj"
    proj.mkdir()
    mod.run(_args(project=str(proj), print_only=True, frequency="daily"),
            lambda x: captured.setdefault("p", x))
    args = captured["p"]["entry"]
    assert "daily" in args
    # daily should NOT have /d SUN
    assert "SUN" not in args


def test_windows_task_name_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    proj = tmp_path / "weird path!@#with$$$chars"
    proj.mkdir()
    captured = {}
    mod.run(_args(project=str(proj), print_only=True),
            lambda x: captured.setdefault("p", x))
    name = captured["p"]["taskName"]
    assert name.startswith("agent-plus-skill-plus-scan-")
    # No special characters left
    suffix = name[len("agent-plus-skill-plus-scan-"):]
    assert all(c.isalnum() or c == "-" for c in suffix), suffix
    # Multiple non-alnum collapse to single dash
    assert "--" not in suffix


# ─── idempotency / install via mocked subprocess ──────────────────────────────


class _FakeRunner:
    """Mock subprocess.run substitute: scripted responses + call recorder."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        if not self.responses:
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")
        r = self.responses.pop(0)
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=r.get("rc", 0),
            stdout=r.get("stdout", ""),
            stderr=r.get("stderr", ""),
        )


def test_posix_idempotent_replaces_existing_block(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    proj = tmp_path / "p"
    proj.mkdir()
    marker = f"# skill-plus auto-installed for {proj.resolve()}"
    existing = f"# user comment\n0 0 * * * other_job\n{marker}\n0 99 * * 0 STALE_ENTRY\n"
    runner = _FakeRunner([
        {"rc": 0, "stdout": existing},  # crontab -l
        {"rc": 0},                       # crontab -
    ])
    payload = mod._posix_action(proj.resolve(), "weekly",
                                print_only=False, uninstall=False, runner=runner)
    assert payload["action"] == "reinstalled"
    # Verify the write call's stdin
    write_call = runner.calls[1]
    written = write_call["kwargs"]["input"]
    assert "STALE_ENTRY" not in written
    assert marker in written
    assert "0 3 * * 0" in written
    assert "other_job" in written  # other lines preserved


def test_posix_install_appends_when_no_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    proj = tmp_path / "p"
    proj.mkdir()
    runner = _FakeRunner([
        {"rc": 1, "stdout": "", "stderr": "no crontab for user"},  # no existing
        {"rc": 0},                                                   # write
    ])
    payload = mod._posix_action(proj.resolve(), "daily",
                                print_only=False, uninstall=False, runner=runner)
    assert payload["action"] == "installed"
    written = runner.calls[1]["kwargs"]["input"]
    assert "0 3 * * *" in written


def test_posix_uninstall_strips_block(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    proj = tmp_path / "p"
    proj.mkdir()
    marker = f"# skill-plus auto-installed for {proj.resolve()}"
    existing = f"0 0 * * * keep_me\n{marker}\n0 3 * * 0 the_entry\n"
    runner = _FakeRunner([
        {"rc": 0, "stdout": existing},
        {"rc": 0},
    ])
    payload = mod._posix_action(proj.resolve(), "weekly",
                                print_only=False, uninstall=True, runner=runner)
    assert payload["action"] == "uninstalled"
    assert payload["wasPresent"] is True
    written = runner.calls[1]["kwargs"]["input"]
    assert marker not in written
    assert "the_entry" not in written
    assert "keep_me" in written


def test_posix_uninstall_print_only(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    proj = tmp_path / "p"
    proj.mkdir()
    marker = f"# skill-plus auto-installed for {proj.resolve()}"
    existing = f"{marker}\n0 3 * * 0 entry\n"
    runner = _FakeRunner([{"rc": 0, "stdout": existing}])
    payload = mod._posix_action(proj.resolve(), "weekly",
                                print_only=True, uninstall=True, runner=runner)
    assert payload["action"] == "uninstall-print-only"
    assert payload["wasPresent"] is True
    assert marker in payload["wouldRemove"]
    # No write call made
    assert len(runner.calls) == 1


def test_posix_uninstall_idempotent_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    proj = tmp_path / "p"
    proj.mkdir()
    runner = _FakeRunner([{"rc": 0, "stdout": "0 0 * * * other\n"}])
    payload = mod._posix_action(proj.resolve(), "weekly",
                                print_only=False, uninstall=True, runner=runner)
    assert payload["action"] == "uninstalled"
    assert payload["wasPresent"] is False
    # No write attempted when nothing to remove
    assert len(runner.calls) == 1


def test_consent_granted_on_full_install_via_run(tmp_path, monkeypatch):
    """End-to-end via run() — patch subprocess.run so no real crontab is touched."""
    monkeypatch.setattr(sys, "platform", "linux")
    consent_p = tmp_path / "consent.json"
    mod = _load_install_cron(tmp_path / "state", consent_p)

    runner = _FakeRunner([
        {"rc": 0, "stdout": ""},  # crontab -l empty
        {"rc": 0},                 # crontab - write
    ])
    monkeypatch.setattr(mod.subprocess, "run", runner)

    proj = tmp_path / "myproj"
    proj.mkdir()

    captured = {}
    rc = mod.run(_args(project=str(proj), frequency="weekly"),
                 lambda x: captured.setdefault("p", x))
    assert rc == 0
    p = captured["p"]
    assert p["ok"] is True
    assert p["action"] == "installed"
    assert p["consentGranted"] is True
    # Consent file written
    assert consent_p.exists()
    data = json.loads(consent_p.read_text(encoding="utf-8"))
    assert str(proj.resolve()) in data["projects"]
    assert data["projects"][str(proj.resolve())]["source"] == "install-cron"


def test_windows_uninstall_idempotent_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    runner = _FakeRunner([{"rc": 1, "stderr": "ERROR: The system cannot find the file specified."}])
    proj = tmp_path / "p"
    proj.mkdir()
    payload = mod._windows_action(proj.resolve(), "weekly",
                                  print_only=False, uninstall=True, runner=runner)
    assert payload["action"] == "uninstalled"
    assert payload["wasPresent"] is False


def test_windows_install_when_task_already_exists_reports_reinstalled(tmp_path, monkeypatch):
    """When schtasks /query returns rc=0 (task exists), action must be 'reinstalled'."""
    monkeypatch.setattr(sys, "platform", "win32")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    proj = tmp_path / "p"
    proj.mkdir()
    runner = _FakeRunner([
        {"rc": 0, "stdout": "TaskName: ..."},   # /query → exists
        {"rc": 0},                                 # /create succeeds
    ])
    payload = mod._windows_action(proj.resolve(), "weekly",
                                  print_only=False, uninstall=False, runner=runner)
    assert payload["action"] == "reinstalled"
    # Two calls: query + create
    assert len(runner.calls) == 2
    # First was a query
    assert "/query" in runner.calls[0]["args"][0]
    # Second was a create
    assert "/create" in runner.calls[1]["args"][0]


def test_windows_install_when_task_absent_reports_installed(tmp_path, monkeypatch):
    """When schtasks /query returns nonzero (task does not exist), action must be 'installed'."""
    monkeypatch.setattr(sys, "platform", "win32")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    proj = tmp_path / "p"
    proj.mkdir()
    runner = _FakeRunner([
        {"rc": 1, "stderr": "ERROR: The system cannot find the file specified."},  # /query → absent
        {"rc": 0},                                                                    # /create succeeds
    ])
    payload = mod._windows_action(proj.resolve(), "weekly",
                                  print_only=False, uninstall=False, runner=runner)
    assert payload["action"] == "installed"
    assert len(runner.calls) == 2


def test_windows_uninstall_when_present_uses_exit_code_path(tmp_path, monkeypatch):
    """Uninstall must rely on /query exit code (locale-independent), not stderr substrings."""
    monkeypatch.setattr(sys, "platform", "win32")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    proj = tmp_path / "p"
    proj.mkdir()
    runner = _FakeRunner([
        {"rc": 0, "stdout": "TaskName: ..."},  # /query → exists
        {"rc": 0},                              # /delete succeeds
    ])
    payload = mod._windows_action(proj.resolve(), "weekly",
                                  print_only=False, uninstall=True, runner=runner)
    assert payload["action"] == "uninstalled"
    assert payload["wasPresent"] is True
    # Two calls: query + delete (no stderr substring matching)
    assert len(runner.calls) == 2
    assert "/query" in runner.calls[0]["args"][0]
    assert "/delete" in runner.calls[1]["args"][0]


def test_windows_uninstall_when_absent_localized_stderr(tmp_path, monkeypatch):
    """Even with a non-English schtasks error message, the exit-code probe must work."""
    monkeypatch.setattr(sys, "platform", "win32")
    mod = _load_install_cron(tmp_path / "state", tmp_path / "consent.json")
    proj = tmp_path / "p"
    proj.mkdir()
    # German-ish localized stderr — the OLD substring-match code would have failed.
    runner = _FakeRunner([
        {"rc": 1, "stderr": "FEHLER: Die angegebene Datei wurde nicht gefunden."},
    ])
    payload = mod._windows_action(proj.resolve(), "weekly",
                                  print_only=False, uninstall=True, runner=runner)
    assert payload["action"] == "uninstalled"
    assert payload["wasPresent"] is False
    # No /delete call attempted
    assert len(runner.calls) == 1


def test_subcommand_module_resolves_via_bin_dispatcher():
    """Verify the bin's hyphen→underscore mapping picks up install_cron.py."""
    bin_mod = _load_bin_module()
    handler = bin_mod._load_subcommand("install-cron")
    assert callable(handler)
