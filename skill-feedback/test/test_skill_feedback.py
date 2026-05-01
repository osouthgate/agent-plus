"""Unit tests for skill-feedback. Stdlib unittest only — no pytest, no network."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "skill-feedback"
    loader = SourceFileLoader("skill_feedback", str(bin_path))
    spec = importlib.util.spec_from_loader("skill_feedback", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


sf = _load_module()
BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-feedback"


def _run(*args: str, env: dict | None = None) -> tuple[int, str, str]:
    """Invoke the CLI as a subprocess. Returns (returncode, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True,
        env={**os.environ, **(env or {})},
        timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ──────────────────────────── secret scrubbing ────────────────────────────


class TestScrubText(unittest.TestCase):
    """The privacy contract: free-text fields must never carry token-shaped
    substrings to disk. This is the canary test — if it fails, we leak.

    Canaries are constructed via string concatenation rather than literals
    so GitHub's push-protection / TruffleHog / etc. don't false-positive
    on the test file. The runtime joined value is identical to a real
    token shape and exercises the real regex."""

    # Concatenated at runtime → identical to a single literal, but the file
    # contents never contain a complete recognisable token.
    canaries = {
        "github_classic": "ghp_" + "abcdefghij1234567890ABCD",
        "github_fine":    "github_" + "pat_abc123def456ghi789jklmnoPQR",
        "github_oauth":   "gho_" + "abcdefghij1234567890ABCD",
        "github_app":     "ghs_" + "abcdefghij1234567890ABCD",
        "aws":            "AKIA" + "1234567890ABCDEF",
        "anthropic":      "sk-ant-" + "abcdefghij1234567890ABCD",
        "langfuse_pub":   "pk-lf-" + "abcdefghij1234567890ABCD",
        "langfuse_sec":   "sk-lf-" + "abcdefghij1234567890ABCD",
        "openai":         "sk-" + "abcdefghij1234567890ABCD",
        "stripe_live":    "sk" + "_live_" + "abcdefghij1234567890ABCD",
        "stripe_test":    "sk" + "_test_" + "abcdefghij1234567890ABCD",
        "stripe_pub":     "pk" + "_live_" + "abcdefghij1234567890ABCD",
        "stripe_restricted": "rk" + "_live_" + "abcdefghij1234567890ABCD",
        "supabase":       "sbp_" + "abcdefghij1234567890ABCD",
        "sentry":         "sntrys_" + "abcdefghij1234567890ABCD",
        "google_api":     "AIza" + "SyA-1234567890abcdefghij_klmnopqrstuv",
        "slack_bot":      "xoxb-" + "1234567890-1234567890-abcdefghij",
        "slack_user":     "xoxp-" + "1234567890-1234567890-abcdefghij",
        "discord_bot":    "MTAxMjM0NTY3ODkwMTIzNDU2" + ".GabcDe." + "fghijklmnopqrstuvwxyz1234567890ABCDEF",
        "jwt":            "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "bearer":         "Bearer " + "abcdefghij1234567890ABCD",
        "auth_header":    "Authorization: " + "Token abc123def456",
    }

    def test_each_canary_redacted(self) -> None:
        for label, secret in self.canaries.items():
            blob = f"prefix {secret} suffix"
            out = sf._scrub_text(blob)
            self.assertIsNotNone(out)
            self.assertNotIn(secret, out, f"{label}: {secret!r} survived scrub")

    def test_scrub_is_idempotent(self) -> None:
        s = "ghp_abcdefghij1234567890ABCD AKIA1234567890ABCDEF"
        once = sf._scrub_text(s)
        twice = sf._scrub_text(once)
        self.assertEqual(once, twice)

    def test_none_passthrough(self) -> None:
        self.assertIsNone(sf._scrub_text(None))

    def test_empty_string_passthrough(self) -> None:
        self.assertEqual(sf._scrub_text(""), "")

    def test_clean_text_unchanged(self) -> None:
        s = "no streaming chat support; fell back to curl"
        self.assertEqual(sf._scrub_text(s), s)


class TestScrubLogIntegration(unittest.TestCase):
    """End-to-end: secrets passed via --note / --friction must not appear
    in the on-disk JSONL, in `show`, in `report`, or in `submit` output."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"SKILL_FEEDBACK_DIR": self.tmp.name}
        self.canary = "ghp_canarytoken1234567890XYZ"

    def test_canary_absent_from_jsonl_show_report_submit(self) -> None:
        rc, _, err = _run(
            "log", "myskill",
            "--rating", "3", "--outcome", "partial",
            "--friction", f"leak {self.canary} test",
            "--note", f"another {self.canary} here",
            env=self.env,
        )
        self.assertEqual(rc, 0, err)

        # On-disk file
        log = Path(self.tmp.name) / "myskill.jsonl"
        self.assertTrue(log.is_file())
        on_disk = log.read_text(encoding="utf-8")
        self.assertNotIn(self.canary, on_disk, "canary leaked to JSONL on disk")

        # show / report / submit (dry-run) output paths
        for cmd_args in (
            ["show", "myskill"],
            ["report"],
            ["submit", "myskill", "--repo", "owner/name"],
        ):
            rc, out, err = _run(*cmd_args, env=self.env)
            self.assertEqual(rc, 0, f"{cmd_args} failed: {err}")
            combined = out + err
            self.assertNotIn(self.canary, combined,
                             f"canary leaked through {cmd_args!r}: {combined!r}")


# ──────────────────────────── skill-name validation ────────────────────────────


class TestSkillNameValidation(unittest.TestCase):
    def test_valid_names(self) -> None:
        for ok in ("foo", "foo-bar", "foo.bar", "foo_bar", "f00", "a"):
            self.assertEqual(sf._validate_skill_name(ok), ok)

    def test_path_traversal_rejected(self) -> None:
        for bad in ("../etc/passwd", "..", "../foo", "foo/bar", "foo\\bar",
                    "foo bar", "", "foo$bar"):
            with self.assertRaises(SystemExit, msg=f"{bad!r} should reject"):
                sf._validate_skill_name(bad)

    def test_long_name_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            sf._validate_skill_name("a" * 200)

    def test_null_byte_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            sf._validate_skill_name("foo\x00bar")


# ──────────────────────────── storage precedence ────────────────────────────


class TestStorageRoot(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Run inside an isolated dir that is NOT a git repo by default.
        self.cwd = Path(self.tmp.name) / "work"
        self.cwd.mkdir()

    def test_env_override_wins(self) -> None:
        target = Path(self.tmp.name) / "envroot"
        env = {"SKILL_FEEDBACK_DIR": str(target)}
        rc, out, _ = _run("path", env=env)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["source"], "env")
        self.assertEqual(Path(data["storage_root"]), target.resolve())

    def test_home_fallback_when_no_git_no_marker(self) -> None:
        # Use a fake HOME so we don't touch the user's real home dir.
        fake_home = Path(self.tmp.name) / "home"
        fake_home.mkdir()
        # HOME for POSIX, USERPROFILE for Windows (Path.home() reads USERPROFILE on win32).
        env = {"HOME": str(fake_home), "USERPROFILE": str(fake_home)}
        # Run from a non-git, no-marker directory.
        proc = subprocess.run(
            [sys.executable, str(BIN), "path"],
            capture_output=True, text=True, cwd=str(self.cwd),
            env={k: v for k, v in os.environ.items() if k != "SKILL_FEEDBACK_DIR"} | env,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertIn(data["source"], ("git", "cwd", "home"))
        # If the test runner itself is in a git repo, source will be 'git' and
        # the root will live under that repo. Otherwise we expect home.
        if data["source"] == "home":
            self.assertEqual(
                Path(data["storage_root"]),
                (fake_home / ".agent-plus" / "skill-feedback").resolve(),
            )


# ──────────────────────────── schema round-trip ────────────────────────────


class TestSchemaRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"SKILL_FEEDBACK_DIR": self.tmp.name}

    def test_log_then_show_returns_same_entry(self) -> None:
        rc, out, err = _run(
            "log", "roundtrip",
            "--rating", "4", "--outcome", "success",
            "--friction", "missing flag",
            "--tool-version", "1.2.3",
            env=self.env,
        )
        self.assertEqual(rc, 0, err)
        logged = json.loads(out)["logged"]
        self.assertEqual(logged["schema"], 1)
        self.assertEqual(logged["rating"], 4)
        self.assertEqual(logged["outcome"], "success")
        self.assertEqual(logged["friction"], "missing flag")
        self.assertEqual(logged["tool_version"], "1.2.3")

        rc, out, err = _run("show", "roundtrip", env=self.env)
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertEqual(data["count"], 1)
        e = data["entries"][0]
        self.assertEqual(e["rating"], 4)
        self.assertEqual(e["outcome"], "success")
        self.assertEqual(e["friction"], "missing flag")
        self.assertEqual(e["schema"], 1)


# ──────────────────────────── repo URL parsing ────────────────────────────


class TestRepoUrlRegex(unittest.TestCase):
    """The regex must handle dots-in-name and trailing .git, otherwise
    `submit` files issues against the wrong repo."""

    cases = [
        ("https://github.com/foo/bar",            ("foo", "bar")),
        ("https://github.com/foo/bar.git",        ("foo", "bar")),
        ("https://github.com/foo/some.lib",       ("foo", "some.lib")),
        ("https://github.com/foo/some.lib.git",   ("foo", "some.lib")),
        ("git@github.com:foo/bar.git",            ("foo", "bar")),
        ("git@github.com:foo/some.lib.git",       ("foo", "some.lib")),
        ("https://github.com/foo/bar/issues",     ("foo", "bar")),
        ("https://github.com/foo/bar?ref=main",   ("foo", "bar")),
        ("https://github.com/foo/bar#section",    ("foo", "bar")),
    ]

    def test_each_case(self) -> None:
        for url, expected in self.cases:
            m = sf._REPO_URL_RE.search(url)
            self.assertIsNotNone(m, f"{url!r} did not match")
            self.assertEqual((m.group(1), m.group(2)), expected, f"{url!r} parsed wrong")


# ──────────────────────────── rating + duration ────────────────────────────


class TestRatingValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"SKILL_FEEDBACK_DIR": self.tmp.name}

    def test_rating_out_of_range_rejected(self) -> None:
        rc, _, err = _run(
            "log", "x", "--rating", "9", "--outcome", "success", env=self.env,
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("rating must be 1-5", err)

    def test_invalid_outcome_rejected_by_argparse(self) -> None:
        rc, _, err = _run(
            "log", "x", "--rating", "3", "--outcome", "maybe", env=self.env,
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("invalid choice", err)


class TestParseSince(unittest.TestCase):
    def test_units(self) -> None:
        self.assertEqual(sf._parse_since("30s").total_seconds(), 30)
        self.assertEqual(sf._parse_since("5m").total_seconds(), 300)
        self.assertEqual(sf._parse_since("2h").total_seconds(), 7200)
        self.assertEqual(sf._parse_since("7d").days, 7)

    def test_bad_inputs(self) -> None:
        for bad in ("yesterday", "5", "5x", "", "-5d", "5 d"):
            with self.assertRaises(SystemExit):
                sf._parse_since(bad)


# ──────────────────────────── submit dry-run ────────────────────────────


class TestSubmitDryRun(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"SKILL_FEEDBACK_DIR": self.tmp.name}

    def test_dry_run_does_not_call_gh(self) -> None:
        _run("log", "x", "--rating", "5", "--outcome", "success", env=self.env)
        rc, out, err = _run(
            "submit", "x", "--repo", "owner/repo",
            env=self.env,
        )
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["repo"], "owner/repo")
        self.assertIn("body", data)

    def test_no_repo_resolved_with_no_flag(self) -> None:
        # Create an isolated cwd so the dev-checkout sibling-plugin lookup
        # doesn't accidentally resolve agent-plus's repository.
        with tempfile.TemporaryDirectory() as isolated_cwd:
            _run("log", "unknownskill", "--rating", "5", "--outcome", "success",
                 env=self.env)
            proc = subprocess.run(
                [sys.executable, str(BIN), "submit", "unknownskill"],
                capture_output=True, text=True, cwd=isolated_cwd,
                env={**os.environ, **self.env}, timeout=10,
            )
            self.assertEqual(proc.returncode, 0)
            data = json.loads(proc.stdout)
            self.assertTrue(data["dry_run"])
            self.assertIsNone(data["repo"])
            self.assertIn("note", data)


# ──────────────────────────── --version ────────────────────────────


class TestVersionFlag(unittest.TestCase):
    def test_version_prints_and_exits_zero(self) -> None:
        rc, out, _ = _run("--version")
        self.assertEqual(rc, 0)
        self.assertTrue(out.strip())  # non-empty
        self.assertNotIn("{", out)    # plain string, not JSON


# ──────────────────────────── timestamp handling (blockers 1+2) ────────────────────────────


class TestParseIso(unittest.TestCase):
    """Naive ISO strings must coerce to UTC (not return naive datetime),
    otherwise comparing them to the tz-aware cutoff in _filter_since
    raises TypeError. Empty / None inputs return None."""

    def test_aware_z(self) -> None:
        dt = sf._parse_iso("2026-04-26T21:30:00Z")
        self.assertIsNotNone(dt)
        self.assertIsNotNone(dt.tzinfo)

    def test_aware_offset(self) -> None:
        dt = sf._parse_iso("2026-04-26T21:30:00+00:00")
        self.assertIsNotNone(dt)
        self.assertIsNotNone(dt.tzinfo)

    def test_naive_coerced_to_utc(self) -> None:
        dt = sf._parse_iso("2026-04-26T21:30:00")
        self.assertIsNotNone(dt, "naive ISO must parse, not return None")
        self.assertIsNotNone(dt.tzinfo, "naive ISO must coerce to tz-aware")

    def test_none_passthrough(self) -> None:
        self.assertIsNone(sf._parse_iso(None))

    def test_empty_passthrough(self) -> None:
        self.assertIsNone(sf._parse_iso(""))

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(sf._parse_iso("yesterday"))
        self.assertIsNone(sf._parse_iso("2026-13-99T99:99:99Z"))


class TestFilterSinceCorrectness(unittest.TestCase):
    """Blocker: previously _filter_since KEPT entries with unparseable ts
    (predicate was `if ts is None or ts >= cutoff`). Now drops them, so
    aggregates aren't skewed by hand-edited entries."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"SKILL_FEEDBACK_DIR": self.tmp.name}

    def _write_jsonl(self, skill: str, lines: list[dict]) -> Path:
        p = Path(self.tmp.name) / f"{skill}.jsonl"
        with p.open("w", encoding="utf-8") as fh:
            for ln in lines:
                fh.write(json.dumps(ln) + "\n")
        return p

    def test_malformed_ts_dropped_by_since(self) -> None:
        # Mix: one fresh-and-valid, one malformed-ts, one ancient.
        self._write_jsonl("x", [
            {"ts": sf._now_iso(), "skill": "x", "rating": 5, "outcome": "success"},
            {"ts": "not-a-timestamp", "skill": "x", "rating": 3, "outcome": "partial"},
            {"ts": "2000-01-01T00:00:00Z", "skill": "x", "rating": 1, "outcome": "failure"},
        ])
        rc, out, err = _run("show", "x", "--since", "1d", env=self.env)
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertEqual(data["count"], 1, f"expected only the fresh entry: {data}")
        self.assertEqual(data["entries"][0]["rating"], 5)

    def test_naive_ts_does_not_crash_show(self) -> None:
        # Blocker: naive ts vs aware cutoff used to raise TypeError.
        self._write_jsonl("x", [
            {"ts": sf._now_iso(), "skill": "x", "rating": 5, "outcome": "success"},
            {"ts": "2030-01-01T00:00:00", "skill": "x", "rating": 4, "outcome": "success"},
        ])
        rc, out, err = _run("show", "x", "--since", "365d", env=self.env)
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        # The fresh entry is always in window. The 2030 entry is in window
        # while tests run within ~365d of 2030 — assertion stays >= 1 so
        # the test stays stable past 2031. The point of the test is that
        # the naive ts doesn't raise TypeError, not the count.
        self.assertGreaterEqual(data["count"], 1)
        self.assertNotIn("TypeError", err)

    def test_no_since_returns_all_including_malformed(self) -> None:
        # Without --since, malformed ts is irrelevant — all entries are kept.
        self._write_jsonl("x", [
            {"ts": "garbage", "skill": "x", "rating": 5, "outcome": "success"},
            {"ts": sf._now_iso(), "skill": "x", "rating": 3, "outcome": "partial"},
        ])
        rc, out, err = _run("show", "x", env=self.env)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["count"], 2)


