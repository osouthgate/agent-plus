"""Contract test for the agent-plus-meta nextSteps funnel (2026-07 fix).

Covers the three confirmed defects from the 2026-07-02 field report:

  1. `_inject_next_steps` used to phrase `init`'s steps as human prompts
     ("Ask Claude 'what is this repo?' to trigger repo-analyze") --
     self-referential and non-runnable when the reader IS the agent.
     Every step must now be a literal `"<runnable command> -- <why>"`.
  2. `main()` used to suppress the JSON envelope (the only nextSteps
     carrier) whenever stdout was an interactive TTY, with no fallback --
     humans never saw the chain. A "Next:"/"Then:" stderr footer now
     covers that case without changing *when* the envelope itself prints
     (that remains a frozen contract).
  3. Only init/doctor/envcheck/refresh/marketplace/extensions injected
     steps, and marketplace/extensions got ONE generic step regardless of
     which sub-subcommand actually ran. All 10 top-level subcommands, all
     7 marketplace sub-subcommands, and all 4 extensions sub-subcommands
     now map onto something specific.

Unlike its sibling `test/contract/test_envelope_contract.py` (which
discovers *installed* plugins under `~/.claude/plugins/cache` and skips
the whole suite when none are found), this file loads the working-tree
bin directly via `SourceFileLoader` -- the same idiom as
`test_agent_plus.py` / `test_uninstall.py` -- so it always exercises the
code actually being edited, never a stale installed copy.

Stdlib unittest only. No network. Subprocess use is restricted to the
`TestIntegrationSubprocess` class at the bottom, which only runs
read-only, credential-free subcommands (envcheck, doctor, list,
extensions list, marketplace list) in an isolated temp workspace/HOME.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Optional
from unittest.mock import patch


def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent.parent / "bin" / "agent-plus-meta"
    loader = SourceFileLoader("agent_plus_next_steps", str(bin_path))
    spec = importlib.util.spec_from_loader("agent_plus_next_steps", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    # Register under its own name BEFORE exec: cmd_init() (in the bin
    # script) resolves `sys.modules[__name__]` to hand itself to
    # _subcommands/init.py's bind(). That's a no-op under normal execution
    # (__name__ == "__main__", always registered) or subprocess (same), but
    # a SourceFileLoader-loaded module is only self-consistent if we insert
    # it into sys.modules ourselves first -- otherwise in-process calls to
    # ap.main([...,"init",...]) raise KeyError on the module's own name.
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


ap = _load_module()
BIN = Path(__file__).resolve().parent.parent.parent / "bin" / "agent-plus-meta"

# Every nextSteps entry must be "<allowed-prefix> ... -- ... why".
STEP_RE = re.compile(
    r"^(agent-plus-meta|repo-analyze|skill-plus|skill-feedback|diff-summary|claude|git)( |$)"
)


def _assert_step_shape(tc: unittest.TestCase, steps, ctx: str) -> None:
    tc.assertTrue(steps, f"{ctx}: nextSteps is empty (or missing)")
    for s in steps:
        tc.assertIsInstance(s, str, f"{ctx}: step {s!r} is not a string")
        tc.assertRegex(
            s, STEP_RE, f"{ctx}: step {s!r} does not start with an allowed command"
        )
        tc.assertIn(" -- ", s, f"{ctx}: step {s!r} missing the ' -- ' separator")


# ─── full grid: every top-level subcommand + every marketplace/extensions ────
# ─── sub-subcommand, with representative fake payloads (no network) ─────────

EXPECTED_TOP_LEVEL_CMDS = {
    "init", "envcheck", "doctor", "refresh", "list",
    "extensions", "marketplace", "upgrade-check", "upgrade", "uninstall",
}
EXPECTED_MARKETPLACE_SUBCMDS = {
    "init", "install", "list", "update", "remove", "search", "prefer",
}
EXPECTED_EXTENSIONS_SUBCMDS = {"list", "validate", "add", "remove"}

# (cmd, sub_cmd, payload) -- payload shapes mirror what the real cmd_*
# handlers return (verified against bin/agent-plus-meta and
# bin/_subcommands/{upgrade,upgrade_check,uninstall}.py).
FAKE_PAYLOAD_CASES = [
    ("init", None, {}),
    ("envcheck", None, {"missing": ["GITHUB_TOKEN"]}),
    ("envcheck", None, {"missing": []}),
    ("doctor", None, {"issues": [{"severity": "warn", "category": "self", "message": "x"}]}),
    ("doctor", None, {"issues": []}),
    ("refresh", None, {"services": {}}),
    ("list", None, {"plugins": [], "count": 0}),
    ("upgrade-check", None, {"verdict": "upgrade_available", "latest_version": "9.9.9"}),
    ("upgrade-check", None, {"verdict": "up_to_date"}),
    ("upgrade-check", None, {"verdict": "unknown", "errors": [{"code": "upgrade_check_network_failed"}]}),
    ("upgrade", None, {"verdict": "success"}),
    ("upgrade", None, {"verdict": "rolled_back"}),
    ("upgrade", None, {"verdict": "noop"}),
    ("upgrade", None, {"verdict": "error"}),
    # uninstall: dry-run / aborted / real-with-kept-hint / real-with-nothing-left
    ("uninstall", None, {"dry_run": True, "user_confirmed": False, "paths": []}),
    ("uninstall", None, {"dry_run": False, "user_confirmed": False, "paths": []}),
    ("uninstall", None, {
        "dry_run": False, "user_confirmed": True,
        "paths": [{"status": "kept", "kind": "claude_plugin",
                   "hint": "claude plugin uninstall github-remote@agent-plus"}],
    }),
    ("uninstall", None, {
        "dry_run": False, "user_confirmed": True,
        "paths": [{"status": "removed", "kind": "primitive_bin"}],
    }),
    # marketplace: one success + one failure case per sub-subcommand where
    # the payload actually distinguishes them, plus the "no sub_cmd" edge.
    ("marketplace", "init", {"marketplace": {"owner": "alice", "name": "agent-plus-skills"}}),
    ("marketplace", "init", {"error": "invalid slug 'x'; expected `<user>/<name>`"}),
    ("marketplace", "install", {"marketplace": {
        "owner": "alice", "name": "agent-plus-skills", "first_run_accepted": True,
    }}),
    ("marketplace", "install", {"marketplace": {
        "owner": "alice", "name": "agent-plus-skills", "first_run_accepted": False,
    }}),
    ("marketplace", "install", {"error": "git clone failed: timeout"}),
    ("marketplace", "search", {"ok": True, "query": "db", "results": [
        {"slug": "bob/agent-plus-skills", "score": 12.0},
    ]}),
    ("marketplace", "search", {"ok": True, "query": "db", "results": []}),
    ("marketplace", "update", {"root": "/x", "updates": []}),
    ("marketplace", "remove", {"slug": "a/b", "status": "removed", "path": "/x"}),
    ("marketplace", "prefer", {"ok": True, "skillPreferences": {}}),
    ("marketplace", "list", {"root": "/x", "marketplaces": []}),
    ("marketplace", None, {"error": "marketplace subcommand required: init|install|list|update|remove|search|prefer"}),
    # extensions: same idea.
    ("extensions", "add", {"added": "x", "command_hash": "abc", "count": 1}),
    ("extensions", "add", {"error": "extension 'x' already registered; use `extensions remove` first"}),
    ("extensions", "validate", {"ok": True, "issues": [], "count": 1}),
    ("extensions", "validate", {"ok": False, "issues": [{"level": "error", "message": "dup"}], "count": 1}),
    ("extensions", "list", {"extensions": [], "count": 0}),
    ("extensions", "remove", {"removed": "x", "count": 0, "services_cleaned": False}),
    ("extensions", None, {"error": "extensions subcommand required: list | validate | add | remove"}),
]


class TestNextStepsFormatAllCommands(unittest.TestCase):
    """Walks every top-level subcommand and every marketplace/extensions
    sub-subcommand mapping with representative fake payloads (no network,
    no subprocess). Asserts nextSteps is non-empty and every step matches
    the `<allowed-command> ... -- ... why` contract."""

    def test_grid(self) -> None:
        for cmd, sub_cmd, payload in FAKE_PAYLOAD_CASES:
            with self.subTest(cmd=cmd, sub_cmd=sub_cmd):
                out: dict = {}
                ap._inject_next_steps(out, cmd, payload, sub_cmd)
                _assert_step_shape(self, out.get("nextSteps"), f"cmd={cmd} sub_cmd={sub_cmd}")

    def test_grid_covers_every_top_level_command(self) -> None:
        covered = {cmd for cmd, _sub, _payload in FAKE_PAYLOAD_CASES}
        self.assertEqual(
            covered, EXPECTED_TOP_LEVEL_CMDS,
            "FAKE_PAYLOAD_CASES must cover every key in main()'s `handlers` dict "
            "(add a row here whenever a new top-level subcommand is added)",
        )

    def test_grid_covers_every_marketplace_subcommand(self) -> None:
        covered = {
            sub for cmd, sub, _payload in FAKE_PAYLOAD_CASES
            if cmd == "marketplace" and sub is not None
        }
        self.assertEqual(covered, EXPECTED_MARKETPLACE_SUBCMDS)

    def test_grid_covers_every_extensions_subcommand(self) -> None:
        covered = {
            sub for cmd, sub, _payload in FAKE_PAYLOAD_CASES
            if cmd == "extensions" and sub is not None
        }
        self.assertEqual(covered, EXPECTED_EXTENSIONS_SUBCMDS)


class TestHonestBranches(unittest.TestCase):
    """Named regression tests for the non-obvious "don't lie" branches --
    each encodes a design decision that a generic regex-shape check
    wouldn't catch on its own."""

    def test_uninstall_real_removal_does_not_suggest_agent_plus_meta(self) -> None:
        # Once a real (non-dry-run, confirmed) uninstall has run, the
        # default tier (including this very bin) is gone -- suggesting
        # another agent-plus-meta command would be a dead command.
        out: dict = {}
        payload = {
            "dry_run": False, "user_confirmed": True,
            "paths": [{"status": "removed", "kind": "primitive_bin"}],
        }
        ap._inject_next_steps(out, "uninstall", payload)
        step = out["nextSteps"][0]
        self.assertFalse(step.startswith("agent-plus-meta"), step)
        self.assertTrue(step.startswith("git "), step)

    def test_uninstall_dry_run_may_still_suggest_agent_plus_meta(self) -> None:
        # Nothing was actually removed yet -- the bin is still there.
        out: dict = {}
        ap._inject_next_steps(out, "uninstall", {"dry_run": True, "user_confirmed": False, "paths": []})
        self.assertTrue(out["nextSteps"][0].startswith("agent-plus-meta uninstall"))

    def test_uninstall_kept_claude_plugin_hint_is_surfaced(self) -> None:
        out: dict = {}
        payload = {
            "dry_run": False, "user_confirmed": True,
            "paths": [{"status": "kept", "kind": "claude_plugin",
                       "hint": "claude plugin uninstall github-remote@agent-plus"}],
        }
        ap._inject_next_steps(out, "uninstall", payload)
        self.assertTrue(out["nextSteps"][0].startswith("claude plugin uninstall github-remote@agent-plus"))

    def test_upgrade_check_available_points_at_upgrade(self) -> None:
        out: dict = {}
        ap._inject_next_steps(out, "upgrade-check", {"verdict": "upgrade_available"})
        self.assertIn("agent-plus-meta upgrade --", out["nextSteps"][0])

    def test_upgrade_check_up_to_date_does_not_upsell_upgrade(self) -> None:
        out: dict = {}
        ap._inject_next_steps(out, "upgrade-check", {"verdict": "up_to_date"})
        self.assertNotIn("agent-plus-meta upgrade --", out["nextSteps"][0])

    def test_marketplace_install_unaccepted_suggests_remove_not_refresh(self) -> None:
        # refresh silently SKIPS un-accepted marketplaces -- chaining
        # straight to refresh here would look like a no-op to the user.
        out: dict = {}
        payload = {"marketplace": {
            "owner": "alice", "name": "agent-plus-skills", "first_run_accepted": False,
        }}
        ap._inject_next_steps(out, "marketplace", payload, "install")
        step = out["nextSteps"][0]
        self.assertIn("marketplace remove alice/agent-plus-skills", step)
        self.assertNotIn("agent-plus-meta refresh", step)

    def test_marketplace_install_accepted_chains_to_refresh(self) -> None:
        out: dict = {}
        payload = {"marketplace": {
            "owner": "alice", "name": "agent-plus-skills", "first_run_accepted": True,
        }}
        ap._inject_next_steps(out, "marketplace", payload, "install")
        self.assertTrue(out["nextSteps"][0].startswith("agent-plus-meta refresh"))

    def test_extensions_validate_failing_does_not_chain_to_refresh(self) -> None:
        # Chaining a failed validate into refresh would run the broken
        # extension script.
        out: dict = {}
        payload = {"ok": False, "issues": [{"level": "error", "message": "dup"}]}
        ap._inject_next_steps(out, "extensions", payload, "validate")
        self.assertNotIn("agent-plus-meta refresh", out["nextSteps"][0])

    def test_extensions_validate_ok_chains_to_refresh(self) -> None:
        out: dict = {}
        ap._inject_next_steps(out, "extensions", {"ok": True, "issues": []}, "validate")
        self.assertTrue(out["nextSteps"][0].startswith("agent-plus-meta refresh"))

    def test_marketplace_search_no_results_does_not_fabricate_a_slug(self) -> None:
        out: dict = {}
        ap._inject_next_steps(out, "marketplace", {"ok": True, "query": "zz", "results": []}, "search")
        step = out["nextSteps"][0]
        self.assertTrue(step.startswith("agent-plus-meta marketplace search"))
        self.assertNotIn("install", step)


