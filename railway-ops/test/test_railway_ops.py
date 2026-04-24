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


# ──────────────────────────── fingerprint + kinds ────────────────────────────


class TestFingerprint(unittest.TestCase):
    def test_numbers_collapse(self) -> None:
        a = ro.fingerprint_message("insert or update on table violates FK row 12345")
        b = ro.fingerprint_message("insert or update on table violates FK row 67890")
        self.assertEqual(a, b)

    def test_uuids_collapse(self) -> None:
        a = ro.fingerprint_message("job 550e8400-e29b-41d4-a716-446655440000 failed")
        b = ro.fingerprint_message("job 6ba7b810-9dad-11d1-80b4-00c04fd430c8 failed")
        self.assertEqual(a, b)
        self.assertIn("<uuid>", a)

    def test_quoted_strings_collapse(self) -> None:
        a = ro.fingerprint_message('column "foo_bar" does not exist')
        b = ro.fingerprint_message('column "baz_qux" does not exist')
        self.assertEqual(a, b)

    def test_truncates_very_long(self) -> None:
        fp = ro.fingerprint_message("x" * 500)
        self.assertLessEqual(len(fp), ro.FINGERPRINT_MAX_LEN + 1)  # +1 for ellipsis

    def test_empty_is_empty(self) -> None:
        self.assertEqual(ro.fingerprint_message(""), "")


class TestKindsBucketing(unittest.TestCase):
    def test_flood_of_identical_errors_counts_all(self) -> None:
        # 847 FK violations with different row IDs — all should bucket together
        # under a single fingerprint with count=847, even though errors[] is
        # capped at 20.
        entries = [
            {"level": "error",
             "message": f"insert or update on relationship_evidence row {i} violates FK",
             "timestamp": f"t{i}"}
            for i in range(847)
        ]
        errors, warnings, stats = ro._bucket_with_totals(entries, cap=20)
        self.assertLessEqual(len(errors), 20)
        self.assertEqual(stats["errorTotal"], 847)
        # Exactly one fingerprint bucket — all 847 collapse into it.
        self.assertEqual(len(stats["errorKinds"]), 1)
        fp, count = next(iter(stats["errorKinds"].items()))
        self.assertEqual(count, 847)
        self.assertIn("<n>", fp)

    def test_top_n_kinds_sorted_desc(self) -> None:
        kinds = {"a": 5, "b": 100, "c": 1, "d": 100}
        top = ro.top_n_kinds(kinds, n=3)
        # Sort: count desc, fingerprint asc for ties.
        self.assertEqual(list(top.keys()), ["b", "d", "a"])
        self.assertEqual(top["b"], 100)

    def test_top_n_kinds_respects_n(self) -> None:
        kinds = {str(i): i for i in range(20)}
        self.assertEqual(len(ro.top_n_kinds(kinds, n=5)), 5)


# ──────────────────────────── summarize snapshots ────────────────────────────


