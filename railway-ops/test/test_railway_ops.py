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


# ──────────────────────────── tool metadata ────────────────────────────


class TestToolMeta(unittest.TestCase):
    def test_injects_tool_field_on_dict(self) -> None:
        wrapped = ro._with_tool_meta({"project": "x"})
        self.assertIn("tool", wrapped)
        self.assertEqual(wrapped["tool"]["name"], "railway-ops")
        self.assertIn("version", wrapped["tool"])
        # Tool field is first (dict insertion order) so agents see it up-top.
        self.assertEqual(list(wrapped.keys())[0], "tool")
        self.assertEqual(wrapped["project"], "x")

    def test_non_dict_passes_through(self) -> None:
        self.assertEqual(ro._with_tool_meta([1, 2, 3]), [1, 2, 3])
        self.assertEqual(ro._with_tool_meta("str"), "str")
        self.assertIsNone(ro._with_tool_meta(None))

    def test_existing_tool_field_preserved(self) -> None:
        payload = {"tool": {"name": "other", "version": "9.9"}, "data": 1}
        wrapped = ro._with_tool_meta(payload)
        # Don't overwrite — caller already set it.
        self.assertEqual(wrapped["tool"]["name"], "other")

    def test_plugin_version_reads_manifest(self) -> None:
        # Version should match what's in plugin.json; if manifest missing, 'unknown'.
        v = ro._plugin_version()
        self.assertIsInstance(v, str)
        self.assertNotEqual(v, "")


# ──────────────────────────── pg log classification ────────────────────────────


class TestPostgresLogClassification(unittest.TestCase):
    """Postgres writes routine activity to stderr as LOG/DETAIL/HINT/STATEMENT.
    Railway stamps stderr as level=error, so without pg-aware classification
    the bucket fills with 500 `checkpoint complete` lines and drowns the real
    signal. These tests pin the embedded-level override."""

    def test_checkpoint_log_not_error(self) -> None:
        msg = (
            "2026-04-24 13:36:15.296 UTC [22] LOG:  checkpoint complete: "
            "wrote 3 buffers (0.0%)"
        )
        self.assertTrue(ro.is_pg_log_line(msg))
        self.assertIsNone(ro.pg_embedded_level(msg))
        # Even when Railway stamps it as error, classify_log_line returns None.
        self.assertIsNone(ro.classify_log_line({"level": "error", "message": msg}))

    def test_pg_error_still_classified_as_error(self) -> None:
        msg = (
            '2026-04-24 13:37:46.719 UTC [1991] ERROR:  column "provider" '
            "does not exist at character 82"
        )
        self.assertEqual(ro.pg_embedded_level(msg), "error")
        self.assertEqual(ro.classify_log_line({"level": "error", "message": msg}), "error")

    def test_pg_statement_line_not_error(self) -> None:
        msg = (
            "2026-04-24 13:37:46.719 UTC [1991] STATEMENT:  "
            "SELECT coalesce(json_agg(t), '[]'::json)::text FROM ..."
        )
        # STATEMENT is attached to an adjacent ERROR; the ERROR line itself
        # is already counted, so we don't want to double-count.
        self.assertIsNone(ro.pg_embedded_level(msg))

    def test_pg_warning_classified_as_warning(self) -> None:
        msg = (
            "2026-04-24 13:37:46.719 UTC [1991] WARNING:  deprecated cast"
        )
        self.assertEqual(ro.pg_embedded_level(msg), "warning")

    def test_pg_fatal_classified_as_error(self) -> None:
        msg = "2026-04-24 13:37:46.719 UTC [1991] FATAL:  connection refused"
        self.assertEqual(ro.pg_embedded_level(msg), "error")

    def test_non_pg_line_falls_through(self) -> None:
        self.assertFalse(ro.is_pg_log_line("regular app log"))
        self.assertIsNone(ro.pg_embedded_level("regular app log"))

    def test_flood_of_pg_checkpoints_collapses_to_zero_errors(self) -> None:
        """The exact scenario from the user's real loamdb-postgres snapshot:
        500 LOG lines stamped error=true by Railway should produce 0 errors."""
        entries = [
            {"level": "error",
             # Rotate across a plausible HH:MM:SS space so every line still
             # parses as a real pg log (the real source has 500 distinct
             # checkpoint events spread over hours, not 500 within one minute).
             "message": (f"2026-04-24 {(i // 3600) % 24:02d}:"
                         f"{(i // 60) % 60:02d}:{i % 60:02d}.296 UTC [22] "
                         "LOG:  checkpoint complete: wrote 3 buffers"),
             "timestamp": f"t{i}"}
            for i in range(500)
        ]
        # Add 2 real errors too
        entries.append({
            "level": "error",
            "message": '2026-04-24 13:37:46.719 UTC [1991] ERROR:  '
                       'column "provider" does not exist at character 82',
            "timestamp": "t-real",
        })
        errors, warnings, stats = ro._bucket_with_totals(entries, cap=20)
        self.assertEqual(stats["errorTotal"], 1, "only the real ERROR should count")
        self.assertEqual(stats["warningTotal"], 0)
        self.assertEqual(len(errors), 1)


