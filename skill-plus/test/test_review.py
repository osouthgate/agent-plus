"""Tests for `skill-plus review` (v0.18.0).

Tests follow the same pattern as test_inquire.py:
  - Load the subcommand module via spec_from_file_location (same as the bin loader).
  - Inject the minimal helper stubs that bin/skill-plus normally provides.
  - Use subprocess.run for end-to-end CLI tests.

Covers the 5 test cases from the plan:
  1. test_personas_default_set_present     -- 4 shipped persona briefs exist
  2. test_review_emits_dispatch_envelope   -- dispatch shape is correct
  3. test_synth_merges_findings_dir        -- --synth-from reads N JSON, computes verdict
  4. test_user_persona_extension           -- user persona auto-discovered
  5. test_envelope_v1_1_compat             -- synth output passes envelope contract

Stdlib unittest-compatible (also runs under pytest). No network. No subprocess
inside the module under test (Option B: we never spawn sub-agents). Clean-env
safe: no leaking maintainer env vars required.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# ─── module loading ──────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "skill-plus"
SUBCMD = ROOT / "bin" / "_subcommands" / "review.py"
PERSONAS_DIR = ROOT / "personas"


def _load_review_module():
    spec = importlib.util.spec_from_file_location("_skill_plus_review_test", SUBCMD)
    mod = importlib.util.module_from_spec(spec)

    import datetime as _dt

    def _now_iso():
        return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _tool_meta():
        return {"name": "skill-plus", "version": "test"}

    mod.__dict__["_now_iso"] = _now_iso
    mod.__dict__["_tool_meta"] = _tool_meta
    spec.loader.exec_module(mod)
    return mod


review = _load_review_module()


# ─── helpers ────────────────────────────────────────────────────────────────


def _run_bin(*args, home=None, env=None):
    e = os.environ.copy()
    # Suppress transcript scanning in tests.
    e["AGENT_PLUS_INQUIRE_NO_TRANSCRIPTS"] = "1"
    if home is not None:
        e["HOME"] = str(home)
        e["USERPROFILE"] = str(home)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True, timeout=30,
        env=e,
    )


def _make_fake_plugin(tmp: Path, name: str) -> Path:
    p = tmp / name
    (p / "bin").mkdir(parents=True)
    (p / "bin" / name).write_text("#!/usr/bin/env python3\nprint('hello')\n",
                                  encoding="utf-8")
    (p / ".claude-plugin").mkdir()
    (p / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.1.0"}), encoding="utf-8"
    )
    return p


def _make_fake_skill(tmp: Path, name: str) -> Path:
    p = tmp / name
    (p / "bin").mkdir(parents=True)
    (p / "bin" / name).write_text("#!/usr/bin/env python3\nprint('hi')\n",
                                  encoding="utf-8")
    p.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A test skill\n---\n# {name}\n",
        encoding="utf-8"
    )
    return p


def _write_persona_findings(findings_dir: Path, persona: str,
                             findings: list[dict],
                             praise: list[str] | None = None,
                             anti_confirmation: str = "Nothing beyond the focus list.") -> Path:
    out = findings_dir / f"{persona}.json"
    data = {
        "persona": persona,
        "findings": findings,
        "praise": praise or [],
        "anti_confirmation": anti_confirmation,
    }
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


# ─── 1. Default persona set present ─────────────────────────────────────────


class TestPersonasDefaultSetPresent(unittest.TestCase):
    def test_all_four_defaults_exist(self):
        for name in ("security", "agent-ux", "docs-clarity", "edge-cases"):
            p = PERSONAS_DIR / f"{name}.md"
            self.assertTrue(p.exists(), f"Missing shipped persona brief: {p}")

    def test_persona_briefs_have_frontmatter(self):
        for name in ("security", "agent-ux", "docs-clarity", "edge-cases"):
            p = PERSONAS_DIR / f"{name}.md"
            text = p.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---"), f"{name}.md missing frontmatter")
            self.assertIn("name:", text, f"{name}.md missing 'name:' in frontmatter")
            self.assertIn("focus:", text, f"{name}.md missing 'focus:' in frontmatter")

    def test_persona_briefs_contain_anti_confirmation_rule(self):
        """Each brief MUST include the anti-confirmation discipline text."""
        for name in ("security", "agent-ux", "docs-clarity", "edge-cases"):
            p = PERSONAS_DIR / f"{name}.md"
            text = p.read_text(encoding="utf-8")
            # Must contain the keyword that communicates the anti-confirmation rule.
            self.assertIn(
                "anti_confirmation",
                text,
                f"{name}.md does not mention anti_confirmation",
            )
            self.assertIn(
                "Nothing beyond the focus list",
                text,
                f"{name}.md missing the canonical 'Nothing beyond the focus list.' phrase",
            )

    def test_persona_briefs_contain_output_schema(self):
        """Each brief must document the expected JSON output schema."""
        for name in ("security", "agent-ux", "docs-clarity", "edge-cases"):
            p = PERSONAS_DIR / f"{name}.md"
            text = p.read_text(encoding="utf-8")
            self.assertIn('"findings"', text, f"{name}.md missing output schema findings key")
            self.assertIn('"severity"', text, f"{name}.md missing severity in schema")


# ─── 2. Dispatch envelope shape ──────────────────────────────────────────────


class TestReviewEmitsDispatchEnvelope(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run_dispatch(self, target_path: str, home: Path | None = None) -> dict:
        result = _run_bin("review", target_path, "--pretty",
                          home=home or self.tmp / "fake_home")
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        return json.loads(result.stdout)

    def test_dispatch_envelope_top_keys(self):
        target = _make_fake_plugin(self.tmp, "my-plugin")
        home = self.tmp / "home"
        home.mkdir()
        data = self._run_dispatch(str(target), home=home)
        for key in ("envelope_version", "mode", "target", "personas",
                    "findings_dir", "synth_command", "instructions"):
            self.assertIn(key, data, f"Missing key: {key}")

    def test_dispatch_mode_field(self):
        target = _make_fake_plugin(self.tmp, "plug2")
        home = self.tmp / "home"
        home.mkdir()
        data = self._run_dispatch(str(target), home=home)
        self.assertEqual(data["mode"], "dispatch")

    def test_dispatch_target_block(self):
        target = _make_fake_plugin(self.tmp, "plug3")
        home = self.tmp / "home"
        home.mkdir()
        data = self._run_dispatch(str(target), home=home)
        t = data["target"]
        self.assertIn("kind", t)
        self.assertIn("name", t)
        self.assertIn("path", t)
        self.assertEqual(t["kind"], "plugin")

    def test_dispatch_personas_list(self):
        target = _make_fake_plugin(self.tmp, "plug4")
        home = self.tmp / "home"
        home.mkdir()
        data = self._run_dispatch(str(target), home=home)
        self.assertIsInstance(data["personas"], list)
        self.assertGreater(len(data["personas"]), 0)
        first = data["personas"][0]
        for key in ("name", "brief_path", "target_path", "target_files", "output_path"):
            self.assertIn(key, first, f"persona entry missing key: {key}")

    def test_dispatch_synth_command_contains_path(self):
        target = _make_fake_plugin(self.tmp, "plug5")
        home = self.tmp / "home"
        home.mkdir()
        data = self._run_dispatch(str(target), home=home)
        self.assertIn("--synth-from", data["synth_command"])

    def test_dispatch_envelope_version(self):
        target = _make_fake_plugin(self.tmp, "plug6")
        home = self.tmp / "home"
        home.mkdir()
        data = self._run_dispatch(str(target), home=home)
        self.assertEqual(data["envelope_version"], "1.1")

    def test_dispatch_skill_target_detected(self):
        target = _make_fake_skill(self.tmp, "my-skill")
        home = self.tmp / "home"
        home.mkdir()
        data = self._run_dispatch(str(target), home=home)
        self.assertEqual(data["target"]["kind"], "skill")

    def test_dispatch_no_tool_meta_double_wrap(self):
        """The bin adds tool meta -- envelope must have exactly one 'tool' key."""
        target = _make_fake_plugin(self.tmp, "plug7")
        home = self.tmp / "home"
        home.mkdir()
        data = self._run_dispatch(str(target), home=home)
        # tool key should be present (added by bin wrapper) and be a dict.
        self.assertIn("tool", data)
        self.assertIsInstance(data["tool"], dict)


# ─── 3. Synth merges findings dir ────────────────────────────────────────────


class TestSynthMergesFindingsDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run_synth(self, findings_dir: Path, target_path: str | None = None,
                   home: Path | None = None) -> dict:
        cmd = ["review"]
        if target_path:
            cmd.append(target_path)
        cmd += ["--synth-from", str(findings_dir), "--pretty"]
        result = _run_bin(*cmd, home=home or self.tmp / "home")
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}\nstdout: {result.stdout}")
        return json.loads(result.stdout)

    def test_synth_approve_when_no_findings(self):
        fd = self.tmp / "findings_clean"
        fd.mkdir()
        _write_persona_findings(fd, "security", [])
        _write_persona_findings(fd, "edge-cases", [])
        data = self._run_synth(fd)
        self.assertEqual(data["verdict"], "approve")

    def test_synth_approve_with_nits_on_p2_only(self):
        fd = self.tmp / "findings_nits"
        fd.mkdir()
        _write_persona_findings(fd, "security", [
            {"severity": "p2", "file": "bin/foo", "line": 1,
             "issue": "minor thing", "suggestion": "fix it"}
        ])
        data = self._run_synth(fd)
        self.assertEqual(data["verdict"], "approve_with_nits")

    def test_synth_request_changes_on_p1(self):
        fd = self.tmp / "findings_p1"
        fd.mkdir()
        _write_persona_findings(fd, "security", [
            {"severity": "p1", "file": "bin/foo", "line": 5,
             "issue": "bad thing", "suggestion": "fix now"}
        ])
        data = self._run_synth(fd)
        self.assertEqual(data["verdict"], "request_changes")

    def test_synth_request_changes_on_p0(self):
        fd = self.tmp / "findings_p0"
        fd.mkdir()
        _write_persona_findings(fd, "agent-ux", [
            {"severity": "p0", "file": "SKILL.md", "line": 1,
             "issue": "completely broken", "suggestion": "rewrite"}
        ])
        data = self._run_synth(fd)
        self.assertEqual(data["verdict"], "request_changes")

    def test_synth_p0_beats_p2(self):
        """p0 present with p2 others -> request_changes, not approve_with_nits."""
        fd = self.tmp / "findings_mix"
        fd.mkdir()
        _write_persona_findings(fd, "security", [
            {"severity": "p2", "file": "a", "line": 1, "issue": "nit", "suggestion": "ok"}
        ])
        _write_persona_findings(fd, "edge-cases", [
            {"severity": "p0", "file": "b", "line": 2, "issue": "critical", "suggestion": "fix"}
        ])
        data = self._run_synth(fd)
        self.assertEqual(data["verdict"], "request_changes")

    def test_synth_summary_counts(self):
        fd = self.tmp / "findings_counts"
        fd.mkdir()
        _write_persona_findings(fd, "security", [
            {"severity": "p0", "file": "a", "line": 1, "issue": "x", "suggestion": "y"},
            {"severity": "p1", "file": "b", "line": 2, "issue": "x", "suggestion": "y"},
        ], praise=["good redactor"])
        _write_persona_findings(fd, "docs-clarity", [
            {"severity": "p2", "file": "c", "line": 3, "issue": "x", "suggestion": "y"},
        ], praise=["clear headline", "good example"])
        data = self._run_synth(fd)
        s = data["summary"]
        self.assertEqual(s["p0"], 1)
        self.assertEqual(s["p1"], 1)
        self.assertEqual(s["p2"], 1)
        self.assertEqual(s["findings_total"], 3)
        self.assertEqual(s["praise"], 3)

    def test_synth_personas_run_populated(self):
        fd = self.tmp / "findings_personas"
        fd.mkdir()
        _write_persona_findings(fd, "security", [])
        _write_persona_findings(fd, "agent-ux", [])
        data = self._run_synth(fd)
        self.assertIn("personas_run", data)
        self.assertIn("security", data["personas_run"])
        self.assertIn("agent-ux", data["personas_run"])

    def test_synth_findings_sorted_by_severity(self):
        fd = self.tmp / "findings_sort"
        fd.mkdir()
        _write_persona_findings(fd, "security", [
            {"severity": "p2", "file": "a", "line": 1, "issue": "x", "suggestion": "y"},
            {"severity": "p0", "file": "b", "line": 2, "issue": "x", "suggestion": "y"},
        ])
        data = self._run_synth(fd)
        severities = [f["severity"] for f in data["findings"]]
        self.assertEqual(severities[0], "p0", "p0 should be first")

    def test_synth_anti_confirmation_merged(self):
        fd = self.tmp / "findings_ac"
        fd.mkdir()
        _write_persona_findings(fd, "security", [],
                                anti_confirmation="I noticed a dead code path.")
        _write_persona_findings(fd, "edge-cases", [],
                                anti_confirmation="Nothing beyond the focus list.")
        data = self._run_synth(fd)
        # Non-nothing anti_confirmation from security should appear.
        self.assertIn("security", data["anti_confirmation"])
        self.assertIn("dead code path", data["anti_confirmation"])

    def test_synth_pr_body_draft_present(self):
        fd = self.tmp / "findings_pr"
        fd.mkdir()
        _write_persona_findings(fd, "security", [
            {"severity": "p1", "file": "bin/foo", "line": 1,
             "issue": "thing", "suggestion": "fix"}
        ])
        data = self._run_synth(fd)
        self.assertIn("pr_body_draft", data)
        pr = data["pr_body_draft"]
        self.assertIn("request_changes", pr)

    def test_synth_mode_field(self):
        fd = self.tmp / "findings_mode"
        fd.mkdir()
        _write_persona_findings(fd, "security", [])
        data = self._run_synth(fd)
        self.assertEqual(data["mode"], "review")

    def test_synth_nonexistent_dir_returns_error(self):
        result = _run_bin("review", ".", "--synth-from",
                          str(self.tmp / "does_not_exist"), "--pretty",
                          home=self.tmp / "home")
        self.assertNotEqual(result.returncode, 0)


# ─── 4. User persona extension ───────────────────────────────────────────────


class TestUserPersonaExtension(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_user_persona_discovered_from_home(self):
        """A persona in ~/.agent-plus/review-personas/ is picked up."""
        fake_home = self.tmp / "home"
        user_personas = fake_home / ".agent-plus" / "review-personas"
        user_personas.mkdir(parents=True)
        custom_brief = user_personas / "custom-reviewer.md"
        custom_brief.write_text(
            "---\nname: custom-reviewer\nfocus: custom\n---\nCustom review persona.\n",
            encoding="utf-8"
        )
        # Test via the module API.
        result = review.resolve_persona_path("custom-reviewer", None)
        # This uses the real home, not fake_home; test via monkeypatching instead.
        # Direct path test:
        self.assertTrue(custom_brief.exists())
        # Test the path resolution logic directly.
        orig_home = Path.home
        try:
            Path.home = staticmethod(lambda: fake_home)
            found = review.resolve_persona_path("custom-reviewer", None)
            self.assertIsNotNone(found, "custom-reviewer persona not found")
            self.assertEqual(found, custom_brief)
        finally:
            Path.home = staticmethod(orig_home)

    def test_plugin_local_persona_overrides_user_global(self):
        """Plugin-local persona takes precedence over user-global."""
        fake_home = self.tmp / "home"
        user_personas = fake_home / ".agent-plus" / "review-personas"
        user_personas.mkdir(parents=True)
        user_brief = user_personas / "security.md"
        user_brief.write_text("---\nname: security\nfocus: user-level\n---\nUser.\n",
                              encoding="utf-8")

        plugin_dir = self.tmp / "my-plugin"
        plugin_dir.mkdir()
        local_personas = plugin_dir / "personas"
        local_personas.mkdir()
        local_brief = local_personas / "security.md"
        local_brief.write_text("---\nname: security\nfocus: plugin-level\n---\nLocal.\n",
                               encoding="utf-8")

        orig_home = Path.home
        try:
            Path.home = staticmethod(lambda: fake_home)
            found = review.resolve_persona_path("security", str(plugin_dir))
            self.assertEqual(found, local_brief, "Plugin-local should win over user-global")
        finally:
            Path.home = staticmethod(orig_home)

    def test_shipped_default_is_fallback(self):
        """With no user persona, the shipped default is returned."""
        found = review.resolve_persona_path("security", None)
        self.assertIsNotNone(found)
        self.assertIn("personas", str(found))

    def test_unknown_persona_returns_none(self):
        found = review.resolve_persona_path("does-not-exist-xyzzy", None)
        self.assertIsNone(found)


# ─── 5. Envelope v1.1 compat probe ───────────────────────────────────────────


class TestEnvelopeV11Compat(unittest.TestCase):
    """The synth output must satisfy the ENVELOPE_VERSION 1.1 contract."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _synth(self, findings: dict[str, list]) -> dict:
        fd = self.tmp / "fd"
        fd.mkdir(exist_ok=True)
        for persona, flist in findings.items():
            _write_persona_findings(fd, persona, flist)
        result = _run_bin("review", ".", "--synth-from", str(fd), "--pretty",
                          home=self.tmp / "home")
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        return json.loads(result.stdout)

    def test_envelope_version_field(self):
        data = self._synth({"security": []})
        self.assertEqual(data.get("envelope_version"), "1.1")

    def test_required_top_level_keys(self):
        data = self._synth({"security": []})
        required = ("envelope_version", "verdict", "mode", "target",
                    "personas_run", "summary", "findings",
                    "anti_confirmation", "pr_body_draft", "cached_at")
        for key in required:
            self.assertIn(key, data, f"Missing required envelope key: {key}")

    def test_verdict_values_are_canonical(self):
        valid = {"approve", "approve_with_nits", "request_changes"}
        for sev, expected in [("p0", "request_changes"), ("p1", "request_changes"),
                               ("p2", "approve_with_nits")]:
            data = self._synth({"security": [
                {"severity": sev, "file": "f", "line": 1, "issue": "x", "suggestion": "y"}
            ]})
            self.assertIn(data["verdict"], valid)
            self.assertEqual(data["verdict"], expected)

    def test_findings_have_persona_tag(self):
        data = self._synth({"security": [
            {"severity": "p1", "file": "f", "line": 1, "issue": "x", "suggestion": "y"}
        ]})
        for f in data["findings"]:
            self.assertIn("persona", f)

    def test_summary_shape(self):
        data = self._synth({"security": [
            {"severity": "p0", "file": "f", "line": 1, "issue": "x", "suggestion": "y"}
        ]})
        s = data["summary"]
        for key in ("findings_total", "p0", "p1", "p2", "praise"):
            self.assertIn(key, s, f"summary missing key: {key}")

    def test_pr_body_draft_contains_verdict(self):
        data = self._synth({"security": [
            {"severity": "p1", "file": "f", "line": 1, "issue": "x", "suggestion": "y"}
        ]})
        self.assertIn("request_changes", data["pr_body_draft"])

    def test_pr_body_draft_ascii_only(self):
        """pr_body_draft must not contain em-dashes or other non-ASCII (Windows console rule)."""
        data = self._synth({"security": [
            {"severity": "p1", "file": "f", "line": 1, "issue": "x", "suggestion": "y"}
        ]})
        pr = data.get("pr_body_draft", "")
        # Check no non-ASCII characters (simplified: all chars <= 127).
        non_ascii = [c for c in pr if ord(c) > 127]
        self.assertEqual(non_ascii, [], f"pr_body_draft contains non-ASCII: {non_ascii[:5]}")

    def test_cached_at_iso_format(self):
        data = self._synth({"security": []})
        import re
        self.assertRegex(data["cached_at"],
                         r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                         "cached_at is not ISO-8601")