# ──────────────────────────── empty-submit guard (blocker 3) ────────────────────────────


class TestSubmitEmptyGuard(unittest.TestCase):
    """Blocker: --no-dry-run with 0 entries used to file a 'No entries…'
    issue on the upstream repo. Now refuses with an explanatory error."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"SKILL_FEEDBACK_DIR": self.tmp.name}

    def test_no_dry_run_with_zero_entries_does_not_call_gh(self) -> None:
        # Pre-condition: storage root exists but has no entries for 'empty'.
        # Use --no-dry-run + a resolvable repo. The guard must fire BEFORE gh.
        rc, out, err = _run(
            "submit", "empty", "--repo", "owner/name", "--no-dry-run",
            env=self.env,
        )
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertEqual(data["entries_included"], 0)
        self.assertIn("error", data, "guard should populate result['error']")
        self.assertNotIn("issue_url", data,
                         "must not have shelled to gh on empty submit")

    def test_dry_run_with_zero_entries_still_prints_body(self) -> None:
        rc, out, err = _run(
            "submit", "empty", "--repo", "owner/name",
            env=self.env,
        )
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertTrue(data["dry_run"])
        self.assertIn("body", data)
        # 'error' should NOT be set on the dry-run path — that's preview.
        self.assertNotIn("error", data)


# ──────────────────────────── tightened skill-name (trailing chars) ────────────────────────────


class TestSkillNameTrailing(unittest.TestCase):
    def test_trailing_dot_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            sf._validate_skill_name("foo.")

    def test_trailing_hyphen_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            sf._validate_skill_name("foo-")

    def test_trailing_underscore_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            sf._validate_skill_name("foo_")

    def test_single_char_alphanumeric_allowed(self) -> None:
        self.assertEqual(sf._validate_skill_name("a"), "a")
        self.assertEqual(sf._validate_skill_name("5"), "5")

    def test_dots_in_middle_still_allowed(self) -> None:
        self.assertEqual(sf._validate_skill_name("foo.bar"), "foo.bar")
        self.assertEqual(sf._validate_skill_name("a.b.c"), "a.b.c")


# ──────────────────────────── repo resolution (homepage dropped) ────────────────────────────


class TestRepoResolutionNoHomepageFallback(unittest.TestCase):
    """The `homepage` field is not used as a fallback for `repository`.
    A skill author who set `homepage` to their personal site shouldn't have
    `submit` quietly file issues there."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_repository_field_resolves(self) -> None:
        # Build a fake plugin tree under <tmp>/<skill>/.claude-plugin/plugin.json
        # and point _resolve_repo_from_plugin at it via monkeypatching.
        skill_dir = Path(self.tmp.name) / "myskill" / ".claude-plugin"
        skill_dir.mkdir(parents=True)
        (skill_dir / "plugin.json").write_text(json.dumps({
            "name": "myskill",
            "repository": "https://github.com/me/myskill",
        }))
        bin_path = Path(self.tmp.name) / "skill-feedback" / "bin" / "skill-feedback"
        bin_path.parent.mkdir(parents=True)
        bin_path.write_text("# stub")
        with patch.object(sf, "__file__", str(bin_path)):
            self.assertEqual(sf._resolve_repo_from_plugin("myskill"), "me/myskill")

    def test_homepage_only_does_not_resolve(self) -> None:
        # Only `homepage` is set — even if it's a github URL, we ignore it.
        skill_dir = Path(self.tmp.name) / "myskill" / ".claude-plugin"
        skill_dir.mkdir(parents=True)
        (skill_dir / "plugin.json").write_text(json.dumps({
            "name": "myskill",
            "homepage": "https://github.com/wrongowner/wrongrepo",
        }))
        bin_path = Path(self.tmp.name) / "skill-feedback" / "bin" / "skill-feedback"
        bin_path.parent.mkdir(parents=True)
        bin_path.write_text("# stub")
        # Also stub out the home-dir fallback path so we don't accidentally
        # resolve from a real installed plugin.
        fake_home = Path(self.tmp.name) / "fakehome"
        fake_home.mkdir()
        with patch.object(sf, "__file__", str(bin_path)), \
             patch.object(sf.Path, "home", classmethod(lambda cls: fake_home)):
            self.assertIsNone(sf._resolve_repo_from_plugin("myskill"))


