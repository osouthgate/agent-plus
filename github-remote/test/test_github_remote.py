"""Unit tests for github-remote. Stdlib unittest only — no pytest, no GitHub account."""

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
    bin_path = here.parent.parent / "bin" / "github-remote"
    loader = SourceFileLoader("github_remote", str(bin_path))
    spec = importlib.util.spec_from_loader("github_remote", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


gr = _load_module()


# ──────────────────────────── _scrub ────────────────────────────


class TestScrub(unittest.TestCase):
    def test_strips_top_level_secrets(self) -> None:
        obj = {
            "id": "x",
            "token": "ghp_abc",
            "password": "hunter2",
            "authorization": "Bearer xyz",
            "client_secret": "sec",
            "private_key": "BEGIN KEY",
            "webhook_url_with_secret": "https://example.com?s=abc",
        }
        out = gr._scrub(obj)
        self.assertEqual(out["id"], "x")
        for k in ("token", "password", "authorization", "client_secret", "private_key", "webhook_url_with_secret"):
            self.assertEqual(out[k], "[REDACTED]", f"{k} not redacted")

    def test_strips_nested_secrets(self) -> None:
        obj = {
            "meta": {"sha": "abc123", "token": "ghp_xxx"},
            "auth": {"password": "super-secret", "enabled": True},
        }
        out = gr._scrub(obj)
        self.assertEqual(out["meta"]["sha"], "abc123")
        self.assertEqual(out["meta"]["token"], "[REDACTED]")
        self.assertEqual(out["auth"]["password"], "[REDACTED]")
        self.assertTrue(out["auth"]["enabled"])

    def test_case_insensitive_key_match(self) -> None:
        obj = {"Token": "x", "PASSWORD": "y", "Client_Secret": "z"}
        out = gr._scrub(obj)
        self.assertEqual(out["Token"], "[REDACTED]")
        self.assertEqual(out["PASSWORD"], "[REDACTED]")
        self.assertEqual(out["Client_Secret"], "[REDACTED]")

    def test_lists_walked(self) -> None:
        obj = {"items": [{"token": "a"}, {"token": "b"}, {"safe": "ok"}]}
        out = gr._scrub(obj)
        self.assertEqual(out["items"][0]["token"], "[REDACTED]")
        self.assertEqual(out["items"][1]["token"], "[REDACTED]")
        self.assertEqual(out["items"][2]["safe"], "ok")

    def test_primitives_pass_through(self) -> None:
        self.assertEqual(gr._scrub("hello"), "hello")
        self.assertEqual(gr._scrub(42), 42)
        self.assertEqual(gr._scrub(None), None)


# ──────────────────────────── _scrub_text (regex patterns) ────────────────────────────


class TestScrubText(unittest.TestCase):
    def test_ghp_classic(self) -> None:
        s = "token is ghp_" + "A" * 36 + " visible"
        self.assertNotIn("ghp_" + "A" * 36, gr._scrub_text(s))
        self.assertIn("[REDACTED]", gr._scrub_text(s))

    def test_github_pat_fine_grained(self) -> None:
        s = "pat: github_pat_" + "B" * 82
        self.assertNotIn("github_pat_" + "B" * 82, gr._scrub_text(s))

    def test_oauth_prefixes(self) -> None:
        for prefix in ("gho_", "ghu_", "ghs_", "ghr_"):
            s = f"key {prefix}{'Z' * 36} here"
            self.assertNotIn(f"{prefix}{'Z' * 36}", gr._scrub_text(s))

    def test_aws_key(self) -> None:
        s = "aws AKIAIOSFODNN7EXAMPLE thing"
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", gr._scrub_text(s))

    def test_bearer_token(self) -> None:
        s = "Authorization: Bearer abcdef1234567890abcdef1234"
        self.assertNotIn("abcdef1234567890abcdef1234", gr._scrub_text(s))

    def test_empty_passthrough(self) -> None:
        self.assertEqual(gr._scrub_text(""), "")

    def test_benign_text_passthrough(self) -> None:
        s = "hello world, no secrets here, build passed"
        self.assertEqual(gr._scrub_text(s), s)


# ──────────────────────────── canary no-leak ────────────────────────────


class TestCanaryNoLeak(unittest.TestCase):
    """A known canary secret substring must never appear in any emitted output."""

    CANARY = "CANARY_SECRET_DO_NOT_LEAK_4f2b9a"

    def _capture_emit(self, obj) -> str:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            gr.emit_json(obj, pretty=True)
        return buf.getvalue()

    def test_canary_in_token_key(self) -> None:
        obj = {"user": "alice", "token": self.CANARY}
        out = self._capture_emit(obj)
        self.assertNotIn(self.CANARY, out)
        self.assertIn("alice", out)

    def test_canary_in_password_key(self) -> None:
        obj = {"auth": {"password": self.CANARY, "enabled": True}}
        self.assertNotIn(self.CANARY, self._capture_emit(obj))

    def test_canary_in_nested_client_secret(self) -> None:
        obj = {"a": [{"b": {"client_secret": self.CANARY}}]}
        self.assertNotIn(self.CANARY, self._capture_emit(obj))

    def test_canary_in_free_text_regex_patterns(self) -> None:
        # Pattern-matched secrets in free-text logs should be scrubbed.
        fake_log = f"build failed, token leaked: ghp_{'A' * 36} and {self.CANARY}"
        # The CANARY itself isn't a matching pattern, but the ghp_ must go.
        scrubbed = gr._scrub_text(fake_log)
        self.assertNotIn(f"ghp_{'A' * 36}", scrubbed)

    def test_canary_in_run_log_lines_structure(self) -> None:
        # Simulated run_logs output shape: jobs -> lines. _scrub preserves lines
        # (not a secret key), so canary-via-regex-pattern is the vector here.
        leaked_line = f"Error: Bearer {'X' * 30}"
        obj = {"run": 1, "jobs": [{"id": 2, "name": "j", "lines": [gr._scrub_text(leaked_line)]}]}
        out = self._capture_emit(obj)
        self.assertNotIn("X" * 30, out)


# ──────────────────────────── auth precedence ────────────────────────────


class TestAuthPrecedence(unittest.TestCase):
    def test_env_var_wins(self) -> None:
        cfg = {"GITHUB_TOKEN": "from_env"}
        with patch.object(gr, "_gh_auth_token", return_value="from_gh"):
            self.assertEqual(gr.require_token(cfg), "from_env")

    def test_gh_fallback_when_env_missing(self) -> None:
        with patch.object(gr, "_gh_auth_token", return_value="from_gh"):
            self.assertEqual(gr.require_token({}), "from_gh")

    def test_both_missing_dies(self) -> None:
        with patch.object(gr, "_gh_auth_token", return_value=None):
            with self.assertRaises(SystemExit):
                gr.require_token({})

    def test_empty_env_token_falls_back(self) -> None:
        with patch.object(gr, "_gh_auth_token", return_value="from_gh"):
            self.assertEqual(gr.require_token({"GITHUB_TOKEN": "   "}), "from_gh")


# ──────────────────────────── repo resolution ────────────────────────────


class TestRepoResolve(unittest.TestCase):
    def test_flag_wins(self) -> None:
        cfg = {"GITHUB_REPO": "env/repo"}
        with patch.object(gr, "_git_remote_repo", return_value="git/remote"):
            self.assertEqual(gr.resolve_repo("flag/repo", cfg), ("flag", "repo"))

    def test_env_over_git(self) -> None:
        with patch.object(gr, "_git_remote_repo", return_value="git/remote"):
            self.assertEqual(gr.resolve_repo(None, {"GITHUB_REPO": "env/repo"}), ("env", "repo"))

    def test_git_remote_fallback(self) -> None:
        with patch.object(gr, "_git_remote_repo", return_value="git/remote"):
            self.assertEqual(gr.resolve_repo(None, {}), ("git", "remote"))

    def test_all_missing_dies(self) -> None:
        with patch.object(gr, "_git_remote_repo", return_value=None):
            with self.assertRaises(SystemExit):
                gr.resolve_repo(None, {})

    def test_invalid_format_dies(self) -> None:
        with self.assertRaises(SystemExit):
            gr.resolve_repo("noslash", {})

    def test_git_url_parser_ssh(self) -> None:
        # internal regex test — simulate the parse step
        m = gr._REPO_URL_RE.search("git@github.com:foo/bar.git")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "foo")  # type: ignore[union-attr]
        self.assertEqual(m.group(2), "bar")  # type: ignore[union-attr]

    def test_git_url_parser_https(self) -> None:
        m = gr._REPO_URL_RE.search("https://github.com/foo/bar.git")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "foo")  # type: ignore[union-attr]
        self.assertEqual(m.group(2), "bar")  # type: ignore[union-attr]


