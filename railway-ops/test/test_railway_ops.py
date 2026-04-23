"""Unit tests for railway-ops. Stdlib unittest only — no pytest, no Railway account."""

from __future__ import annotations

import importlib.util
import json
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "railway-ops"
    loader = SourceFileLoader("railway_ops", str(bin_path))
    spec = importlib.util.spec_from_loader("railway_ops", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ro = _load_module()


# ──────────────────────────── strip_env_values ────────────────────────────


class TestStripEnvValues(unittest.TestCase):
    def test_json_dict_returns_keys_only(self) -> None:
        raw = json.dumps({"KEY1": "value1", "KEY2": "value2", "DATABASE_URL": "postgres://user:pw@h/db"})
        out = ro.strip_env_values(raw)
        self.assertEqual(out, ["DATABASE_URL", "KEY1", "KEY2"])

    def test_kv_fallback_returns_keys_only(self) -> None:
        raw = "KEY1=value1\nKEY2=value with spaces\nKEY3=k=v=ok"
        out = ro.strip_env_values(raw)
        self.assertEqual(out, ["KEY1", "KEY2", "KEY3"])

    def test_leaks_no_value_substring(self) -> None:
        # Distinctive values that the output must not contain.
        values = {
            "OPENAI_API_KEY": "sk-proj-ZZZdistinctivevalue111",
            "LANGFUSE_PUBLIC_KEY": "pk-lf-UNIQUEstring222",
            "DATABASE_URL": "postgresql://u:VERYSECRETpw@host:5432/db",
        }
        raw = json.dumps(values)
        out = ro.strip_env_values(raw)
        blob = json.dumps(out)
        for v in values.values():
            # Probe each value against the full output string.
            self.assertNotIn(v, blob, f"value leaked: {v!r}")
            # And each distinctive substring too.
            for chunk in ("sk-proj-ZZZ", "pk-lf-UNIQUE", "VERYSECRETpw", "postgresql://"):
                self.assertNotIn(chunk, blob, f"substring leaked: {chunk!r}")

    def test_empty_input(self) -> None:
        self.assertEqual(ro.strip_env_values(""), [])
        self.assertEqual(ro.strip_env_values("   "), [])

    def test_malformed_json_falls_back_to_kv(self) -> None:
        raw = "{bad json\nKEY=x"  # forces KV fallback, KEY should still be extracted
        out = ro.strip_env_values(raw)
        self.assertIn("KEY", out)


# ──────────────────────────── log classification ────────────────────────────


class TestClassifyLogLine(unittest.TestCase):
    def test_level_error(self) -> None:
        self.assertEqual(ro.classify_log_line({"level": "error", "message": "boom"}), "error")
        self.assertEqual(ro.classify_log_line({"level": "FATAL", "message": "x"}), "error")

    def test_level_warn(self) -> None:
        self.assertEqual(ro.classify_log_line({"level": "warn", "message": "x"}), "warning")
        self.assertEqual(ro.classify_log_line({"level": "warning", "message": "x"}), "warning")

    def test_numeric_pino_level(self) -> None:
        self.assertEqual(ro.classify_log_line({"level": 50, "message": "x"}), "error")
        self.assertEqual(ro.classify_log_line({"level": 60, "message": "x"}), "error")
        self.assertEqual(ro.classify_log_line({"level": 40, "message": "x"}), "warning")
        self.assertIsNone(ro.classify_log_line({"level": 30, "message": "x"}))

    def test_message_regex_fallback_error(self) -> None:
        self.assertEqual(
            ro.classify_log_line({"level": "info", "message": "Unhandled exception in handler"}),
            "error",
        )
        self.assertEqual(
            ro.classify_log_line({"message": "Traceback (most recent call last):"}),
            "error",
        )

    def test_message_regex_fallback_warning(self) -> None:
        self.assertEqual(
            ro.classify_log_line({"level": "info", "message": "warning: slow query"}),
            "warning",
        )

    def test_ambiguous_info_returns_none(self) -> None:
        self.assertIsNone(ro.classify_log_line({"level": "info", "message": "started up"}))
        self.assertIsNone(ro.classify_log_line({"level": "debug", "message": "hello"}))

    def test_non_json_text_classify(self) -> None:
        self.assertEqual(ro.classify_log_text("ERROR: something went wrong"), "error")
        self.assertEqual(ro.classify_log_text("WARN: slow"), "warning")
        self.assertIsNone(ro.classify_log_text("everything is fine"))


# ──────────────────────────── bucket dedupe + cap ────────────────────────────


class TestBucketLogEntries(unittest.TestCase):
    def test_dedupes_consecutive_identical(self) -> None:
        entries = [
            {"level": "error", "message": "boom", "timestamp": "1"},
            {"level": "error", "message": "boom", "timestamp": "2"},
            {"level": "error", "message": "boom", "timestamp": "3"},
            {"level": "error", "message": "different", "timestamp": "4"},
        ]
        errors, warnings = ro.bucket_log_entries(entries, cap=20)
        self.assertEqual(len(errors), 2)
        self.assertEqual(warnings, [])

    def test_caps_at_limit(self) -> None:
        entries = [
            {"level": "error", "message": f"err-{i}", "timestamp": str(i)}
            for i in range(100)
        ]
        errors, _ = ro.bucket_log_entries(entries, cap=5)
        self.assertEqual(len(errors), 5)

    def test_mixed_errors_and_warnings(self) -> None:
        entries = [
            {"level": "error", "message": "e1"},
            {"level": "warn", "message": "w1"},
            {"level": "info", "message": "info"},
            {"level": "error", "message": "e2"},
        ]
        errors, warnings = ro.bucket_log_entries(entries, cap=20)
        self.assertEqual([e["message"] for e in errors], ["e1", "e2"])
        self.assertEqual([w["message"] for w in warnings], ["w1"])


# ──────────────────────────── overview shape (stubbed subprocess) ────────────────────────────


class StubRunner:
    """Injectable subprocess runner. Matches signature of default_run_cmd."""

    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str, str]]) -> None:
        # Key: tuple of argv after 'railway'. Value: (rc, stdout, stderr).
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, timeout=30, stdin=None):  # noqa: ARG002
        self.calls.append(list(argv))
        # Strip leading 'railway' to match keys.
        if argv and argv[0] == "railway":
            key = tuple(argv[1:])
        else:
            key = tuple(argv)
        if key in self.responses:
            return self.responses[key]
        # Prefix match — return any response whose key is a prefix of argv.
        for k, v in self.responses.items():
            if len(k) <= len(key) and tuple(key[: len(k)]) == k:
                return v
        return (0, "", "")  # default: success with empty output