class TestHistoryBranch(unittest.TestCase):
    """`_has_session_history()` forks init/envcheck(ok)/doctor(clean)/list
    between `skill-plus scan` (returning user) and `repo-analyze` (new
    user). Deliberately does NOT decode project-dir slugs -- see the
    docstring on `_has_session_history` for why."""

    def test_no_claude_dir_at_all(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with patch.object(Path, "home", return_value=home):
                self.assertFalse(ap._has_session_history())

    def test_project_dir_without_jsonl_is_still_no_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".claude" / "projects" / "some-project").mkdir(parents=True)
            with patch.object(Path, "home", return_value=home):
                self.assertFalse(ap._has_session_history())

    def test_jsonl_present_means_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            proj = home / ".claude" / "projects" / "some-project"
            proj.mkdir(parents=True)
            (proj / "session.jsonl").write_text("{}\n", encoding="utf-8")
            with patch.object(Path, "home", return_value=home):
                self.assertTrue(ap._has_session_history())

    def test_end_to_end_fork_via_inject_next_steps(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with patch.object(Path, "home", return_value=home):
                out: dict = {}
                ap._inject_next_steps(out, "list", {})
                self.assertEqual(out["nextSteps"], [ap._STEP_HISTORY_FALSE])

            proj = home / ".claude" / "projects" / "some-project"
            proj.mkdir(parents=True)
            (proj / "session.jsonl").write_text("{}\n", encoding="utf-8")
            with patch.object(Path, "home", return_value=home):
                out2: dict = {}
                ap._inject_next_steps(out2, "list", {})
                self.assertEqual(out2["nextSteps"], [ap._STEP_HISTORY_TRUE])
                self.assertTrue(out2["nextSteps"][0].startswith("skill-plus scan"))


class _TTYStringIO(io.StringIO):
    """A StringIO that claims to be a TTY, so main()'s `sys.stdout.isatty()`
    envelope-suppression branch fires under test."""

    def isatty(self) -> bool:  # noqa: D102
        return True


class TestTTYFooter(unittest.TestCase):
    """B: the stderr "Next:"/"Then:" footer that fires whenever the JSON
    envelope is suppressed for being on an interactive terminal. Must not
    change *when* the envelope prints (frozen contract) -- only add a
    footer alongside it."""

    def _invoke(self, argv: list[str], home: Optional[Path] = None):
        out_buf, err_buf = _TTYStringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, "stdout", out_buf))
            stack.enter_context(patch.object(sys, "stderr", err_buf))
            if home is not None:
                stack.enter_context(patch.object(Path, "home", return_value=home))
            rc = ap.main(argv)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def test_footer_appears_and_stdout_stays_empty_when_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            rc, out, err = self._invoke(["doctor", "--dir", str(ws)])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "", "stdout must stay empty when the envelope is suppressed")
        self.assertIn("Next: ", err)

    def test_then_line_present_when_two_steps(self) -> None:
        # `init` is the one real command with 2 steps, but deliberately do
        # NOT drive it here via ap.main(["init", ...]): cmd_init's wrapper
        # calls `_subcommands.init.bind(sys.modules[__name__])`, and
        # `_subcommands.init` is a process-wide singleton module (normal
        # `import` caching applies across every test file in this pytest
        # run, including test_agent_plus.py). Calling `init` in-process here
        # would rebind that shared singleton's `_host` to *this* file's `ap`
        # instance, which silently breaks test_agent_plus.py's
        # `TestInitWizard` tests (they bind once at collection time and
        # call `_subcommands.init.cmd_init` directly without re-binding, so
        # their `patch.object(ap, "cmd_doctor", ...)` stops taking effect
        # once some other test's `bind()` call runs later in the session).
        # `doctor` has no such submodule/bind indirection, so patch
        # `_inject_next_steps` to force a 2-step scenario on that instead --
        # this test is only about the footer's "Then:" line rendering, and
        # `TestNextStepsFormatAllCommands.test_grid` already separately
        # proves `cmd="init"` really does produce 2 well-formed steps.
        fake_steps = [
            "agent-plus-meta envcheck -- confirm which plugin env vars are already set",
            "repo-analyze -- orient Claude on this codebase",
        ]

        def _fake_inject(out, cmd, payload, sub_cmd=None):  # noqa: ANN001, ARG001
            out["nextSteps"] = fake_steps

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            with patch.object(ap, "_inject_next_steps", side_effect=_fake_inject):
                rc, out, err = self._invoke(["doctor", "--dir", str(ws)])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, "")
        self.assertIn(f"Next: {fake_steps[0]}", err)
        self.assertIn(f"Then: {fake_steps[1]}", err)

    def test_json_flag_forces_envelope_and_suppresses_footer(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            # Top-level --json (dest=force_json), passed BEFORE the
            # subcommand -- doctor's own subparser has no competing --json.
            rc, out, err = self._invoke(["--json", "doctor", "--dir", str(ws)])
        self.assertEqual(rc, 0)
        envelope = json.loads(out)
        self.assertIn("nextSteps", envelope)
        self.assertTrue(envelope["nextSteps"])
        self.assertNotIn("Next: ", err)

    def test_version_footer(self) -> None:
        rc, out, err = self._invoke(["--version"])
        self.assertEqual(rc, 0)
        self.assertTrue(out.strip().startswith("agent-plus-meta "))
        self.assertIn("Next: agent-plus-meta doctor", err)

    def test_version_no_footer_when_not_a_tty(self) -> None:
        # Plain io.StringIO() (not the TTY subclass) -> isatty() is False.
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdout", out_buf), patch.object(sys, "stderr", err_buf):
            rc = ap.main(["--version"])
        self.assertEqual(rc, 0)
        self.assertEqual(err_buf.getvalue(), "")


class TestIntegrationSubprocess(unittest.TestCase):
    """Runs the safe, read-only, credential-free subcommands as a real
    subprocess (stdout is never a TTY under subprocess capture, so the
    envelope always prints) and asserts the on-the-wire envelope carries
    nextSteps. Isolated via a fake HOME so it never touches the real
    ~/.agent-plus or ~/.claude."""

    def _run(self, argv: list[str], home: Path, cwd: Path):
        env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home)}
        proc = subprocess.run(
            [sys.executable, str(BIN), *argv],
            capture_output=True, text=True, timeout=20, env=env, cwd=str(cwd),
        )
        return proc.returncode, proc.stdout, proc.stderr

    def test_safe_commands_carry_next_steps(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            ws = Path(td) / "ws"
            ws.mkdir()
            dir_flag = str(ws / ".agent-plus")
            cases = [
                ["envcheck", "--dir", dir_flag],
                ["doctor", "--dir", dir_flag],
                ["list", "--dir", dir_flag],
                ["extensions", "list", "--dir", dir_flag],
                ["marketplace", "list", "--dir", dir_flag],
            ]
            for argv in cases:
                with self.subTest(argv=argv):
                    rc, out, err = self._run(argv, home, ws)
                    self.assertEqual(rc, 0, msg=f"{argv} failed: {err}")
                    envelope = json.loads(out)
                    _assert_step_shape(self, envelope.get("nextSteps"), f"subprocess {argv}")

    def test_branch_via_subprocess_no_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            ws = Path(td) / "ws"
            ws.mkdir()
            rc, out, err = self._run(["list", "--dir", str(ws / ".agent-plus")], home, ws)
            self.assertEqual(rc, 0, msg=err)
            envelope = json.loads(out)
            self.assertEqual(envelope["nextSteps"], [ap._STEP_HISTORY_FALSE])

    def test_branch_via_subprocess_with_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            proj = home / ".claude" / "projects" / "some-project"
            proj.mkdir(parents=True)
            (proj / "session.jsonl").write_text("{}\n", encoding="utf-8")
            ws = Path(td) / "ws"
            ws.mkdir()
            rc, out, err = self._run(["list", "--dir", str(ws / ".agent-plus")], home, ws)
            self.assertEqual(rc, 0, msg=err)
            envelope = json.loads(out)
            self.assertEqual(envelope["nextSteps"], [ap._STEP_HISTORY_TRUE])


if __name__ == "__main__":
    unittest.main(verbosity=2)