# ──────────────────────────── build log enrichment ────────────────────────────


class TestEnrichDeployWithBuildLogs(unittest.TestCase):
    """When latestDeploy is FAILED and distinct from activeDeploy, the overview
    should auto-include a build-log tail + errorKinds so one call tells the
    whole story — no second invocation needed."""

    def test_no_enrichment_for_success_deploy(self) -> None:
        deploy = {"id": "d1", "status": "SUCCESS"}
        result = ro.enrich_deploy_with_build_logs(deploy)
        self.assertNotIn("buildLogTail", result)
        self.assertNotIn("buildErrorKinds", result)

    def test_no_enrichment_for_none(self) -> None:
        self.assertIsNone(ro.enrich_deploy_with_build_logs(None))

    def test_no_enrichment_for_missing_id(self) -> None:
        deploy = {"status": "FAILED"}
        result = ro.enrich_deploy_with_build_logs(deploy)
        self.assertNotIn("buildLogTail", result)

    def test_failed_deploy_gets_build_log_tail(self) -> None:
        # Mirror the real Railway build failure shape — lines prefixed with
        # [err] from the builder, culminating in the "Build Failed" line.
        build_logs = "\n".join([
            json.dumps({"timestamp": "t1", "message": "[err] [builder 3/6] COPY packages/ packages/"}),
            json.dumps({"timestamp": "t2", "message": "[err] [builder 4/6] COPY package.json ./"}),
            json.dumps({
                "timestamp": "t3",
                "message": (
                    '[err] Build Failed: failed to compute cache key: failed to '
                    'calculate checksum of ref abc::def: "/package.json": not found'
                ),
            }),
        ])
        stub = StubRunner({
            ("logs", "--deployment", "d-failed", "--json", "--lines", "300"): (0, build_logs, ""),
        })
        deploy = {"id": "d-failed", "status": "FAILED"}
        with patch.object(ro, "run_cmd", stub):
            result = ro.enrich_deploy_with_build_logs(deploy, tail=10)
        self.assertIn("buildLogTail", result)
        self.assertIn("buildErrorKinds", result)
        self.assertEqual(result["buildLineCount"], 3)
        # All three lines classified as errors (contain "err" / "Build Failed").
        # errorKinds should surface the cache-key failure fingerprint.
        self.assertGreater(len(result["buildErrorKinds"]), 0)
        # The final "Build Failed" line should be in the tail somewhere.
        tail_messages = [e["message"] for e in result["buildLogTail"]]
        self.assertTrue(
            any("Build Failed" in m for m in tail_messages),
            f"Build Failed line missing from tail: {tail_messages}",
        )

    def test_empty_build_logs_safe(self) -> None:
        # `railway logs --deployment` returns empty for a deploy that never
        # reached any builder output (e.g. queue eviction). Enrichment must
        # not crash, must not add empty fields.
        stub = StubRunner({})  # empty — unknown-call fallback is (0, "", "")
        deploy = {"id": "d-skipped", "status": "FAILED"}
        with patch.object(ro, "run_cmd", stub):
            result = ro.enrich_deploy_with_build_logs(deploy)
        self.assertNotIn("buildLogTail", result)


# ──────────────────────────── deploy splitting ────────────────────────────


