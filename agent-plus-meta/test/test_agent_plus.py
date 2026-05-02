"""Unit tests for agent-plus. Stdlib unittest only — no pytest, no network.

Run with:
    python3 -m unittest agent-plus/test/test_agent_plus.py
or:
    python3 -m pytest agent-plus/test/
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "agent-plus-meta"
    loader = SourceFileLoader("agent_plus", str(bin_path))
    spec = importlib.util.spec_from_loader("agent_plus", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ap = _load_module()
BIN = Path(__file__).resolve().parent.parent / "bin" / "agent-plus-meta"


def _run(*args: str, env: dict | None = None, cwd: str | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True,
        env={**os.environ, **(env or {})},
        cwd=cwd,
        timeout=15,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ─── envelope ────────────────────────────────────────────────────────────────


class TestEnvelope(unittest.TestCase):
    """Pattern #6: every JSON output carries `tool: {name, version}`."""

    def test_tool_meta_shape(self) -> None:
        meta = ap._tool_meta()
        self.assertEqual(meta["name"], "agent-plus-meta")
        self.assertIsInstance(meta["version"], str)

    def test_with_tool_meta_dict(self) -> None:
        out = ap._with_tool_meta({"foo": "bar"})
        self.assertIn("tool", out)
        self.assertEqual(out["tool"]["name"], "agent-plus-meta")
        self.assertEqual(out["foo"], "bar")

    def test_with_tool_meta_passthrough_for_non_dict(self) -> None:
        # Non-dicts pass through unchanged.
        self.assertEqual(ap._with_tool_meta(["a", "b"]), ["a", "b"])
        self.assertEqual(ap._with_tool_meta("hi"), "hi")

    def test_envelope_does_not_overwrite_existing_tool_key(self) -> None:
        out = ap._with_tool_meta({"tool": "preset", "x": 1})
        self.assertEqual(out["tool"], "preset")


# ─── --version ───────────────────────────────────────────────────────────────


class TestVersionFlag(unittest.TestCase):
    def test_version_exits_zero_and_prints_version(self) -> None:
        rc, out, err = _run("--version")
        self.assertEqual(rc, 0, msg=f"stderr={err!r}")
        self.assertTrue(out.strip(), "version flag printed nothing")
        # Manifest version is 0.1.0 in v0.1.0 — but don't hardcode in case
        # of bumps; just assert it's a non-empty string.


# ─── init ────────────────────────────────────────────────────────────────────