# ──────────────────────────── --limit semantics ────────────────────────────


# ──────────────────────────── agent privacy-review fields ────────────────────────────


class TestAgentReviewFieldsOnDryRun(unittest.TestCase):
    """Regex scrubbing can't catch PII / customer names / internal identifiers.
    The agent doing the submit is the final gate, and the dry-run JSON
    surfaces that responsibility via agent_review_required + checklist
    so the agent (which reads the JSON) can't miss it."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"SKILL_FEEDBACK_DIR": self.tmp.name}
        _run("log", "x", "--rating", "5", "--outcome", "success",
             "--note", "regular feedback note",
             env=self.env)

    def test_dry_run_exposes_review_fields(self) -> None:
        rc, out, err = _run(
            "submit", "x", "--repo", "owner/name",
            env=self.env,
        )
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertTrue(data["dry_run"])
        self.assertIs(data["agent_review_required"], True,
                      "dry-run must flag the review responsibility")
        self.assertIn("agent_review_instructions", data)
        self.assertIn("ABORT", data["agent_review_instructions"],
                      "instructions should be unambiguous about aborting")
        self.assertIn("agent_review_checklist", data)
        # The checklist must mention the categories the regex can't catch.
        joined = " ".join(data["agent_review_checklist"]).lower()
        for category in ("pii", "customer", "hostname", "ticket"):
            self.assertIn(category, joined,
                          f"checklist missing category: {category}")

    def test_no_dry_run_with_zero_entries_does_not_expose_review_fields(self) -> None:
        # Empty-submit guard fires BEFORE the live submit, so even with
        # --no-dry-run + 0 entries we don't surface review fields (the
        # body never reaches a tracker, so review is moot).
        empty_env = {"SKILL_FEEDBACK_DIR": tempfile.mkdtemp()}
        self.addCleanup(lambda: shutil.rmtree(empty_env["SKILL_FEEDBACK_DIR"], ignore_errors=True))
        rc, out, _ = _run(
            "submit", "empty", "--repo", "owner/name", "--no-dry-run",
            env=empty_env,
        )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertFalse(data["dry_run"])
        self.assertNotIn("agent_review_required", data,
                         "review fields should be dry-run-only")
        self.assertNotIn("agent_review_checklist", data)

    def test_dry_run_with_zero_entries_still_exposes_review_fields(self) -> None:
        # Even with no entries, dry-run is a preview — the agent reading
        # an empty body should still see the review-required flag, so it
        # learns the habit. Doesn't cost anything; doesn't trigger gh.
        empty_env = {"SKILL_FEEDBACK_DIR": tempfile.mkdtemp()}
        self.addCleanup(lambda: shutil.rmtree(empty_env["SKILL_FEEDBACK_DIR"], ignore_errors=True))
        rc, out, _ = _run(
            "submit", "empty", "--repo", "owner/name",
            env=empty_env,
        )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["entries_included"], 0)
        self.assertIs(data["agent_review_required"], True)


class TestLimitZeroSemantics(unittest.TestCase):
    """--limit 0 means unbounded (documented in --help)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"SKILL_FEEDBACK_DIR": self.tmp.name}
        for i in range(7):
            _run("log", "x", "--rating", "5", "--outcome", "success",
                 env=self.env)

    def test_show_limit_zero_returns_all(self) -> None:
        rc, out, err = _run("show", "x", "--limit", "0", env=self.env)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["count"], 7)

    def test_show_default_limit(self) -> None:
        rc, out, _ = _run("show", "x", env=self.env)
        self.assertEqual(rc, 0)
        # default 50 is well above 7
        self.assertEqual(json.loads(out)["count"], 7)