# ──────────────────────────── name resolution ambiguity ────────────────────────────


class TestPRResolveAmbiguity(unittest.TestCase):
    """Ambiguity must exit non-zero and NEVER auto-pick."""

    def _fake_prs(self):
        return [
            {"number": 1, "title": "fix auth bug", "state": "open",
             "head": {"ref": "fix/auth", "sha": "aaa"}, "base": {"ref": "main"},
             "user": {"login": "a"}, "html_url": "u1"},
            {"number": 2, "title": "fix auth again", "state": "open",
             "head": {"ref": "fix/auth-2", "sha": "bbb"}, "base": {"ref": "main"},
             "user": {"login": "b"}, "html_url": "u2"},
            {"number": 3, "title": "unrelated", "state": "open",
             "head": {"ref": "feat/x", "sha": "ccc"}, "base": {"ref": "main"},
             "user": {"login": "c"}, "html_url": "u3"},
        ]

    def test_exact_branch_unique_returns(self) -> None:
        with patch.object(gr, "_api", return_value=self._fake_prs()):
            result = gr.pr_resolve("o", "r", "fix/auth", {"GITHUB_TOKEN": "t"})
            self.assertEqual(result["number"], 1)

    def test_substring_ambiguous_exits_with_candidates(self) -> None:
        with patch.object(gr, "_api", return_value=self._fake_prs()):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as ctx:
                    gr.pr_resolve("o", "r", "fix auth", {"GITHUB_TOKEN": "t"})
            self.assertNotEqual(ctx.exception.code, 0)
            emitted = json.loads(buf.getvalue())
            self.assertEqual(emitted["error"], "ambiguous")
            self.assertEqual(len(emitted["matches"]), 2)

    def test_substring_unique_returns(self) -> None:
        with patch.object(gr, "_api", return_value=self._fake_prs()):
            result = gr.pr_resolve("o", "r", "unrelated", {"GITHUB_TOKEN": "t"})
            self.assertEqual(result["number"], 3)

    def test_no_match_exits_not_found(self) -> None:
        with patch.object(gr, "_api", return_value=self._fake_prs()):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit):
                    gr.pr_resolve("o", "r", "no-such-thing-anywhere", {"GITHUB_TOKEN": "t"})
            emitted = json.loads(buf.getvalue())
            self.assertEqual(emitted["error"], "not_found")

    def test_empty_query_dies(self) -> None:
        with self.assertRaises(SystemExit):
            gr.pr_resolve("o", "r", "", {"GITHUB_TOKEN": "t"})