class TestInit(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _init(self) -> dict:
        rc, out, err = _run("init", "--dir", str(self.dir))
        self.assertEqual(rc, 0, msg=f"init failed: {err!r}")
        return json.loads(out)

    def test_init_creates_three_files_when_absent(self) -> None:
        result = self._init()
        ws = Path(result["workspace"])
        self.assertTrue(ws.is_dir())
        for f in ("manifest.json", "services.json", "env-status.json"):
            self.assertTrue((ws / f).is_file(), f"missing {f}")
        self.assertEqual(sorted(result["created"]),
                         ["env-status.json", "manifest.json", "services.json"])
        self.assertEqual(result["skipped"], [])

    def test_init_envelope_present(self) -> None:
        result = self._init()
        self.assertIn("tool", result)
        self.assertEqual(result["tool"]["name"], "agent-plus-meta")

    def test_init_idempotent_skips_existing_files(self) -> None:
        self._init()
        result2 = self._init()
        self.assertEqual(result2["created"], [])
        self.assertEqual(sorted(result2["skipped"]),
                         ["env-status.json", "manifest.json", "services.json"])

    def test_init_manifest_shape(self) -> None:
        self._init()
        ws = self.dir / ".agent-plus"
        manifest = json.loads((ws / "manifest.json").read_text())
        self.assertEqual(manifest["plugins"], [])
        self.assertIn("version", manifest)
        self.assertIn("createdAt", manifest)

    def test_init_env_status_shape(self) -> None:
        self._init()
        ws = self.dir / ".agent-plus"
        es = json.loads((ws / "env-status.json").read_text())
        self.assertEqual(es["checked"], [])
        self.assertEqual(es["missing"], [])
        self.assertIsNone(es["checkedAt"])

    def test_init_services_shape(self) -> None:
        self._init()
        ws = self.dir / ".agent-plus"
        svc = json.loads((ws / "services.json").read_text())
        self.assertEqual(svc, {"services": {}})

    def test_init_returns_tool_created_skipped(self) -> None:
        result = self._init()
        for k in ("tool", "created", "skipped"):
            self.assertIn(k, result)


class TestInitBadDirError(unittest.TestCase):
    """F2 (v0.15.6): `init --dir <unwritable>` returns a structured
    three-tier envelope (problem/cause/fix) instead of leaking raw OS
    errors. See agent-plus-meta CHANGELOG entry for the original incident.
    """

    def test_bad_dir_returns_structured_envelope(self) -> None:
        # Pick a path whose deepest existing ancestor is read-only on every
        # platform: on POSIX this is `/proc/1` (owned by root), on Windows
        # we use a path under the system root that's reliably write-denied
        # for unprivileged users. We just need ANY OSError on mkdir to
        # exercise the structured-error path; the contents of `cause` are
        # platform-specific by design.
        if sys.platform == "win32":
            bad = r"C:\Windows\System32\agent-plus-test-deny-xxx"
        else:
            bad = "/proc/1/agent-plus-test-deny-xxx"
        rc, out, err = _run("init", "--non-interactive", "--auto",
                            "--dir", bad)
        self.assertEqual(rc, 1, msg=f"expected rc=1, got {rc}; stderr={err!r}")
        # Structured envelope is emitted on stderr (matches the existing
        # generic-error contract).
        try:
            payload = json.loads(err.strip())
        except json.JSONDecodeError:
            self.fail(f"stderr was not JSON: {err!r}")
        self.assertEqual(payload.get("error"),
                         "could not create workspace directory")
        for k in ("problem", "cause", "fix", "tool", "cmd"):
            self.assertIn(k, payload, f"missing {k} in {payload!r}")
        # The `fix` line must point users toward a writable home-relative
        # path — this is the actionable bit that distinguishes the new
        # envelope from the old `[WinError 5]` leak.
        self.assertIn("--dir", payload["fix"])
        self.assertIn("~/", payload["fix"])

    def test_msys_detection_helper_handles_git_prefix(self) -> None:
        """Unit-level check on the MSYS-prefix helper. Independent of the
        running platform — we just verify the prefix list catches the
        Git for Windows install dir when sys.platform=='win32'.
        """
        # Force-import the init submodule (uses _load_init_module above).
        with patch.object(_init_mod.sys, "platform", "win32"):
            self.assertTrue(_init_mod._looks_msys_mangled(
                "/foo", Path(r"C:\Program Files\Git\foo")))
            self.assertFalse(_init_mod._looks_msys_mangled(
                "/foo", Path(r"C:\Users\me\foo")))
        with patch.object(_init_mod.sys, "platform", "linux"):
            # Non-Windows: never flag MSYS even if path matches.
            self.assertFalse(_init_mod._looks_msys_mangled(
                "/foo", Path(r"C:\Program Files\Git\foo")))


# ─── init wizard (v0.12.0) ───────────────────────────────────────────────────


def _load_init_module():
    """Load the wizard submodule against the test-loaded host bin so its
    bind() resolves to the same module the test imported as `ap`."""
    bin_dir = Path(__file__).resolve().parent.parent / "bin"
    if str(bin_dir) not in sys.path:
        sys.path.insert(0, str(bin_dir))
    from _subcommands import init as init_mod  # noqa: PLC0415
    init_mod.bind(ap)
    return init_mod


_init_mod = _load_init_module()


def _wizard_args(dir_flag: str, *,
                 non_interactive: bool = True,
                 auto: bool = True,
                 env_file: str | None = None,
                 pretty: bool = False) -> 'object':
    import argparse as _argparse
    return _argparse.Namespace(
        dir=dir_flag,
        env_file=env_file,
        pretty=pretty,
        non_interactive=non_interactive,
        auto=auto,
    )


class TestInitWizard(unittest.TestCase):
    """v0.12.0 persona-aware onboarding wizard tests. All subprocess calls
    are mocked via patch — these tests assert orchestration only."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.fake_home = Path(self.tmp.name) / "fake_home"
        self.fake_home.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _patch_subprocess_ok(self):
        """Make every subprocess.run + shutil.which call succeed silently."""
        from unittest.mock import MagicMock
        ok = MagicMock(returncode=0, stdout='{"candidates": []}', stderr="")
        return [
            patch.object(_init_mod.subprocess, "run", return_value=ok),
            patch.object(_init_mod.shutil, "which", return_value="/usr/bin/stub"),
        ]

    # ── state detection ────────────────────────────────────────────────

    def test_detect_user_state_empty_workspace(self) -> None:
        with patch.object(_init_mod, "_h", return_value=ap):
            with patch.object(Path, "home", return_value=self.fake_home):
                state = _init_mod._detect_user_state(self.dir / ".agent-plus")
        self.assertFalse(state["has_skills"])
        self.assertFalse(state["has_claude_projects_history"])
        self.assertFalse(state["agent_plus_already_init"])
        self.assertIn("homeless", state)

    def test_detect_skills_present(self) -> None:
        proj = self.dir / "proj"
        (proj / ".claude" / "skills" / "my-skill").mkdir(parents=True)
        (proj / ".claude" / "skills" / "my-skill" / "SKILL.md").write_text(
            "x", encoding="utf-8")
        with patch.object(_init_mod, "_h", return_value=ap):
            with patch.object(Path, "home", return_value=self.fake_home):
                state = _init_mod._detect_user_state(
                    proj / ".agent-plus", project_root=proj)
        self.assertTrue(state["has_skills"])

    def test_detect_claude_projects_history(self) -> None:
        (self.fake_home / ".claude" / "projects" / "C--dev-foo").mkdir(parents=True)
        with patch.object(_init_mod, "_h", return_value=ap):
            state = _init_mod._detect_user_state(
                self.dir / ".agent-plus", home=self.fake_home, cwd=self.dir)
        self.assertTrue(state["has_claude_projects_history"])

    # ── homeless detection ─────────────────────────────────────────────

    def test_homeless_detection_when_cwd_is_home(self) -> None:
        # Empty fake home, no markers, no git.
        with patch.object(_init_mod, "_h", return_value=ap):
            self.assertTrue(
                _init_mod._detect_homeless(cwd=self.fake_home, home=self.fake_home))

    def test_homeless_false_when_package_json_present(self) -> None:
        (self.fake_home / "package.json").write_text("{}", encoding="utf-8")
        with patch.object(_init_mod, "_h", return_value=ap):
            self.assertFalse(
                _init_mod._detect_homeless(cwd=self.fake_home, home=self.fake_home))

    def test_homeless_false_when_in_git_repo(self) -> None:
        proj = self.dir / "proj"
        proj.mkdir()
        # Force _git_toplevel to return a path → not homeless
        with patch.object(ap, "_git_toplevel", return_value=proj):
            with patch.object(_init_mod, "_h", return_value=ap):
                self.assertFalse(
                    _init_mod._detect_homeless(cwd=proj, home=self.fake_home))

    # ── branch picker ──────────────────────────────────────────────────

    def test_pick_branch_skill_author_wins(self) -> None:
        b, r = _init_mod._pick_branch({
            "has_skills": True, "has_claude_projects_history": True,
            "agent_plus_already_init": True, "env_vars_ready_count": 5,
            "homeless": False,
        })
        self.assertEqual(b, "skill_author")
        self.assertIsNone(r)

    def test_pick_branch_returning(self) -> None:
        b, _ = _init_mod._pick_branch({
            "has_skills": False, "has_claude_projects_history": True,
            "agent_plus_already_init": False, "env_vars_ready_count": 0,
            "homeless": False,
        })
        self.assertEqual(b, "returning")

    def test_pick_branch_new(self) -> None:
        b, _ = _init_mod._pick_branch({
            "has_skills": False, "has_claude_projects_history": False,
            "agent_plus_already_init": False, "env_vars_ready_count": 0,
            "homeless": False,
        })
        self.assertEqual(b, "new")

    def test_pick_branch_homeless_pivots_to_new(self) -> None:
        b, r = _init_mod._pick_branch({
            "has_skills": False, "has_claude_projects_history": False,
            "agent_plus_already_init": False, "env_vars_ready_count": 0,
            "homeless": True,
        })
        self.assertEqual(b, "new")
        self.assertEqual(r, "homeless_no_repo_context")

    def test_pick_branch_tie_break_documented(self) -> None:
        # Ambiguous: history + skills + already-init all True → skill-author wins.
        b, _ = _init_mod._pick_branch({
            "has_skills": True, "has_claude_projects_history": True,
            "agent_plus_already_init": True, "env_vars_ready_count": 11,
            "homeless": False,
        })
        self.assertEqual(b, "skill_author")

    # ── claude-project-dir decoder ─────────────────────────────────────

    def test_decode_windows_drive_form(self) -> None:
        # Verify the Windows-encoding heuristic (single drive letter +
        # `--` separator + `-` → `/`). Use a synthetic encoded name with
        # a fake drive letter — the decoder returns the most-likely
        # candidate even when no real path exists on disk (per the
        # docstring: "if none exist, returns first candidate so caller
        # can decide"). Caller `_discover_recent_claude_repos` filters
        # via `_safe_exists`. So this test exercises the heuristic
        # itself, NOT the existence filter, and works identically on
        # Windows + macOS + Linux.
        decoded = _init_mod._decode_claude_project_dir("C--dev-myrepo")
        self.assertIsNotNone(decoded)
        # The decoder produces `C:/dev/myrepo`. Path normalises to
        # `C:\dev\myrepo` on Windows (with backslash) or `C:/dev/myrepo`
        # on POSIX (Path treats colon as a normal char on POSIX). Either
        # way: drive letter "C" appears, "dev" and "myrepo" appear as
        # path segments after the drive.
        s = str(decoded).replace("\\", "/")
        self.assertTrue(s.startswith("C:"), f"unexpected decode: {s}")
        self.assertIn("dev", s)
        self.assertIn("myrepo", s)

    def test_decode_posix_form(self) -> None:
        decoded = _init_mod._decode_claude_project_dir("-Users-bob-code-bar")
        self.assertIsNotNone(decoded)
        self.assertEqual(str(decoded).replace("\\", "/"), "/Users/bob/code/bar")

    def test_decode_empty_returns_none(self) -> None:
        self.assertIsNone(_init_mod._decode_claude_project_dir(""))

    # ── _discover_recent_claude_repos ──────────────────────────────────

    def test_discover_returns_empty_when_dir_missing(self) -> None:
        # fake_home has no .claude/projects/
        out = _init_mod._discover_recent_claude_repos(home=self.fake_home)
        self.assertEqual(out, [])

    def test_discover_sorts_by_mtime_and_caps_at_4(self) -> None:
        proj_root = self.fake_home / ".claude" / "projects"
        proj_root.mkdir(parents=True)
        # Create 6 fake decoded targets and corresponding project dirs.
        targets = []
        now = time.time() if False else 1700000000.0
        import time as _t
        now = _t.time()
        for i in range(6):
            real = self.dir / f"repo{i}"
            real.mkdir()
            targets.append(real)
            enc_dir = proj_root / f"-{str(real).lstrip('/').replace(chr(92), '-').replace(':', '-').replace('/', '-')}"
            # Easier: just create a project dir whose decoded form points at `real`.
            # POSIX-style decoding: leading `-` then `-` → `/`. We'll construct
            # a name that decodes to the absolute path of `real`.
            real_str = str(real.resolve())
            # Normalise to forward slashes
            real_norm = real_str.replace("\\", "/")
            # Windows: "C:/x/y" → "C--x-y"; POSIX: "/x/y" → "-x-y"
            if len(real_norm) > 1 and real_norm[1] == ":":
                enc = real_norm[0] + "--" + real_norm[3:].replace("/", "-")
            else:
                enc = "-" + real_norm.lstrip("/").replace("/", "-")
            d = proj_root / enc
            d.mkdir()
            jsonl = d / "session.jsonl"
            jsonl.write_text("x", encoding="utf-8")
            os.utime(jsonl, (now - i * 100, now - i * 100))
        out = _init_mod._discover_recent_claude_repos(
            home=self.fake_home, limit=4)
        self.assertLessEqual(len(out), 4)

    # ── --auto envelope schema ─────────────────────────────────────────

    def test_auto_envelope_schema_complete(self) -> None:
        env = {**os.environ, "PATH": ""}  # nothing on PATH → first-win fails recoverable
        rc, out, _err = _run("init", "--dir", str(self.dir),
                             "--non-interactive", "--auto",
                             env={"PATH": ""})
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        # Frozen v0.12.0 schema fields:
        for k in ("workspace", "source", "created", "skipped",
                  "suggested_skills", "verdict", "branch_chosen",
                  "tie_break_reason", "detection", "cross_repo_offered",
                  "cross_repo_accepted", "cross_repo_results",
                  "doctor_verdict", "doctor_summary", "first_win_command",
                  "first_win_result", "ttl_total_ms", "errors", "tool"):
            self.assertIn(k, payload, f"missing envelope key: {k}")
        # detection sub-schema:
        for k in ("has_claude_projects_history", "has_skills",
                  "env_vars_ready_count", "agent_plus_already_init",
                  "homeless"):
            self.assertIn(k, payload["detection"])
        # doctor_summary sub-schema:
        for k in ("primitives_installed", "primitives_total",
                  "envcheck_ready", "envcheck_total",
                  "marketplaces_installed", "stale_services_count"):
            self.assertIn(k, payload["doctor_summary"])
        # verdict is a known string
        self.assertIn(payload["verdict"], ("success", "warn", "error"))
        # branch_chosen is one of three
        self.assertIn(payload["branch_chosen"], ("new", "returning", "skill_author"))

    def test_auto_exits_zero_even_with_recoverable_errors(self) -> None:
        rc, _out, _err = _run("init", "--dir", str(self.dir),
                              "--non-interactive", "--auto",
                              env={"PATH": ""})
        self.assertEqual(rc, 0)

    # ── observability log ──────────────────────────────────────────────

    def test_init_log_appended(self) -> None:
        _run("init", "--dir", str(self.dir),
             "--non-interactive", "--auto")
        log_path = self.dir / ".agent-plus" / "init.log"
        self.assertTrue(log_path.is_file(), "init.log not written")
        line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
        entry = json.loads(line)
        for k in ("ts", "branch_chosen", "detection",
                  "cross_repo_accepted", "doctor_verdict"):
            self.assertIn(k, entry)

    # ── error format round-trip ────────────────────────────────────────

    def test_emit_error_appends_structured_entry(self) -> None:
        errs: list[dict] = []
        _init_mod._emit_error(
            _init_mod.ERR_DOCTOR_UNREACHABLE,
            "doctor failed",
            "run agent-plus-meta doctor",
            recoverable=True, errors_list=errs, interactive=False,
        )
        self.assertEqual(len(errs), 1)
        for k in ("code", "message", "hint", "recoverable"):
            self.assertIn(k, errs[0])
        self.assertEqual(errs[0]["code"], _init_mod.ERR_DOCTOR_UNREACHABLE)

    def test_all_lane_a_error_codes_round_trip(self) -> None:
        for code in (
            _init_mod.ERR_CONSENT_REQUIRED,
            _init_mod.ERR_CROSS_REPO_SCAN_FAILED,
            _init_mod.ERR_CROSS_REPO_INTERRUPTED,
            _init_mod.ERR_STACK_DETECT_UNREADABLE,
            _init_mod.ERR_DOCTOR_UNREACHABLE,
            _init_mod.ERR_SKILL_PLUS_MISSING,
            _init_mod.ERR_AUTO_TIE_BREAK,
        ):
            errs: list[dict] = []
            _init_mod._emit_error(code, "m", "h",
                                  recoverable=True, errors_list=errs,
                                  interactive=False)
            self.assertEqual(errs[0]["code"], code)

    # ── manual paste validator ─────────────────────────────────────────

    def test_manual_paste_accepts_valid_path(self) -> None:
        proj = self.dir / "valid"
        proj.mkdir()
        (proj / "package.json").write_text("{}", encoding="utf-8")
        p, warn = _init_mod._validate_manual_path(str(proj))
        self.assertIsNotNone(p)
        self.assertIsNone(warn)

    def test_manual_paste_rejects_missing_path(self) -> None:
        p, warn = _init_mod._validate_manual_path(str(self.dir / "nope"))
        self.assertIsNone(p)
        self.assertIn("not found", warn or "")

    def test_manual_paste_warns_on_no_markers_but_accepts(self) -> None:
        proj = self.dir / "barebones"
        proj.mkdir()
        p, warn = _init_mod._validate_manual_path(str(proj))
        self.assertIsNotNone(p)
        self.assertTrue((warn or "").startswith("warn:"))

    # ── doctor finale ──────────────────────────────────────────────────

    def test_doctor_finale_invoked(self) -> None:
        called = []

        def fake_doctor(args):
            called.append(args)
            return {"verdict": "healthy", "primitives": {"a": "installed"},
                    "envcheck": {"ready_count": 5, "missing_count": 0},
                    "marketplaces": {"installed": []},
                    "stale_services_entries": []}

        with patch.object(ap, "cmd_doctor", side_effect=fake_doctor):
            payload = _init_mod.cmd_init(_wizard_args(str(self.dir)))
        self.assertEqual(len(called), 1)
        self.assertEqual(payload["doctor_verdict"], "healthy")

    def test_doctor_failure_tolerated(self) -> None:
        def boom(args):
            raise RuntimeError("doctor exploded")

        with patch.object(ap, "cmd_doctor", side_effect=boom):
            payload = _init_mod.cmd_init(_wizard_args(str(self.dir)))
        # Wizard does not crash; doctor_verdict stays at default ("broken")
        # and an error entry exists with the rescue code.
        codes = [e["code"] for e in payload["errors"]]
        self.assertIn(_init_mod.ERR_DOCTOR_UNREACHABLE, codes)

    # ── first-win mocking ──────────────────────────────────────────────

    def test_first_win_failed_when_command_missing(self) -> None:
        with patch.object(_init_mod.shutil, "which", return_value=None):
            res = _init_mod._run_first_win("new", self.dir)
        self.assertEqual(res["result"], "failed")

    def test_first_win_skipped_when_homeless_new(self) -> None:
        # Force homeless=True via state-only path: simulate by calling
        # cmd_init on a workspace whose detect path reports homeless.
        with patch.object(_init_mod, "_detect_user_state", return_value={
            "has_claude_projects_history": False, "has_skills": False,
            "env_vars_ready_count": 0, "agent_plus_already_init": False,
            "homeless": True,
        }):
            with patch.object(ap, "cmd_doctor", return_value={
                "verdict": "broken", "primitives": {},
                "envcheck": {"ready_count": 0, "missing_count": 0},
                "marketplaces": {"installed": []},
                "stale_services_entries": []}):
                payload = _init_mod.cmd_init(_wizard_args(str(self.dir)))
        self.assertEqual(payload["branch_chosen"], "new")
        self.assertEqual(payload["tie_break_reason"], "homeless_no_repo_context")
        self.assertEqual(payload["first_win_result"], "skipped")
        self.assertIsNone(payload["first_win_command"])

    # ── empty discovery → no offer ─────────────────────────────────────

    def test_empty_claude_projects_no_offer(self) -> None:
        with patch.object(_init_mod, "_discover_recent_claude_repos",
                          return_value=[]):
            with patch.object(ap, "cmd_doctor", return_value={
                "verdict": "healthy", "primitives": {},
                "envcheck": {"ready_count": 0, "missing_count": 0},
                "marketplaces": {"installed": []},
                "stale_services_entries": []}):
                payload = _init_mod.cmd_init(_wizard_args(str(self.dir)))
        self.assertEqual(payload["cross_repo_offered"], [])
        self.assertEqual(payload["cross_repo_accepted"], [])

    # ── auto: scans run silently across discovered repos ───────────────

    def test_auto_scans_all_discovered_repos(self) -> None:
        repo_a = self.dir / "ra"
        repo_b = self.dir / "rb"
        repo_a.mkdir()
        repo_b.mkdir()
        with patch.object(_init_mod, "_discover_recent_claude_repos",
                          return_value=[repo_a, repo_b]):
            with patch.object(_init_mod, "_run_skill_plus_scan",
                              return_value={"status": "ok",
                                            "candidates_found": 3}):
                with patch.object(ap, "cmd_doctor", return_value={
                    "verdict": "healthy", "primitives": {},
                    "envcheck": {"ready_count": 0, "missing_count": 0},
                    "marketplaces": {"installed": []},
                    "stale_services_entries": []}):
                    payload = _init_mod.cmd_init(_wizard_args(str(self.dir)))
        self.assertEqual(len(payload["cross_repo_accepted"]), 2)
        self.assertEqual(len(payload["cross_repo_results"]), 2)
        for r in payload["cross_repo_results"]:
            self.assertEqual(r["status"], "ok")
            self.assertEqual(r["candidates_found"], 3)

    def test_auto_skill_plus_missing_emits_recoverable_error(self) -> None:
        repo = self.dir / "ra"
        repo.mkdir()
        with patch.object(_init_mod, "_discover_recent_claude_repos",
                          return_value=[repo]):
            with patch.object(_init_mod, "_run_skill_plus_scan",
                              return_value={"status": "skipped",
                                            "candidates_found": 0,
                                            "reason": "skill-plus not on PATH"}):
                with patch.object(ap, "cmd_doctor", return_value={
                    "verdict": "healthy", "primitives": {},
                    "envcheck": {"ready_count": 0, "missing_count": 0},
                    "marketplaces": {"installed": []},
                    "stale_services_entries": []}):
                    payload = _init_mod.cmd_init(_wizard_args(str(self.dir)))
        codes = [e["code"] for e in payload["errors"]]
        self.assertIn(_init_mod.ERR_SKILL_PLUS_MISSING, codes)

    # ── TTHW budget assertion (mocked subprocess) ──────────────────────

    def test_auto_orchestration_under_budget(self) -> None:
        # With all subprocesses mocked, --auto orchestration must complete
        # well under 30s (Persona 4 budget).
        with patch.object(_init_mod, "_discover_recent_claude_repos",
                          return_value=[]):
            with patch.object(_init_mod, "_run_first_win",
                              return_value={"command": "noop",
                                            "result": "ok"}):
                with patch.object(ap, "cmd_doctor", return_value={
                    "verdict": "healthy", "primitives": {},
                    "envcheck": {"ready_count": 0, "missing_count": 0},
                    "marketplaces": {"installed": []},
                    "stale_services_entries": []}):
                    import time as _t
                    t0 = _t.time()
                    payload = _init_mod.cmd_init(_wizard_args(str(self.dir)))
                    elapsed = _t.time() - t0
        self.assertLess(elapsed, 5.0,
                        msg=f"orchestration overhead too high: {elapsed:.2f}s")
        self.assertLess(payload["ttl_total_ms"], 5000)

    # ── homeless: branch ends at doctor when no claude projects ────────

    def test_homeless_with_empty_claude_projects_ends_at_doctor(self) -> None:
        with patch.object(_init_mod, "_detect_user_state", return_value={
            "has_claude_projects_history": False, "has_skills": False,
            "env_vars_ready_count": 0, "agent_plus_already_init": False,
            "homeless": True,
        }):
            with patch.object(_init_mod, "_discover_recent_claude_repos",
                              return_value=[]):
                doc_called = []
                def fake_doc(a):
                    doc_called.append(1)
                    return {"verdict": "healthy", "primitives": {},
                            "envcheck": {"ready_count": 0, "missing_count": 0},
                            "marketplaces": {"installed": []},
                            "stale_services_entries": []}
                with patch.object(ap, "cmd_doctor", side_effect=fake_doc):
                    payload = _init_mod.cmd_init(_wizard_args(str(self.dir)))
        self.assertEqual(len(doc_called), 1)
        self.assertEqual(payload["first_win_result"], "skipped")
        self.assertEqual(payload["cross_repo_offered"], [])

    # ── envelope verdict reflects errors ───────────────────────────────

    def test_verdict_warn_on_recoverable_errors(self) -> None:
        with patch.object(_init_mod, "_run_first_win",
                          return_value={"command": "skill-plus list",
                                        "result": "failed",
                                        "reason": "skill-plus not on PATH"}):
            with patch.object(_init_mod, "_detect_user_state", return_value={
                "has_claude_projects_history": False, "has_skills": True,
                "env_vars_ready_count": 0, "agent_plus_already_init": False,
                "homeless": False,
            }):
                with patch.object(_init_mod, "_discover_recent_claude_repos",
                                  return_value=[]):
                    with patch.object(ap, "cmd_doctor", return_value={
                        "verdict": "healthy", "primitives": {},
                        "envcheck": {"ready_count": 0, "missing_count": 0},
                        "marketplaces": {"installed": []},
                        "stale_services_entries": []}):
                        payload = _init_mod.cmd_init(_wizard_args(str(self.dir)))
        # skill-plus missing emits ERR_SKILL_PLUS_MISSING (recoverable=True)
        self.assertEqual(payload["verdict"], "warn")

    # ── feedback invitation prints in interactive mode only ────────────

    def test_feedback_invitation_interactive_only(self) -> None:
        # Capture stderr while running interactive mode end-to-end (mocked).
        with patch.object(_init_mod, "_discover_recent_claude_repos",
                          return_value=[]):
            with patch.object(_init_mod, "_run_first_win",
                              return_value={"command": "x", "result": "ok"}):
                with patch.object(ap, "cmd_doctor", return_value={
                    "verdict": "healthy", "primitives": {},
                    "envcheck": {"ready_count": 0, "missing_count": 0},
                    "marketplaces": {"installed": []},
                    "stale_services_entries": []}):
                    captured = io.StringIO()
                    with patch.object(sys, "stderr", captured):
                        _init_mod.cmd_init(_wizard_args(
                            str(self.dir),
                            non_interactive=False, auto=False))
        self.assertIn("skill-feedback log agent-plus-meta-init",
                      captured.getvalue())

    def test_feedback_invitation_silent_in_auto(self) -> None:
        captured = io.StringIO()
        with patch.object(_init_mod, "_discover_recent_claude_repos",
                          return_value=[]):
            with patch.object(_init_mod, "_run_first_win",
                              return_value={"command": "x", "result": "ok"}):
                with patch.object(ap, "cmd_doctor", return_value={
                    "verdict": "healthy", "primitives": {},
                    "envcheck": {"ready_count": 0, "missing_count": 0},
                    "marketplaces": {"installed": []},
                    "stale_services_entries": []}):
                    with patch.object(sys, "stderr", captured):
                        _init_mod.cmd_init(_wizard_args(str(self.dir)))
        self.assertNotIn("skill-feedback", captured.getvalue())

    # ── cmd.exe stdin compat for _prompt_yes_no ────────────────────────

    def test_prompt_yes_no_via_subprocess_shell_true(self) -> None:
        # Simulates cmd.exe stdin: feed "y\n" via subprocess and assert
        # the wizard's helper parses it correctly. We exercise the helper
        # in-process by injecting a fake stdin — covers the readline path.
        with patch.object(sys, "stdin", io.StringIO("y\n")):
            with patch.object(sys, "stderr", io.StringIO()):
                self.assertTrue(_init_mod._prompt_yes_no("ok?"))
        with patch.object(sys, "stdin", io.StringIO("\n")):
            with patch.object(sys, "stderr", io.StringIO()):
                self.assertFalse(_init_mod._prompt_yes_no("ok?"))


# ─── envcheck ────────────────────────────────────────────────────────────────


class TestEnvcheck(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        # Init the workspace so envcheck can persist.
        _run("init", "--dir", str(self.dir))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _envcheck(self, env_extra: dict | None = None) -> dict:
        # Strip every var the spec cares about so subprocess inherits a clean
        # state and we control which are set in this test.
        clean_env: dict[str, str] = {}
        for k, v in os.environ.items():
            # keep core OS vars; drop everything our spec cares about so we
            # can deterministically assert what's set.
            if any(k.startswith(prefix) for prefix in (
                "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
            )):
                continue
            clean_env[k] = v
        if env_extra:
            clean_env.update(env_extra)
        # Run from a tempdir so .env in the real repo doesn't pollute.
        rc, out, err = _run(
            "envcheck", "--dir", str(self.dir),
            env=clean_env, cwd=str(self.dir),
        )
        # We don't reset os.environ here because subprocess gets its own env.
        self.assertEqual(rc, 0, msg=f"envcheck failed: {err!r}")
        # We deliberately overwrite env for the subprocess; need a way to
        # clear inherited values. Re-spawn with the cleaned env above.
        return json.loads(out)

    def test_envcheck_identifies_missing_when_unset(self) -> None:
        result = self._envcheck()
        # Nothing in our hardcoded list should be set.
        # LINEAR_API_KEY is required and should appear in `missing`.
        self.assertIn("LINEAR_API_KEY", result["missing"])

    def test_envcheck_identifies_set_var(self) -> None:
        result = self._envcheck(env_extra={"LINEAR_API_KEY": "lin_test_value"})
        self.assertIn("LINEAR_API_KEY", result["set"])
        self.assertNotIn("LINEAR_API_KEY", result["missing"])

    def test_envcheck_persists_to_env_status_json(self) -> None:
        self._envcheck(env_extra={"LINEAR_API_KEY": "lin_test"})
        es_path = self.dir / ".agent-plus" / "env-status.json"
        es = json.loads(es_path.read_text())
        self.assertIn("LINEAR_API_KEY", es["set"])
        self.assertIsNotNone(es["checkedAt"])

    def test_envcheck_envelope_present(self) -> None:
        result = self._envcheck()
        self.assertEqual(result["tool"]["name"], "agent-plus-meta")

    def test_envcheck_per_plugin_ready(self) -> None:
        result = self._envcheck(env_extra={
            "LINEAR_API_KEY": "lin_test",
        })
        plugins = result["plugins"]
        self.assertTrue(plugins["linear-remote"]["ready"])
        self.assertFalse(plugins["vercel-remote"]["ready"])
        # skill-feedback has no env requirement → always ready.
        self.assertTrue(plugins["skill-feedback"]["ready"])

    def test_envcheck_railway_binary_check_present(self) -> None:
        result = self._envcheck()
        binary = result["plugins"]["railway-ops"]["binary"]
        self.assertIsNotNone(binary)
        self.assertEqual(binary["binary"], "railway")
        self.assertIn("on_path", binary)


# ─── canary: no env-var VALUES leak into output ─────────────────────────────


class TestCanaryNoLeak(unittest.TestCase):
    """Pattern #5: env-var names land in output, but VALUES never do.

    We use a recognisable canary value across multiple env vars, run
    envcheck, and assert the canary string is nowhere in stdout, the
    persisted JSON file, or the program payload.
    """

    CANARY = "CANARY_AGENTPLUS_DO_NOT_LEAK_7e3b1c"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        _run("init", "--dir", str(self.dir))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_canary_not_in_envcheck_stdout(self) -> None:
        env_extra = {
            "GITHUB_TOKEN": self.CANARY,
            "VERCEL_TOKEN": self.CANARY,
            "LINEAR_API_KEY": self.CANARY,
            "SUPABASE_ACCESS_TOKEN": self.CANARY,
            "OPENROUTER_API_KEY": self.CANARY,
            "HCLOUD_TOKEN": self.CANARY,
            "COOLIFY_API_KEY": self.CANARY,
            "LANGFUSE_SECRET_KEY": self.CANARY,
            "HERMES_CHAT_API_KEY": self.CANARY,
        }
        # Build a clean env — we want only the canary to be the source of
        # secret-shaped strings flowing through the process.
        clean: dict[str, str] = {}
        for k, v in os.environ.items():
            if any(k.startswith(prefix) for prefix in (
                "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
            )):
                continue
            clean[k] = v
        clean.update(env_extra)

        rc, out, err = _run("envcheck", "--dir", str(self.dir),
                            "--pretty", env=clean, cwd=str(self.dir))
        self.assertEqual(rc, 0, msg=err)
        self.assertNotIn(self.CANARY, out, "canary leaked into stdout")
        self.assertNotIn(self.CANARY, err, "canary leaked into stderr")

        # And not on disk either.
        es_path = self.dir / ".agent-plus" / "env-status.json"
        text_on_disk = es_path.read_text()
        self.assertNotIn(self.CANARY, text_on_disk,
                         "canary leaked into env-status.json")

        # Names should appear, but not values.
        self.assertIn("GITHUB_TOKEN", text_on_disk)
        self.assertIn("LINEAR_API_KEY", text_on_disk)


# ─── refresh handler discovery + execution (no network) ─────────────


class TestRefreshHandlerDiscovery(unittest.TestCase):
    """`_discover_refresh_handlers` walks ~/.claude/plugins/cache/<marketplace>/
    <plugin>/<version>/.claude-plugin/plugin.json and returns the
    refresh_handler block declared by each plugin."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, marketplace: str, plugin: str, version: str, manifest: dict) -> None:
        d = self.cache / marketplace / plugin / version / ".claude-plugin"
        d.mkdir(parents=True, exist_ok=True)
        (d / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_missing_cache_dir_returns_empty(self) -> None:
        bogus = self.cache / "does-not-exist"
        handlers, errors = ap._discover_refresh_handlers(bogus)
        self.assertEqual(handlers, {})
        self.assertEqual(errors, [])

    def test_plugin_without_refresh_handler_silently_skipped(self) -> None:
        self._write("mk", "no-handler", "0.1.0", {
            "name": "no-handler", "version": "0.1.0",
        })
        handlers, errors = ap._discover_refresh_handlers(self.cache)
        self.assertEqual(handlers, {})
        self.assertEqual(errors, [])

    def test_plugin_with_handler_collected(self) -> None:
        self._write("mk", "vercel-remote", "0.4.0", {
            "name": "vercel-remote",
            "version": "0.4.0",
            "refresh_handler": {
                "command": "vercel-remote whoami --json",
                "timeout_seconds": 5,
                "identity_keys": ["projects"],
                "failure_mode": "soft",
            },
        })
        handlers, errors = ap._discover_refresh_handlers(self.cache)
        self.assertEqual(errors, [])
        self.assertIn("vercel-remote", handlers)
        self.assertEqual(handlers["vercel-remote"]["command"], "vercel-remote whoami --json")
        self.assertEqual(handlers["vercel-remote"]["timeout_seconds"], 5)
        self.assertEqual(handlers["vercel-remote"]["identity_keys"], ["projects"])
        self.assertEqual(handlers["vercel-remote"]["failure_mode"], "soft")

    def test_highest_version_wins(self) -> None:
        self._write("mk", "x", "0.9.0", {
            "name": "x", "refresh_handler": {"command": "old --json"},
        })
        self._write("mk", "x", "0.10.0", {
            "name": "x", "refresh_handler": {"command": "new --json"},
        })
        handlers, _ = ap._discover_refresh_handlers(self.cache)
        # Natural version sort: 0.10.0 > 0.9.0.
        self.assertEqual(handlers["x"]["command"], "new --json")

    def test_defaults_applied(self) -> None:
        self._write("mk", "x", "0.1.0", {
            "name": "x", "refresh_handler": {"command": "x --json"},
        })
        handlers, _ = ap._discover_refresh_handlers(self.cache)
        h = handlers["x"]
        self.assertEqual(h["timeout_seconds"], 10)
        self.assertEqual(h["failure_mode"], "soft")
        self.assertEqual(h["identity_keys"], [])

    def test_malformed_block_records_error_does_not_crash(self) -> None:
        self._write("mk", "bad", "0.1.0", {
            "name": "bad", "refresh_handler": {"command": ""},
        })
        handlers, errors = ap._discover_refresh_handlers(self.cache)
        self.assertNotIn("bad", handlers)
        self.assertTrue(any("bad" in e for e in errors))

    def test_malformed_plugin_json_recorded(self) -> None:
        d = self.cache / "mk" / "plugin" / "0.1.0" / ".claude-plugin"
        d.mkdir(parents=True, exist_ok=True)
        (d / "plugin.json").write_text("{not valid json", encoding="utf-8")
        handlers, errors = ap._discover_refresh_handlers(self.cache)
        self.assertEqual(handlers, {})
        self.assertEqual(len(errors), 1)


class TestRunRefreshHandler(unittest.TestCase):
    """`_run_refresh_handler` runs the declared command, parses JSON, extracts
    `identity_keys`, and respects `failure_mode`."""

    def _block(self, **overrides) -> dict:
        block = {
            "command": "echo {}",
            "timeout_seconds": 10,
            "identity_keys": [],
            "failure_mode": "soft",
        }
        block.update(overrides)
        return block

    def test_ok_extracts_identity_keys(self) -> None:
        fake = type("P", (), {"returncode": 0,
                              "stdout": json.dumps({"login": "olive", "default_org": "acme",
                                                    "extra": "ignored"}),
                              "stderr": ""})()
        with patch.object(ap.subprocess, "run", return_value=fake):
            out = ap._run_refresh_handler(
                "github-remote",
                self._block(command="github-remote whoami --json",
                            identity_keys=["login", "default_org"]),
                {},
            )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["plugin"], "github-remote")
        self.assertEqual(out["source"], "plugin-manifest")
        self.assertEqual(out["identity"], {"login": "olive", "default_org": "acme"})
        # Non-identity keys passed through.
        self.assertEqual(out["extra"], "ignored")

    def test_timeout_soft_does_not_raise(self) -> None:
        with patch.object(ap.subprocess, "run",
                          side_effect=ap.subprocess.TimeoutExpired("x", 1)):
            out = ap._run_refresh_handler("p", self._block(failure_mode="soft"), {})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason"], "timeout")

    def test_timeout_hard_raises(self) -> None:
        with patch.object(ap.subprocess, "run",
                          side_effect=ap.subprocess.TimeoutExpired("x", 1)):
            with self.assertRaises(RuntimeError):
                ap._run_refresh_handler("p", self._block(failure_mode="hard"), {})

    def test_command_not_found_soft(self) -> None:
        with patch.object(ap.subprocess, "run", side_effect=FileNotFoundError("nope")):
            out = ap._run_refresh_handler("p", self._block(), {})
        self.assertEqual(out["status"], "unconfigured")
        self.assertIn("not found", out["reason"])

    def test_nonzero_exit_soft(self) -> None:
        fake = type("P", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()
        with patch.object(ap.subprocess, "run", return_value=fake):
            out = ap._run_refresh_handler("p", self._block(), {})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["stderr_tail"], "boom")

    def test_invalid_json_soft(self) -> None:
        fake = type("P", (), {"returncode": 0, "stdout": "not json", "stderr": ""})()
        with patch.object(ap.subprocess, "run", return_value=fake):
            out = ap._run_refresh_handler("p", self._block(), {})
        self.assertEqual(out["status"], "error")
        self.assertIn("not valid JSON", out["reason"])


class TestRefreshSubcommandWithDiscovery(unittest.TestCase):
    """End-to-end: cmd_refresh consults `_discover_refresh_handlers`."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        _run("init", "--dir", str(self.dir))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_unknown_plugin_reports_clear_error(self) -> None:
        with patch.object(ap, "_discover_refresh_handlers", return_value=({}, [])):
            args = ap.argparse.Namespace(
                dir=str(self.dir), env_file=None, plugin="nonexistent-plugin",
                no_extensions=False, extensions_only=False,
            )
            payload = ap.cmd_refresh(args)
        self.assertIn("error", payload)
        self.assertIn("nonexistent-plugin", payload["error"])

    def test_no_handlers_no_extensions_returns_empty_services(self) -> None:
        with patch.object(ap, "_discover_refresh_handlers", return_value=({}, [])):
            args = ap.argparse.Namespace(
                dir=str(self.dir), env_file=None, plugin=None,
                no_extensions=True, extensions_only=False,
            )
            payload = ap.cmd_refresh(args)
        self.assertEqual(payload["services"], {})
        self.assertIn("refreshedAt", payload)


# ─── workspace resolution ────────────────────────────────────────────────────


class TestWorkspaceResolution(unittest.TestCase):
    def test_resolve_with_dir_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws, src = ap.resolve_workspace(td)
            self.assertEqual(ws, Path(td).resolve() / ".agent-plus")
            self.assertEqual(src, "flag")

    def test_resolve_for_read_falls_back_to_home(self) -> None:
        with patch.object(ap, "_git_toplevel", return_value=None):
            with tempfile.TemporaryDirectory() as td:
                # Ensure cwd has no .agent-plus dir.
                old_cwd = os.getcwd()
                os.chdir(td)
                try:
                    ws, src = ap._resolve_for_read(None)
                    # Read fallback is ~/.agent-plus
                    self.assertEqual(src, "home")
                    self.assertEqual(ws, (Path.home() / ".agent-plus").resolve())
                finally:
                    os.chdir(old_cwd)


# ─── plugin spec sanity ──────────────────────────────────────────────────────


class TestPluginSpec(unittest.TestCase):
    """The hardcoded PLUGIN_ENV_SPEC must cover every plugin in the
    repo. If a new plugin is added without updating the spec, this test
    fires to remind the author."""

    def test_known_plugins_present(self) -> None:
        expected = {
            "coolify-remote", "hcloud-remote", "hermes-remote",
            "langfuse-remote", "openrouter-remote", "railway-ops",
            "supabase-remote", "vercel-remote", "github-remote",
            "linear-remote", "skill-feedback",
        }
        self.assertEqual(set(ap.PLUGIN_ENV_SPEC), expected)

    def test_each_entry_has_required_and_optional_lists(self) -> None:
        for name, spec in ap.PLUGIN_ENV_SPEC.items():
            self.assertIn("required", spec, f"{name} missing required")
            self.assertIn("optional", spec, f"{name} missing optional")
            self.assertIsInstance(spec["required"], list)
            self.assertIsInstance(spec["optional"], list)


# ─── list ─────────────────────────────────────────────────────────────────────


class TestList(unittest.TestCase):
    def test_list_returns_envelope_and_plugins(self) -> None:
        rc, out, err = _run("list")
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["tool"]["name"], "agent-plus-meta")
        self.assertIsInstance(result["plugins"], list)
        # Framework-only repo ships 5 universal primitives (agent-plus,
        # repo-analyze, diff-summary, skill-feedback, skill-plus). The 10
        # service wrappers extracted to osouthgate/agent-plus-skills.
        self.assertGreaterEqual(result["count"], 5)

    def test_list_each_plugin_has_name_and_description(self) -> None:
        rc, out, _ = _run("list")
        result = json.loads(out)
        for entry in result["plugins"]:
            self.assertIn("name", entry)
            self.assertIn("description", entry)
            self.assertIn("source", entry)
            # headline_commands may be None but the key must be present.
            self.assertIn("headline_commands", entry)

    def test_list_names_only(self) -> None:
        rc, out, err = _run("list", "--names-only")
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["tool"]["name"], "agent-plus-meta")
        self.assertIsInstance(result["plugins"], list)
        for n in result["plugins"]:
            self.assertIsInstance(n, str)
        self.assertIn("agent-plus-meta", result["plugins"])

    def test_list_extracts_some_headline_for_known_plugin(self) -> None:
        # agent-plus-meta's own README has a "Headline commands" section, so
        # the preview should be non-null and contain the words.
        rc, out, _ = _run("list")
        result = json.loads(out)
        meta = next(p for p in result["plugins"] if p["name"] == "agent-plus-meta")
        self.assertIsNotNone(meta["headline_commands"])
        self.assertIn("Headline commands", meta["headline_commands"])

    def test_list_handles_missing_readme(self) -> None:
        # Build a fake repo with one plugin that has no README.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
                "name": "fake",
                "plugins": [
                    {"name": "no-readme", "source": "./no-readme",
                     "description": "a plugin without a README"},
                ],
            }), encoding="utf-8")
            (root / "no-readme").mkdir()
            # Initialise as git repo so _git_toplevel resolves to root.
            subprocess.run(["git", "init"], cwd=str(root),
                           capture_output=True, check=False, timeout=10)
            rc, out, err = _run("list", cwd=str(root))
            self.assertEqual(rc, 0, msg=err)
            result = json.loads(out)
            self.assertEqual(result["count"], 1)
            entry = result["plugins"][0]
            self.assertEqual(entry["name"], "no-readme")
            self.assertIsNone(entry["headline_commands"])

    def test_list_includes_ready_when_envstatus_present(self) -> None:
        # Run envcheck first so env-status.json populates `ready`.
        with tempfile.TemporaryDirectory() as td:
            ws_dir = Path(td)
            _run("init", "--dir", str(ws_dir))
            # Strip env so we get deterministic ready states.
            clean: dict[str, str] = {}
            for k, v in os.environ.items():
                if any(k.startswith(prefix) for prefix in (
                    "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                    "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
                )):
                    continue
                clean[k] = v
            _run("envcheck", "--dir", str(ws_dir), env=clean, cwd=str(ws_dir))
            rc, out, err = _run("list", "--dir", str(ws_dir),
                                env=clean, cwd=str(ws_dir))
            self.assertEqual(rc, 0, msg=err)
            result = json.loads(out)
            # At least one plugin should have a `ready` field now.
            with_ready = [p for p in result["plugins"] if "ready" in p]
            self.assertGreaterEqual(len(with_ready), 1)


# ─── extensions ─────────────────────────────────────────────────────────────


def _write_ext_script(tmp: Path, name: str, body: str) -> Path:
    """Write a small Python script the extension can invoke. We use python3
    as the executable so tests are portable across Windows/POSIX (no bash)."""
    p = tmp / name
    p.write_text(body, encoding="utf-8")
    return p


def _write_extensions_json(workspace: Path, exts: list[dict]) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    p = workspace / "extensions.json"
    p.write_text(json.dumps({"extensions": exts}, indent=2), encoding="utf-8")
    return p


class TestExtensionConfigValidation(unittest.TestCase):
    def test_valid_minimal(self) -> None:
        err = ap._validate_extension_config({
            "name": "my-ext", "command": ["python3", "x.py"],
        })
        self.assertIsNone(err)

    def test_name_must_be_slug(self) -> None:
        err = ap._validate_extension_config({"name": "Bad Name", "command": ["x"]})
        self.assertIsNotNone(err)
        self.assertIn("slug", err.lower())

    def test_name_starts_with_letter(self) -> None:
        err = ap._validate_extension_config({"name": "1bad", "command": ["x"]})
        self.assertIsNotNone(err)

    def test_name_too_long(self) -> None:
        err = ap._validate_extension_config({
            "name": "a" * 40, "command": ["x"],
        })
        self.assertIsNotNone(err)

    def test_name_collision_with_builtin_rejected(self) -> None:
        err = ap._validate_extension_config({
            "name": "github-remote", "command": ["x"],
        })
        self.assertIsNotNone(err)
        self.assertIn("collides", err)

    def test_command_must_be_list(self) -> None:
        err = ap._validate_extension_config({"name": "x", "command": "ls"})
        self.assertIsNotNone(err)

    def test_command_must_be_non_empty(self) -> None:
        err = ap._validate_extension_config({"name": "x", "command": []})
        self.assertIsNotNone(err)

    def test_command_elements_must_be_strings(self) -> None:
        err = ap._validate_extension_config({"name": "x", "command": ["ok", 123]})
        self.assertIsNotNone(err)

    def test_timeout_must_be_int(self) -> None:
        err = ap._validate_extension_config({
            "name": "x", "command": ["a"], "timeout_seconds": "30",
        })
        self.assertIsNotNone(err)

    def test_timeout_bool_rejected(self) -> None:
        # bool is a subclass of int in Python — explicitly guard against it.
        err = ap._validate_extension_config({
            "name": "x", "command": ["a"], "timeout_seconds": True,
        })
        self.assertIsNotNone(err)

    def test_enabled_must_be_bool(self) -> None:
        err = ap._validate_extension_config({
            "name": "x", "command": ["a"], "enabled": "yes",
        })
        self.assertIsNotNone(err)


class TestExtensionRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cwd = self.dir
        self.cwd.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_run_extension_success(self) -> None:
        script = _write_ext_script(self.dir, "ok.py",
            "import json; print(json.dumps({'status':'ok','foo':'bar'}))")
        out = ap._run_extension(
            {"name": "test-ok", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["plugin"], "test-ok")
        self.assertEqual(out["source"], "extension")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["foo"], "bar")

    def test_run_extension_non_json(self) -> None:
        script = _write_ext_script(self.dir, "bad.py", "print('not json')")
        out = ap._run_extension(
            {"name": "bad", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["status"], "error")
        self.assertIn("not valid JSON", out["reason"])

    def test_run_extension_json_array_rejected(self) -> None:
        # Array on stdout is a contract violation: must be a JSON object.
        script = _write_ext_script(self.dir, "arr.py", "print('[1,2,3]')")
        out = ap._run_extension(
            {"name": "arr", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["status"], "error")
        self.assertIn("not a JSON object", out["reason"])

    def test_run_extension_nonzero_exit(self) -> None:
        script = _write_ext_script(self.dir, "fail.py",
            "import sys; sys.stderr.write('boom\\n'); sys.exit(7)")
        out = ap._run_extension(
            {"name": "fail", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["status"], "error")
        self.assertIn("exited 7", out["reason"])
        self.assertIn("boom", out.get("stderr_tail", ""))

    def test_run_extension_timeout(self) -> None:
        script = _write_ext_script(self.dir, "slow.py",
            "import time; time.sleep(5)")
        out = ap._run_extension(
            {"name": "slow", "command": [sys.executable, str(script)],
             "timeout_seconds": 1},
            cwd=self.cwd,
        )
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason"], "timeout")

    def test_run_extension_stderr_tail_capped(self) -> None:
        # stderr longer than 500 chars must be truncated.
        script = _write_ext_script(self.dir, "loud.py",
            "import sys; sys.stderr.write('x' * 2000); sys.exit(1)")
        out = ap._run_extension(
            {"name": "loud", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["status"], "error")
        self.assertLessEqual(len(out["stderr_tail"]), 500)

    def test_extension_cannot_overwrite_plugin_or_source(self) -> None:
        # If a script tries to set plugin/source, the orchestrator wins.
        script = _write_ext_script(self.dir, "evil.py",
            "import json; print(json.dumps({"
            "'plugin':'github-remote','source':'builtin','status':'ok'}))")
        out = ap._run_extension(
            {"name": "evil", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["plugin"], "evil")
        self.assertEqual(out["source"], "extension")
        self.assertEqual(out["status"], "ok")

    def test_extension_default_status_ok(self) -> None:
        # Script omits status — orchestrator fills in "ok" by default.
        script = _write_ext_script(self.dir, "nostatus.py",
            "import json; print(json.dumps({'count':3}))")
        out = ap._run_extension(
            {"name": "nostatus", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["count"], 3)


class TestExtensionsRefreshIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / ".agent-plus"
        _run("init", "--dir", str(self.root))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _clean_env(self, extra: dict | None = None) -> dict:
        clean: dict[str, str] = {}
        for k, v in os.environ.items():
            if any(k.startswith(prefix) for prefix in (
                "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
            )):
                continue
            clean[k] = v
        if extra:
            clean.update(extra)
        return clean

    def test_refresh_runs_extension(self) -> None:
        script = _write_ext_script(self.root, "ext1.py",
            "import json; print(json.dumps({'status':'ok','msg':'hello'}))")
        _write_extensions_json(self.workspace, [
            {"name": "ext1", "command": [sys.executable, str(script)]},
        ])
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertIn("ext1", result["services"])
        ext1 = result["services"]["ext1"]
        self.assertEqual(ext1["status"], "ok")
        self.assertEqual(ext1["source"], "extension")
        self.assertEqual(ext1["plugin"], "ext1")
        self.assertEqual(ext1["msg"], "hello")
        self.assertIn("ext1", result["extensions_ran"])

    def test_no_extensions_flag_skips(self) -> None:
        # `--no-extensions` alone (no --plugin filter) — plugin handlers may
        # be empty in this test env, but the extension must NOT run.
        script = _write_ext_script(self.root, "ext1.py",
            "import json; print(json.dumps({'status':'ok'}))")
        _write_extensions_json(self.workspace, [
            {"name": "skipme", "command": [sys.executable, str(script)]},
        ])
        rc, out, err = _run(
            "refresh", "--no-extensions",
            "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertNotIn("skipme", result.get("services", {}))

    def test_extensions_only_skips_builtins(self) -> None:
        script = _write_ext_script(self.root, "ext1.py",
            "import json; print(json.dumps({'status':'ok'}))")
        _write_extensions_json(self.workspace, [
            {"name": "only-me", "command": [sys.executable, str(script)]},
        ])
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertIn("only-me", result["services"])
        # No built-in handlers ran.
        for builtin in ("github-remote", "vercel-remote", "linear-remote"):
            self.assertNotIn(builtin, result["services"])

    def test_disabled_extension_skipped(self) -> None:
        script = _write_ext_script(self.root, "ext1.py",
            "import json; print(json.dumps({'status':'ok'}))")
        _write_extensions_json(self.workspace, [
            {"name": "off", "command": [sys.executable, str(script)],
             "enabled": False},
        ])
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertNotIn("off", result["services"])

    def test_refresh_default_runs_extensions_too(self) -> None:
        # No --extensions-only / --no-extensions / --plugin: both fire.
        script = _write_ext_script(self.root, "ext1.py",
            "import json; print(json.dumps({'status':'ok','tag':'default-run'}))")
        _write_extensions_json(self.workspace, [
            {"name": "default-ext", "command": [sys.executable, str(script)]},
        ])
        rc, out, err = _run(
            "refresh", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertIn("default-ext", result["services"])
        self.assertEqual(result["services"]["default-ext"]["tag"], "default-run")
        # Plugin-manifest handlers may or may not be present in the test env
        # depending on whether ~/.claude/plugins/cache is populated. The
        # default-on extension surface is what this test guards.

    def test_canary_no_env_leak_into_extension_output(self) -> None:
        """Pattern 5 canary: even when the host env has a token, that value
        must not surface in refresh output unless the script itself emits it."""
        canary = "CANARY-12345-DO-NOT-LEAK"
        script = _write_ext_script(self.root, "ext1.py",
            "import json; print(json.dumps({'status':'ok','note':'hi'}))")
        _write_extensions_json(self.workspace, [
            {"name": "canary-ext", "command": [sys.executable, str(script)]},
        ])
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env({"GITHUB_TOKEN": canary}),
            cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertNotIn(canary, out, "canary leaked into stdout")
        self.assertNotIn(canary, err, "canary leaked into stderr")
        services_disk = (self.workspace / "services.json").read_text()
        self.assertNotIn(canary, services_disk)

    def test_extension_stderr_does_not_propagate_beyond_tail(self) -> None:
        # Script prints a fake token-shaped string to its OWN stderr. That ends
        # up in stderr_tail (because the script chose to emit it). But the
        # orchestrator must not propagate stdout/stderr text outside the
        # bounded tail field.
        canary = "TOKEN=ABCXYZ123"
        script = _write_ext_script(self.root, "leaker.py",
            f"import sys; sys.stderr.write({canary!r}); sys.exit(2)")
        _write_extensions_json(self.workspace, [
            {"name": "leaker", "command": [sys.executable, str(script)]},
        ])
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        leaker = result["services"]["leaker"]
        self.assertEqual(leaker["status"], "error")
        # stderr_tail SHOULD contain the canary (script chose to emit it).
        self.assertIn(canary, leaker.get("stderr_tail", ""))
        # But err (orchestrator's stderr) must be clean.
        self.assertNotIn(canary, err)

    def test_timeout_via_subprocess(self) -> None:
        script = _write_ext_script(self.root, "slow.py",
            "import time; time.sleep(5)")
        _write_extensions_json(self.workspace, [
            {"name": "slowpoke", "command": [sys.executable, str(script)],
             "timeout_seconds": 1},
        ])
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        slow = result["services"]["slowpoke"]
        self.assertEqual(slow["status"], "error")
        self.assertEqual(slow["reason"], "timeout")

    def test_malformed_extensions_file_surfaces_error(self) -> None:
        (self.workspace / "extensions.json").write_text("not json{",
                                                         encoding="utf-8")
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertIn("extension_errors", result)


# ─── extensions subcommand ─────────────────────────────────────────────────


class TestExtensionsSubcommand(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / ".agent-plus"
        _run("init", "--dir", str(self.root))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_list_empty(self) -> None:
        rc, out, err = _run("extensions", "list", "--dir", str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["extensions"], [])

    def test_add_then_list(self) -> None:
        rc, out, err = _run(
            "extensions", "add", "--name", "myext", "--command", sys.executable,
            "--command-arg=-c", "--command-arg=print('{}')",
            "--description", "test ext", "--dir", str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["added"], "myext")
        self.assertTrue((self.workspace / "extensions.json").is_file())

        rc, out, err = _run("extensions", "list", "--dir", str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["extensions"][0]["name"], "myext")
        self.assertEqual(result["extensions"][0]["description"], "test ext")
        # Command path itself NOT in output — only the hash.
        self.assertNotIn(sys.executable, out)
        self.assertEqual(len(result["extensions"][0]["command_hash"]), 64)  # sha256

    def test_add_rejects_collision_with_builtin(self) -> None:
        rc, out, err = _run(
            "extensions", "add", "--name", "github-remote",
            "--command", "python3", "--dir", str(self.root),
        )
        # Returns 0 but with error in payload (envelope-wrapped).
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)
        self.assertIn("collides", result["error"])

    def test_add_rejects_invalid_name(self) -> None:
        rc, out, _ = _run(
            "extensions", "add", "--name", "BadName",
            "--command", "python3", "--dir", str(self.root),
        )
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)

    def test_add_then_add_same_name_rejected(self) -> None:
        _run("extensions", "add", "--name", "dupe", "--command", "python3",
             "--dir", str(self.root))
        rc, out, _ = _run("extensions", "add", "--name", "dupe",
                          "--command", "python3", "--dir", str(self.root))
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)
        self.assertIn("already registered", result["error"])

    def test_remove(self) -> None:
        _run("extensions", "add", "--name", "togo", "--command", "python3",
             "--dir", str(self.root))
        rc, out, err = _run("extensions", "remove", "--name", "togo",
                            "--dir", str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["removed"], "togo")
        # Now list confirms it's gone.
        rc, out, _ = _run("extensions", "list", "--dir", str(self.root))
        result = json.loads(out)
        self.assertEqual(result["count"], 0)

    def test_remove_cleans_stale_services_json_entry(self) -> None:
        # Regression: after `extensions remove`, the entry in services.json
        # left behind by an earlier `refresh` must be cleaned out so list /
        # agent context don't show services for an extension the user
        # just removed (Gate 2 papercut A, 2026-04-29).
        script = _write_ext_script(self.root, "togo.py",
            "import json; print(json.dumps({'status':'ok','tag':'lingerer'}))")
        _run("extensions", "add", "--name", "togo",
             "--command", sys.executable, "--command-arg", str(script),
             "--dir", str(self.root))

        # Populate services.json by running refresh.
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        services_disk = json.loads(
            (self.workspace / "services.json").read_text(encoding="utf-8")
        )
        self.assertIn("togo", services_disk["services"])

        # Now remove the extension.
        rc, out, err = _run("extensions", "remove", "--name", "togo",
                            "--dir", str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["removed"], "togo")
        self.assertTrue(result.get("services_cleaned"))

        # services.json no longer has a stale entry.
        services_disk = json.loads(
            (self.workspace / "services.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("togo", services_disk["services"])

    def test_remove_when_extension_not_in_services_json_is_noop(self) -> None:
        # Removing an extension that was never refreshed shouldn't choke
        # — services_cleaned just stays False, services.json untouched.
        _run("extensions", "add", "--name", "fresh", "--command", "python3",
             "--dir", str(self.root))
        services_path = self.workspace / "services.json"
        before = services_path.read_text(encoding="utf-8") if services_path.is_file() else None
        rc, out, err = _run("extensions", "remove", "--name", "fresh",
                            "--dir", str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["removed"], "fresh")
        self.assertFalse(result.get("services_cleaned"))
        if before is not None:
            self.assertEqual(services_path.read_text(encoding="utf-8"), before)

    def test_remove_nonexistent_errors(self) -> None:
        rc, out, _ = _run("extensions", "remove", "--name", "ghost",
                          "--dir", str(self.root))
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)

    def test_validate_clean(self) -> None:
        # Use a script that exists.
        script = self.root / "real.py"
        script.write_text("# noop\n", encoding="utf-8")
        _write_extensions_json(self.workspace, [
            {"name": "good", "command": [sys.executable, "real.py"]},
        ])
        rc, out, err = _run("extensions", "validate", "--dir", str(self.root),
                            cwd=str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertTrue(result["ok"])

    def test_validate_reports_missing_command(self) -> None:
        _write_extensions_json(self.workspace, [
            {"name": "broken", "command": ["does-not-exist-xyz.py"]},
        ])
        rc, out, err = _run("extensions", "validate", "--dir", str(self.root),
                            cwd=str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        # Warning, not error — file just doesn't exist on disk.
        self.assertTrue(any(it["level"] == "warning" for it in result["issues"]))

    def test_validate_reports_collision_on_disk_load(self) -> None:
        _write_extensions_json(self.workspace, [
            {"name": "github-remote", "command": ["python3"]},
        ])
        rc, out, _ = _run("extensions", "validate", "--dir", str(self.root))
        result = json.loads(out)
        self.assertFalse(result["ok"])
        self.assertTrue(any("collides" in it["message"] for it in result["issues"]))

    def test_add_refuses_when_extensions_json_malformed(self) -> None:
        (self.workspace / "extensions.json").write_text("[broken",
                                                         encoding="utf-8")
        rc, out, _ = _run(
            "extensions", "add", "--name", "newone",
            "--command", "python3", "--dir", str(self.root),
        )
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)


class TestListIntegrationWithExtensions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / ".agent-plus"
        _run("init", "--dir", str(self.root))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_list_includes_extensions_array(self) -> None:
        _write_extensions_json(self.workspace, [
            {"name": "ext-one", "command": [sys.executable],
             "description": "first ext"},
        ])
        rc, out, err = _run("list", "--dir", str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertIn("extensions", result)
        self.assertEqual(result["extensions_count"], 1)
        ext = result["extensions"][0]
        self.assertEqual(ext["name"], "ext-one")
        self.assertEqual(ext["description"], "first ext")
        self.assertEqual(len(ext["command_hash"]), 64)
        # command itself NOT in output.
        self.assertNotIn(sys.executable, out)

    def test_list_names_only_includes_extensions(self) -> None:
        _write_extensions_json(self.workspace, [
            {"name": "ext-a", "command": ["a"]},
            {"name": "ext-b", "command": ["b"]},
        ])
        rc, out, err = _run("list", "--names-only", "--dir", str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(sorted(result["extensions"]), ["ext-a", "ext-b"])


# ─── marketplace init ──────────────────────────────────────────────────────


class TestMarketplaceInit(unittest.TestCase):
    """Phase 1 / Slice 1: `agent-plus marketplace init <user>/<name>`."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _init(self, slug: str, *, extra: list[str] | None = None,
              cwd: str | None = None) -> tuple[int, str, str]:
        args = ["marketplace", "init", slug]
        if extra:
            args.extend(extra)
        return _run(*args, cwd=cwd or str(self.cwd))

    def test_happy_path_scaffolds_all_files(self) -> None:
        rc, out, err = self._init("testuser/agent-plus-skills")
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["tool"]["name"], "agent-plus-meta")
        m = result["marketplace"]
        self.assertEqual(m["owner"], "testuser")
        self.assertEqual(m["name"], "agent-plus-skills")
        self.assertEqual(sorted(m["files_written"]),
                         [".gitignore", "CHANGELOG.md", "LICENSE",
                          "README.md", "marketplace.json"])
        target = Path(m["path"])
        self.assertTrue(target.is_dir())
        for f in ("marketplace.json", "README.md", "LICENSE",
                  ".gitignore", "CHANGELOG.md"):
            self.assertTrue((target / f).is_file(), f"missing {f}")

    def test_marketplace_json_shape(self) -> None:
        rc, out, _ = self._init("testuser/agent-plus-skills")
        self.assertEqual(rc, 0)
        target = Path(json.loads(out)["marketplace"]["path"])
        mj = json.loads((target / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(mj["name"], "agent-plus-skills")
        self.assertEqual(mj["owner"], "testuser")
        self.assertEqual(mj["version"], "0.1.0")
        self.assertEqual(mj["agent_plus_version"], ">=0.5")
        self.assertEqual(mj["surface"], "claude-code")
        self.assertEqual(mj["skills"], [])
        self.assertEqual(mj["homepage"], "https://github.com/testuser/agent-plus-skills")
        self.assertEqual(mj["license"], "MIT")

    def test_license_is_mit(self) -> None:
        rc, out, _ = self._init("testuser/agent-plus-skills")
        self.assertEqual(rc, 0)
        target = Path(json.loads(out)["marketplace"]["path"])
        license_text = (target / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("testuser", license_text)
        self.assertIn("Permission is hereby granted", license_text)

    def test_readme_mentions_owner_and_name(self) -> None:
        rc, out, _ = self._init("testuser/agent-plus-skills")
        target = Path(json.loads(out)["marketplace"]["path"])
        readme = (target / "README.md").read_text(encoding="utf-8")
        self.assertIn("testuser", readme)
        self.assertIn("agent-plus-skills", readme)

    def test_next_steps_printed_not_executed(self) -> None:
        rc, out, _ = self._init("testuser/agent-plus-skills")
        result = json.loads(out)
        steps = result["marketplace"]["next_steps"]
        self.assertEqual(len(steps), 3)
        self.assertTrue(any("gh repo create" in s for s in steps))
        self.assertTrue(any("agent-plus-skills" in s and "add-topic" in s for s in steps))
        self.assertTrue(any("claude-code" in s and "add-topic" in s for s in steps))

    def test_default_target_is_cwd_plus_name(self) -> None:
        rc, out, _ = self._init("testuser/agent-plus-skills")
        result = json.loads(out)
        expected = (self.cwd / "agent-plus-skills").resolve()
        self.assertEqual(Path(result["marketplace"]["path"]), expected)

    def test_path_override(self) -> None:
        custom = self.cwd / "my-custom-dir"
        rc, out, err = self._init("testuser/agent-plus-skills",
                                  extra=["--path", str(custom)])
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(Path(result["marketplace"]["path"]), custom.resolve())
        self.assertTrue((custom / "marketplace.json").is_file())

    def test_name_mismatch_rejected(self) -> None:
        rc, out, _ = self._init("testuser/wrong-name")
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)
        self.assertIn("agent-plus-skills", result["error"])
        self.assertIn("wrong-name", result["error"])

    def test_invalid_slug_rejected(self) -> None:
        rc, out, _ = self._init("not-a-slug")
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)
        self.assertIn("slug", result["error"].lower())

    def test_existing_non_empty_dir_rejected(self) -> None:
        target = self.cwd / "agent-plus-skills"
        target.mkdir()
        (target / "preexisting.txt").write_text("hi", encoding="utf-8")
        rc, out, _ = self._init("testuser/agent-plus-skills")
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)
        self.assertIn("non-empty", result["error"])
        # Existing file untouched.
        self.assertEqual((target / "preexisting.txt").read_text(encoding="utf-8"), "hi")
        self.assertFalse((target / "marketplace.json").is_file())

    def test_existing_empty_dir_accepted(self) -> None:
        target = self.cwd / "agent-plus-skills"
        target.mkdir()
        rc, out, err = self._init("testuser/agent-plus-skills")
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertIn("marketplace", result)
        self.assertTrue((target / "marketplace.json").is_file())

    def test_envelope_present(self) -> None:
        rc, out, _ = self._init("testuser/agent-plus-skills")
        result = json.loads(out)
        self.assertIn("tool", result)
        self.assertEqual(result["tool"]["name"], "agent-plus-meta")
        self.assertIsInstance(result["tool"]["version"], str)

    def test_pretty_flag_emits_indented_json(self) -> None:
        rc, out, err = self._init("testuser/agent-plus-skills", extra=["--pretty"])
        self.assertEqual(rc, 0, msg=err)
        # Pretty output has newlines + indentation.
        self.assertIn("\n  ", out)