class TestClassifyDeploys(unittest.TestCase):
    def test_returns_none_for_empty_history(self) -> None:
        active, latest = ro.classify_deploys([])
        self.assertIsNone(active)
        self.assertIsNone(latest)

    def test_latest_is_first_active_is_most_recent_success(self) -> None:
        # Real-world shape: newest attempt failed, previous one serving.
        history = [
            {"id": "d3", "status": "FAILED", "createdAt": "2026-04-24T14:00:00Z"},
            {"id": "d2", "status": "SUCCESS", "createdAt": "2026-04-23T14:00:00Z"},
            {"id": "d1", "status": "SUCCESS", "createdAt": "2026-04-22T14:00:00Z"},
        ]
        active, latest = ro.classify_deploys(history)
        self.assertEqual(latest["id"], "d3")
        self.assertEqual(active["id"], "d2")

    def test_active_equals_latest_when_newest_succeeded(self) -> None:
        history = [
            {"id": "d3", "status": "SUCCESS", "createdAt": "t3"},
            {"id": "d2", "status": "FAILED", "createdAt": "t2"},
        ]
        active, latest = ro.classify_deploys(history)
        self.assertEqual(active["id"], latest["id"])
        self.assertEqual(latest["id"], "d3")

    def test_active_is_none_when_nothing_ever_succeeded(self) -> None:
        history = [
            {"id": "d2", "status": "FAILED"},
            {"id": "d1", "status": "CRASHED"},
        ]
        active, latest = ro.classify_deploys(history)
        self.assertIsNone(active)
        self.assertEqual(latest["id"], "d2")


class TestSlimDeploy(unittest.TestCase):
    def test_extracts_commit_meta(self) -> None:
        node = {
            "id": "d1",
            "status": "SUCCESS",
            "createdAt": "2026-04-23T14:00:00Z",
            "updatedAt": "2026-04-23T14:01:00Z",
            "staticUrl": "https://example.up.railway.app",
            "meta": {
                "commitHash": "abc123def",
                "commitMessage": "feat: add thing\n\nlong body here",
                "prNumber": 639,
                "branch": "main",
            },
        }
        slim = ro._slim_deploy(node)
        self.assertEqual(slim["id"], "d1")
        self.assertEqual(slim["status"], "SUCCESS")
        self.assertEqual(slim["commitSha"], "abc123def")
        self.assertIn("feat: add thing", slim["commitMessage"])
        self.assertEqual(slim["prNumber"], 639)
        self.assertEqual(slim["branch"], "main")

    def test_none_passthrough(self) -> None:
        self.assertIsNone(ro._slim_deploy(None))

    def test_empty_meta_gives_none_fields(self) -> None:
        slim = ro._slim_deploy({"id": "d1", "status": "SUCCESS"})
        self.assertIsNone(slim["commitSha"])
        self.assertIsNone(slim["commitMessage"])
        self.assertIsNone(slim["prNumber"])


class TestResolveEnvName(unittest.TestCase):
    """--env prod should resolve to 'production' without error."""

    status = {
        "environments": {
            "edges": [
                {"node": {"id": "e-prod", "name": "production"}},
                {"node": {"id": "e-stage", "name": "staging"}},
                {"node": {"id": "e-dev", "name": "development"}},
            ]
        }
    }

    def test_exact_match(self) -> None:
        self.assertEqual(ro._resolve_env_name("production", self.status), "production")

    def test_prefix_match_short_form(self) -> None:
        self.assertEqual(ro._resolve_env_name("prod", self.status), "production")
        self.assertEqual(ro._resolve_env_name("stag", self.status), "staging")
        self.assertEqual(ro._resolve_env_name("dev", self.status), "development")

    def test_case_insensitive(self) -> None:
        self.assertEqual(ro._resolve_env_name("PROD", self.status), "production")
        self.assertEqual(ro._resolve_env_name("Staging", self.status), "staging")

    def test_none_passthrough(self) -> None:
        self.assertIsNone(ro._resolve_env_name(None, self.status))

    def test_unknown_env_passthrough(self) -> None:
        # No match — pass through unchanged so the CLI produces its natural error
        self.assertEqual(ro._resolve_env_name("preview", self.status), "preview")

    def test_ambiguous_raises(self) -> None:
        status = {"environments": {"edges": [
            {"node": {"id": "e1", "name": "production"}},
            {"node": {"id": "e2", "name": "production-canary"}},
        ]}}
        with self.assertRaises(SystemExit):
            ro._resolve_env_name("prod", status)

    def test_empty_status_passthrough(self) -> None:
        self.assertEqual(ro._resolve_env_name("prod", {}), "prod")


class TestResolveEnvironmentId(unittest.TestCase):
    def test_matches_env_name_case_insensitive(self) -> None:
        status = {
            "environments": {
                "edges": [
                    {"node": {"id": "env-prod", "name": "production"}},
                    {"node": {"id": "env-staging", "name": "staging"}},
                ]
            }
        }
        self.assertEqual(ro._resolve_environment_id(status, "Production"), "env-prod")
        self.assertEqual(ro._resolve_environment_id(status, "staging"), "env-staging")

    def test_returns_none_when_env_missing(self) -> None:
        status = {"environments": {"edges": []}}
        self.assertIsNone(ro._resolve_environment_id(status, "production"))

    def test_returns_none_when_env_name_is_none(self) -> None:
        self.assertIsNone(ro._resolve_environment_id({}, None))


