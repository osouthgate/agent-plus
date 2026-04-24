"""Unit tests for vercel-remote. Stdlib unittest only — no pytest, no Vercel account."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "vercel-remote"
    loader = SourceFileLoader("vercel_remote", str(bin_path))
    spec = importlib.util.spec_from_loader("vercel_remote", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


vr = _load_module()


# ──────────────────────────── _scrub ────────────────────────────


class TestScrub(unittest.TestCase):
    def test_strips_top_level_secrets(self) -> None:
        obj = {"id": "x", "token": "sk-abc", "password": "hunter2", "githubToken": "ghp_xxx"}
        out = vr._scrub(obj)
        self.assertEqual(out["id"], "x")
        self.assertEqual(out["token"], "[REDACTED]")
        self.assertEqual(out["password"], "[REDACTED]")
        self.assertEqual(out["githubToken"], "[REDACTED]")

    def test_strips_nested_secrets_in_dicts(self) -> None:
        obj = {
            "meta": {"githubCommitSha": "abc123", "githubToken": "ghp_xxx"},
            "passwordProtection": {"password": "super-secret", "enabled": True},
        }
        out = vr._scrub(obj)
        self.assertEqual(out["meta"]["githubCommitSha"], "abc123")
        self.assertEqual(out["meta"]["githubToken"], "[REDACTED]")
        self.assertEqual(out["passwordProtection"]["password"], "[REDACTED]")
        self.assertTrue(out["passwordProtection"]["enabled"])

    def test_strips_env_var_values_in_list(self) -> None:
        obj = {"envs": [
            {"key": "DATABASE_URL", "value": "postgres://user:pw@h/db", "target": ["production"]},
            {"key": "API_KEY", "value": "sk-super-secret", "target": ["production"]},
        ]}
        out = vr._scrub(obj)
        for item in out["envs"]:
            self.assertEqual(item["value"], "[REDACTED]")
            self.assertIn(item["key"], ("DATABASE_URL", "API_KEY"))

    def test_allowlist_keeps_metadata_fields(self) -> None:
        obj = {"passwordProtection": {"enabled": True, "password": "x"}, "valueType": "encrypted", "valueLength": 42}
        out = vr._scrub(obj)
        self.assertEqual(out["valueType"], "encrypted")
        self.assertEqual(out["valueLength"], 42)
        self.assertEqual(out["passwordProtection"]["password"], "[REDACTED]")

    def test_primitives_pass_through(self) -> None:
        self.assertEqual(vr._scrub("hello"), "hello")
        self.assertEqual(vr._scrub(42), 42)
        self.assertEqual(vr._scrub(None), None)


# ──────────────────────────── canary no-leak ────────────────────────────


class TestCanaryNoLeak(unittest.TestCase):
    """Asserts a known canary secret substring never appears in any emitted output."""

    CANARY = "CANARY_SECRET_DO_NOT_LEAK_4f2b9a"

    def _capture_emit(self, obj) -> str:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            vr.emit_json(obj, pretty=True)
        return buf.getvalue()

    def test_canary_not_in_env_value(self) -> None:
        obj = {"envs": [{"key": "SECRET_KEY", "value": self.CANARY, "target": ["production"]}]}
        out = self._capture_emit(obj)
        self.assertNotIn(self.CANARY, out)
        self.assertIn("SECRET_KEY", out)

    def test_canary_not_in_nested_password(self) -> None:
        obj = {"projects": [{"name": "x", "passwordProtection": {"password": self.CANARY}}]}
        out = self._capture_emit(obj)
        self.assertNotIn(self.CANARY, out)

    def test_canary_not_in_github_token(self) -> None:
        obj = {"meta": {"githubToken": self.CANARY, "githubCommitSha": "abc123"}}
        out = self._capture_emit(obj)
        self.assertNotIn(self.CANARY, out)
        self.assertIn("abc123", out)  # non-secret meta survives

    def test_canary_not_in_deeply_nested_structures(self) -> None:
        obj = {"a": [{"b": {"c": [{"secret": self.CANARY}, {"token": self.CANARY}]}}]}
        out = self._capture_emit(obj)
        self.assertNotIn(self.CANARY, out)


# ──────────────────────────── _parse_since_to_ms ────────────────────────────


class TestParseSince(unittest.TestCase):
    def test_hours(self) -> None:
        ms = int(vr._parse_since_to_ms("1h"))
        import time
        expected = (int(time.time()) - 3600) * 1000
        self.assertAlmostEqual(ms / 1000, expected / 1000, delta=5)

    def test_minutes(self) -> None:
        ms = int(vr._parse_since_to_ms("30m"))
        import time
        expected = (int(time.time()) - 1800) * 1000
        self.assertAlmostEqual(ms / 1000, expected / 1000, delta=5)

    def test_days(self) -> None:
        import time
        ms = int(vr._parse_since_to_ms("2d"))
        expected = (int(time.time()) - 172800) * 1000
        self.assertAlmostEqual(ms / 1000, expected / 1000, delta=5)


# ──────────────────────────── config loading ────────────────────────────


class TestConfigLoading(unittest.TestCase):
    def test_require_token_dies_without_token(self) -> None:
        with self.assertRaises(SystemExit):
            vr.require_token({})

    def test_require_token_returns_token(self) -> None:
        self.assertEqual(vr.require_token({"VERCEL_TOKEN": "abc"}), "abc")


# ──────────────────────────── deployment ref normalisation ────────────────────────────


class TestDeploymentRef(unittest.TestCase):
    def test_strips_https_prefix(self) -> None:
        out = vr._resolve_deployment_ref("https://my-app-xyz.vercel.app")
        self.assertEqual(out, "my-app-xyz.vercel.app")

    def test_passes_through_id(self) -> None:
        out = vr._resolve_deployment_ref("dpl_ABC123")
        self.assertEqual(out, "dpl_ABC123")


# ──────────────────────────── tool metadata ────────────────────────────


class TestToolMeta(unittest.TestCase):
    def test_injects_tool_field_on_dict(self) -> None:
        wrapped = vr._with_tool_meta({"project": "x"})
        self.assertIn("tool", wrapped)
        self.assertEqual(wrapped["tool"]["name"], "vercel-remote")
        self.assertIn("version", wrapped["tool"])
        # Tool field is first (dict insertion order) so agents see it up-top.
        self.assertEqual(list(wrapped.keys())[0], "tool")
        self.assertEqual(wrapped["project"], "x")

    def test_non_dict_passes_through(self) -> None:
        self.assertEqual(vr._with_tool_meta([1, 2, 3]), [1, 2, 3])
        self.assertEqual(vr._with_tool_meta("str"), "str")
        self.assertIsNone(vr._with_tool_meta(None))

    def test_existing_tool_field_preserved(self) -> None:
        payload = {"tool": {"name": "other", "version": "9.9"}, "data": 1}
        wrapped = vr._with_tool_meta(payload)
        # Don't overwrite — caller already set it.
        self.assertEqual(wrapped["tool"]["name"], "other")

    def test_plugin_version_reads_manifest(self) -> None:
        v = vr._plugin_version()
        self.assertIsInstance(v, str)
        self.assertNotEqual(v, "")

    def test_emit_json_injects_tool_on_dict(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            vr.emit_json({"hello": "world"}, pretty=False)
        parsed = json.loads(buf.getvalue())
        self.assertIn("tool", parsed)
        self.assertEqual(parsed["tool"]["name"], "vercel-remote")
        self.assertEqual(parsed["hello"], "world")

    def test_emit_json_passes_through_list(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            vr.emit_json([{"a": 1}, {"b": 2}], pretty=False)
        parsed = json.loads(buf.getvalue())
        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 2)


class TestWriteOutputFile(unittest.TestCase):
    """The --output envelope shape is part of the public contract."""

    def _write(self, payload: dict) -> tuple[dict, Path]:
        import tempfile
        td = Path(tempfile.mkdtemp())
        out = td / "nested" / "payload.json"
        summary = vr._write_output_file(payload, str(out))
        return summary, out

    def test_writes_file_and_returns_envelope(self) -> None:
        payload = {"tool": {"name": "x"}, "deployment": "dep_123",
                   "lines": ["a", "b", "c"]}
        summary, path = self._write(payload)
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(path.read_text("utf-8")), payload)
        self.assertEqual(summary["savedTo"], str(path.resolve()))
        self.assertGreater(summary["bytes"], 0)
        self.assertEqual(set(summary["payloadKeys"]), {"deployment", "lines"})
        self.assertNotIn("tool", summary["payloadKeys"])

    def test_log_payload_gets_head_and_tail_preview(self) -> None:
        payload = {"tool": {}, "lines": [f"line {i}" for i in range(100)]}
        summary, _ = self._write(payload)
        self.assertEqual(summary["preview"]["totalLines"], 100)
        self.assertEqual(summary["preview"]["head"][0], "line 0")
        self.assertEqual(summary["preview"]["tail"][-1], "line 99")

    def test_short_log_payload_omits_tail(self) -> None:
        summary, _ = self._write({"tool": {}, "lines": ["a", "b", "c"]})
        self.assertEqual(summary["preview"]["tail"], [])

    def test_non_log_payload_has_no_preview(self) -> None:
        summary, _ = self._write({"tool": {}, "deployments": [{"id": "x"}]})
        self.assertNotIn("preview", summary)

    def test_creates_parent_directories(self) -> None:
        _, path = self._write({"tool": {}, "k": "v"})
        self.assertTrue(path.parent.is_dir())

    def test_output_flag_parses(self) -> None:
        parser = vr.build_parser()
        args = parser.parse_args(["--output", "/tmp/x.json",
                                  "logs", "my-deployment"])
        self.assertEqual(args.output, "/tmp/x.json")


if __name__ == "__main__":
    unittest.main()