# ─── CLI integration tests ────────────────────────────────────────────────────


class TestReviewCLIIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_review_help_exits_zero(self):
        result = _run_bin("review", "--help")
        self.assertEqual(result.returncode, 0)

    def test_review_missing_path_uses_cwd(self):
        """review with no path uses '.' which should resolve to cwd."""
        home = self.tmp / "home"
        home.mkdir()
        # We call from a real dir -- just check it doesn't crash.
        result = _run_bin("review", "--pretty",
                          home=home)
        # May succeed or fail depending on cwd content, but should not Python-crash.
        self.assertIn(result.returncode, (0, 1, 2))

    def test_review_nonexistent_path_returns_error(self):
        home = self.tmp / "home"
        home.mkdir()
        result = _run_bin("review", "/does/not/exist/xyzzy", "--pretty",
                          home=home)
        self.assertNotEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertFalse(data.get("ok", True))

    def test_review_custom_personas_flag(self):
        """--personas security,docs-clarity only runs those two."""
        target = _make_fake_plugin(self.tmp, "plugA")
        home = self.tmp / "home"
        home.mkdir()
        result = _run_bin("review", str(target),
                          "--personas", "security,docs-clarity",
                          "--pretty", home=home)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        data = json.loads(result.stdout)
        persona_names = [p["name"] for p in data["personas"]]
        self.assertIn("security", persona_names)
        self.assertIn("docs-clarity", persona_names)
        self.assertNotIn("agent-ux", persona_names)
        self.assertNotIn("edge-cases", persona_names)

    def test_review_unknown_persona_returns_error(self):
        target = _make_fake_plugin(self.tmp, "plugB")
        home = self.tmp / "home"
        home.mkdir()
        result = _run_bin("review", str(target),
                          "--personas", "does-not-exist-xyzzy",
                          "--pretty", home=home)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