# ─── stack detection / suggested_skills ─────────────────────────────────────


class TestSuggestedSkills(unittest.TestCase):
    """Stack-marker → skill suggestion mapping. Hardcoded, no network."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace_dir = self.root  # parent dir; .agent-plus is appended by --dir resolver

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_clean(self, *args: str, env_extra: dict | None = None) -> tuple[int, str, str]:
        # Build a deterministic env: bypass _run's merge with os.environ.
        clean_env: dict[str, str] = {}
        for k, v in os.environ.items():
            if any(k.startswith(prefix) for prefix in (
                "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
            )):
                continue
            clean_env[k] = v
        if env_extra:
            clean_env.update(env_extra)
        proc = subprocess.run(
            [sys.executable, str(BIN), *args],
            capture_output=True, text=True,
            env=clean_env,
            cwd=str(self.workspace_dir),
            timeout=15,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def _init(self, env_extra: dict | None = None) -> dict:
        rc, out, err = self._run_clean(
            "init", "--dir", str(self.workspace_dir),
            env_extra=env_extra,
        )
        self.assertEqual(rc, 0, msg=f"init failed: {err!r}")
        return json.loads(out)

    def test_empty_workspace_yields_empty_suggestions(self) -> None:
        result = self._init()
        self.assertIn("suggested_skills", result)
        self.assertEqual(result["suggested_skills"], [])

    def test_envelope_still_has_tool_and_workspace(self) -> None:
        result = self._init()
        self.assertIn("tool", result)
        self.assertIn("workspace", result)

    def test_vercel_json_triggers_vercel_remote(self) -> None:
        (self.root / "vercel.json").write_text("{}", encoding="utf-8")
        result = self._init()
        names = [s["name"] for s in result["suggested_skills"]]
        self.assertIn("vercel-remote", names)
        # Reason wording check.
        v = next(s for s in result["suggested_skills"] if s["name"] == "vercel-remote")
        self.assertIn("Vercel", v["reason"])
        self.assertEqual(v["marketplace"], "osouthgate/agent-plus-skills")
        self.assertIn("vercel-remote@agent-plus-skills", v["install_hint"])

    def test_dot_vercel_directory_triggers_vercel_remote(self) -> None:
        (self.root / ".vercel").mkdir()
        result = self._init()
        names = [s["name"] for s in result["suggested_skills"]]
        self.assertIn("vercel-remote", names)

    def test_supabase_dir_triggers_supabase_remote(self) -> None:
        (self.root / "supabase").mkdir()
        (self.root / "supabase" / "config.toml").write_text("", encoding="utf-8")
        result = self._init()
        names = [s["name"] for s in result["suggested_skills"]]
        self.assertIn("supabase-remote", names)

    def test_multiple_markers_yield_multiple_suggestions(self) -> None:
        (self.root / "vercel.json").write_text("{}", encoding="utf-8")
        (self.root / "supabase").mkdir()
        (self.root / "supabase" / "config.toml").write_text("", encoding="utf-8")
        result = self._init()
        names = [s["name"] for s in result["suggested_skills"]]
        self.assertIn("vercel-remote", names)
        self.assertIn("supabase-remote", names)

    def test_github_workflows_triggers_github_remote(self) -> None:
        (self.root / ".github" / "workflows").mkdir(parents=True)
        result = self._init()
        names = [s["name"] for s in result["suggested_skills"]]
        self.assertIn("github-remote", names)

    def test_railway_json_triggers_railway_ops(self) -> None:
        (self.root / "railway.json").write_text("{}", encoding="utf-8")
        result = self._init()
        names = [s["name"] for s in result["suggested_skills"]]
        self.assertIn("railway-ops", names)

    def test_langfuse_env_var_triggers_suggestion_without_leaking_value(self) -> None:
        secret = "lf_pk_super_secret_DO_NOT_LEAK"
        result = self._init(env_extra={"LANGFUSE_PUBLIC_KEY": secret})
        lf = next((s for s in result["suggested_skills"] if s["name"] == "langfuse-remote"), None)
        self.assertIsNotNone(lf, "langfuse-remote not suggested when env var present")
        # Privacy: value never leaks into output.
        self.assertNotIn(secret, json.dumps(result))
        # Reason mentions the integration but not a value.
        self.assertIn("Langfuse", lf["reason"])

    def test_openrouter_env_var_triggers_suggestion(self) -> None:
        secret = "sk-or-canary-DO-NOT-LEAK"
        result = self._init(env_extra={"OPENROUTER_API_KEY": secret})
        names = [s["name"] for s in result["suggested_skills"]]
        self.assertIn("openrouter-remote", names)
        self.assertNotIn(secret, json.dumps(result))

    def test_pretty_renders_human_section_to_stderr(self) -> None:
        (self.root / "vercel.json").write_text("{}", encoding="utf-8")
        rc, out, err = self._run_clean(
            "init", "--dir", str(self.workspace_dir), "--pretty",
        )
        self.assertEqual(rc, 0, msg=err)
        # JSON on stdout still parses.
        parsed = json.loads(out)
        self.assertIn("suggested_skills", parsed)
        # Human section appears on stderr.
        self.assertIn("Suggested skills", err)
        self.assertIn("vercel-remote", err)
        self.assertIn("claude plugin marketplace add osouthgate/agent-plus-skills", err)

    def test_pretty_silent_when_no_suggestions(self) -> None:
        rc, out, err = self._run_clean(
            "init", "--dir", str(self.workspace_dir), "--pretty",
        )
        self.assertEqual(rc, 0, msg=err)
        # No "Suggested skills" header on empty.
        self.assertNotIn("Suggested skills", err)


# ─── marketplace search ──────────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ns(**kwargs):
    import argparse as _ap
    return _ap.Namespace(**kwargs)


class TestMarketplaceSearch(unittest.TestCase):
    """Gate 4.1: `agent-plus marketplace search [query]`."""

    def test_search_no_gh_returns_clear_error(self) -> None:
        with patch.object(ap.shutil, "which", return_value=None):
            out = ap.cmd_marketplace_search(_ns(query=""))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "gh_not_installed")
        self.assertIn("cli.github.com", out["hint"])

    def test_search_happy_path_ranks_by_stars_and_recency(self) -> None:
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc)
        fresh = (now - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = (now - dt.timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
        repos = [
            {"name": "low", "owner": {"login": "a"}, "description": "x",
             "stargazerCount": 10, "updatedAt": old, "url": "u1"},
            {"name": "fresh", "owner": {"login": "b"}, "description": "y",
             "stargazerCount": 5, "updatedAt": fresh, "url": "u2"},
            {"name": "mid", "owner": {"login": "c"}, "description": "z",
             "stargazerCount": 20, "updatedAt": old, "url": "u3"},
        ]
        fake = _FakeProc(0, json.dumps(repos), "")
        with patch.object(ap.shutil, "which", return_value="/usr/bin/gh"), \
             patch.object(ap.subprocess, "run", return_value=fake):
            out = ap.cmd_marketplace_search(_ns(query=""))
        self.assertTrue(out["ok"])
        slugs = [r["slug"] for r in out["results"]]
        # 'fresh' (5 + ~58 boost) > 'mid' (20) > 'low' (10).
        self.assertEqual(slugs, ["b/fresh", "c/mid", "a/low"])
        for r in out["results"]:
            self.assertIn("score", r)
            self.assertIn("stars", r)

    def test_search_with_query_passes_through_to_gh(self) -> None:
        captured: dict = {}

        def fake_run(cmd, **kwargs):  # noqa: ARG001
            captured["cmd"] = cmd
            return _FakeProc(0, "[]", "")

        with patch.object(ap.shutil, "which", return_value="/usr/bin/gh"), \
             patch.object(ap.subprocess, "run", side_effect=fake_run):
            out = ap.cmd_marketplace_search(_ns(query="database"))
        self.assertTrue(out["ok"])
        cmd = captured["cmd"]
        # query is the first positional after `gh search repos`.
        self.assertEqual(cmd[:4], ["gh", "search", "repos", "database"])
        self.assertIn("--topic", cmd)
        topic_idx = cmd.index("--topic")
        self.assertEqual(cmd[topic_idx + 1], "agent-plus-skills")
        self.assertIn("--json", cmd)
        self.assertIn("--limit", cmd)

    def test_search_gh_returns_nonzero(self) -> None:
        fake = _FakeProc(1, "", "boom: rate limited")
        with patch.object(ap.shutil, "which", return_value="/usr/bin/gh"), \
             patch.object(ap.subprocess, "run", return_value=fake):
            out = ap.cmd_marketplace_search(_ns(query=""))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "gh_search_failed")
        self.assertIn("rate limited", out["stderr"])

    def test_search_timeout_returns_clear_error(self) -> None:
        def _raise_timeout(*_a, **_k):
            raise ap.subprocess.TimeoutExpired(cmd=["gh"], timeout=20)
        with patch.object(ap.shutil, "which", return_value="/usr/bin/gh"), \
             patch.object(ap.subprocess, "run", side_effect=_raise_timeout):
            out = ap.cmd_marketplace_search(_ns(query=""))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "gh_search_timeout")

    def test_search_oserror_returns_clear_error(self) -> None:
        def _raise_os(*_a, **_k):
            raise OSError("No such file or directory: 'gh'")
        with patch.object(ap.shutil, "which", return_value="/usr/bin/gh"), \
             patch.object(ap.subprocess, "run", side_effect=_raise_os):
            out = ap.cmd_marketplace_search(_ns(query=""))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "gh_search_unavailable")

    def test_search_malformed_json_returns_clear_error(self) -> None:
        fake = _FakeProc(0, "this is not json", "")
        with patch.object(ap.shutil, "which", return_value="/usr/bin/gh"), \
             patch.object(ap.subprocess, "run", return_value=fake):
            out = ap.cmd_marketplace_search(_ns(query=""))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "gh_search_failed")

    def test_search_envelope_has_tool_meta(self) -> None:
        fake = _FakeProc(0, "[]", "")
        with patch.object(ap.shutil, "which", return_value="/usr/bin/gh"), \
             patch.object(ap.subprocess, "run", return_value=fake):
            payload = ap.cmd_marketplace_search(_ns(query=""))
        wrapped = ap._with_tool_meta(payload)
        self.assertEqual(wrapped["tool"]["name"], "agent-plus-meta")
        self.assertIsInstance(wrapped["tool"]["version"], str)


# ─── marketplace prefer ──────────────────────────────────────────────────────


def _ns_pref(**kw):
    """argparse.Namespace builder for cmd_marketplace_prefer."""
    import argparse as _argparse
    defaults = {
        "user_repo": None,
        "skill": None,
        "list_prefs": False,
        "clear": False,
    }
    defaults.update(kw)
    return _argparse.Namespace(**defaults)


class TestMarketplacePrefer(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.prefs_path = self.tmpdir / "preferences.json"
        os.environ["AGENT_PLUS_PREFERENCES_PATH"] = str(self.prefs_path)

    def tearDown(self) -> None:
        os.environ.pop("AGENT_PLUS_PREFERENCES_PATH", None)
        self.tmp.cleanup()

    def test_prefer_sets_and_persists(self) -> None:
        out = ap.cmd_marketplace_prefer(_ns_pref(
            user_repo="osouthgate/agent-plus-skills",
            skill="repo-analyze",
        ))
        self.assertTrue(out["ok"])
        self.assertEqual(out["skill"], "repo-analyze")
        self.assertEqual(out["preferredMarketplace"], "osouthgate/agent-plus-skills")
        self.assertIsNone(out["previousPreference"])
        self.assertEqual(out["preferencesPath"], str(self.prefs_path.resolve()))
        # File on disk has the right shape.
        data = json.loads(self.prefs_path.read_text(encoding="utf-8"))
        entry = data["skillPreferences"]["repo-analyze"]
        self.assertEqual(entry["preferredMarketplace"],
                         "osouthgate/agent-plus-skills")
        self.assertIsInstance(entry["setAt"], str)
        self.assertTrue(entry["setAt"].endswith("Z") or "T" in entry["setAt"])

    def test_prefer_overwrites_with_previousPreference_in_envelope(self) -> None:
        ap.cmd_marketplace_prefer(_ns_pref(
            user_repo="alice/agent-plus-skills", skill="repo-analyze",
        ))
        out2 = ap.cmd_marketplace_prefer(_ns_pref(
            user_repo="bob/agent-plus-skills", skill="repo-analyze",
        ))
        self.assertEqual(out2["previousPreference"], "alice/agent-plus-skills")
        self.assertEqual(out2["preferredMarketplace"], "bob/agent-plus-skills")

    def test_prefer_validates_user_repo_format(self) -> None:
        out = ap.cmd_marketplace_prefer(_ns_pref(
            user_repo="bad slug", skill="repo-analyze",
        ))
        self.assertIn("error", out)
        self.assertIn("user/repo", out["error"])
        # Must not have written anything.
        self.assertFalse(self.prefs_path.exists())

    def test_prefer_validates_skill_name_format(self) -> None:
        out = ap.cmd_marketplace_prefer(_ns_pref(
            user_repo="alice/agent-plus-skills", skill="Bad_Name",
        ))
        self.assertIn("error", out)
        self.assertIn("--skill", out["error"])
        self.assertFalse(self.prefs_path.exists())

    def test_prefer_list(self) -> None:
        ap.cmd_marketplace_prefer(_ns_pref(
            user_repo="alice/agent-plus-skills", skill="repo-analyze",
        ))
        # Snapshot mtime — list must not modify.
        mtime_before = self.prefs_path.stat().st_mtime_ns
        out = ap.cmd_marketplace_prefer(_ns_pref(list_prefs=True))
        self.assertTrue(out["ok"])
        self.assertIn("repo-analyze", out["skillPreferences"])
        self.assertEqual(
            out["skillPreferences"]["repo-analyze"]["preferredMarketplace"],
            "alice/agent-plus-skills",
        )
        self.assertEqual(out["preferencesPath"], str(self.prefs_path.resolve()))
        self.assertEqual(self.prefs_path.stat().st_mtime_ns, mtime_before)

    def test_prefer_list_when_file_absent(self) -> None:
        out = ap.cmd_marketplace_prefer(_ns_pref(list_prefs=True))
        self.assertTrue(out["ok"])
        self.assertEqual(out["skillPreferences"], {})

    def test_prefer_clear_skill_present(self) -> None:
        ap.cmd_marketplace_prefer(_ns_pref(
            user_repo="alice/agent-plus-skills", skill="repo-analyze",
        ))
        out = ap.cmd_marketplace_prefer(_ns_pref(
            clear=True, skill="repo-analyze",
        ))
        self.assertTrue(out["ok"])
        self.assertTrue(out["cleared"])
        self.assertTrue(out["wasPresent"])
        data = json.loads(self.prefs_path.read_text(encoding="utf-8"))
        self.assertNotIn("repo-analyze", data["skillPreferences"])

    def test_prefer_clear_skill_absent(self) -> None:
        out = ap.cmd_marketplace_prefer(_ns_pref(
            clear=True, skill="ghost-skill",
        ))
        self.assertTrue(out["ok"])
        self.assertFalse(out["wasPresent"])

    def test_prefer_clear_requires_skill(self) -> None:
        out = ap.cmd_marketplace_prefer(_ns_pref(clear=True))
        self.assertIn("error", out)

    def test_prefer_set_requires_both_args(self) -> None:
        out = ap.cmd_marketplace_prefer(_ns_pref(
            user_repo="alice/agent-plus-skills",
        ))
        self.assertIn("error", out)
        out2 = ap.cmd_marketplace_prefer(_ns_pref(skill="repo-analyze"))
        self.assertIn("error", out2)


# ─── refresh collision resolution via prefer ─────────────────────────────────


class TestRefreshCollisionResolution(unittest.TestCase):
    """Two installed marketplaces both declare `demo` — preference picks one."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.mp_root = self.tmpdir / "marketplaces"
        self.mp_root.mkdir()
        self.prefs_path = self.tmpdir / "preferences.json"
        os.environ["AGENT_PLUS_MARKETPLACES_ROOT"] = str(self.mp_root)
        os.environ["AGENT_PLUS_PREFERENCES_PATH"] = str(self.prefs_path)

    def tearDown(self) -> None:
        os.environ.pop("AGENT_PLUS_MARKETPLACES_ROOT", None)
        os.environ.pop("AGENT_PLUS_PREFERENCES_PATH", None)
        self.tmp.cleanup()

    def _make_marketplace(self, owner: str, *, command: str) -> None:
        mdir = self.mp_root / f"{owner}-agent-plus-skills"
        plugin_dir = mdir / "demo" / ".claude-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps({
                "name": "demo",
                "version": "0.1.0",
                "refresh_handler": {
                    "command": command,
                    "timeout_seconds": 5,
                    "identity_keys": [],
                    "failure_mode": "soft",
                },
            }) + "\n",
            encoding="utf-8",
        )
        (mdir / "marketplace.json").write_text(
            json.dumps({
                "name": "agent-plus-skills",
                "owner": owner,
                "version": "0.1.0",
                "agent_plus_version": ">=0.5",
                "surface": "claude-code",
                "skills": [{"name": "demo", "version": "0.1.0", "path": "demo/"}],
            }) + "\n",
            encoding="utf-8",
        )
        # Mark accepted.
        (mdir / ".agent-plus-meta.json").write_text(
            json.dumps({"accepted_first_run": True, "pinned_sha": "x"}) + "\n",
            encoding="utf-8",
        )

    def test_collision_first_wins_when_no_preference(self) -> None:
        self._make_marketplace("alice", command="echo {}")
        self._make_marketplace("bob", command="echo {}")
        handlers, _errors, _skipped, collisions = (
            ap._discover_marketplace_refresh_handlers()
        )
        self.assertIn("demo", handlers)
        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0]["skill"], "demo")
        self.assertEqual(collisions[0]["reason"], "first_wins")
        # Sorted iteration → alice before bob.
        self.assertEqual(collisions[0]["chosen"], "alice/agent-plus-skills")
        self.assertEqual(
            sorted(collisions[0]["candidates"]),
            ["alice/agent-plus-skills", "bob/agent-plus-skills"],
        )

    def test_refresh_resolves_collision_using_preference(self) -> None:
        self._make_marketplace("alice", command="echo {}")
        self._make_marketplace("bob", command="echo {}")
        # Set preference to bob — the non-default.
        ap.cmd_marketplace_prefer(_ns_pref(
            user_repo="bob/agent-plus-skills", skill="demo",
        ))
        handlers, _errors, _skipped, collisions = (
            ap._discover_marketplace_refresh_handlers()
        )
        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0]["reason"], "preference")
        self.assertEqual(collisions[0]["chosen"], "bob/agent-plus-skills")

    def test_no_collision_means_no_collisions_slot(self) -> None:
        self._make_marketplace("alice", command="echo {}")
        handlers, _errors, _skipped, collisions = (
            ap._discover_marketplace_refresh_handlers()
        )
        self.assertIn("demo", handlers)
        self.assertEqual(collisions, [])


