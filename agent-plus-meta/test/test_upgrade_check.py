"""Tests for `agent-plus-meta upgrade-check` (v0.13.5).

Stdlib unittest only. Mocks `urllib.request.urlopen` so the suite stays
offline. Each test redirects ~/.agent-plus state to a tempdir via
HOME-patching so real user state is never touched.

Cuts honored: `--sentinel` mode is CUT (no tests for it). Telemetry
envelope field is CUT.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
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
# Locate the upgrade_check submodule via the bin's sys.path tweak.
_HERE = Path(__file__).resolve()
_BIN_DIR = _HERE.parent.parent / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))
from _subcommands import upgrade_check as uc  # noqa: E402

uc.bind(HOST)


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


def _mock_urlopen(version_str: str, *, status: int = 200, delay_ms: float = 0.0):
    def _opener(req, timeout=None):
        if delay_ms:
            time.sleep(delay_ms / 1000.0)
        return _FakeResp(version_str.encode("utf-8"), status=status)
    return _opener


def _ns(**kw) -> argparse.Namespace:
    defaults = dict(
        force=False, snooze=None, clear_snooze=False, timeout=3.0,
        non_interactive=False, no_telemetry=False, check=True,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class _TempHome(unittest.TestCase):
    """Redirect HOME so each test gets a fresh ~/.agent-plus."""

    def setUp(self):
        # Re-bind upgrade_check to OUR host (other test modules may have
        # bound a different host instance via their own _load_host).
        uc.bind(HOST)
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._patch = patch.object(Path, "home", return_value=self.home)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()


class TestVersionParsing(_TempHome):
    def test_parse_semver(self):
        self.assertEqual(uc._parse_semver("0.13.5"), (0, 13, 5))
        self.assertEqual(uc._parse_semver("1.2.3+rc1"), (1, 2, 3))
        self.assertEqual(uc._parse_semver("garbage"), (0, 0, 0))

    def test_compare_versions(self):
        self.assertEqual(uc._compare_versions("0.13.0", "0.13.5"), "upgrade_available")
        self.assertEqual(uc._compare_versions("0.13.5", "0.13.5"), "up_to_date")
        self.assertEqual(uc._compare_versions("0.14.0", "0.13.5"), "up_to_date")

    def test_looks_like_version(self):
        self.assertTrue(uc._looks_like_version("0.13.5"))
        self.assertTrue(uc._looks_like_version("1.2.3+rc1"))
        self.assertFalse(uc._looks_like_version(""))
        self.assertFalse(uc._looks_like_version("hello"))


class TestCacheBehaviour(_TempHome):
    def test_cache_hit_skips_network(self):
        # Pre-populate cache (still fresh, up_to_date).
        uc._write_json(uc._cache_path(), {
            "last_check_ts": int(time.time()),
            "ttl_sec": uc.TTL_UP_TO_DATE_SEC,
            "current_version": HOST._plugin_version(),
            "latest_version": HOST._plugin_version(),
            "result": "up_to_date",
        })
        with patch("urllib.request.urlopen") as mock_open:
            t0 = time.monotonic()
            env = uc.cmd_upgrade_check(_ns())
            elapsed = time.monotonic() - t0
        self.assertFalse(mock_open.called, "cache hit must not hit the network")
        self.assertTrue(env["cache"]["hit"])
        self.assertEqual(env["verdict"], "up_to_date")
        # TTHW1: cache hit < 1s
        self.assertLess(elapsed, 1.0)

    def test_cache_miss_probes_network(self):
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen("0.13.5")):
            env = uc.cmd_upgrade_check(_ns())
        self.assertFalse(env["cache"]["hit"])
        self.assertTrue(env["network"]["attempted"])
        self.assertTrue(env["network"]["ok"])
        self.assertEqual(env["latest_version"], "0.13.5")
        self.assertTrue(uc._cache_path().is_file())

    def test_force_bypasses_cache(self):
        uc._write_json(uc._cache_path(), {
            "last_check_ts": int(time.time()),
            "ttl_sec": uc.TTL_UP_TO_DATE_SEC,
            "current_version": "0.13.0",
            "latest_version": "0.13.0",
            "result": "up_to_date",
        })
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen("0.99.0")) as mo:
            env = uc.cmd_upgrade_check(_ns(force=True))
        self.assertTrue(mo.called)
        self.assertFalse(env["cache"]["hit"])
        self.assertEqual(env["latest_version"], "0.99.0")


class TestNetworkFailures(_TempHome):
    def test_timeout_returns_unknown(self):
        import socket
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            env = uc.cmd_upgrade_check(_ns())
        self.assertEqual(env["verdict"], "unknown")
        self.assertFalse(env["network"]["ok"])
        self.assertEqual(env["errors"][0]["code"], uc.ERR_NETWORK_FAILED)

    def test_malformed_response_returns_unknown(self):
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen("\x00binary\x00")):
            env = uc.cmd_upgrade_check(_ns())
        self.assertEqual(env["verdict"], "unknown")
        self.assertEqual(env["errors"][0]["code"], uc.ERR_NETWORK_FAILED)

    def test_http_404_returns_unknown(self):
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen("missing", status=404)):
            env = uc.cmd_upgrade_check(_ns())
        self.assertEqual(env["verdict"], "unknown")
        self.assertEqual(env["errors"][0]["code"], uc.ERR_NETWORK_FAILED)

    def test_network_probe_sub_5s_with_100ms_delay(self):
        # TTHW2
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen("0.13.5", delay_ms=100)):
            t0 = time.monotonic()
            env = uc.cmd_upgrade_check(_ns())
            elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 5.0)
        self.assertTrue(env["network"]["ok"])


class TestSnoozeLadder(_TempHome):
    def test_snooze_24h_writes_record(self):
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen("0.13.5")):
            env = uc.cmd_upgrade_check(_ns(snooze="24h"))
        s = uc._read_snooze()
        self.assertTrue(s["active"])
        self.assertEqual(s["ladder_step"], "24h")
        self.assertIsInstance(s["expires_ts"], int)
        self.assertEqual(env["snooze"]["ladder_step"], "24h")

    def test_snooze_advances_ladder(self):
        # First 24h then ask for 48h — ladder advances.
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen("0.13.5")):
            uc.cmd_upgrade_check(_ns(snooze="24h"))
            env = uc.cmd_upgrade_check(_ns(snooze="48h"))
        self.assertEqual(env["snooze"]["ladder_step"], "48h")

    def test_clear_snooze_resets_ladder(self):
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen("0.13.5")):
            uc.cmd_upgrade_check(_ns(snooze="48h"))
            env = uc.cmd_upgrade_check(_ns(clear_snooze=True))
        self.assertFalse(env["snooze"]["active"])
        self.assertEqual(env["snooze"]["ladder_step"], "none")

    def test_new_version_resets_snooze(self):
        # Snooze for v0.13.5
        uc._write_snooze({
            "active": True,
            "expires_ts": int(time.time()) + 24 * 3600,
            "ladder_step": "24h",
            "snoozed_for_version": "0.13.5",
        })
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen("0.13.6")):
            env = uc.cmd_upgrade_check(_ns())
        self.assertFalse(env["snooze"]["active"])
        self.assertEqual(env["snooze"]["ladder_step"], "none")

    def test_snooze_never_persists(self):
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen("0.13.5")):
            env = uc.cmd_upgrade_check(_ns(snooze="never"))
        self.assertEqual(env["snooze"]["ladder_step"], "never")
        self.assertIsNone(env["snooze"]["expires_ts"])


class TestEnvelopeSchema(_TempHome):
    REQUIRED_TOP = (
        "tool", "verdict", "current_version", "latest_version",
        "version_source", "cache", "snooze", "config", "network",
        "ttl_total_ms", "errors",
    )

    def test_envelope_has_required_fields(self):
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen("0.13.5")):
            env = uc.cmd_upgrade_check(_ns())
        for k in self.REQUIRED_TOP:
            self.assertIn(k, env, f"missing top-level field {k}")
        # telemetry CUT — must NOT appear
        self.assertNotIn("telemetry", env)
        # silent_upgrade_policy CUT — must NOT appear
        self.assertNotIn("silent_upgrade_policy", env["config"])
        self.assertEqual(env["version_source"], "root_VERSION_file")

    def test_envelope_verdict_enum(self):
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen("0.13.5")):
            env = uc.cmd_upgrade_check(_ns())
        self.assertIn(env["verdict"], (
            "up_to_date", "upgrade_available", "just_upgraded", "unknown",
        ))


if __name__ == "__main__":
    unittest.main()