class TestIssueResolveAmbiguity(unittest.TestCase):
    def _fake_issues(self):
        return [
            {"number": 10, "title": "flaky test", "state": "open",
             "user": {"login": "a"}, "assignees": [], "labels": [], "html_url": "u"},
            {"number": 11, "title": "flaky test again", "state": "open",
             "user": {"login": "b"}, "assignees": [], "labels": [], "html_url": "u"},
        ]

    def test_ambiguous_exits(self) -> None:
        with patch.object(gr, "_api", return_value=self._fake_issues()):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit):
                    gr.issue_resolve("o", "r", "flaky", {"GITHUB_TOKEN": "t"})
            self.assertEqual(json.loads(buf.getvalue())["error"], "ambiguous")

    def test_excludes_prs_from_issue_resolve(self) -> None:
        mixed = [
            {"number": 10, "title": "real issue", "state": "open",
             "user": {"login": "a"}, "assignees": [], "labels": [], "html_url": "u"},
            {"number": 11, "title": "real issue PR", "state": "open", "pull_request": {"url": "x"},
             "user": {"login": "b"}, "assignees": [], "labels": [], "html_url": "u"},
        ]
        with patch.object(gr, "_api", return_value=mixed):
            # substring 'real issue' matches both titles, but PR is filtered out → unique.
            result = gr.issue_resolve("o", "r", "real issue", {"GITHUB_TOKEN": "t"})
            self.assertEqual(result["number"], 10)