# ──────────────── provenance-aware feedback / submit (v0.4.0) ────────────────


def _fake_proc(stdout: str, returncode: int = 0):
    """Build a minimal CompletedProcess-like object for subprocess.run mocks."""
    class _P:
        pass
    p = _P()
    p.stdout = stdout
    p.stderr = ""
    p.returncode = returncode
    return p


class TestProvenanceAwareFeedback(unittest.TestCase):
    """`skill-feedback feedback <name>` consults `skill-plus where` and emits
    a tier-appropriate recommended action. We mock subprocess.run + which so
    skill-plus is never actually invoked."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Pre-populate one log entry so feedback_summary has something to show.
        env = {"SKILL_FEEDBACK_DIR": self.tmp.name}
        _run("log", "demo", "--rating", "4", "--outcome", "success",
             "--friction", "minor", env=env)
        self.env_dir = self.tmp.name

    def _patch_skill_plus(self, where_payload: dict, available: bool = True):
        """Returns a context-manager-style tuple of patches to enter."""
        def fake_which(name):
            if name == "skill-plus":
                return "/fake/bin/skill-plus" if available else None
            return shutil.which(name)

        def fake_run(cmd, *a, **kw):
            if isinstance(cmd, list) and cmd and cmd[0] == "/fake/bin/skill-plus":
                return _fake_proc(json.dumps(where_payload))
            return subprocess.run(cmd, *a, **kw)

        return (
            patch.object(sf.shutil, "which", side_effect=fake_which),
            patch.object(sf.subprocess, "run", side_effect=fake_run),
        )

    def test_feedback_subcommand_project_tier(self) -> None:
        project_dir = "/repo/.claude/skills/demo"
        payload = {
            "verdict": "found",
            "name": "demo",
            "locations": [{"scope": "project", "path": project_dir}],
            "resolution_hint": "project",
            "collision": False,
        }
        p1, p2 = self._patch_skill_plus(payload)
        with p1, p2:
            args = argparse.Namespace(skill="demo", since="30d")
            os.environ["SKILL_FEEDBACK_DIR"] = self.env_dir
            try:
                result = sf.cmd_feedback(args)
            finally:
                os.environ.pop("SKILL_FEEDBACK_DIR", None)
        self.assertEqual(result["provenance"]["tier"], "project")
        self.assertEqual(result["recommended_action"]["kind"], "edit")
        self.assertIn("SKILL.md", result["provenance"]["edit_hint"])
        # Path() may normalise separators (\ on Windows); compare via Path.
        eh = Path(result["provenance"]["edit_hint"])
        self.assertEqual(eh.parent, Path(project_dir))

    def test_feedback_subcommand_plugin_tier(self) -> None:
        # Build a fake plugin cache with a plugin.json that declares a repo.
        tmp_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))
        plug = Path(tmp_root) / "vercel-remote"
        skill_dir = plug / "skills" / "vercel-deploy"
        skill_dir.mkdir(parents=True)
        (plug / ".claude-plugin").mkdir()
        (plug / ".claude-plugin" / "plugin.json").write_text(json.dumps({
            "name": "vercel-remote",
            "version": "0.4.3",
            "repository": "https://github.com/osouthgate/agent-plus-skills",
        }))
        payload = {
            "verdict": "found",
            "name": "vercel-deploy",
            "locations": [{
                "scope": "plugin",
                "path": str(skill_dir),
                "plugin": "vercel-remote",
                "version": "0.4.3",
            }],
            "resolution_hint": "plugin",
            "collision": False,
        }
        p1, p2 = self._patch_skill_plus(payload)
        with p1, p2:
            args = argparse.Namespace(skill="vercel-deploy", since="30d")
            os.environ["SKILL_FEEDBACK_DIR"] = self.env_dir
            try:
                result = sf.cmd_feedback(args)
            finally:
                os.environ.pop("SKILL_FEEDBACK_DIR", None)
        self.assertEqual(result["provenance"]["tier"], "plugin")
        self.assertEqual(result["provenance"]["marketplace_repo"],
                         "osouthgate/agent-plus-skills")
        self.assertEqual(result["recommended_action"]["kind"], "submit")

    def test_feedback_subcommand_ambiguous_tier(self) -> None:
        payload = {
            "verdict": "found",
            "name": "deploy",
            "locations": [
                {"scope": "project", "path": "/repo/.claude/skills/deploy"},
                {"scope": "global", "path": "/home/u/.claude/skills/deploy"},
            ],
            "resolution_hint": "project",
            "collision": True,
        }
        p1, p2 = self._patch_skill_plus(payload)
        with p1, p2:
            args = argparse.Namespace(skill="deploy", since="30d")
            os.environ["SKILL_FEEDBACK_DIR"] = self.env_dir
            try:
                result = sf.cmd_feedback(args)
            finally:
                os.environ.pop("SKILL_FEEDBACK_DIR", None)
        self.assertEqual(result["provenance"]["tier"], "ambiguous")
        self.assertTrue(result["provenance"]["collision"])
        self.assertEqual(result["recommended_action"]["kind"],
                         "resolve_collision")

    def test_feedback_subcommand_unknown_tier_skill_plus_unavailable(self) -> None:
        # skill-plus not on PATH → tier=unknown + helpful install message.
        p1, p2 = self._patch_skill_plus({}, available=False)
        with p1, p2:
            args = argparse.Namespace(skill="demo", since="30d")
            os.environ["SKILL_FEEDBACK_DIR"] = self.env_dir
            try:
                result = sf.cmd_feedback(args)
            finally:
                os.environ.pop("SKILL_FEEDBACK_DIR", None)
        self.assertEqual(result["provenance"]["tier"], "unknown")
        self.assertEqual(result["recommended_action"]["kind"], "no_action")
        explanation = result["recommended_action"]["explanation"].lower()
        self.assertIn("skill-plus", explanation)


class TestSubmitProvenanceAware(unittest.TestCase):
    """`submit` without --repo now consults `skill-plus where` and refuses
    project/global skills with an actionable edit hint, while plugin-tier
    skills auto-resolve their marketplace repo."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_dir = self.tmp.name
        env = {"SKILL_FEEDBACK_DIR": self.env_dir}
        _run("log", "myskill", "--rating", "5", "--outcome", "success",
             env=env)

    def _patch_skill_plus(self, where_payload: dict):
        def fake_which(name):
            if name == "skill-plus":
                return "/fake/bin/skill-plus"
            return shutil.which(name)

        def fake_run(cmd, *a, **kw):
            if isinstance(cmd, list) and cmd and cmd[0] == "/fake/bin/skill-plus":
                return _fake_proc(json.dumps(where_payload))
            return subprocess.run(cmd, *a, **kw)

        return (
            patch.object(sf.shutil, "which", side_effect=fake_which),
            patch.object(sf.subprocess, "run", side_effect=fake_run),
        )

    def test_submit_refuses_on_project_skill_with_edit_hint(self) -> None:
        proj_dir = "/repo/.claude/skills/myskill"
        payload = {
            "verdict": "found",
            "name": "myskill",
            "locations": [{"scope": "project", "path": proj_dir}],
            "resolution_hint": "project",
            "collision": False,
        }
        p1, p2 = self._patch_skill_plus(payload)
        with p1, p2:
            args = argparse.Namespace(
                skill="myskill", since="30d", repo=None, dry_run=True,
            )
            os.environ["SKILL_FEEDBACK_DIR"] = self.env_dir
            try:
                result = sf.cmd_submit(args)
            finally:
                os.environ.pop("SKILL_FEEDBACK_DIR", None)
        self.assertIn("error", result)
        self.assertIn("project", result["error"].lower())
        self.assertIn("edit_hint", result)
        self.assertIn("SKILL.md", result["edit_hint"])
        self.assertIsNone(result["repo"])
        self.assertNotIn("issue_url", result)

    def test_submit_uses_provenance_marketplace_repo_when_no_explicit_repo(self) -> None:
        tmp_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))
        plug = Path(tmp_root) / "some-plugin"
        skill_dir = plug / "skills" / "myskill"
        skill_dir.mkdir(parents=True)
        (plug / ".claude-plugin").mkdir()
        (plug / ".claude-plugin" / "plugin.json").write_text(json.dumps({
            "name": "some-plugin",
            "repository": "https://github.com/acme/widgets.git",
        }))
        payload = {
            "verdict": "found",
            "name": "myskill",
            "locations": [{
                "scope": "plugin",
                "path": str(skill_dir),
                "plugin": "some-plugin",
            }],
            "resolution_hint": "plugin",
            "collision": False,
        }
        p1, p2 = self._patch_skill_plus(payload)
        with p1, p2:
            args = argparse.Namespace(
                skill="myskill", since="30d", repo=None, dry_run=True,
            )
            os.environ["SKILL_FEEDBACK_DIR"] = self.env_dir
            try:
                result = sf.cmd_submit(args)
            finally:
                os.environ.pop("SKILL_FEEDBACK_DIR", None)
        self.assertEqual(result["repo"], "acme/widgets")
        self.assertNotIn("error", result)
        self.assertEqual(result["provenance"]["tier"], "plugin")


if __name__ == "__main__":
    unittest.main()