class TestSummarizeSnapshots(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(
            ro._summarize_snapshots([]),
            {"services": 0, "failures": 0, "errors": 0, "warnings": 0},
        )

    def test_counts_errors_warnings_and_failures(self) -> None:
        snapshots = [
            {"name": "a", "status": "SUCCESS", "errors": [{"message": "e1"}],
             "warnings": [{"message": "w1"}, {"message": "w2"}]},
            {"name": "b", "status": "FAILED", "errors": [], "warnings": []},
            {"name": "c", "status": "CRASHED", "errors": [{"message": "e2"}], "warnings": []},
            {"name": "d", "error": "snapshot failed: boom"},  # collection error
        ]
        summary = ro._summarize_snapshots(snapshots)
        self.assertEqual(summary["services"], 4)
        # b (FAILED) + c (CRASHED) + d (collection error) → 3
        self.assertEqual(summary["failures"], 3)
        # a: 1, c: 1 → 2
        self.assertEqual(summary["errors"], 2)
        # a: 2 → 2
        self.assertEqual(summary["warnings"], 2)

    def test_status_case_insensitive(self) -> None:
        snapshots = [{"name": "x", "status": "failed", "errors": [], "warnings": []}]
        self.assertEqual(ro._summarize_snapshots(snapshots)["failures"], 1)

    def test_missing_buckets_treated_as_empty(self) -> None:
        snapshots = [{"name": "x", "status": "SUCCESS"}]
        summary = ro._summarize_snapshots(snapshots)
        self.assertEqual(summary, {"services": 1, "failures": 0, "errors": 0, "warnings": 0})


# ──────────────────────────── overview schema contract ────────────────────────────


def _overview_stub_responses(
    services: list[dict],
    *,
    logs_by_service: dict[str, str] | None = None,
    variables_by_service: dict[str, str] | None = None,
    since: str = "24h",
    log_lines: int = 500,
    env: str | None = None,
) -> dict[tuple[str, ...], tuple[int, str, str]]:
    """Build a StubRunner response map for a complete overview call.

    When ``env`` is set, the ``railway`` commands issued by ``build_overview``
    append ``--environment <env>`` to ``service status``, ``logs``, and
    ``variables``. Stub keys must match exactly (StubRunner uses prefix match
    against argv), so we assemble them dynamically here.
    """
    logs_by_service = logs_by_service or {}
    variables_by_service = variables_by_service or {}

    env_suffix: tuple[str, ...] = ("--environment", env) if env else ()

    service_status_key = ("service", "status", "--all", "--json") + env_suffix

    responses: dict[tuple[str, ...], tuple[int, str, str]] = {
        ("--version",): (0, "railway 4.0.0", ""),
        ("whoami",): (0, "Logged in as test@example.com", ""),
        ("status", "--json"): (
            0,
            json.dumps({"name": "test-project", "id": "proj-1"}),
            "",
        ),
        service_status_key: (0, json.dumps(services), ""),
    }
    for svc in services:
        name = svc["name"]
        logs = logs_by_service.get(name, "")
        variables = variables_by_service.get(name, "{}")
        logs_key = (
            "logs", "-s", name, "--json", "--lines", str(log_lines),
            "--since", since,
        ) + env_suffix
        variables_key = ("variables", "-s", name, "--json") + env_suffix
        responses[logs_key] = (0, logs, "")
        responses[variables_key] = (0, variables, "")
    return responses


class TestOverviewSchemaContract(unittest.TestCase):
    """Contract tests: overview JSON shape must remain stable."""

    def _build(self, responses, **kwargs):
        stub = StubRunner(responses)
        with patch.object(ro, "run_cmd", stub):
            return ro.build_overview("production", "24h", 500, **kwargs), stub

    def test_top_level_keys_present(self) -> None:
        services = [
            {"id": "svc-1", "name": "api", "deploymentId": "dep-1",
             "status": "SUCCESS", "stopped": False},
        ]
        responses = _overview_stub_responses(services, env="production")
        data, _ = self._build(responses)

        expected_keys = {"project", "projectId", "env", "since", "filter", "summary", "services"}
        self.assertEqual(set(data.keys()), expected_keys,
                         "overview top-level keys must remain stable")

        self.assertEqual(data["project"], "test-project")
        self.assertEqual(data["projectId"], "proj-1")
        self.assertEqual(data["env"], "production")
        self.assertEqual(data["since"], "24h")
        self.assertIsNone(data["filter"])  # no --service supplied
        self.assertIsInstance(data["services"], list)

    def test_summary_shape(self) -> None:
        services = [
            {"id": "svc-1", "name": "api", "deploymentId": "dep-1",
             "status": "SUCCESS", "stopped": False},
            {"id": "svc-2", "name": "worker", "deploymentId": "dep-2",
             "status": "FAILED", "stopped": False},
        ]
        logs = {
            "api": json.dumps({"level": "error", "message": "boom", "timestamp": "t1"}),
            "worker": "\n".join([
                json.dumps({"level": "error", "message": "e1", "timestamp": "t1"}),
                json.dumps({"level": "warn", "message": "w1", "timestamp": "t2"}),
            ]),
        }
        responses = _overview_stub_responses(services, logs_by_service=logs, env="production")
        data, _ = self._build(responses)

        summary = data["summary"]
        self.assertEqual(set(summary.keys()), {"services", "failures", "errors", "warnings"},
                         "summary keys must remain stable")
        self.assertEqual(summary["services"], 2)
        self.assertEqual(summary["failures"], 1)  # worker is FAILED
        self.assertEqual(summary["errors"], 2)    # api: 1, worker: 1
        self.assertEqual(summary["warnings"], 1)  # worker: 1

    def test_service_filter_narrows_result(self) -> None:
        services = [
            {"id": "svc-1", "name": "api", "deploymentId": "dep-1",
             "status": "SUCCESS", "stopped": False},
            {"id": "svc-2", "name": "worker", "deploymentId": "dep-2",
             "status": "SUCCESS", "stopped": False},
            {"id": "svc-3", "name": "postgres", "deploymentId": "dep-3",
             "status": "SUCCESS", "stopped": False},
        ]
        responses = _overview_stub_responses(services, env="production")
        data, stub = self._build(responses, service_filter="worker")

        self.assertEqual(data["filter"], {"service": "worker"})
        self.assertEqual(len(data["services"]), 1)
        self.assertEqual(data["services"][0]["name"], "worker")
        self.assertEqual(data["summary"]["services"], 1)

        # Confirm we only hit the worker's logs/variables, not the others.
        log_calls = [c for c in stub.calls if len(c) > 1 and c[1] == "logs"]
        service_args_hit = {c[3] for c in log_calls if len(c) > 3}
        self.assertEqual(service_args_hit, {"worker"})

    def test_service_filter_case_insensitive(self) -> None:
        services = [
            {"id": "svc-1", "name": "API", "deploymentId": "dep-1",
             "status": "SUCCESS", "stopped": False},
        ]
        # logs-call key uses the original casing 'API'
        responses = _overview_stub_responses(services, env="production")
        data, _ = self._build(responses, service_filter="api")
        self.assertEqual(len(data["services"]), 1)
        self.assertEqual(data["services"][0]["name"], "API")

    def test_service_filter_no_match_returns_empty_services(self) -> None:
        services = [
            {"id": "svc-1", "name": "api", "deploymentId": "dep-1",
             "status": "SUCCESS", "stopped": False},
        ]
        responses = _overview_stub_responses(services, env="production")
        data, _ = self._build(responses, service_filter="nonexistent")
        self.assertEqual(data["services"], [])
        self.assertEqual(data["summary"]["services"], 0)
        self.assertEqual(data["filter"], {"service": "nonexistent"})

    def test_bucket_cap_respected(self) -> None:
        services = [
            {"id": "svc-1", "name": "api", "deploymentId": "dep-1",
             "status": "SUCCESS", "stopped": False},
        ]
        # 10 distinct error lines; cap=3 should keep only 3.
        logs = "\n".join(
            json.dumps({"level": "error", "message": f"err-{i}", "timestamp": f"t{i}"})
            for i in range(10)
        )
        responses = _overview_stub_responses(
            services, logs_by_service={"api": logs}, env="production"
        )
        data, _ = self._build(responses, bucket_cap=3)
        snap = data["services"][0]
        # errors[] is capped at 3, but errorTotal and the summary now track
        # the pre-cap count so a flood can't hide behind the truncation.
        self.assertEqual(len(snap["errors"]), 3)
        self.assertEqual(snap["errorTotal"], 10)
        self.assertTrue(snap["truncated"])
        self.assertEqual(data["summary"]["errors"], 10)

    def test_service_snapshot_keys_stable(self) -> None:
        services = [
            {"id": "svc-1", "name": "api", "deploymentId": "dep-1",
             "status": "SUCCESS", "stopped": False},
        ]
        variables = {"api": json.dumps({"KEY1": "v1"})}
        responses = _overview_stub_responses(
            services, variables_by_service=variables, env="production"
        )
        data, _ = self._build(responses)

        snap = data["services"][0]
        expected_service_keys = {
            "name", "id", "status", "stopped", "latestDeploy",
            "errors", "warnings", "errorTotal", "warningTotal",
            "errorKinds", "warningKinds", "truncated", "envVarNames",
        }
        self.assertEqual(set(snap.keys()), expected_service_keys,
                         "service snapshot keys must remain stable")
        self.assertIsInstance(snap["latestDeploy"], dict)
        self.assertEqual(set(snap["latestDeploy"].keys()), {"id", "status"})

    def test_json_round_trips_cleanly(self) -> None:
        """End-to-end: build_overview output must serialise to JSON without errors."""
        services = [
            {"id": "svc-1", "name": "api", "deploymentId": "dep-1",
             "status": "SUCCESS", "stopped": False},
        ]
        responses = _overview_stub_responses(services, env="production")
        data, _ = self._build(responses)
        blob = json.dumps(data)
        self.assertIsInstance(blob, str)
        parsed = json.loads(blob)
        self.assertEqual(parsed, json.loads(json.dumps(data)))


# ──────────────────────────── argparse contract ────────────────────────────


class TestArgparseContract(unittest.TestCase):
    """Verify the CLI surface — flag names are part of the public contract."""

    def test_overview_accepts_service_and_limit(self) -> None:
        parser = ro.build_parser()
        args = parser.parse_args([
            "overview", "--env", "production", "--service", "api", "--limit", "5",
        ])
        self.assertEqual(args.cmd, "overview")
        self.assertEqual(args.env, "production")
        self.assertEqual(args.service, "api")
        self.assertEqual(args.limit, 5)

    def test_overview_defaults(self) -> None:
        parser = ro.build_parser()
        args = parser.parse_args(["overview"])
        self.assertIsNone(args.service)
        self.assertEqual(args.limit, ro.DEFAULT_OVERVIEW_BUCKET_CAP)
        self.assertEqual(args.since, ro.DEFAULT_SINCE)
        self.assertEqual(args.log_lines, ro.DEFAULT_OVERVIEW_LOG_LINES)

    def test_back_compat_bucket_cap_alias(self) -> None:
        self.assertEqual(ro.OVERVIEW_BUCKET_CAP, ro.DEFAULT_OVERVIEW_BUCKET_CAP)


if __name__ == "__main__":
    unittest.main()
