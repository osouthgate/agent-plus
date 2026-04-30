"""Tests for `agent-plus-meta upgrade` (v0.13.5).

Stdlib unittest. Mocks `urllib.request.urlopen` and the in-process
`cmd_doctor` so the suite stays offline and never touches real bins.
HOME is redirected to a tempdir per test.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import MagicMock, patch


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
from _subcommands import upgrade as up  # noqa: E402

up.bind(HOST)


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self._status = status

    def read(self):
        return self._body

    def getcode(self):
        return self._status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _mock_urlopen_factory(body: bytes = b"#!/usr/bin/env python3\nprint('hi')\n", *,
                          status: int = 200):
    def _opener(req, timeout=None):
        return _FakeResp(body, status=status)
    return _opener


def _ns(**kw) -> argparse.Namespace:
    defaults = dict(
        rollback=False, dry_run=False, non_interactive=True, auto=True,
        user_choice=None, no_telemetry=False,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class _TempHome(unittest.TestCase):
    def setUp(self):
        # Re-bind upgrade module to OUR host. Other test files may have
        # rebound `_host` to their own freshly-loaded host module, which
        # breaks `patch.object(HOST, ...)` here. Rebinding per-test
        # guarantees mocks land on the right module instance.
        up.bind(HOST)
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._patch_home = patch.object(Path, "home", return_value=self.home)
        self._patch_home.start()
        # Stage fake bins
        self.bin_dir = self.home / ".local" / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        for name in up.PRIMITIVES:
            (self.bin_dir / name).write_text(f"#!/bin/sh\necho old-{name}\n",
                                              encoding="utf-8")
        # Force install detector to find our staged bin
        self._patch_install = patch(
            "_subcommands.upgrade.detect_install_type",
            return_value={
                "install_type": "global",
                "bin_dir": str(self.bin_dir),
            },
        )
        self._patch_install.start()
        # Mock the upgrade_check helper used to discover latest_version
        self._patch_fetch = patch(
            "_subcommands.upgrade_check._fetch_latest_version",
            return_value=("0.99.0", None, 50),
        )
        self._patch_fetch.start()
        # Mock cmd_doctor → healthy by default
        self._doctor_mock = MagicMock(return_value={"verdict": "healthy"})
        self._patch_doctor = patch.object(HOST, "cmd_doctor", self._doctor_mock)
        self._patch_doctor.start()

    def tearDown(self):
        self._patch_doctor.stop()
        self._patch_fetch.stop()
        self._patch_install.stop()
        self._patch_home.stop()
        self._tmp.cleanup()


class TestInstallTypeDetection(unittest.TestCase):
    def test_global_via_local_bin(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            bin_dir = home / ".local" / "bin"
            bin_dir.mkdir(parents=True)
            fake = bin_dir / "agent-plus-meta"
            fake.write_text("x")
            with patch.object(Path, "home", return_value=home):
                got = up.detect_install_type(meta_bin=str(fake))
        self.assertEqual(got["install_type"], "global")

    def test_git_local(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".git").mkdir()
            (root / "bin").mkdir()
            fake = root / "bin" / "agent-plus-meta"
            fake.write_text("x")
            got = up.detect_install_type(meta_bin=str(fake))
        self.assertEqual(got["install_type"], "git_local")

    def test_no_vendored_branch(self):
        # Per /review C4: vendored detector branch is CUT.
        # The install_type field can only be one of these three values.
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "agent-plus-meta"
            fake.write_text("x")
            got = up.detect_install_type(meta_bin=str(fake))
        self.assertIn(got["install_type"], ("global", "git_local", "unknown"))
        self.assertNotEqual(got["install_type"], "vendored")


class TestBinReplacement(_TempHome):
    # The default mocked latest_version (0.99.0) vs current (plugin.json's
    # 0.13.5) is a minor/major bump. Under T5's pre-1.0 safety policy, --auto
    # would degrade to snooze for non-patch bumps. These tests exercise the
    # bin-replacement logic specifically, so they pass user_choice="yes"
    # explicitly to bypass the auto-degradation policy.
    def test_successful_upgrade_writes_baks_and_replaces(self):
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen_factory(b"#!/bin/sh\necho new\n")):
            env = up.cmd_upgrade(_ns(user_choice="yes"))
        self.assertEqual(env["verdict"], "success")
        self.assertEqual(len(env["bins_replaced"]), len(up.PRIMITIVES))
        for entry in env["bins_replaced"]:
            self.assertEqual(entry["status"], "ok")
            self.assertTrue(Path(entry["backup_path"]).is_file())
        # New content lands.
        self.assertIn("echo new", (self.bin_dir / "agent-plus-meta").read_text(encoding="utf-8"))

    def test_rollback_when_one_bin_fails(self):
        # Make _replace_primitive fail for skill-feedback.
        original = up._replace_primitive

        def _maybe_fail(bin_dir, name, body):
            if name == "skill-feedback":
                return False
            return original(bin_dir, name, body)
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen_factory(b"#!/bin/sh\necho new\n")), \
             patch.object(up, "_replace_primitive", side_effect=_maybe_fail):
            env = up.cmd_upgrade(_ns(user_choice="yes"))
        self.assertEqual(env["verdict"], "rolled_back")
        self.assertEqual(env["errors"][0]["code"], up.ERR_PARTIAL_FAILURE)
        self.assertTrue(env["post_test"]["rollback_triggered"])

    def test_doctor_broken_triggers_rollback(self):
        self._doctor_mock.return_value = {"verdict": "broken"}
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen_factory(b"#!/bin/sh\necho new\n")):
            env = up.cmd_upgrade(_ns(user_choice="yes"))
        self.assertEqual(env["verdict"], "rolled_back")
        codes = [e["code"] for e in env["errors"]]
        self.assertIn(up.ERR_ROLLBACK_REQUIRED, codes)


class TestUserChoiceModes(_TempHome):
    def test_explicit_yes(self):
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen_factory()):
            env = up.cmd_upgrade(_ns(non_interactive=True, auto=False, user_choice="yes"))
        self.assertEqual(env["user_choice"], "yes")
        self.assertIn(env["verdict"], ("success", "rolled_back"))

    def test_snooze_advances_ladder_and_noops(self):
        # Pre-existing snooze at 24h
        from _subcommands import upgrade_check as uc
        uc.bind(HOST)
        uc._write_snooze({
            "active": True,
            "expires_ts": int(time.time()) + 24 * 3600,
            "ladder_step": "24h",
            "snoozed_for_version": "0.99.0",
        })
        env = up.cmd_upgrade(_ns(non_interactive=True, auto=False,
                                 user_choice="snooze"))
        self.assertEqual(env["verdict"], "noop")
        self.assertEqual(env["user_choice"], "snooze")
        snooze = uc._read_snooze()
        self.assertEqual(snooze["ladder_step"], "48h")

    def test_never_sets_update_check_false(self):
        env = up.cmd_upgrade(_ns(non_interactive=True, auto=False,
                                 user_choice="never"))
        self.assertEqual(env["verdict"], "noop")
        self.assertEqual(env["user_choice"], "never")
        cfg = up._read_json(up._config_path())
        self.assertEqual(cfg["update_check"], False)

    def test_always_sets_silent_upgrade_true(self):
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen_factory()):
            env = up.cmd_upgrade(_ns(non_interactive=True, auto=False,
                                     user_choice="always"))
        self.assertEqual(env["user_choice"], "always")
        cfg = up._read_json(up._config_path())
        self.assertEqual(cfg["silent_upgrade"], True)

    def test_auto_with_silent_upgrade_patch_picks_always(self):
        # T5: silent_upgrade=true + patch bump → auto picks 'always'.
        # Stage config and a patch bump (current=0.99.0 → still 0.99.0
        # is a noop, so we mock the host plugin version to 0.99.0 and
        # latest to 0.99.1 to get a real patch bump).
        up._write_config_field("silent_upgrade", True)
        with patch.object(HOST, "_plugin_version", return_value="0.99.0"), \
             patch("_subcommands.upgrade_check._fetch_latest_version",
                   return_value=("0.99.1", None, 10)), \
             patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen_factory()):
            env = up.cmd_upgrade(_ns(non_interactive=True, auto=True))
        self.assertEqual(env["user_choice"], "always")

    def test_auto_with_silent_upgrade_minor_degrades_to_snooze(self):
        # T5 (corrected at /review): minor/major bumps under --auto MUST NOT
        # silently land — non-interactive can't prompt, so we degrade to
        # snooze (noop). This is the foot-gun T5 was designed to prevent for
        # someone running --auto in CI: a minor bump that renames a flag
        # silently breaks them. The next interactive run sees UPGRADE_AVAILABLE
        # and lets the user accept consciously. Patch bumps still flow normally.
        up._write_config_field("silent_upgrade", True)
        with patch.object(HOST, "_plugin_version", return_value="0.99.0"), \
             patch("_subcommands.upgrade_check._fetch_latest_version",
                   return_value=("1.0.0", None, 10)), \
             patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen_factory()):
            env = up.cmd_upgrade(_ns(non_interactive=True, auto=True))
        self.assertEqual(env["user_choice"], "snooze")
        # Verdict is noop because snooze short-circuits before bin replacement.
        self.assertEqual(env["verdict"], "noop")


class TestRollbackStandalone(_TempHome):
    def test_rollback_restores_most_recent_bak(self):
        # Run an upgrade so .bak exists, then mutate the bin and roll back.
        # user_choice="yes" bypasses T5 auto-degradation (mocked latest is a
        # minor/major bump from current — this test exercises rollback, not
        # the auto-degradation policy).
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen_factory(b"#!/bin/sh\necho new\n")):
            up.cmd_upgrade(_ns(user_choice="yes"))
        self.assertIn("echo new",
                      (self.bin_dir / "agent-plus-meta").read_text(encoding="utf-8"))

        t0 = time.monotonic()
        env = up.cmd_upgrade(_ns(rollback=True))
        elapsed = time.monotonic() - t0
        self.assertEqual(env["verdict"], "rolled_back")
        # Pre-upgrade content was "echo old-agent-plus-meta"
        self.assertIn("echo old-agent-plus-meta",
                      (self.bin_dir / "agent-plus-meta").read_text(encoding="utf-8"))
        # TTHW5: rollback < 2s
        self.assertLess(elapsed, 2.0)

    def test_rollback_with_no_baks_returns_error(self):
        env = up.cmd_upgrade(_ns(rollback=True))
        self.assertEqual(env["verdict"], "error")
        self.assertEqual(env["errors"][0]["code"], up.ERR_PARTIAL_FAILURE)


class TestDryRun(_TempHome):
    def test_dry_run_no_filesystem_changes(self):
        before = (self.bin_dir / "agent-plus-meta").read_text(encoding="utf-8")
        t0 = time.monotonic()
        env = up.cmd_upgrade(_ns(dry_run=True))
        elapsed = time.monotonic() - t0
        after = (self.bin_dir / "agent-plus-meta").read_text(encoding="utf-8")
        self.assertEqual(env["verdict"], "noop")
        self.assertEqual(before, after)
        # No .bak directory should appear under dry-run.
        self.assertFalse(up._bak_root().is_dir())
        # TTHW4: dry-run < 3s
        self.assertLess(elapsed, 3.0)


class TestEnvelopeSchema(_TempHome):
    REQUIRED = (
        "tool", "verdict", "from_version", "to_version",
        "install_type_detected", "bins_replaced", "migrations_applied",
        "post_test", "user_choice", "ttl_total_ms", "errors",
    )

    def test_envelope_schema(self):
        env = up.cmd_upgrade(_ns(dry_run=True))
        for k in self.REQUIRED:
            self.assertIn(k, env)
        self.assertNotIn("telemetry", env)
        self.assertIn(env["install_type_detected"], ("global", "git_local", "unknown"))


if __name__ == "__main__":
    unittest.main()