# ─── doctor ──────────────────────────────────────────────────────────────────


class TestDoctor(unittest.TestCase):
    """Read-only diagnostic. No mutations to the workspace, even if absent."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        # Marketplace root override → empty dir → 0 installed marketplaces.
        self.mp_root = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()
        self.mp_root.cleanup()

    def _clean_env(self, extra: dict | None = None) -> dict:
        out: dict[str, str] = {}
        for k, v in os.environ.items():
            if any(k.startswith(prefix) for prefix in (
                "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
            )):
                continue
            out[k] = v
        out["AGENT_PLUS_MARKETPLACES_ROOT"] = self.mp_root.name
        if extra:
            out.update(extra)
        return out

    def _doctor(self, extra_env: dict | None = None,
                dir_flag: str | None = None,
                cwd: str | None = None,
                pretty: bool = False) -> tuple[int, dict, str]:
        argv = ["doctor"]
        if dir_flag:
            argv += ["--dir", dir_flag]
        if pretty:
            argv += ["--pretty"]
        rc, out, err = _run(*argv, env=self._clean_env(extra_env),
                            cwd=cwd or self.tmp.name)
        return rc, json.loads(out), err

    def test_doctor_envelope_has_tool_meta(self) -> None:
        rc, payload, _err = self._doctor(dir_flag=str(self.dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["tool"]["name"], "agent-plus-meta")
        self.assertIsInstance(payload["tool"]["version"], str)
        # Top-level shape contract.
        for key in ("verdict", "self", "workspace", "primitives",
                    "envcheck", "marketplaces", "stale_services_entries",
                    "checks_run", "issues"):
            self.assertIn(key, payload, f"missing top-level key {key!r}")

    def test_doctor_broken_when_workspace_missing(self) -> None:
        # Fresh tempdir; no .agent-plus/ created → workspace.exists False.
        rc, payload, _err = self._doctor(dir_flag=str(self.dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["verdict"], "broken")
        self.assertFalse(payload["workspace"]["exists"])
        # And NEVER write — the dir we pointed at must remain workspace-free.
        self.assertFalse((self.dir / ".agent-plus").is_dir())

    def test_doctor_healthy_in_clean_repo(self) -> None:
        # Init the workspace so files exist.
        rc0, _o, err0 = _run("init", "--dir", str(self.dir))
        self.assertEqual(rc0, 0, msg=err0)
        # Provide every required env var so envcheck is fully ready, and
        # ensure the railway binary on PATH is fine to skip via fake bin.
        # Easiest: provide env vars; railway-ops has no required env vars,
        # but the CLI binary check will fail. Set PATH to include a stub.
        stub_dir = Path(self.tmp.name) / "stubs"
        stub_dir.mkdir()
        railway_stub = stub_dir / ("railway.bat" if os.name == "nt"
                                   else "railway")
        railway_stub.write_text("")
        try:
            railway_stub.chmod(0o755)
        except OSError:
            pass
        # Also stub out the framework primitives so primitives section is
        # all-installed (warns otherwise — but warns don't downgrade verdict).
        for prim in ("agent-plus-meta", "repo-analyze", "diff-summary",
                     "skill-feedback", "skill-plus"):
            stub = stub_dir / (f"{prim}.bat" if os.name == "nt" else prim)
            stub.write_text("")
            try:
                stub.chmod(0o755)
            except OSError:
                pass
        env_extra = {
            "PATH": str(stub_dir) + os.pathsep + os.environ.get("PATH", ""),
            "COOLIFY_URL": "x", "COOLIFY_API_KEY": "x",
            "HCLOUD_TOKEN": "x",
            "HERMES_URL": "x", "HERMES_CHAT_API_KEY": "x",
            "LANGFUSE_PUBLIC_KEY": "x", "LANGFUSE_SECRET_KEY": "x",
            "OPENROUTER_API_KEY": "x",
            "SUPABASE_ACCESS_TOKEN": "x",
            "VERCEL_TOKEN": "x",
            "LINEAR_API_KEY": "x",
        }
        rc, payload, _err = self._doctor(extra_env=env_extra,
                                         dir_flag=str(self.dir))
        self.assertEqual(rc, 0)
        # Either healthy (ideal) or degraded (windows binary detection
        # quirks). Must NOT be broken.
        self.assertNotEqual(payload["verdict"], "broken",
                            msg=f"unexpected broken: {payload['issues']}")
        self.assertTrue(payload["workspace"]["exists"])
        self.assertEqual(payload["envcheck"]["missing_count"], 0)

    def test_doctor_degraded_when_envvar_partially_configured(self) -> None:
        # v0.15.4 corrected the verdict semantics:
        #   - all-missing env vars (fresh install) = healthy (not-yet-configured)
        #   - PARTIAL config (some plugins ready, others missing) = degraded
        # This test exercises the partial-config path: set LINEAR_API_KEY only
        # so user_configured_count >= 1 AND missing_count > 0 → degraded.
        rc0, _o, _e = _run("init", "--dir", str(self.dir))
        self.assertEqual(rc0, 0)
        rc, payload, _err = self._doctor(
            dir_flag=str(self.dir),
            extra_env={"LINEAR_API_KEY": "test-key-not-real"},
        )
        self.assertEqual(rc, 0)
        # user_configured_count > 0 + missing_count > 0 → degraded.
        self.assertEqual(payload["verdict"], "degraded",
                         msg=f"partial config should be degraded, got {payload['verdict']}")
        self.assertGreater(len(payload["issues"]), 0)
        # Confirm at least one envcheck issue surfaced.
        cats = [i["category"] for i in payload["issues"]]
        self.assertIn("envcheck", cats)

    def test_doctor_pretty_renders_summary(self) -> None:
        _rc0, _o, _e = _run("init", "--dir", str(self.dir))
        rc, _payload, err = self._doctor(dir_flag=str(self.dir), pretty=True)
        self.assertEqual(rc, 0)
        # Pretty summary lands on stderr.
        self.assertIn("agent-plus-meta doctor:", err)
        # One of the verdict words must appear (HEALTHY = zero issues,
        # OK = healthy with warnings, DEGRADED, BROKEN).
        self.assertTrue(
            any(v in err for v in ("HEALTHY", "OK", "DEGRADED", "BROKEN")),
            msg=f"no verdict word in stderr: {err!r}",
        )
        # At least one bullet (either an issue or "No issues detected.").
        self.assertTrue(
            ("- [" in err) or ("No issues detected." in err)
            or ("! " in err) or ("  - " in err),
            msg=f"no summary bullet in stderr: {err!r}",
        )


class TestDoctorPrimitivesMultiSource(unittest.TestCase):
    """v0.15.2 P2 fix: doctor's primitives detection must be multi-source.

    PATH-only detection broke for v0.15.1 tarball installs where the
    user's PATH didn't include $AGENT_PLUS_INSTALL_DIR yet (fresh install,
    tempdir test, or CI dogfood). Now doctor checks PATH → INSTALL_DIR →
    PREFIX in that order, recording the source per-primitive.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.mp_root = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()
        self.mp_root.cleanup()

    def _clean_env(self, extra: dict | None = None) -> dict:
        out: dict[str, str] = {}
        for k, v in os.environ.items():
            if any(k.startswith(prefix) for prefix in (
                "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
            )):
                continue
            # Strip the user's real PATH so shutil.which doesn't find
            # the maintainer's installed agent-plus-meta during the test.
            if k == "PATH":
                continue
            out[k] = v
        out["PATH"] = ""  # explicit empty PATH
        out["AGENT_PLUS_MARKETPLACES_ROOT"] = self.mp_root.name
        if extra:
            out.update(extra)
        return out

    def _doctor(self, extra_env: dict | None = None) -> dict:
        rc, out, err = _run("doctor", "--dir", str(self.dir),
                            env=self._clean_env(extra_env),
                            cwd=self.tmp.name)
        return json.loads(out)

    def test_primitives_detected_via_install_dir(self) -> None:
        # Stage fake wrapper bins in $AGENT_PLUS_INSTALL_DIR; PATH is empty.
        install_dir = self.dir / "bin"
        install_dir.mkdir(parents=True)
        for prim in ap.FRAMEWORK_PRIMITIVES:
            (install_dir / prim).write_text(
                "#!/bin/sh\necho stub\n", encoding="utf-8"
            )
        result = self._doctor({
            "AGENT_PLUS_INSTALL_DIR": str(install_dir),
            # Ensure prefix dir doesn't accidentally satisfy detection too.
            "AGENT_PLUS_PREFIX": str(self.dir / "nonexistent-prefix"),
        })
        self.assertIn("primitives_source", result)
        sources = result["primitives_source"]
        for prim in ap.FRAMEWORK_PRIMITIVES:
            self.assertEqual(
                sources[prim], "install_dir",
                msg=f"{prim} should be detected via install_dir, got {sources[prim]}",
            )
            self.assertEqual(result["primitives"][prim], "installed")

    def test_primitives_detected_via_prefix_tree(self) -> None:
        # Stage tarball-style $PREFIX layout; INSTALL_DIR is empty.
        prefix = self.dir / "share"
        for prim in ap.FRAMEWORK_PRIMITIVES:
            cp = prefix / prim / ".claude-plugin"
            cp.mkdir(parents=True)
            (cp / "plugin.json").write_text(
                json.dumps({"name": prim, "version": "0.0.1"}),
                encoding="utf-8",
            )
        result = self._doctor({
            "AGENT_PLUS_INSTALL_DIR": str(self.dir / "nonexistent-bin"),
            "AGENT_PLUS_PREFIX": str(prefix),
        })
        sources = result["primitives_source"]
        for prim in ap.FRAMEWORK_PRIMITIVES:
            self.assertEqual(
                sources[prim], "prefix",
                msg=f"{prim} should be detected via prefix, got {sources[prim]}",
            )
            self.assertEqual(result["primitives"][prim], "installed")

    def test_primitives_missing_when_nowhere(self) -> None:
        result = self._doctor({
            "AGENT_PLUS_INSTALL_DIR": str(self.dir / "nope-bin"),
            "AGENT_PLUS_PREFIX": str(self.dir / "nope-prefix"),
        })
        sources = result["primitives_source"]
        for prim in ap.FRAMEWORK_PRIMITIVES:
            self.assertEqual(sources[prim], "missing")
            self.assertEqual(result["primitives"][prim], "missing")

    def test_install_dir_takes_precedence_over_prefix(self) -> None:
        # When both are present, INSTALL_DIR wins (matches the resolution
        # order: PATH → install_dir → prefix). Shows source=install_dir.
        install_dir = self.dir / "bin"
        install_dir.mkdir(parents=True)
        prefix = self.dir / "share"
        for prim in ap.FRAMEWORK_PRIMITIVES:
            (install_dir / prim).write_text("#!/bin/sh\n", encoding="utf-8")
            cp = prefix / prim / ".claude-plugin"
            cp.mkdir(parents=True)
            (cp / "plugin.json").write_text("{}", encoding="utf-8")
        result = self._doctor({
            "AGENT_PLUS_INSTALL_DIR": str(install_dir),
            "AGENT_PLUS_PREFIX": str(prefix),
        })
        sources = result["primitives_source"]
        for prim in ap.FRAMEWORK_PRIMITIVES:
            self.assertEqual(sources[prim], "install_dir")


