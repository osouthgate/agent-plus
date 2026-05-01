"""Tests for `skill-plus inquire` (Phase A).

Stdlib unittest-compatible (also runs under pytest). NO network — urllib +
subprocess are mocked. NO third-party libraries.

# F3 ANTI-CONFIRMATION-BIAS SEALED EXPECTATIONS (do not edit before audit run):
# Q1 against github-remote:
#   EXPECTED: Level 1 (regex over log text), recommendation: Level 3 annotations
# Q2 against github-remote:
#   EXPECTED: ok — plugin has _resolve helpers
# Q3 against github-remote:
#   EXPECTED: ok at Level 2 (--wait helpers present, no streaming)
# Q4 against github-remote:
#   EXPECTED: ok — plugin emits json envelopes
# Q5 against github-remote:
#   EXPECTED: ok — uses API not scrape
# Q6 against github-remote:
#   EXPECTED: ok — _scrub helper present
# Q7 against github-remote:
#   EXPECTED: ok — TOOL_NAME + version envelope
# Surprise to surface (per F3): unknown — the audit's job is to find it,
# not have us pre-write it. Anything beyond "Q1 is Level 1" is the win.
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
from unittest import mock

# ─── module loading (the bin file injects helpers at runtime, so we have to
#    import the subcommand module the same way bin/skill-plus does) ───────────

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "skill-plus"
SUBCMD = ROOT / "bin" / "_subcommands" / "inquire.py"


def _load_inquire_module():
    spec = importlib.util.spec_from_file_location("_skill_plus_inquire_test", SUBCMD)
    mod = importlib.util.module_from_spec(spec)

    # Inject the helpers bin/skill-plus normally provides.
    def _now_iso():
        import datetime as dt
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _tool_meta():
        return {"name": "skill-plus", "version": "0.4.0"}

    mod.__dict__["_now_iso"] = _now_iso
    mod.__dict__["_tool_meta"] = _tool_meta
    spec.loader.exec_module(mod)
    return mod


inquire = _load_inquire_module()


# ─── helpers ────────────────────────────────────────────────────────────────


def _run_bin(*args, cwd=None, env=None, home=None):
    e = os.environ.copy()
    if home is not None:
        e["HOME"] = str(home)
        e["USERPROFILE"] = str(home)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True, timeout=30,
        cwd=str(cwd) if cwd else None, env=e,
    )


def _make_fake_plugin(tmp: Path, name: str, bin_text: str, version: str = "0.1.0") -> Path:
    p = tmp / name
    (p / "bin").mkdir(parents=True)
    (p / "bin" / name).write_text(bin_text, encoding="utf-8")
    (p / ".claude-plugin").mkdir()
    (p / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": version}), encoding="utf-8"
    )
    return p


# Sample plugin bin texts for ladder placement tests.

PLUGIN_LEVEL1 = '''#!/usr/bin/env python3
"""fake plugin — Level 1 errors_surface (regex log scrape)."""
TOOL_NAME = "fake"
import re
def errors_only(text):
    return re.findall(r"\\b(error|failed|fatal)\\b", text)
GITHUB_TOKEN = None
'''

PLUGIN_LEVEL3_ANNOTATIONS = '''#!/usr/bin/env python3
"""fake plugin - Level 3, uses /check-runs/{id}/annotations."""
TOOL_NAME = "fake"
import urllib.request
def get_errors(check_id):
    url = f"/repos/foo/bar/check-runs/{check_id}/annotations"
    return urllib.request.urlopen(url).read()
def _resolve(name):
    return name
def _scrub(d):
    return d
import json
def emit(p):
    print(json.dumps({"tool": {"name": "fake", "version": "0.1.0"}, **p}))
'''

# R6 fix: realistic-but-loose L3 mention should NOT score Level 3.
# A plugin that only mentions /check-runs/{id}/annotations in a comment
# (no actual API call) is still at Level 1 or 2. The probe must require
# a call-site to credit Level 3.
PLUGIN_LEVEL3_LOOSE_MENTION = '''#!/usr/bin/env python3
"""fake plugin - mentions annotations in a comment but does not call it."""
TOOL_NAME = "fake"
# TODO: someday call /repos/foo/bar/check-runs/123/annotations to surface
# structured errors. For now we just regex-grep logs.
import re
def errors_only(text):
    return re.findall(r"\\b(error|failed|fatal)\\b", text)
'''

PLUGIN_WITH_WAIT = '''#!/usr/bin/env python3
TOOL_NAME = "fake"
def wait(run_id):
    return _poll_until(run_id)
def _poll_until(x):
    return x
def deploy():
    pass
'''


# ─── per-probe tests ────────────────────────────────────────────────────────


class TestQ1Probes(unittest.TestCase):
    def test_q1_plugin_level1_regex_scrape(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "plugin_path": str(p)}
            r = inquire.probe_q1_plugin(target)
            self.assertIsNotNone(r)
            self.assertEqual(r["level"], 1)
            self.assertEqual(r["answer"], "improvable")

    def test_q1_plugin_level3_annotations(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL3_ANNOTATIONS)
            target = {"tool": "fake", "plugin_path": str(p)}
            r = inquire.probe_q1_plugin(target)
            self.assertEqual(r["level"], 3)
            self.assertEqual(r["answer"], "ok")

    def test_q1_plugin_no_path(self):
        self.assertIsNone(inquire.probe_q1_plugin({"tool": "x"}))

    def test_q1_plugin_loose_annotations_mention_not_L3(self):
        """R6 fix: a plugin that mentions /check-runs/{id}/annotations only in
        a comment (without a co-located API call) must NOT score Level 3."""
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL3_LOOSE_MENTION)
            target = {"tool": "fake", "plugin_path": str(p)}
            r = inquire.probe_q1_plugin(target)
            self.assertNotEqual(r.get("level"), 3,
                                "loose mention without call site must not score L3")
            self.assertEqual(r["level"], 1,
                             "should fall through to L1 regex-scrape detection")

    def test_q1_cli_not_on_path(self):
        with mock.patch.object(inquire, "cli_on_path", return_value=None):
            self.assertIsNone(inquire.probe_q1_cli({"tool": "x", "cli": "definitely-not-a-cli"}))

    def test_q1_cli_help_mentions_annotations(self):
        with mock.patch.object(inquire, "cli_on_path", return_value="/usr/bin/fakecli"):
            with mock.patch.object(inquire, "run_cli",
                                   return_value=(0, "Usage: fakecli annotations [opts]", "")):
                r = inquire.probe_q1_cli({"tool": "fakecli", "cli": "fakecli"})
        self.assertEqual(r["level"], 3)
        self.assertEqual(r["answer"], "ok")

    def test_q1_web_finds_annotation_keyword(self):
        with mock.patch.object(inquire, "web_search",
                               return_value=["GitHub annotations API gives you per-line records"]):
            r = inquire.probe_q1_web({"tool": "github"})
        self.assertEqual(r["answer"], "ok")
        self.assertEqual(r["level"], 3)

    def test_q1_web_empty(self):
        with mock.patch.object(inquire, "web_search", return_value=[]):
            self.assertIsNone(inquire.probe_q1_web({"tool": "x"}))


class TestQ2Probes(unittest.TestCase):
    def test_q2_plugin_with_resolver(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake",
                                  "def _resolve(name):\n    return name\n")
            r = inquire.probe_q2_plugin({"tool": "fake", "plugin_path": str(p)})
        self.assertEqual(r["answer"], "ok")

    def test_q2_plugin_without_resolver(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", "def main():\n    pass\n")
            r = inquire.probe_q2_plugin({"tool": "fake", "plugin_path": str(p)})
        self.assertEqual(r["answer"], "improvable")


class TestQ3Probes(unittest.TestCase):
    def test_q3_plugin_with_wait(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_WITH_WAIT)
            r = inquire.probe_q3_plugin({"tool": "fake", "plugin_path": str(p)})
        self.assertEqual(r["level"], 2)
        self.assertEqual(r["answer"], "improvable")

    def test_q3_plugin_no_wait(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", "def main():\n    pass\n")
            r = inquire.probe_q3_plugin({"tool": "fake", "plugin_path": str(p)})
        self.assertEqual(r["level"], 1)


class TestQ4Probes(unittest.TestCase):
    def test_q4_plugin_emits_json(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake",
                                  "import json\nprint(json.dumps({'a':1}))\n")
            r = inquire.probe_q4_plugin({"tool": "fake", "plugin_path": str(p)})
        self.assertEqual(r["answer"], "ok")

    def test_q4_plugin_text_only(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", "print('hi')\n")
            r = inquire.probe_q4_plugin({"tool": "fake", "plugin_path": str(p)})
        self.assertEqual(r["answer"], "improvable")


class TestQ5Q6Q7Probes(unittest.TestCase):
    def test_q5_api_only(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake",
                                  "import urllib.request\nurl='https://api.github.com/v3/x'\n")
            r = inquire.probe_q5_plugin({"tool": "fake", "plugin_path": str(p)})
        self.assertEqual(r["answer"], "ok")

    def test_q5_scrape_only(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake",
                                  "from html.parser import HTMLParser\n")
            r = inquire.probe_q5_plugin({"tool": "fake", "plugin_path": str(p)})
        self.assertEqual(r["answer"], "improvable")

    def test_q6_redactor_present(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake",
                                  "def _scrub(x):\n    return 'REDACTED'\n")
            r = inquire.probe_q6_plugin({"tool": "fake", "plugin_path": str(p)})
        self.assertEqual(r["answer"], "ok")

    def test_q6_no_redactor(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", "def main():\n    pass\n")
            r = inquire.probe_q6_plugin({"tool": "fake", "plugin_path": str(p)})
        self.assertEqual(r["answer"], "improvable")

    def test_q7_envelope_present(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake",
                                  '"tool": {"name": "fake", "version": "0.1.0"}\n')
            r = inquire.probe_q7_plugin({"tool": "fake", "plugin_path": str(p)})
        self.assertEqual(r["answer"], "ok")

    def test_q7_envelope_missing(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", "def main():\n    pass\n")
            r = inquire.probe_q7_plugin({"tool": "fake", "plugin_path": str(p)})
        self.assertEqual(r["answer"], "improvable")


# ─── source stacking + confidence ───────────────────────────────────────────


class TestSourceStacking(unittest.TestCase):
    def test_two_sources_agree_high_confidence(self):
        per_source = {
            "cli": {"answer": "ok", "evidence": {"source": "cli", "detail": "x"}},
            "plugin": {"answer": "ok", "evidence": {"source": "plugin", "detail": "y"}},
        }
        ans, conf, ev = inquire.stack_sources(per_source)
        self.assertEqual(ans, "ok")
        self.assertEqual(conf, "high")
        self.assertEqual(len(ev), 2)

    def test_one_authoritative_source_medium_confidence(self):
        per_source = {
            "cli": {"answer": "ok", "evidence": {"source": "cli", "detail": "x"}},
        }
        ans, conf, _ = inquire.stack_sources(per_source)
        self.assertEqual(ans, "ok")
        self.assertEqual(conf, "medium")

    def test_only_web_source_low_confidence(self):
        per_source = {
            "web": {"answer": "ok", "evidence": {"source": "web", "detail": "x"}},
        }
        ans, conf, _ = inquire.stack_sources(per_source)
        self.assertEqual(conf, "low")

    def test_no_known_sources_unknown_none(self):
        per_source = {
            "web": {"answer": "unknown", "evidence": {"source": "web", "detail": "x"}},
        }
        ans, conf, _ = inquire.stack_sources(per_source)
        self.assertEqual(ans, "unknown")
        self.assertEqual(conf, "none")

    def test_disagreement_picks_more_supported(self):
        per_source = {
            "cli": {"answer": "ok", "evidence": {"source": "cli", "detail": "x"}},
            "plugin": {"answer": "ok", "evidence": {"source": "plugin", "detail": "y"}},
            "web": {"answer": "improvable", "evidence": {"source": "web", "detail": "z"}},
        }
        ans, conf, _ = inquire.stack_sources(per_source)
        self.assertEqual(ans, "ok")
        self.assertEqual(conf, "high")


# ─── cache ──────────────────────────────────────────────────────────────────


class TestCache(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self._patcher = mock.patch.object(
            inquire, "cache_dir",
            return_value=self.tmp / ".agent-plus" / "inquire-cache",
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._td.cleanup()

    def test_cache_miss_returns_none(self):
        self.assertIsNone(inquire.cache_load("never-cached"))

    def test_cache_hit_round_trip(self):
        env = {"verdict": "ok", "results": []}
        inquire.cache_store("foo", env)
        loaded = inquire.cache_load("foo")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["result"], env)

    def test_cache_expiry(self):
        env = {"verdict": "ok"}
        inquire.cache_store("foo", env)
        # Mutate the file's expires_at to the past.
        p = inquire.cache_path_for("foo")
        data = json.loads(p.read_text(encoding="utf-8"))
        data["expires_at"] = "2020-01-01T00:00:00Z"
        p.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(inquire.cache_load("foo"))

    def test_cache_clear_removes_files(self):
        inquire.cache_store("a", {"x": 1})
        inquire.cache_store("b", {"x": 2})
        n = inquire.cache_clear()
        self.assertEqual(n, 2)
        self.assertFalse(any(inquire.cache_dir().glob("*.json")))

    def test_cache_clear_when_dir_missing(self):
        # Re-point cache to a missing directory.
        with mock.patch.object(inquire, "cache_dir",
                               return_value=self.tmp / "nonexistent"):
            self.assertEqual(inquire.cache_clear(), 0)


# ─── envelope shape ─────────────────────────────────────────────────────────


class TestEnvelopeShape(unittest.TestCase):
    def test_audit_envelope_contains_required_fields(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "definitely-not-on-path",
                      "plugin_path": str(p)}
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    env = inquire.build_envelope(target, mode="audit")
        for key in ("verdict", "mode", "target", "summary", "results", "pr_body_draft"):
            self.assertIn(key, env)
        for key in ("kind", "name", "sources_used", "sources_unavailable", "path", "version"):
            self.assertIn(key, env["target"])
        for key in ("questions_asked", "ok", "gaps", "na", "unknown", "high_confidence_gaps"):
            self.assertIn(key, env["summary"])
        self.assertEqual(env["summary"]["questions_asked"], 7)
        for r in env["results"]:
            self.assertIn("q_id", r)
            self.assertIn("q_label", r)
            self.assertIn("answer", r)
            self.assertIn("confidence", r)
            self.assertIn("evidence", r)

    def test_pr_body_draft_non_empty_with_gap_recommendations(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "no-such-bin",
                      "plugin_path": str(p)}
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    env = inquire.build_envelope(target, mode="audit")
        self.assertTrue(env["pr_body_draft"])
        self.assertIn("Summary", env["pr_body_draft"])
        self.assertIn("Gaps", env["pr_body_draft"])

    def test_generate_mode_includes_recommended_skill(self):
        target = {"tool": "newtool", "name": "newtool", "cli": "no-such-bin"}
        with mock.patch.object(inquire, "cli_on_path", return_value=None):
            with mock.patch.object(inquire, "web_search", return_value=[]):
                env = inquire.build_envelope(target, mode="generate")
        self.assertIn("recommended_skill", env)
        self.assertIn("killer_command", env["recommended_skill"])

    def test_verdict_gaps_found(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "x", "plugin_path": str(p)}
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    env = inquire.build_envelope(target, mode="audit")
        self.assertIn(env["verdict"], ("gaps_found", "mostly_unknown"))

    def test_na_outcome_for_non_applicable_q(self):
        # Q7 is na for raw-tool generator mode (no plugin path).
        target = {"tool": "newtool", "name": "newtool", "cli": "no-such"}
        with mock.patch.object(inquire, "cli_on_path", return_value=None):
            with mock.patch.object(inquire, "web_search", return_value=[]):
                env = inquire.build_envelope(target, mode="generate")
        q7 = next(r for r in env["results"] if r["q_id"] == "Q7")
        self.assertEqual(q7["answer"], "na")


# ─── maturity ladder ────────────────────────────────────────────────────────


class TestMaturityLadder(unittest.TestCase):
    def test_q1_plugin_level1_recommends_higher(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "x",
                      "plugin_path": str(p)}
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    env = inquire.build_envelope(target, mode="audit")
        q1 = next(r for r in env["results"] if r["q_id"] == "Q1")
        self.assertEqual(q1.get("current_level"), 1)
        self.assertEqual(q1.get("recommended_level"), 2)
        self.assertEqual(q1.get("ladder"), "Q1 errors_surface")

    def test_q1_cli_with_annotations_shows_level3(self):
        with mock.patch.object(inquire, "cli_on_path", return_value="/x/gh"):
            with mock.patch.object(inquire, "run_cli",
                                   return_value=(0, "use annotations endpoint", "")):
                r = inquire.probe_q1_cli({"tool": "gh", "cli": "gh"})
        self.assertEqual(r["level"], 3)


# ─── CLI integration ───────────────────────────────────────────────────────


class TestCLIIntegration(unittest.TestCase):
    def test_inquire_help(self):
        res = _run_bin("inquire", "--help")
        self.assertEqual(res.returncode, 0)
        self.assertIn("inquire", res.stdout)
        self.assertIn("--audit", res.stdout)

    def test_inquire_clear_cache(self):
        with tempfile.TemporaryDirectory() as home:
            res = _run_bin("inquire", "--clear-cache", home=Path(home))
            self.assertEqual(res.returncode, 0)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["verdict"], "cache_cleared")

    def test_inquire_missing_tool(self):
        res = _run_bin("inquire")
        self.assertEqual(res.returncode, 2)
        payload = json.loads(res.stdout)
        self.assertEqual(payload["verdict"], "error_missing_tool")

    def test_inquire_audit_against_fake_plugin(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            plugin_root = Path(td) / "plug"
            plugin_root.mkdir()
            p = _make_fake_plugin(plugin_root, "fakeplug", PLUGIN_LEVEL3_ANNOTATIONS)
            res = _run_bin(
                "inquire", "fakeplug", "--audit",
                "--plugin-path", str(p), "--no-cache",
                "--cli", "definitely-not-on-path-binary",
                home=home,
            )
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
            env = json.loads(res.stdout)
            self.assertEqual(env["mode"], "audit")
            self.assertEqual(env["target"]["kind"], "plugin")
            self.assertEqual(env["target"]["name"], "fakeplug")
            self.assertEqual(env["summary"]["questions_asked"], 7)
            self.assertIn("pr_body_draft", env)


# ─── DDG parser smoke test (no network — feed canned HTML) ─────────────────


class TestDDGParser(unittest.TestCase):
    def test_parser_extracts_snippets(self):
        html_body = (
            '<html><body>'
            '<a class="result__a" href="x">First result title</a>'
            '<a class="result__snippet" href="y">A snippet about annotations API</a>'
            '<a class="other" href="z">Should not capture</a>'
            '</body></html>'
        )
        p = inquire._DDGParser()
        p.feed(html_body)
        self.assertGreaterEqual(len(p.snippets), 2)
        joined = " ".join(p.snippets)
        self.assertIn("annotations", joined)
        self.assertNotIn("Should not capture", joined)

    def test_web_search_network_failure_returns_empty(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=OSError("no network")):
            self.assertEqual(inquire.web_search("anything"), [])


# ─── applicability ──────────────────────────────────────────────────────────


class TestApplicability(unittest.TestCase):
    def test_q7_na_when_no_plugin(self):
        self.assertFalse(inquire.applicability("Q7", {"tool": "x"}))

    def test_q7_applicable_with_plugin(self):
        self.assertTrue(inquire.applicability("Q7", {"tool": "x", "plugin_path": "/x"}))

    def test_q1_always_applicable(self):
        self.assertTrue(inquire.applicability("Q1", {"tool": "x"}))


# ─── R1 + R2: max-achievable overrides + platform_limit_note ────────────────


class TestPlatformCeilings(unittest.TestCase):
    """R1 fix: per-tool max_achievable_level overrides; R2 fix: populate
    platform_limit_note when current_level is at the ceiling."""

    def test_max_achievable_default(self):
        # No override: Q1 default ceiling is 3.
        self.assertEqual(inquire._max_achievable("Q1", {"tool": "fake"}, 3), 3)

    def test_max_achievable_vercel_override(self):
        # vercel-remote can't reach Q1 L3 because Vercel API doesn't expose
        # source-location records. Override caps at 2.
        self.assertEqual(
            inquire._max_achievable("Q1", {"tool": "vercel-remote"}, 3), 2)
        # Other Qs for vercel-remote stay at their default ceiling.
        self.assertEqual(
            inquire._max_achievable("Q3", {"tool": "vercel-remote"}, 3), 3)

    def test_platform_limit_note_below_ceiling_is_none(self):
        # Plugin not yet at the ceiling — no note.
        note = inquire._platform_limit_note(
            "Q1", {"tool": "vercel-remote"}, current=1, max_level=2)
        self.assertIsNone(note)

    def test_platform_limit_note_at_ceiling_populated_for_vercel_q1(self):
        note = inquire._platform_limit_note(
            "Q1", {"tool": "vercel-remote"}, current=2, max_level=2)
        self.assertIsNotNone(note)
        self.assertIn("Vercel", note)
        self.assertIn("source-location", note)

    def test_platform_limit_note_generic_at_ceiling(self):
        note = inquire._platform_limit_note(
            "Q1", {"tool": "unknown-tool"}, current=3, max_level=3)
        self.assertIsNotNone(note)
        self.assertIn("ceiling", note.lower())


# ─── R3: ladder registry ────────────────────────────────────────────────────


class TestLadderRegistry(unittest.TestCase):
    """R3 fix: ladder placement uses registry, not hardcoded if/elif."""

    def test_q1_in_registry(self):
        self.assertIn("Q1", inquire.LADDER_REGISTRY)
        ladder, label, default_max = inquire.LADDER_REGISTRY["Q1"]
        self.assertEqual(label, "Q1 errors_surface")
        self.assertEqual(default_max, 3)
        self.assertIn(1, ladder)

    def test_q3_in_registry(self):
        self.assertIn("Q3", inquire.LADDER_REGISTRY)

    def test_qs_without_ladders_not_in_registry(self):
        # Q2/Q4/Q5/Q6/Q7 are binary (no ladder defined yet).
        for q in ("Q2", "Q4", "Q5", "Q6", "Q7"):
            self.assertNotIn(q, inquire.LADDER_REGISTRY,
                             f"{q} should not be in registry until a ladder is defined")


# ─── R8: laddered Qs use 'improvable', non-laddered Qs use 'gap' ────────────


class TestAnswerNormalization(unittest.TestCase):
    """R8 fix: Qs without a ladder should report binary 'gap', not the
    laddered 'improvable' (which renders with empty cur/rec fields)."""

    def test_q4_improvable_downgrades_to_gap(self):
        # Q4 has no ladder. If a probe returns 'improvable', the result
        # builder must downgrade to 'gap' so the envelope row doesn't
        # carry empty level fields.
        target = {"tool": "fake", "plugin_path": None}
        # Use a probe that we know returns 'improvable' for Q-without-ladder.
        # Easiest: just call run_question with mocked probes returning improvable.
        with mock.patch.dict(inquire.PROBE_TABLE["Q4"]["probes"],
                             {"plugin": lambda t: {"answer": "improvable",
                                                    "evidence": inquire._make_evidence(
                                                        "plugin", "no --json found")}}):
            r = inquire.run_question("Q4", {"plugin_path": "/x", "tool": "fake"},
                                     sources_used=set(), sources_unavailable=set())
        self.assertEqual(r["answer"], "gap",
                         "Q4 has no ladder; improvable must downgrade to gap")
        self.assertNotIn("current_level", r)


# ─── R9: per-Q confidence ceiling ───────────────────────────────────────────


class TestConfidenceCeiling(unittest.TestCase):
    """R9 fix: Q6 confidence is structurally capped at 'medium' because the
    behavioral CLI probe is deliberately skipped for safety."""

    def test_q6_in_ceilings(self):
        self.assertIn("Q6", inquire.CONFIDENCE_CEILINGS)
        self.assertEqual(inquire.CONFIDENCE_CEILINGS["Q6"], "medium")

    def test_cap_confidence_high_to_medium(self):
        self.assertEqual(inquire._cap_confidence("Q6", "high"), "medium")

    def test_cap_confidence_already_at_or_below_ceiling(self):
        self.assertEqual(inquire._cap_confidence("Q6", "medium"), "medium")
        self.assertEqual(inquire._cap_confidence("Q6", "low"), "low")

    def test_cap_confidence_other_qs_unaffected(self):
        self.assertEqual(inquire._cap_confidence("Q1", "high"), "high")


# ─── R5: 0-gap pr_body_draft is short, not boilerplate ──────────────────────


class TestPRBodyDraft(unittest.TestCase):
    """R5 fix: when verdict is ok, pr_body_draft must not emit the
    boilerplate '## Changes / TODO' headers."""

    def test_zero_gaps_returns_short_message(self):
        results = [{"q_id": "Q1", "q_label": "errors_surface", "answer": "ok"},
                   {"q_id": "Q2", "q_label": "lookup_keys", "answer": "ok"}]
        body = inquire._pr_body_draft("fake-plugin", results)
        self.assertNotIn("## Summary", body)
        self.assertNotIn("## Changes", body)
        self.assertNotIn("TODO", body)
        self.assertIn("No PR needed", body)
        self.assertIn("fake-plugin", body)

    def test_with_gaps_emits_full_body(self):
        results = [{"q_id": "Q1", "q_label": "errors_surface",
                    "answer": "improvable", "current_level": 1,
                    "recommended_level": 2, "max_achievable_level": 3,
                    "recommended_pattern": "Filter on level field",
                    "evidence": [{"source": "plugin", "detail": "regex scrape"}]}]
        body = inquire._pr_body_draft("fake-plugin", results)
        self.assertIn("## Summary", body)
        self.assertIn("## Gaps", body)
        # R4 fix: ASCII arrow, not em-dash.
        self.assertNotIn("—", body)  # em-dash U+2014 must not appear
        self.assertNotIn("–", body)  # en-dash U+2013 must not appear


if __name__ == "__main__":
    unittest.main()
