"""Unit tests for skill-feedback. Stdlib unittest only — no pytest, no network."""

from __future__ import annotations

import importlib.util
import io
import json
import os
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
    substrings to disk. This is the canary test — if it fails, we leak."""

    canaries = {
        "github_classic": "ghp_abcdefghij1234567890ABCD",
        "github_fine":    "github_pat_abc123def456ghi789jklmnoPQR",
        "github_oauth":   "gho_abcdefghij1234567890ABCD",
        "github_app":     "ghs_abcdefghij1234567890ABCD",
        "aws":            "AKIA1234567890ABCDEF",
        "anthropic":      "sk-ant-abcdefghij1234567890ABCD",
        "langfuse_pub":   "pk-lf-abcdefghij1234567890ABCD",
        "langfuse_sec":   "sk-lf-abcdefghij1234567890ABCD",
        "openai":         "sk-abcdefghij1234567890ABCD",
        "slack_bot":      "xoxb-1234567890-1234567890-abcdefghij",
        "slack_user":     "xoxp-1234567890-1234567890-abcdefghij",
        "bearer":         "Bearer abcdefghij1234567890ABCD",
        "auth_header":    "Authorization: Token abc123def456",
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
        env = {"HOME": str(fake_home)}
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


if __name__ == "__main__":
    unittest.main()