class TestBuildServiceSnapshot(unittest.TestCase):
    def test_snapshot_shape_and_no_value_leak(self) -> None:
        service_row = {
            "id": "svc-1",
            "name": "api",
            "deploymentId": "dep-1",
            "status": "SUCCESS",
            "stopped": False,
        }
        # Fake logs (JSON-per-line) and variables (JSON dict).
        logs_json = "\n".join([
            json.dumps({"level": "info", "message": "started", "timestamp": "t1"}),
            json.dumps({"level": "error", "message": "SECRET-IN-MESSAGE-xyz boom", "timestamp": "t2"}),
            json.dumps({"level": "warn", "message": "slow", "timestamp": "t3"}),
        ])
        variables_json = json.dumps({
            "OPENAI_API_KEY": "sk-proj-LEAKCANARY-aaa",
            "DATABASE_URL": "postgresql://u:LEAKCANARY-bbb@h/db",
        })
        responses = {
            ("logs", "-s", "api", "--json", "--lines", "500", "--since", "24h"): (0, logs_json, ""),
            ("variables", "-s", "api", "--json"): (0, variables_json, ""),
        }
        stub = StubRunner(responses)
        with patch.object(ro, "run_cmd", stub):
            snap = ro.build_service_snapshot(service_row, None, since="24h", log_lines=500, bucket_cap=20)

        self.assertEqual(snap["name"], "api")
        self.assertEqual(snap["status"], "SUCCESS")
        self.assertEqual(snap["latestDeploy"]["id"], "dep-1")
        self.assertEqual(snap["envVarNames"], ["DATABASE_URL", "OPENAI_API_KEY"])
        self.assertEqual(len(snap["errors"]), 1)
        self.assertEqual(len(snap["warnings"]), 1)

        # No value canaries anywhere in the serialized snapshot.
        blob = json.dumps(snap)
        for canary in ("LEAKCANARY-aaa", "LEAKCANARY-bbb", "sk-proj-LEAKCANARY", "postgresql://u:"):
            self.assertNotIn(canary, blob, f"value canary leaked: {canary!r}")

    def test_blocked_write_subcommand(self) -> None:
        stub = StubRunner({})
        with patch.object(ro, "run_cmd", stub):
            rc, out, err = ro.railway(["up"])
        self.assertEqual(rc, 1)
        self.assertIn("blocked write subcommand", err)
        # Confirm the stub was never invoked.
        self.assertEqual(stub.calls, [])


# ──────────────────────────── parse_log_entries ────────────────────────────


class TestParseLogEntries(unittest.TestCase):
    def test_mixed_json_and_text(self) -> None:
        raw = (
            json.dumps({"level": "error", "message": "boom"}) + "\n"
            "a raw text error line\n"
            "\n"
            + json.dumps({"level": "info", "message": "ok"})
        )
        entries = ro.parse_log_entries(raw)
        self.assertEqual(len(entries), 3)
        self.assertTrue(entries[1].get("_non_json"))


if __name__ == "__main__":
    unittest.main()
