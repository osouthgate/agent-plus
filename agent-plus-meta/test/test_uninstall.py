"""Tests for `agent-plus-meta uninstall` (v0.15.0).

Stdlib unittest. Mocks Path.home() to a tempdir per test so the real
filesystem never gets touched. Mocks `_read_yes_no` and the PURGE input
prompt so prompts don't block the suite.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


def _load_host():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "agent-plus-meta"
    loader = SourceFileLoader("agent_plus", str(bin_path))
    spec = importlib.util.spec_from_loader("agent_plus", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


HOST = _load_host()
_HERE = Path(__file__).resolve()
_BIN_DIR = _HERE.parent.parent / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))
from _subcommands import uninstall as un  # noqa: E402

un.bind(HOST)


def _ns(**kw) -> argparse.Namespace:
    defaults = dict(
        workspace=False,
        marketplaces=False,
        all=False,
        purge=False,
        dry_run=False,
        non_interactive=True,
        json=True,
        install_dir=None,
        prefix=None,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class _TempHome(unittest.TestCase):
    def setUp(self) -> None:
        un.bind(HOST)
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._patch_home = patch.object(Path, "home", return_value=self.home)
        self._patch_home.start()

        # Stage the install dir + 5 framework primitives.
        self.bin_dir = self.home / ".local" / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        for name in un.PRIMITIVES:
            (self.bin_dir / name).write_text(
                f"#!/bin/sh\necho stub-{name}\n", encoding="utf-8"
            )

        # Stage workspace dirs (~/.agent-plus and a fake repo).
        self.user_ws = self.home / ".agent-plus"
        self.user_ws.mkdir(parents=True, exist_ok=True)
        (self.user_ws / "manifest.json").write_text("{}", encoding="utf-8")

        self.repo_root = self.home / "repo"
        (self.repo_root / ".git").mkdir(parents=True, exist_ok=True)
        self.repo_ws = self.repo_root / ".agent-plus"
        self.repo_ws.mkdir(parents=True, exist_ok=True)

        # Force _git_toplevel to point at our fake repo.
        self._patch_top = patch.object(
            HOST, "_git_toplevel", return_value=self.repo_root
        )
        self._patch_top.start()

        # Stage marketplace state dir under our redirected user_ws.
        # _marketplaces_root() respects AGENT_PLUS_MARKETPLACES_ROOT, but
        # the default already points at ~/.agent-plus/marketplaces (under
        # our tempdir Path.home()). We populate it directly.
        self.market_root = self.user_ws / "marketplaces"
        self.market_root.mkdir(parents=True, exist_ok=True)
        self.market_dir = self.market_root / "alice-agent-plus-skills"
        self.market_dir.mkdir(parents=True, exist_ok=True)
        (self.market_dir / ".agent-plus-meta.json").write_text(
            json.dumps({"slug": "alice/agent-plus-skills", "accepted_first_run": True}),
            encoding="utf-8",
        )

        # Claude plugin cache: stage a fake @agent-plus plugin.
        self.claude_cache = self.home / ".claude" / "plugins" / "cache"
        self.claude_cache.mkdir(parents=True, exist_ok=True)
        plugin_dir = self.claude_cache / "github-remote@agent-plus"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "github-remote", "version": "1.0.0"}),
            encoding="utf-8",
        )

        # Stage user skills + sessions that --purge MUST NOT touch.
        self.user_skills = self.home / ".claude" / "skills"
        self.user_skills.mkdir(parents=True, exist_ok=True)
        (self.user_skills / "my-skill").mkdir(exist_ok=True)
        self.user_sessions = self.home / ".claude" / "projects"
        self.user_sessions.mkdir(parents=True, exist_ok=True)
        (self.user_sessions / "C--dev-foo").mkdir(exist_ok=True)
        self.repo_skills = self.repo_root / ".claude" / "skills"
        self.repo_skills.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._patch_top.stop()
        self._patch_home.stop()
        self._tmp.cleanup()


# ─── tests ───────────────────────────────────────────────────────────────────


class TestUninstallDefault(_TempHome):
    def test_uninstall_default_dry_run(self) -> None:
        out = un.cmd_uninstall(_ns(dry_run=True))
        self.assertEqual(out["mode"], "default")
        self.assertTrue(out["dry_run"])
        bins = [p for p in out["paths"] if p["kind"] == "primitive_bin"]
        self.assertEqual(len(bins), 5)
        for b in bins:
            self.assertEqual(b["status"], "would_remove")
        # Workspace + marketplaces present and listed as skipped.
        ws = [p for p in out["paths"] if p["kind"] == "workspace"]
        self.assertEqual({p["status"] for p in ws}, {"skipped"})
        mk = [p for p in out["paths"] if p["kind"] == "marketplace_state"]
        self.assertTrue(mk)
        self.assertEqual({p["status"] for p in mk}, {"skipped"})
        # No actual removal happened.
        for name in un.PRIMITIVES:
            self.assertTrue((self.bin_dir / name).is_file())
        self.assertEqual(out["summary"]["removed"], 0)

    def test_uninstall_default_removes_bins(self) -> None:
        out = un.cmd_uninstall(_ns())
        self.assertEqual(out["mode"], "default")
        self.assertEqual(out["summary"]["removed"], 5)
        for name in un.PRIMITIVES:
            self.assertFalse((self.bin_dir / name).is_file())
        # Workspace untouched.
        self.assertTrue(self.user_ws.is_dir())
        self.assertTrue(self.repo_ws.is_dir())


class TestUninstallScopes(_TempHome):
    def test_uninstall_workspace_flag(self) -> None:
        out = un.cmd_uninstall(_ns(workspace=True))
        self.assertEqual(out["mode"], "workspace")
        # Workspace tier removed.
        self.assertFalse(self.user_ws.is_dir())
        self.assertFalse(self.repo_ws.is_dir())
        # Bins also removed.
        for name in un.PRIMITIVES:
            self.assertFalse((self.bin_dir / name).is_file())

    def test_uninstall_marketplaces_flag(self) -> None:
        # Re-stage workspace since the test below also exercises bin removal.
        out = un.cmd_uninstall(_ns(marketplaces=True))
        self.assertEqual(out["mode"], "marketplaces")
        self.assertFalse(self.market_dir.is_dir())
        # Workspace stays (marketplaces flag alone).
        self.assertTrue(self.repo_ws.is_dir())
        # Bins also removed (default tier always runs).
        for name in un.PRIMITIVES:
            self.assertFalse((self.bin_dir / name).is_file())

    def test_uninstall_all_flag(self) -> None:
        out = un.cmd_uninstall(_ns(all=True))
        self.assertEqual(out["mode"], "all")
        for name in un.PRIMITIVES:
            self.assertFalse((self.bin_dir / name).is_file())
        self.assertFalse(self.user_ws.is_dir())
        self.assertFalse(self.repo_ws.is_dir())
        self.assertFalse(self.market_dir.is_dir())
        # User skills + sessions UNTOUCHED.
        self.assertTrue(self.user_skills.is_dir())
        self.assertTrue(self.user_sessions.is_dir())
        self.assertTrue(self.repo_skills.is_dir())


class TestUninstallPurge(_TempHome):
    def test_uninstall_purge_requires_literal_PURGE(self) -> None:
        for word in ["y", "yes", "Y", "purge", ""]:
            with patch("builtins.input", return_value=word):
                out = un.cmd_uninstall(_ns(purge=True))
            self.assertFalse(
                out["user_confirmed"],
                msg=f"word {word!r} should NOT confirm PURGE",
            )
            # Bins still present (run aborted).
            for name in un.PRIMITIVES:
                self.assertTrue(
                    (self.bin_dir / name).is_file(),
                    msg=f"bin removed despite aborted PURGE (word={word!r})",
                )
        # Now the magic word.
        with patch("builtins.input", return_value="PURGE"):
            out = un.cmd_uninstall(_ns(purge=True))
        self.assertTrue(out["user_confirmed"])
        for name in un.PRIMITIVES:
            self.assertFalse((self.bin_dir / name).is_file())

    def test_uninstall_purge_under_non_interactive_still_prompts(self) -> None:
        # --non-interactive does NOT bypass the PURGE confirmation (T6).
        # Empty input → abort.
        with patch("builtins.input", return_value="") as mock_in:
            out = un.cmd_uninstall(_ns(purge=True, non_interactive=True))
        self.assertTrue(mock_in.called, "PURGE prompt did not fire")
        self.assertFalse(out["user_confirmed"])

    def test_uninstall_purge_keeps_user_skills_and_sessions(self) -> None:
        with patch("builtins.input", return_value="PURGE"):
            un.cmd_uninstall(_ns(purge=True))
        # User-owned territory: never removed.
        self.assertTrue(self.user_skills.is_dir())
        self.assertTrue((self.user_skills / "my-skill").is_dir())
        self.assertTrue(self.user_sessions.is_dir())
        self.assertTrue(self.repo_skills.is_dir())


class TestUninstallEnvelope(_TempHome):
    def test_uninstall_lists_claude_plugins_with_hints(self) -> None:
        out = un.cmd_uninstall(_ns(dry_run=True))
        hints = out["claude_plugin_hints"]
        self.assertIn("claude plugin uninstall github-remote@agent-plus", hints)
        # Same hint under paths[].
        plugin_paths = [p for p in out["paths"] if p["kind"] == "claude_plugin"]
        self.assertTrue(plugin_paths)
        for p in plugin_paths:
            self.assertEqual(p["status"], "kept")
            self.assertIn("@agent-plus", p["hint"])

    def test_uninstall_json_envelope_schema(self) -> None:
        out = un.cmd_uninstall(_ns(dry_run=True))
        # Required top-level keys.
        for key in [
            "tool", "action", "mode", "dry_run", "interactive",
            "user_confirmed", "paths", "summary", "claude_plugin_hints",
            "next_steps", "errors",
        ]:
            self.assertIn(key, out, msg=f"missing key {key!r}")
        self.assertEqual(out["action"], "uninstall")
        self.assertIn(out["mode"], {
            "default", "workspace", "marketplaces", "all", "purge",
        })
        valid_kinds = {
            "primitive_bin", "primitive_tree", "workspace", "marketplace_state",
            "marketplace_registry", "claude_plugin", "claude_session",
            "user_skill", "feedback_log", "analytics",
            "settings_hook", "daemon_pid", "migration_state",
        }
        valid_scopes = {"default", "workspace", "marketplaces", "purge", "out_of_scope"}
        valid_status = {"removed", "missing", "skipped", "kept", "error", "would_remove"}
        for p in out["paths"]:
            self.assertIn(p["kind"], valid_kinds)
            self.assertIn(p["scope"], valid_scopes)
            self.assertIn(p["status"], valid_status)


class TestUninstallIdempotency(_TempHome):
    def test_uninstall_idempotent_rerun(self) -> None:
        un.cmd_uninstall(_ns())
        out = un.cmd_uninstall(_ns())
        bins = [p for p in out["paths"] if p["kind"] == "primitive_bin"]
        for b in bins:
            self.assertEqual(b["status"], "missing")
        self.assertEqual(out["summary"]["removed"], 0)
        self.assertEqual(out["summary"]["errors"], 0)

    def test_uninstall_partial_state_some_bins_missing(self) -> None:
        # Manually delete two bins before running.
        (self.bin_dir / "diff-summary").unlink()
        (self.bin_dir / "skill-feedback").unlink()
        out = un.cmd_uninstall(_ns())
        bins = {
            p["path"].rsplit(os.sep, 1)[-1]: p["status"]
            for p in out["paths"]
            if p["kind"] == "primitive_bin"
        }
        # Hyphenated names: can't use os.sep split universally. Recompute.
        bins = {Path(p["path"]).name: p["status"] for p in out["paths"]
                if p["kind"] == "primitive_bin"}
        self.assertEqual(bins["diff-summary"], "missing")
        self.assertEqual(bins["skill-feedback"], "missing")
        self.assertEqual(bins["agent-plus-meta"], "removed")
        self.assertEqual(bins["repo-analyze"], "removed")
        self.assertEqual(bins["skill-plus"], "removed")
        self.assertEqual(out["summary"]["errors"], 0)


class TestUninstallSelfDeleteWindows(_TempHome):
    def test_uninstall_self_delete_on_windows_emits_warning(self) -> None:
        # Stage HOST.__file__ to point at one of our fake bins, then mock
        # platform.system() to "Windows" and force the unlink to fail with
        # PermissionError. The other 4 bins should still be removed.
        self_bin = self.bin_dir / "agent-plus-meta"
        original_file = HOST.__file__
        HOST.__file__ = str(self_bin)
        try:
            real_unlink = Path.unlink

            def fake_unlink(self, *a, **kw):
                if str(self) == str(self_bin):
                    raise PermissionError(13, "Access is denied")
                return real_unlink(self, *a, **kw)

            with patch("platform.system", return_value="Windows"), \
                 patch.object(Path, "unlink", fake_unlink):
                out = un.cmd_uninstall(_ns())
        finally:
            HOST.__file__ = original_file

        bins = {Path(p["path"]).name: p for p in out["paths"]
                if p["kind"] == "primitive_bin"}
        self.assertEqual(bins["agent-plus-meta"]["status"], "error")
        self.assertIn("note", bins["agent-plus-meta"])
        self.assertIn("manually", bins["agent-plus-meta"]["note"])
        # The other 4 succeeded.
        for name in ("repo-analyze", "diff-summary", "skill-feedback", "skill-plus"):
            self.assertEqual(bins[name]["status"], "removed",
                             msg=f"{name} should have been removed")


class TestUninstallInstallDirOverride(_TempHome):
    def test_uninstall_install_dir_override(self) -> None:
        # Stage an alt dir + plant the bins there too.
        alt = self.home / "alt" / "bin"
        alt.mkdir(parents=True, exist_ok=True)
        for name in un.PRIMITIVES:
            (alt / name).write_text("stub", encoding="utf-8")

        # AGENT_PLUS_INSTALL_DIR env honored when no flag.
        env_alt = self.home / "envalt" / "bin"
        env_alt.mkdir(parents=True, exist_ok=True)
        for name in un.PRIMITIVES:
            (env_alt / name).write_text("stub", encoding="utf-8")
        env = {**os.environ, "AGENT_PLUS_INSTALL_DIR": str(env_alt)}
        with patch.dict(os.environ, env, clear=False):
            out = un.cmd_uninstall(_ns())
            self.assertEqual(out["install_dir"], str(env_alt.resolve()))
            for name in un.PRIMITIVES:
                self.assertFalse((env_alt / name).is_file(),
                                 msg=f"{name} should be removed under env override")

        # --install-dir flag overrides the env (and our default).
        out = un.cmd_uninstall(_ns(install_dir=str(alt)))
        self.assertEqual(out["install_dir"], str(alt.resolve()))
        for name in un.PRIMITIVES:
            self.assertFalse((alt / name).is_file())


class TestUninstallPrimitiveTree(_TempHome):
    def test_uninstall_removes_prefix_trees(self) -> None:
        # Stage plugin trees under a fake $PREFIX and confirm they're removed
        # alongside the wrappers under $INSTALL_DIR.
        prefix = self.home / "prefix"
        for name in un.PRIMITIVES:
            tree = prefix / name / "bin"
            tree.mkdir(parents=True, exist_ok=True)
            (tree / name).write_text("stub", encoding="utf-8")
        out = un.cmd_uninstall(_ns(prefix=str(prefix)))
        # Trees gone.
        for name in un.PRIMITIVES:
            self.assertFalse((prefix / name).is_dir(),
                             msg=f"tree {name} not removed")
        # Manifest contains primitive_tree entries.
        trees = [p for p in out["paths"] if p["kind"] == "primitive_tree"]
        self.assertEqual(len(trees), 5)
        for t in trees:
            self.assertEqual(t["status"], "removed")

    def test_uninstall_prefix_env_var(self) -> None:
        prefix_env = self.home / "envprefix"
        for name in un.PRIMITIVES:
            (prefix_env / name).mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "AGENT_PLUS_PREFIX": str(prefix_env)}
        with patch.dict(os.environ, env, clear=False):
            out = un.cmd_uninstall(_ns())
            for name in un.PRIMITIVES:
                self.assertFalse((prefix_env / name).is_dir())
            trees = [p for p in out["paths"] if p["kind"] == "primitive_tree"]
            self.assertEqual({t["status"] for t in trees}, {"removed"})


class TestUninstallAutoFlag(unittest.TestCase):
    """`--auto` is a CLI alias for `--non-interactive`. Verified at the
    parser level via the bin's argparse."""

    def test_auto_aliases_non_interactive(self) -> None:
        # Re-create the parser the way the bin does, then assert --auto sets
        # non_interactive=True.
        parser = HOST._build_parser() if hasattr(HOST, "_build_parser") else None
        if parser is None:
            # Fall back: parse via the bin directly.
            import subprocess
            bin_path = _BIN_DIR / "agent-plus-meta"
            proc = subprocess.run(
                ["python3", str(bin_path), "uninstall", "--auto", "--dry-run"],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(proc.returncode, 0,
                             msg=f"--auto rejected: {proc.stderr!r}")
            return
        ns = parser.parse_args(["uninstall", "--auto", "--dry-run"])
        self.assertTrue(ns.non_interactive)
        self.assertTrue(ns.dry_run)


if __name__ == "__main__":
    unittest.main()