class TestFetchDeployHistoryNoToken(unittest.TestCase):
    """Without a token the GraphQL path must silently return None so callers
    fall back to the CLI-only shape. No network calls, no raises."""

    def test_no_token_returns_none(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(ro.fetch_deploy_history("svc-1", "env-1"))

    def test_empty_service_id_returns_none(self) -> None:
        with patch.dict("os.environ", {"RAILWAY_API_TOKEN": "fake"}):
            self.assertIsNone(ro.fetch_deploy_history(None, "env-1"))
            self.assertIsNone(ro.fetch_deploy_history("", "env-1"))


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

        expected_keys = {"project", "projectId", "env", "since", "filter",
                         "railwayApiTokenConfigured", "summary", "services"}
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
            "name", "id", "status", "stopped", "activeDeploy", "latestDeploy",
            "errors", "warnings", "errorTotal", "warningTotal",
            "errorKinds", "warningKinds", "truncated", "envVarNames",
        }
        self.assertEqual(set(snap.keys()), expected_service_keys,
                         "service snapshot keys must remain stable")
        self.assertIsInstance(snap["latestDeploy"], dict)
        self.assertEqual(set(snap["latestDeploy"].keys()), {
            "id", "status", "createdAt", "updatedAt", "staticUrl",
            "commitSha", "commitMessage", "prNumber", "branch",
        })
        # activeDeploy is None when GraphQL enrichment isn't available
        # (no RAILWAY_API_TOKEN in tests); traffic info simply isn't known.
        self.assertIsNone(snap["activeDeploy"])

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

    def test_output_flag_defaults_to_none(self) -> None:
        parser = ro.build_parser()
        args = parser.parse_args(["status"])
        self.assertIsNone(args.output)

    def test_output_flag_parses(self) -> None:
        parser = ro.build_parser()
        args = parser.parse_args(["build-logs", "api", "--output", "/tmp/x.json"])
        self.assertEqual(args.output, "/tmp/x.json")


class TestWriteOutputFile(unittest.TestCase):
    """The --output envelope shape is part of the public contract."""

    def _write(self, payload: dict) -> tuple[dict, Path]:
        import tempfile
        td = Path(tempfile.mkdtemp())
        out = td / "nested" / "payload.json"
        summary = ro._write_output_file(payload, str(out))
        return summary, out

    def test_writes_file_with_full_payload(self) -> None:
        payload = {"tool": {"name": "x"}, "service": "api",
                   "lines": ["a", "b", "c"]}
        summary, path = self._write(payload)
        self.assertTrue(path.exists())
        # Full payload round-trips through disk.
        self.assertEqual(json.loads(path.read_text("utf-8")), payload)
        self.assertEqual(summary["savedTo"], str(path.resolve()))

    def test_envelope_reports_bytes_and_keys(self) -> None:
        payload = {"tool": {}, "service": "api", "env": "prod", "lineCount": 0}
        summary, _ = self._write(payload)
        self.assertGreater(summary["bytes"], 0)
        self.assertEqual(set(summary["payloadKeys"]),
                         {"service", "env", "lineCount"})
        self.assertNotIn("tool", summary["payloadKeys"])

    def test_log_payload_gets_head_and_tail_preview(self) -> None:
        payload = {"tool": {}, "lines": [f"line {i}" for i in range(100)]}
        summary, _ = self._write(payload)
        self.assertIn("preview", summary)
        self.assertEqual(summary["preview"]["totalLines"], 100)
        self.assertEqual(summary["preview"]["head"][0], "line 0")
        self.assertEqual(summary["preview"]["tail"][-1], "line 99")

    def test_short_log_payload_omits_tail(self) -> None:
        # When len(lines) <= 2 * preview_n (20), tail is redundant with head.
        payload = {"tool": {}, "lines": ["a", "b", "c"]}
        summary, _ = self._write(payload)
        self.assertEqual(summary["preview"]["tail"], [])

    def test_non_log_payload_has_no_preview(self) -> None:
        payload = {"tool": {}, "services": [{"name": "x"}]}
        summary, _ = self._write(payload)
        self.assertNotIn("preview", summary)

    def test_creates_parent_directories(self) -> None:
        payload = {"tool": {}, "k": "v"}
        _, path = self._write(payload)
        # _write() put the file under `nested/` which didn't exist — mkdir -p.
        self.assertTrue(path.parent.is_dir())


if __name__ == "__main__":
    unittest.main()