# ──────────────────────────── pr comment validation ────────────────────────────


class TestPRComment(unittest.TestCase):
    def test_empty_body_dies(self) -> None:
        with self.assertRaises(SystemExit):
            gr.pr_comment("o", "r", "1", "", {"GITHUB_TOKEN": "t"})

    def test_whitespace_body_dies(self) -> None:
        with self.assertRaises(SystemExit):
            gr.pr_comment("o", "r", "1", "   \n\t  ", {"GITHUB_TOKEN": "t"})


# ──────────────────────────── overview caps ────────────────────────────


class TestOverviewCaps(unittest.TestCase):
    def test_caps_constants(self) -> None:
        self.assertEqual(gr.OVERVIEW_REVIEWS_CAP, 10)
        self.assertEqual(gr.OVERVIEW_FAILING_JOBS_CAP, 20)
        self.assertEqual(gr.OVERVIEW_RUNS_CAP, 5)


# ──────────────────────────── is_int helper ────────────────────────────


class TestIsInt(unittest.TestCase):
    def test_numeric(self) -> None:
        self.assertTrue(gr._is_int("498"))
        self.assertTrue(gr._is_int("0"))

    def test_non_numeric(self) -> None:
        self.assertFalse(gr._is_int("feat/foo"))
        self.assertFalse(gr._is_int(""))
        self.assertFalse(gr._is_int("12a"))


# ──────────────────────────── since-parser (N/A here, but keep place) ────────────────────────────
# github-remote has no --since parser; run logs use --tail on line count.
# Left intentionally absent.


# ──────────────────────────── tool metadata ────────────────────────────


class TestToolMeta(unittest.TestCase):
    def test_injects_tool_field_on_dict(self) -> None:
        wrapped = gr._with_tool_meta({"pr": 1})
        self.assertIn("tool", wrapped)
        self.assertEqual(wrapped["tool"]["name"], "github-remote")
        self.assertIn("version", wrapped["tool"])
        # Tool field is first (dict insertion order) so agents see it up-top.
        self.assertEqual(list(wrapped.keys())[0], "tool")
        self.assertEqual(wrapped["pr"], 1)

    def test_non_dict_passes_through(self) -> None:
        self.assertEqual(gr._with_tool_meta([1, 2, 3]), [1, 2, 3])
        self.assertEqual(gr._with_tool_meta("str"), "str")
        self.assertIsNone(gr._with_tool_meta(None))

    def test_existing_tool_field_preserved(self) -> None:
        payload = {"tool": {"name": "other", "version": "9.9"}, "data": 1}
        wrapped = gr._with_tool_meta(payload)
        self.assertEqual(wrapped["tool"]["name"], "other")

    def test_plugin_version_reads_manifest(self) -> None:
        v = gr._plugin_version()
        self.assertIsInstance(v, str)
        self.assertNotEqual(v, "")


# ──────────────────────────── emit strips sentinel ────────────────────────────


class TestEmitSentinel(unittest.TestCase):
    def test_exit_nonzero_sentinel_stripped(self) -> None:
        obj = {"status": "timeout", "_exit_nonzero": True, "elapsed_s": 100}
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            gr.emit_json(obj)
        parsed = json.loads(buf.getvalue())
        self.assertNotIn("_exit_nonzero", parsed)
        self.assertEqual(parsed["status"], "timeout")


if __name__ == "__main__":
    unittest.main()