class TestDoctorSelfMultiSourceAndVerdict(unittest.TestCase):
    """v0.15.3 follow-up to v0.15.2: same multi-source pattern applied to
    the self-check (was PATH-only), plus the verdict logic now treats
    `ready_count = 0 + missing_count > 0` as a fresh-install healthy
    state (not-yet-configured) rather than degraded. The lifecycle ring
    claim "install → healthy" requires both.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.mp_root = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()
        self.mp_root.cleanup()

    def _clean_env(self, extra: dict | None = None) -> dict:
        out: dict[str, str] = {}
        for k, v in os.environ.items():
            if any(k.startswith(prefix) for prefix in (
                "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
            )):
                continue
            if k == "PATH":
                continue
            out[k] = v
        out["PATH"] = ""  # explicit empty PATH
        out["AGENT_PLUS_MARKETPLACES_ROOT"] = self.mp_root.name
        if extra:
            out.update(extra)
        return out

    def _doctor(self, extra_env: dict | None = None) -> dict:
        rc, out, err = _run("doctor", "--dir", str(self.dir),
                            env=self._clean_env(extra_env),
                            cwd=self.tmp.name)
        return json.loads(out)

    def test_self_detected_via_install_dir_no_warn(self) -> None:
        # Stage agent-plus-meta wrapper in $INSTALL_DIR; PATH is empty.
        # Self-check should detect it and NOT emit a "not on PATH" warn —
        # instead an `info` severity hint about adding $INSTALL_DIR to PATH.
        install_dir = self.dir / "bin"
        install_dir.mkdir(parents=True)
        (install_dir / "agent-plus-meta").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )
        result = self._doctor({"AGENT_PLUS_INSTALL_DIR": str(install_dir)})
        self_section = result["self"]
        self.assertEqual(self_section["on_path_source"], "install_dir")
        self.assertTrue(self_section["reachable"])
        # No warn-severity self issue; possibly an info-severity hint.
        self_issues = [i for i in result["issues"] if i["category"] == "self"]
        warn_self = [i for i in self_issues if i["severity"] == "warn"]
        self.assertEqual(len(warn_self), 0,
                         msg=f"unexpected warn issues: {warn_self}")
        # Info hint about PATH should be present.
        info_self = [i for i in self_issues if i["severity"] == "info"]
        self.assertEqual(len(info_self), 1)
        self.assertIn("not on $PATH yet", info_self[0]["message"])

    def test_self_truly_unreachable_emits_warn(self) -> None:
        # Neither $INSTALL_DIR nor $PREFIX has agent-plus-meta. Warn fires.
        result = self._doctor({
            "AGENT_PLUS_INSTALL_DIR": str(self.dir / "nope-bin"),
            "AGENT_PLUS_PREFIX": str(self.dir / "nope-prefix"),
        })
        self_section = result["self"]
        self.assertEqual(self_section["on_path_source"], "missing")
        self.assertFalse(self_section["reachable"])
        self_warns = [i for i in result["issues"]
                      if i["category"] == "self" and i["severity"] == "warn"]
        self.assertEqual(len(self_warns), 1)
        self.assertIn("not found on PATH", self_warns[0]["message"])

    def _setup_install_and_workspace(self) -> Path:
        """Stage wrapper bins in $INSTALL_DIR and init the workspace so
        ws_exists=True (otherwise verdict=broken regardless of envcheck).
        Returns install_dir."""
        install_dir = self.dir / "bin"
        install_dir.mkdir(parents=True)
        for prim in ap.FRAMEWORK_PRIMITIVES:
            (install_dir / prim).write_text("#!/bin/sh\n", encoding="utf-8")
        # Init workspace so doctor doesn't return verdict=broken.
        ws = self.dir / ".agent-plus"
        ws.mkdir(parents=True)
        for fname, default in ap._initial_files().items():
            (ws / fname).write_text(json.dumps(default, indent=2) + "\n",
                                     encoding="utf-8")
        return install_dir

    def _doctor_with_envfile(self, install_dir: Path,
                             extra_env: dict | None = None) -> dict:
        """Run doctor with AGENT_PLUS_NO_ENV_FILES=1 to suppress the .env
        walk-up. CRITICAL: bypasses the shared `_run` helper because it
        merges os.environ BACK on top of the cleaned env, defeating the
        strip. We directly subprocess.run with env= (replace, no merge)
        so the child process gets ONLY what we pass — no maintainer env
        leaks in."""
        clean_env = self._clean_env({
            "AGENT_PLUS_INSTALL_DIR": str(install_dir),
            "AGENT_PLUS_NO_ENV_FILES": "1",
            **(extra_env or {}),
        })
        proc = subprocess.run(
            [sys.executable, str(BIN), "doctor", "--dir", str(self.dir)],
            capture_output=True, text=True, env=clean_env,
            cwd=self.tmp.name, timeout=15,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"doctor failed: rc={proc.returncode}\n"
                f"stderr={proc.stderr!r}\nstdout={proc.stdout!r}"
            )
        return json.loads(proc.stdout)

    def test_verdict_healthy_on_fresh_install_no_user_config(self) -> None:
        # v0.15.4: fresh install — bins reachable via $INSTALL_DIR,
        # workspace exists, NO env vars set for any service plugin.
        # user_configured_count = 0, missing_count > 0. Verdict = healthy.
        install_dir = self._setup_install_and_workspace()
        result = self._doctor_with_envfile(install_dir)
        envcheck = result["envcheck"]
        self.assertEqual(envcheck["user_configured_count"], 0,
                         msg=f"no env vars set → no user-configured plugins; "
                             f"got envcheck: {envcheck}")
        self.assertGreater(envcheck["missing_count"], 0)
        self.assertEqual(result["verdict"], "healthy",
                         msg=f"fresh install should be healthy, got "
                             f"{result['verdict']}; issues: {result['issues']}")

    def test_verdict_degraded_on_partial_user_config(self) -> None:
        # v0.15.4: partial config — linear-remote configured, others not.
        # user_configured_count >= 1, missing_count > 0 → degraded.
        install_dir = self._setup_install_and_workspace()
        result = self._doctor_with_envfile(
            install_dir, {"LINEAR_API_KEY": "test-key-not-real"}
        )
        envcheck = result["envcheck"]
        self.assertGreaterEqual(envcheck["user_configured_count"], 1)
        self.assertGreater(envcheck["missing_count"], 0)
        self.assertEqual(result["verdict"], "degraded",
                         msg=f"partial config should be degraded, got "
                             f"{result['verdict']}")


# ─── v0.19.1 A: Doctor pretty heading label ──────────────────────────────────


class TestDoctorPrettyHeadingLabel(unittest.TestCase):
    """_render_doctor_pretty shows the right heading label depending on verdict
    and whether issues are present / all warn-only."""

    def _render(self, verdict: str, issues: list) -> str:
        payload = {
            "verdict": verdict,
            "issues": issues,
            "self": {"on_path": True, "version": "0.0.0"},
            "workspace": {"exists": True, "path": "/tmp/ws"},
            "primitives": {},
            "claude_plugin_registration": {},
            "envcheck": {"ready_count": 0, "missing_count": 0},
            "marketplaces": {"installed": []},
        }
        return ap._render_doctor_pretty(payload)

    def test_healthy_no_issues_shows_HEALTHY(self) -> None:
        out = self._render("healthy", [])
        self.assertIn("HEALTHY", out)
        self.assertNotIn("OK (", out)

    def test_healthy_two_warn_issues_shows_OK_2_warnings(self) -> None:
        issues = [
            {"severity": "warn", "category": "self", "message": "a"},
            {"severity": "warn", "category": "primitives", "message": "b"},
        ]
        out = self._render("healthy", issues)
        self.assertIn("OK (2 warnings)", out)
        self.assertNotIn("HEALTHY", out)

    def test_healthy_one_warn_issue_shows_OK_1_warning_singular(self) -> None:
        issues = [{"severity": "warn", "category": "self", "message": "x"}]
        out = self._render("healthy", issues)
        self.assertIn("OK (1 warning)", out)
        self.assertNotIn("warnings)", out)  # no plural

    def test_degraded_shows_DEGRADED(self) -> None:
        issues = [{"severity": "warn", "category": "envcheck", "message": "y"}]
        out = self._render("degraded", issues)
        self.assertIn("DEGRADED", out)
        self.assertNotIn("HEALTHY", out)
        self.assertNotIn("OK (", out)

    def test_broken_shows_BROKEN(self) -> None:
        out = self._render("broken", [])
        self.assertIn("BROKEN", out)

    def test_healthy_non_warn_issues_shows_OK_not_HEALTHY(self) -> None:
        # Issues present but none severity=warn -> label is "OK" not "HEALTHY"
        # (the issues_for_heading list is non-empty but warn_count==0)
        issues = [{"severity": "info", "category": "self", "message": "z"}]
        out = self._render("healthy", issues)
        self.assertIn("OK", out)
        self.assertNotIn("HEALTHY", out)
        self.assertNotIn("OK (", out)  # no warning count


# ─── v0.19.1 B: Envcheck suppression ─────────────────────────────────────────


class TestEnvcheckSuppression(unittest.TestCase):
    """Uninstalled marketplace plugins with zero configured env vars must NOT
    appear in the `issues` list (noise suppression), even though they still
    appear in the envcheck JSON envelope.

    We call cmd_doctor in-process and mock both shutil.which (controls PATH
    check) and _claude_plugins_cache_dir (controls cache check) so the
    _plugin_is_installed nested function sees a clean slate on any machine.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.mp_root = tempfile.TemporaryDirectory()
        # Build workspace files directly (avoid subprocess so mocks apply).
        ws = self.dir / ".agent-plus"
        ws.mkdir(parents=True)
        for fname, default in ap._initial_files().items():
            (ws / fname).write_text(json.dumps(default, indent=2) + "\n",
                                     encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        self.mp_root.cleanup()

    def _make_args(self, env_overrides: dict | None = None):
        import argparse as _ap
        return _ap.Namespace(
            dir=str(self.dir),
            env_file=None,
            pretty=False,
        )

    def _run_doctor_mocked(self, *,
                           which_map: dict | None = None,
                           env_overrides: dict | None = None) -> dict:
        """Call cmd_doctor in-process with:
        - shutil.which returning only entries in which_map (default: nothing)
        - _claude_plugins_cache_dir pointing to an empty tempdir
        - AGENT_PLUS_MARKETPLACES_ROOT pointing to empty tmpdir
        - env vars controlled via os.environ patch
        """
        empty_cache = Path(self.mp_root.name) / "empty_cache"
        empty_cache.mkdir(exist_ok=True)

        def fake_which(name: str) -> str | None:
            return (which_map or {}).get(name)

        # Build a dict of all service-related env vars to REMOVE, plus our
        # overrides to ADD. patch.dict with clear=False merges on top, so we
        # remove keys by temporarily setting them to "" and relying on
        # load_env() stripping empty-string values. A cleaner approach is to
        # temporarily delete the keys that exist in os.environ.
        strip_prefixes = (
            "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
            "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
        )
        keys_to_remove = [k for k in os.environ
                          if any(k.startswith(p) for p in strip_prefixes)]

        add_env = {
            "AGENT_PLUS_MARKETPLACES_ROOT": self.mp_root.name,
            "AGENT_PLUS_NO_ENV_FILES": "1",
        }
        if env_overrides:
            add_env.update(env_overrides)

        args = self._make_args()
        # Remove service vars, add our controlled set. Use nested patches so
        # os.environ is always restored by the context manager machinery.
        saved = {k: os.environ.pop(k) for k in keys_to_remove if k in os.environ}
        try:
            with patch.object(ap.shutil, "which", side_effect=fake_which), \
                 patch.object(ap, "_claude_plugins_cache_dir",
                              return_value=empty_cache), \
                 patch.dict(os.environ, add_env):
                return ap.cmd_doctor(args)
        finally:
            os.environ.update(saved)

    def test_uninstalled_plugin_zero_env_vars_no_warning_in_issues(self) -> None:
        # No env vars set, which returns None for everything → all plugins
        # uninstalled and none configured → all suppressed from issues.
        payload = self._run_doctor_mocked()
        env_var_issues = [i for i in payload["issues"]
                          if i["category"] == "envcheck"
                          and "env vars" in i.get("message", "")]
        self.assertEqual(
            env_var_issues, [],
            msg=f"Expected no envcheck env-var warnings; got: {env_var_issues}",
        )
        # But the envcheck section in the JSON envelope is still complete.
        self.assertIn("envcheck", payload)
        self.assertIn("plugins", payload["envcheck"])

    def test_uninstalled_plugin_with_some_env_vars_does_produce_warning(self) -> None:
        # coolify-remote requires COOLIFY_URL + COOLIFY_API_KEY.
        # Set COOLIFY_URL only → none_configured=False → suppress=False → warn fires.
        payload = self._run_doctor_mocked(
            env_overrides={"COOLIFY_URL": "http://example.com"}
        )
        coolify_issues = [i for i in payload["issues"]
                          if "coolify-remote" in i.get("message", "")]
        self.assertGreater(
            len(coolify_issues), 0,
            msg="Expected envcheck warn for coolify-remote with COOLIFY_URL set but COOLIFY_API_KEY missing",
        )

    def test_installed_plugin_binary_missing_env_vars_produces_warning(self) -> None:
        # vercel-remote binary wrapper on PATH (plugin considered installed)
        # but VERCEL_TOKEN not set → suppress=False → warn fires.
        payload = self._run_doctor_mocked(
            which_map={"vercel-remote": "/usr/local/bin/vercel-remote"}
        )
        vercel_issues = [i for i in payload["issues"]
                         if "vercel-remote" in i.get("message", "")
                         and "env vars" in i.get("message", "")]
        self.assertGreater(
            len(vercel_issues), 0,
            msg="Expected envcheck warn for installed vercel-remote with VERCEL_TOKEN missing",
        )


# ─── v0.19.1 C: Windows .cmd subprocess fix ──────────────────────────────────


def _load_init_module_for_cmd_tests():
    """Load init submodule bound to `ap`."""
    bin_dir = Path(__file__).resolve().parent.parent / "bin"
    if str(bin_dir) not in sys.path:
        sys.path.insert(0, str(bin_dir))
    from _subcommands import init as _init  # noqa: PLC0415
    _init.bind(ap)
    return _init


_init_mod_cmd = _load_init_module_for_cmd_tests()


class TestWindowsCmdSubprocessFix(unittest.TestCase):
    """Windows .cmd subprocess fix in _run_first_win and _run_skill_plus_scan.

    The fix may be implemented as a standalone `_resolve_cmd` helper or
    inlined directly. We test observable behaviour: when the plain binary is
    absent but <name>.cmd is found on win32, subprocess.run is called with
    shell=True.
    """

    def _has_resolve_cmd(self) -> bool:
        return hasattr(_init_mod_cmd, "_resolve_cmd")

    def test_resolve_cmd_or_inline_posix_no_shell(self) -> None:
        """On non-win32, finding a plain binary never sets shell=True."""
        if self._has_resolve_cmd():
            with patch.object(_init_mod_cmd.shutil, "which",
                              return_value="/usr/local/bin/skill-plus"), \
                 patch.object(_init_mod_cmd.sys, "platform", "linux"):
                exe, use_shell = _init_mod_cmd._resolve_cmd("skill-plus")
            self.assertEqual(exe, "/usr/local/bin/skill-plus")
            self.assertFalse(use_shell)
        else:
            # Inline: test via _run_first_win on a non-win32 platform.
            from unittest.mock import MagicMock
            fake_proc = MagicMock(returncode=0, stdout="", stderr="")
            captured: dict = {}

            def fake_run(cmd, **kwargs):
                captured["kwargs"] = kwargs
                return fake_proc

            with patch.object(_init_mod_cmd.shutil, "which",
                              return_value="/usr/local/bin/repo-analyze"), \
                 patch.object(_init_mod_cmd.sys, "platform", "linux"), \
                 patch.object(_init_mod_cmd.subprocess, "run", side_effect=fake_run):
                with tempfile.TemporaryDirectory() as td:
                    _init_mod_cmd._run_first_win("new", Path(td))
            self.assertFalse(captured.get("kwargs", {}).get("shell", False))

    def test_win32_plain_binary_found_no_shell(self) -> None:
        """Plain exe on win32 (not a .cmd) → shell=False."""
        if self._has_resolve_cmd():
            with patch.object(_init_mod_cmd.shutil, "which",
                              return_value="C:\\tools\\skill-plus.exe"), \
                 patch.object(_init_mod_cmd.sys, "platform", "win32"):
                exe, use_shell = _init_mod_cmd._resolve_cmd("skill-plus")
            self.assertFalse(use_shell)
        else:
            from unittest.mock import MagicMock
            fake_proc = MagicMock(returncode=0, stdout="", stderr="")
            captured: dict = {}

            def fake_run(cmd, **kwargs):
                captured["kwargs"] = kwargs
                return fake_proc

            with patch.object(_init_mod_cmd.shutil, "which",
                              return_value="C:\\tools\\repo-analyze.exe"), \
                 patch.object(_init_mod_cmd.sys, "platform", "win32"), \
                 patch.object(_init_mod_cmd.subprocess, "run", side_effect=fake_run):
                with tempfile.TemporaryDirectory() as td:
                    _init_mod_cmd._run_first_win("new", Path(td))
            self.assertFalse(captured.get("kwargs", {}).get("shell", False))

    def test_win32_plain_not_found_cmd_found_sets_shell_true(self) -> None:
        """When plain binary absent but <name>.cmd found, shell=True."""
        if self._has_resolve_cmd():
            def fake_which(name: str) -> str | None:
                if name == "skill-plus.cmd":
                    return "C:\\tools\\skill-plus.cmd"
                return None

            with patch.object(_init_mod_cmd.shutil, "which", side_effect=fake_which), \
                 patch.object(_init_mod_cmd.sys, "platform", "win32"):
                exe, use_shell = _init_mod_cmd._resolve_cmd("skill-plus")
            self.assertEqual(exe, "C:\\tools\\skill-plus.cmd")
            self.assertTrue(use_shell)
        else:
            from unittest.mock import MagicMock
            fake_proc = MagicMock(returncode=0, stdout="", stderr="")
            captured: dict = {}

            def fake_run(cmd, **kwargs):
                captured["kwargs"] = kwargs
                return fake_proc

            def fake_which(name: str) -> str | None:
                if name == "repo-analyze.cmd":
                    return "C:\\tools\\repo-analyze.cmd"
                return None

            with patch.object(_init_mod_cmd.shutil, "which", side_effect=fake_which), \
                 patch.object(_init_mod_cmd.sys, "platform", "win32"), \
                 patch.object(_init_mod_cmd.subprocess, "run", side_effect=fake_run):
                with tempfile.TemporaryDirectory() as td:
                    result = _init_mod_cmd._run_first_win("new", Path(td))
            self.assertEqual(result["result"], "ok")
            self.assertTrue(captured.get("kwargs", {}).get("shell", False),
                            msg="shell=True must be set for .cmd wrapper")

    def test_win32_cmd_not_found_returns_failed(self) -> None:
        """When neither plain nor .cmd is found on win32, result is failed/None."""
        if self._has_resolve_cmd():
            with patch.object(_init_mod_cmd.shutil, "which", return_value=None), \
                 patch.object(_init_mod_cmd.sys, "platform", "win32"):
                exe, use_shell = _init_mod_cmd._resolve_cmd("skill-plus")
            self.assertIsNone(exe)
            self.assertFalse(use_shell)
        else:
            with patch.object(_init_mod_cmd.shutil, "which", return_value=None), \
                 patch.object(_init_mod_cmd.sys, "platform", "win32"):
                with tempfile.TemporaryDirectory() as td:
                    result = _init_mod_cmd._run_first_win("new", Path(td))
            self.assertEqual(result["result"], "failed")

    def test_win32_run_first_win_uses_shell_true_for_cmd_wrapper(self) -> None:
        """Integration: _run_first_win with .cmd wrapper calls subprocess
        shell=True regardless of whether _resolve_cmd exists."""
        from unittest.mock import MagicMock
        fake_proc = MagicMock(returncode=0, stdout="", stderr="")
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return fake_proc

        cmd_path = "C:\\tools\\repo-analyze.cmd"

        def fake_which(name: str) -> str | None:
            if name == "repo-analyze.cmd":
                return cmd_path
            return None

        with patch.object(_init_mod_cmd.shutil, "which", side_effect=fake_which), \
             patch.object(_init_mod_cmd.sys, "platform", "win32"), \
             patch.object(_init_mod_cmd.subprocess, "run", side_effect=fake_run):
            with tempfile.TemporaryDirectory() as td:
                result = _init_mod_cmd._run_first_win("new", Path(td))

        self.assertEqual(result["result"], "ok")
        self.assertTrue(captured["kwargs"].get("shell"),
                        msg="shell=True must be set for .cmd wrapper")


# ─── v0.19.1 D: Windows uninstall .cmd wrapper ───────────────────────────────


def _load_uninstall_module():
    bin_dir = Path(__file__).resolve().parent.parent / "bin"
    if str(bin_dir) not in sys.path:
        sys.path.insert(0, str(bin_dir))
    from _subcommands import uninstall as _un  # noqa: PLC0415
    _un.bind(ap)
    return _un


_uninstall_mod = _load_uninstall_module()


class TestWindowsUninstallCmdWrapper(unittest.TestCase):
    """build_manifest in uninstall.py: on win32, prefer <name>.cmd when present
    in install_dir."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.install_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_posix_uses_plain_name(self) -> None:
        # Create a plain binary (no .cmd).
        plain = self.install_dir / "agent-plus-meta"
        plain.write_text("#!/bin/sh\n", encoding="utf-8")
        with patch.object(_uninstall_mod.sys, "platform", "linux"):
            paths = _uninstall_mod.build_manifest(
                scope="default", install_dir=self.install_dir
            )
        meta_entry = next(
            (p for p in paths
             if p["kind"] == "primitive_bin"
             and "agent-plus-meta" in p["path"]
             and not p["path"].endswith(".cmd")),
            None,
        )
        self.assertIsNotNone(meta_entry,
                             msg="Expected plain-name entry on POSIX")
        self.assertEqual(meta_entry["status"], "would_remove")

    def test_win32_cmd_file_present_uses_cmd_name(self) -> None:
        # Create a .cmd wrapper as install.ps1 would.
        cmd_file = self.install_dir / "agent-plus-meta.cmd"
        cmd_file.write_text("@echo off\r\n", encoding="utf-8")
        with patch.object(_uninstall_mod.sys, "platform", "win32"):
            paths = _uninstall_mod.build_manifest(
                scope="default", install_dir=self.install_dir
            )
        meta_entry = next(
            (p for p in paths
             if p["kind"] == "primitive_bin"
             and p["path"].endswith("agent-plus-meta.cmd")),
            None,
        )
        self.assertIsNotNone(meta_entry,
                             msg="Expected .cmd entry on win32")
        self.assertEqual(meta_entry["status"], "would_remove")

    def test_win32_no_cmd_file_falls_back_to_plain(self) -> None:
        # No .cmd file; plain binary present.
        plain = self.install_dir / "agent-plus-meta"
        plain.write_text("stub", encoding="utf-8")
        with patch.object(_uninstall_mod.sys, "platform", "win32"):
            paths = _uninstall_mod.build_manifest(
                scope="default", install_dir=self.install_dir
            )
        meta_entry = next(
            (p for p in paths
             if p["kind"] == "primitive_bin"
             and "agent-plus-meta" in p["path"]),
            None,
        )
        self.assertIsNotNone(meta_entry)
        self.assertFalse(meta_entry["path"].endswith(".cmd"),
                         msg="Should fall back to plain name when no .cmd exists")

    def test_win32_cmd_wins_over_plain_when_both_exist(self) -> None:
        # Both exist → .cmd should win.
        (self.install_dir / "agent-plus-meta").write_text("stub", encoding="utf-8")
        (self.install_dir / "agent-plus-meta.cmd").write_text("stub", encoding="utf-8")
        with patch.object(_uninstall_mod.sys, "platform", "win32"):
            paths = _uninstall_mod.build_manifest(
                scope="default", install_dir=self.install_dir
            )
        meta_entries = [
            p for p in paths
            if p["kind"] == "primitive_bin" and "agent-plus-meta" in p["path"]
        ]
        # Should be exactly one entry for agent-plus-meta, the .cmd one.
        self.assertEqual(len(meta_entries), 1)
        self.assertTrue(meta_entries[0]["path"].endswith(".cmd"))


if __name__ == "__main__":
    unittest.main()
