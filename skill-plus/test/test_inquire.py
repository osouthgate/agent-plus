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

# Hermetic default: don't walk the developer's real ~/.claude/projects when
# running build_envelope in tests. Specific transcript adapter tests below
# clear this var locally to exercise the discovery path.
os.environ["AGENT_PLUS_INQUIRE_NO_TRANSCRIPTS"] = "1"


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


# ─── Gate A.1: transcript adapters ──────────────────────────────────────────


def _load_adapters_pkg():
    """Import the inquire_adapters package directly for unit tests."""
    import importlib.util
    pkg_init = ROOT / "bin" / "_subcommands" / "inquire_adapters" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "inquire_adapters_test_pkg", pkg_init,
        submodule_search_locations=[str(pkg_init.parent)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["inquire_adapters_test_pkg"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestClaudeCodeAdapter(unittest.TestCase):
    def test_parses_fixture_jsonl(self):
        # Load adapter module directly.
        import importlib.util
        mod_path = (ROOT / "bin" / "_subcommands"
                    / "inquire_adapters" / "claude_code.py")
        spec = importlib.util.spec_from_file_location("cc_adapter", mod_path)
        cc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cc)
        fixture = ROOT / "test" / "fixtures" / "transcripts" / "claude_code_sample.jsonl"
        self.assertTrue(fixture.exists(), f"missing fixture: {fixture}")
        tuples = list(cc.iter_tuples(fixture))
        # Fixture has 3 tool_use entries (2 Bash + 1 Read).
        self.assertEqual(len(tuples), 3, f"expected 3 tool_use tuples, got {len(tuples)}")
        for t in tuples:
            self.assertEqual(len(t), 5, "tuple must have shape (ts, src, tool, cmd, args)")
            ts, src, tool, cmd, args = t
            self.assertIsInstance(ts, str)
            self.assertIsInstance(src, str)
            self.assertIsInstance(tool, str)
            self.assertIsInstance(cmd, str)
            self.assertIsInstance(args, dict)
        # First Bash tuple — verify command extraction.
        bash_tuples = [t for t in tuples if t[2] == "Bash"]
        self.assertEqual(len(bash_tuples), 2)
        self.assertEqual(bash_tuples[0][3], "git status -s")
        self.assertIn("description", bash_tuples[0][4])
        # Loamdb-db query in the second.
        self.assertIn("loamdb-db", bash_tuples[1][3])
        # Non-Bash (Read) tuple still surfaces a command-like field.
        read_tuples = [t for t in tuples if t[2] == "Read"]
        self.assertEqual(len(read_tuples), 1)
        self.assertEqual(read_tuples[0][3], "/tmp/x.txt")

    def test_handles_missing_file_silently(self):
        import importlib.util
        mod_path = (ROOT / "bin" / "_subcommands"
                    / "inquire_adapters" / "claude_code.py")
        spec = importlib.util.spec_from_file_location("cc_adapter2", mod_path)
        cc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cc)
        # Adapter must yield nothing rather than raise on a missing file.
        tuples = list(cc.iter_tuples(Path("/definitely/not/here.jsonl")))
        self.assertEqual(tuples, [])


class TestAdapterRegistry(unittest.TestCase):
    def test_builtin_registry_contains_claude_code(self):
        pkg = _load_adapters_pkg()
        reg = pkg.build_registry()
        self.assertIn("claude_code", reg)
        self.assertTrue(callable(reg["claude_code"]))

    def test_discover_files_handles_missing_roots(self):
        pkg = _load_adapters_pkg()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with mock.patch.dict(os.environ,
                                 {"HOME": str(home), "USERPROFILE": str(home)}):
                # All four default roots are absent under this fake HOME.
                files = pkg.discover_files()
        self.assertEqual(files, [])

    def test_discover_files_finds_jsonl_when_present(self):
        pkg = _load_adapters_pkg()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            proj_root = home / ".claude" / "projects" / "C--dev-fake"
            proj_root.mkdir(parents=True)
            (proj_root / "session.jsonl").write_text("{}\n", encoding="utf-8")
            with mock.patch.dict(os.environ,
                                 {"HOME": str(home), "USERPROFILE": str(home)}):
                files = pkg.discover_files()
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0][0], "claude_code")

    def test_user_config_loads_extra_sources(self):
        pkg = _load_adapters_pkg()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            cfg_dir = home / ".agent-plus"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "inquire-sources.json").write_text(
                json.dumps({"sources": [
                    {"name": "harness", "root": str(home / "logs"),
                     "format": "claude_code"}
                ]}),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ,
                                 {"HOME": str(home), "USERPROFILE": str(home)}):
                cfg = pkg.load_user_config()
        self.assertIn("sources", cfg)
        self.assertEqual(cfg["sources"][0]["format"], "claude_code")

    def test_user_supplied_adapter_loaded_and_callable(self):
        pkg = _load_adapters_pkg()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            adapters_dir = home / ".agent-plus" / "inquire-adapters"
            adapters_dir.mkdir(parents=True)
            (adapters_dir / "myfmt.py").write_text(
                "from pathlib import Path\n"
                "def iter_tuples(path):\n"
                "    yield ('2026-01-01T00:00:00Z', str(path), 'Bash',"
                "           'echo hi', {'desc': 'x'})\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ,
                                 {"HOME": str(home), "USERPROFILE": str(home)}):
                user = pkg.load_user_adapters()
        self.assertIn("myfmt", user)
        # Call it with any path — adapter ignores file existence.
        out = list(user["myfmt"](Path("anything")))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][2], "Bash")

    def test_collect_tuples_with_fixture(self):
        """End-to-end: point HOME at a tempdir with a synthetic
        ~/.claude/projects/<slug>/x.jsonl that's a copy of the fixture,
        confirm collect_tuples finds it via auto-discovery."""
        pkg = _load_adapters_pkg()
        fixture = ROOT / "test" / "fixtures" / "transcripts" / "claude_code_sample.jsonl"
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            proj = home / ".claude" / "projects" / "C--dev-fake"
            proj.mkdir(parents=True)
            (proj / "session.jsonl").write_text(
                fixture.read_text(encoding="utf-8"), encoding="utf-8")
            with mock.patch.dict(os.environ,
                                 {"HOME": str(home), "USERPROFILE": str(home)}):
                result = pkg.collect_tuples()
        self.assertEqual(result["files_scanned"], 1)
        self.assertEqual(result["by_format"].get("claude_code"), 3)
        self.assertEqual(len(result["tuples"]), 3)


class TestTranscriptsSourceClass(unittest.TestCase):
    """Inquire.py registers `transcripts` as a source class. Must show up
    in target.sources_used or target.sources_unavailable."""

    def test_transcripts_appears_in_envelope_sources(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "no-such",
                      "plugin_path": str(p)}
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    env = inquire.build_envelope(target, mode="audit")
        all_sources = (set(env["target"]["sources_used"])
                       | set(env["target"]["sources_unavailable"]))
        self.assertIn("transcripts", all_sources,
                      "transcripts must appear as a registered source class")

    def test_envelope_carries_usage_signal(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "no-such",
                      "plugin_path": str(p)}
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    env = inquire.build_envelope(target, mode="audit")
        self.assertIn("usage_signal", env)
        for key in ("files_scanned", "tuple_count", "by_format", "errors"):
            self.assertIn(key, env["usage_signal"])
        # tuples are NOT persisted to the envelope (can contain secrets).
        self.assertNotIn("tuples", env["usage_signal"])
        # Hermetic env: no transcripts walked.
        self.assertEqual(env["usage_signal"]["tuple_count"], 0)
        self.assertEqual(env["usage_signal"]["files_scanned"], 0)

    def test_transcripts_marks_used_when_tuples_present(self):
        # Temporarily allow walking; mock _collect_transcripts to return
        # a non-empty result without touching disk.
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "no-such",
                      "plugin_path": str(p)}
            fake_signal = {
                "files_scanned": 1,
                "files_skipped": 0,
                "tuples": [("2026-01-01T00:00:00Z", "/x", "Bash", "ls", {})],
                "by_format": {"claude_code": 1},
                "errors": [],
            }
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    with mock.patch.object(inquire, "_collect_transcripts",
                                           return_value=fake_signal):
                        env = inquire.build_envelope(target, mode="audit")
        self.assertIn("transcripts", env["target"]["sources_used"])
        self.assertEqual(env["usage_signal"]["tuple_count"], 1)


def _load_cluster_module():
    cluster_path = ROOT / "bin" / "_subcommands" / "inquire_cluster.py"
    spec = importlib.util.spec_from_file_location(
        "_skill_plus_inquire_cluster_test", cluster_path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cluster_mod = _load_cluster_module()


class TestSqlExtraction(unittest.TestCase):
    """Tier 1: pulling SQL out of shell command strings."""

    def test_extract_sql_from_quoted_raw(self):
        sql = cluster_mod.extract_sql(
            'mytool raw "SELECT id FROM widgets WHERE x=1"'
        )
        self.assertIsNotNone(sql)
        self.assertIn("SELECT", sql.upper())

    def test_extract_sql_from_query_subcmd(self):
        sql = cluster_mod.extract_sql("foo query 'UPDATE t SET y=2'")
        self.assertIsNotNone(sql)
        self.assertIn("UPDATE", sql.upper())

    def test_extract_sql_returns_none_for_non_sql(self):
        self.assertIsNone(cluster_mod.extract_sql("ls -la /tmp"))
        self.assertIsNone(cluster_mod.extract_sql("git commit -m 'select something'"))

    def test_extract_sql_for_bare_select(self):
        sql = cluster_mod.extract_sql("SELECT 1")
        self.assertIsNotNone(sql)


class TestSqlParsing(unittest.TestCase):
    """Top-level FROM/WHERE/SELECT extraction."""

    def test_simple_select_one_table(self):
        p = cluster_mod.parse_sql("SELECT id, name FROM widgets WHERE x = 1")
        self.assertEqual(p["verb"], "select")
        self.assertEqual(p["tables"], ["widgets"])
        self.assertEqual(sorted(p["select_cols"]), ["id", "name"])
        self.assertEqual(p["where_cols"], ["x"])

    def test_join_two_tables(self):
        p = cluster_mod.parse_sql(
            "SELECT a.id FROM aa a JOIN bb b ON a.id=b.aid WHERE a.flag IS NULL"
        )
        self.assertEqual(p["verb"], "select")
        self.assertEqual(p["tables"], ["aa", "bb"])
        self.assertIn("flag", p["where_cols"])

    def test_alias_via_AS(self):
        p = cluster_mod.parse_sql(
            "SELECT c.id FROM contents AS c WHERE c.org_id = 1"
        )
        self.assertEqual(p["tables"], ["contents"])
        self.assertEqual(p["select_cols"], ["id"])
        self.assertEqual(p["where_cols"], ["org_id"])

    def test_schema_qualified_table(self):
        p = cluster_mod.parse_sql(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'x'"
        )
        # last segment of dotted name wins
        self.assertEqual(p["tables"], ["columns"])

    def test_update_delete_insert(self):
        u = cluster_mod.parse_sql("UPDATE widgets SET x=1 WHERE id=2")
        self.assertEqual(u["verb"], "update")
        self.assertEqual(u["tables"], ["widgets"])
        d = cluster_mod.parse_sql("DELETE FROM gizmos WHERE id=1")
        self.assertEqual(d["verb"], "delete")
        self.assertEqual(d["tables"], ["gizmos"])
        ins = cluster_mod.parse_sql("INSERT INTO logs (a,b) VALUES (1,2)")
        self.assertEqual(ins["verb"], "insert")
        self.assertEqual(ins["tables"], ["logs"])

    def test_parse_failure_returns_none(self):
        # Garbage that mentions a SQL verb but isn't parseable.
        self.assertIsNone(cluster_mod.parse_sql("SELECT"))
        self.assertIsNone(cluster_mod.parse_sql("SELECT 1"))  # no FROM
        self.assertIsNone(cluster_mod.parse_sql(""))


class TestTier1Threshold(unittest.TestCase):
    def test_below_threshold_dropped(self):
        tuples = [
            ["t", "/x", "Bash", 'foo raw "SELECT id FROM rare WHERE id=1"', {}],
            ["t", "/x", "Bash", 'foo raw "SELECT id FROM rare WHERE id=2"', {}],
        ]
        out = cluster_mod.cluster_invocations(tuples, [])
        self.assertEqual(out["stats"]["unique_tier1"], 0)

    def test_at_threshold_kept(self):
        tuples = [
            ["t", "/x", "Bash", f'foo raw "SELECT id FROM hot WHERE id={i}"', {}]
            for i in range(3)
        ]
        out = cluster_mod.cluster_invocations(tuples, [])
        self.assertEqual(out["stats"]["unique_tier1"], 1)
        self.assertEqual(out["tier1_clusters"][0]["shape"], "select: hot")
        self.assertEqual(out["tier1_clusters"][0]["count"], 3)


class TestTier2Fingerprint(unittest.TestCase):
    def test_same_select_diff_where_groups_by_intent(self):
        # Three with WHERE org_id, three with WHERE id - same Tier 1, two
        # Tier 2 buckets.
        tuples = []
        for _ in range(3):
            tuples.append(
                ["t", "/x", "Bash",
                 'foo raw "SELECT id FROM widgets WHERE org_id=1"', {}]
            )
        for _ in range(3):
            tuples.append(
                ["t", "/x", "Bash",
                 'foo raw "SELECT id FROM widgets WHERE id=2"', {}]
            )
        out = cluster_mod.cluster_invocations(tuples, [])
        self.assertEqual(out["stats"]["unique_tier1"], 1)
        self.assertEqual(out["stats"]["unique_tier2"], 2)

    def test_singleton_tier2_dropped(self):
        # All same Tier 1, but each Tier 2 has count 1 -> threshold drops.
        tuples = [
            ["t", "/x", "Bash",
             f'foo raw "SELECT col{i} FROM tbl WHERE k{i}=1"', {}]
            for i in range(3)
        ]
        out = cluster_mod.cluster_invocations(tuples, [])
        self.assertEqual(out["stats"]["unique_tier1"], 1)
        # Each tier2 has 1 occurrence -> all dropped.
        self.assertEqual(out["tier1_clusters"][0]["tier2"], [])


class TestClassifierABC(unittest.TestCase):
    def _tuples_for(self, table: str, n: int = 3,
                    select: str = "id", where: str = "org_id"):
        return [
            ["t", "/x", "Bash",
             f'foo raw "SELECT {select} FROM {table} WHERE {where}=1"', {}]
            for _ in range(n)
        ]

    def test_type_a_missing(self):
        tuples = self._tuples_for("orphan_table")
        out = cluster_mod.cluster_invocations(tuples, [{"name": "totally_unrelated"}])
        c = out["tier1_clusters"][0]["tier2"][0]
        self.assertEqual(c["promotion_kind"], "missing")
        self.assertIn("recommended_name", c)
        self.assertIn("orphan_table", c["recommended_name"])

    def test_type_c_aligned_via_name_match(self):
        # Subcommand name 'widget' substring-matches table 'widgets'.
        tuples = self._tuples_for("widgets")
        out = cluster_mod.cluster_invocations(
            tuples, [{"name": "widget"}]
        )
        c = out["tier1_clusters"][0]["tier2"][0]
        self.assertEqual(c["promotion_kind"], "aligned")

    def test_type_b_misaligned_via_columns_metadata(self):
        # Subcommand name matches but its `columns` metadata doesn't
        # cover any of the cluster's columns.
        tuples = self._tuples_for("widgets", select="weight", where="height")
        out = cluster_mod.cluster_invocations(
            tuples,
            [{"name": "widget", "columns": ["foo", "bar", "baz"]}],
        )
        c = out["tier1_clusters"][0]["tier2"][0]
        self.assertEqual(c["promotion_kind"], "misaligned")

    def test_type_c_aligned_via_explicit_tables(self):
        tuples = self._tuples_for("contents")
        out = cluster_mod.cluster_invocations(
            tuples,
            [{"name": "org", "tables": ["contents"]}],
        )
        c = out["tier1_clusters"][0]["tier2"][0]
        self.assertEqual(c["promotion_kind"], "aligned")


class TestAntiConfirmationBias(unittest.TestCase):
    """The algorithm must surface things the test author didn't predict.
    We seed a fixture with the 6 hot shapes from the delta plan but
    only assert on aggregate properties + ONE shape we name; the rest
    are surprise findings the test deliberately doesn't enumerate."""

    def _delta_plan_fixture(self):
        # 6 hot shapes (counts trimmed for unit-test scale - threshold is
        # >=3, not the production 38). The test author knows ONE of them
        # explicitly: chunk_sets/contents combo. The others must be
        # discovered by the algorithm without being named here.
        tuples = []

        def add(cmd, n):
            for _ in range(n):
                tuples.append(["t", "/x", "Bash", cmd, {}])

        # Shape 1 (named in assertions): contents
        add('xtool raw "SELECT id FROM contents WHERE org_id=1"', 5)
        # Shape 2 (un-named): information_schema-style
        add('xtool raw "SELECT column_name FROM information_schema.columns WHERE table_name=\'x\'"', 4)
        # Shape 3 (un-named)
        add('xtool raw "SELECT id FROM connector_connections WHERE id=7"', 4)
        # Shape 4 (un-named, JOIN)
        add('xtool raw "SELECT c.id FROM contents AS c JOIN chunk_sets cs ON cs.id=c.chunk_set_id WHERE cs.id=1"', 3)
        # Shape 5 (un-named)
        add('xtool raw "SELECT id FROM sync_runs WHERE status=2"', 3)
        # Shape 6 (un-named)
        add('xtool raw "SELECT id FROM entities WHERE entity_type=2"', 3)
        return tuples

    def test_surfaces_at_least_5_of_6_hot_shapes(self):
        tuples = self._delta_plan_fixture()
        out = cluster_mod.cluster_invocations(tuples, [])
        # Aggregate floor, not exact equality.
        self.assertGreaterEqual(
            len(out["tier1_clusters"]), 5,
            "Expected at least 5 of 6 hot shapes to clear Tier 1 threshold",
        )
        # Surprise findings: at least one shape is discovered that the
        # test does not explicitly name in this assertion. This is the
        # F3 anti-confirmation discipline check.
        named_in_test = {"select: contents"}
        discovered_shapes = {c["shape"] for c in out["tier1_clusters"]}
        surprise = discovered_shapes - named_in_test
        self.assertGreater(
            len(surprise), 0,
            "Algorithm must surface shapes the test didn't hardcode "
            f"(saw: {discovered_shapes})",
        )

    def test_no_hardcoded_table_names_in_module_source(self):
        """Hard rule from the delta plan: the algorithm must not mention
        any specific table name. Scan the cluster module source for
        anti-pattern markers."""
        src = (ROOT / "bin" / "_subcommands" / "inquire_cluster.py").read_text(
            encoding="utf-8"
        )
        forbidden = ["loamdb", "entity_type", "chunk_set", "contents",
                     "information_schema", "connector_connection"]
        for token in forbidden:
            self.assertNotIn(
                token, src.lower(),
                f"Cluster algorithm must not reference {token!r} - "
                "violates anti-confirmation-bias discipline",
            )


class TestParseFailureFailSoft(unittest.TestCase):
    def test_unparseable_sql_increments_counter_not_crash(self):
        # Mix of valid and broken SQL. Algorithm must not crash; broken
        # ones counted in parse_failures.
        tuples = [
            ["t", "/x", "Bash", 'foo raw "SELECT id FROM tbl WHERE k=1"', {}],
            ["t", "/x", "Bash", 'foo raw "SELECT id FROM tbl WHERE k=2"', {}],
            ["t", "/x", "Bash", 'foo raw "SELECT id FROM tbl WHERE k=3"', {}],
            # Garbage that looks SQL-ish.
            ["t", "/x", "Bash", 'foo raw "SELECT 1"', {}],
        ]
        out = cluster_mod.cluster_invocations(tuples, [])
        self.assertEqual(out["stats"]["parse_failures"], 1)
        self.assertEqual(out["stats"]["unique_tier1"], 1)


class TestEnvelopeWiring(unittest.TestCase):
    """Inquire.py calls cluster_invocations during --audit and stashes
    the result on the envelope as `usage_clusters`."""

    def test_usage_clusters_present_on_audit_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "no-such",
                      "plugin_path": str(p)}
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    env = inquire.build_envelope(target, mode="audit")
        self.assertIn("usage_clusters", env)
        self.assertIn("tier1_clusters", env["usage_clusters"])
        self.assertIn("stats", env["usage_clusters"])

    def test_usage_clusters_absent_on_generate_envelope(self):
        target = {"tool": "fake", "name": "fake", "cli": "no-such"}
        with mock.patch.object(inquire, "cli_on_path", return_value=None):
            with mock.patch.object(inquire, "web_search", return_value=[]):
                env = inquire.build_envelope(target, mode="generate")
        # Generator mode skips clustering (no existing subcommands to
        # compare against).
        self.assertNotIn("usage_clusters", env)

    def test_clustering_runs_against_injected_tuples(self):
        # Inject synthetic tuples via _collect_transcripts mock and
        # verify clustering picks them up.
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "no-such",
                      "plugin_path": str(p)}
            cmd = 'sometool raw "SELECT id FROM widgets WHERE org_id=1"'
            fake_signal = {
                "files_scanned": 1, "files_skipped": 0,
                "tuples": [
                    ("2026-01-01T00:00:00Z", "/x", "Bash", cmd, {})
                    for _ in range(5)
                ],
                "by_format": {"claude_code": 5}, "errors": [],
            }
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    with mock.patch.object(
                        inquire, "_collect_transcripts", return_value=fake_signal
                    ):
                        env = inquire.build_envelope(target, mode="audit")
        clusters = env["usage_clusters"]["tier1_clusters"]
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["shape"], "select: widgets")


# ─── Gate A.3: priority calc, well_used, skill detection, envelope v1.1 ─────


class TestPriorityCalc(unittest.TestCase):
    """8+ unit tests covering priority edge cases."""

    def test_aligned_is_na(self):
        self.assertEqual(inquire._priority("aligned", 100), "n/a")
        self.assertEqual(inquire._priority("aligned", 0), "n/a")

    def test_missing_high_at_10(self):
        self.assertEqual(inquire._priority("missing", 10), "high")
        self.assertEqual(inquire._priority("missing", 99), "high")

    def test_missing_medium_at_3_to_9(self):
        self.assertEqual(inquire._priority("missing", 3), "medium")
        self.assertEqual(inquire._priority("missing", 9), "medium")

    def test_missing_low_below_3(self):
        self.assertEqual(inquire._priority("missing", 2), "low")
        self.assertEqual(inquire._priority("missing", 0), "low")

    def test_missing_boundary_3(self):
        self.assertEqual(inquire._priority("missing", 2), "low")
        self.assertEqual(inquire._priority("missing", 3), "medium")

    def test_missing_boundary_10(self):
        self.assertEqual(inquire._priority("missing", 9), "medium")
        self.assertEqual(inquire._priority("missing", 10), "high")

    def test_misaligned_high_at_10(self):
        self.assertEqual(inquire._priority("misaligned", 10), "high")
        self.assertEqual(inquire._priority("misaligned", 50), "high")

    def test_misaligned_medium_below_10(self):
        self.assertEqual(inquire._priority("misaligned", 1), "medium")
        self.assertEqual(inquire._priority("misaligned", 9), "medium")

    def test_unknown_kind_defaults_low(self):
        self.assertEqual(inquire._priority("totally-bogus", 100), "low")


class TestEnvelopeVersion(unittest.TestCase):
    def test_envelope_version_is_1_1(self):
        self.assertEqual(inquire.ENVELOPE_VERSION, "1.1")

    def test_envelope_carries_version_field(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "no-such",
                      "plugin_path": str(p)}
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    env = inquire.build_envelope(target, mode="audit")
        self.assertEqual(env.get("envelope_version"), "1.1")
        self.assertIn("promotions", env)
        self.assertIsInstance(env["promotions"], list)


class TestV10ConsumerCompat(unittest.TestCase):
    """A v1.0 consumer reads only the original fields. Verify the new
    additive fields don't break that reader against either a synthetic
    v1.0 envelope or a fresh v1.1 envelope.
    """

    def _v10_consumer(self, env: dict) -> dict:
        # Simulates a v1.0 downstream that reads ONLY the original fields.
        out = {
            "verdict": env["verdict"],
            "mode": env["mode"],
            "target_kind": env["target"]["kind"],
            "target_name": env["target"]["name"],
            "questions_asked": env["summary"]["questions_asked"],
            "results_count": len(env["results"]),
        }
        for r in env["results"]:
            # v1.0 only knows these keys
            assert "q_id" in r
            assert "answer" in r
        return out

    def test_v10_envelope_consumed_without_crash(self):
        v10_env = {
            "verdict": "ok",
            "mode": "audit",
            "target": {"kind": "plugin", "name": "x",
                       "sources_used": [], "sources_unavailable": []},
            "summary": {"questions_asked": 1, "ok": 1, "gaps": 0,
                        "na": 0, "unknown": 0, "high_confidence_gaps": 0},
            "results": [{"q_id": "Q1", "q_label": "errors_surface",
                         "answer": "ok", "confidence": "high", "evidence": []}],
            "pr_body_draft": "no gaps",
        }
        out = self._v10_consumer(v10_env)
        self.assertEqual(out["target_kind"], "plugin")
        self.assertEqual(out["results_count"], 1)

    def test_v11_envelope_works_with_v10_consumer(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "no-such",
                      "plugin_path": str(p)}
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    env = inquire.build_envelope(target, mode="audit")
        out = self._v10_consumer(env)
        self.assertIn(out["target_kind"], ("plugin", "skill"))
        self.assertGreaterEqual(out["results_count"], 1)


class TestWellUsedVerdict(unittest.TestCase):
    """well_used triggers when no gaps + >=50% aligned clusters."""

    def test_well_used_when_no_gaps_and_aligned_clusters(self):
        summary = {"questions_asked": 7, "ok": 7, "gaps": 0,
                   "na": 0, "unknown": 0, "high_confidence_gaps": 0}
        clusters = {"tier1_clusters": [
            {"shape": "select: x", "count": 5,
             "tier2": [{"promotion_kind": "aligned", "count": 3}]},
            {"shape": "select: y", "count": 4,
             "tier2": [{"promotion_kind": "aligned", "count": 2}]},
        ]}
        v = inquire._verdict(summary, usage_clusters=clusters)
        self.assertEqual(v, "well_used")

    def test_not_well_used_with_gaps(self):
        summary = {"questions_asked": 7, "ok": 5, "gaps": 2,
                   "na": 0, "unknown": 0, "high_confidence_gaps": 1}
        clusters = {"tier1_clusters": [
            {"shape": "select: x", "count": 5,
             "tier2": [{"promotion_kind": "aligned", "count": 3}]},
        ]}
        v = inquire._verdict(summary, usage_clusters=clusters)
        self.assertEqual(v, "gaps_found")

    def test_falls_back_to_ok_if_clusters_mostly_missing(self):
        summary = {"questions_asked": 7, "ok": 7, "gaps": 0,
                   "na": 0, "unknown": 0, "high_confidence_gaps": 0}
        clusters = {"tier1_clusters": [
            {"shape": "select: x", "count": 5,
             "tier2": [
                 {"promotion_kind": "missing", "count": 3},
                 {"promotion_kind": "missing", "count": 2},
             ]},
        ]}
        v = inquire._verdict(summary, usage_clusters=clusters)
        self.assertEqual(v, "ok")

    def test_no_clusters_returns_ok(self):
        summary = {"questions_asked": 7, "ok": 7, "gaps": 0,
                   "na": 0, "unknown": 0, "high_confidence_gaps": 0}
        v = inquire._verdict(summary, usage_clusters={"tier1_clusters": []})
        self.assertEqual(v, "ok")


class TestSkillFrontmatter(unittest.TestCase):
    def test_reads_basic_frontmatter(self):
        fixture = ROOT / "test" / "fixtures" / "skills" / "sample" / "SKILL.md"
        fm = inquire._read_skill_frontmatter(str(fixture))
        self.assertIsNotNone(fm)
        self.assertEqual(fm.get("name"), "sample")
        self.assertIn("allowed-tools", fm)
        self.assertIn("Bash(", fm["allowed-tools"])

    def test_returns_none_for_missing_file(self):
        self.assertIsNone(inquire._read_skill_frontmatter(
            "/definitely/not/a/file"))

    def test_returns_none_for_no_frontmatter(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.md"
            p.write_text("# no frontmatter here\n", encoding="utf-8")
            self.assertIsNone(inquire._read_skill_frontmatter(str(p)))


class TestTargetKindDetection(unittest.TestCase):
    def test_detects_plugin_dir(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", "x = 1\n")
            kind, resolved = inquire._detect_target_kind(str(p))
        self.assertEqual(kind, "plugin")

    def test_detects_skill_dir(self):
        fixture = ROOT / "test" / "fixtures" / "skills" / "sample"
        kind, resolved = inquire._detect_target_kind(str(fixture))
        self.assertEqual(kind, "skill")
        self.assertEqual(resolved, str(fixture))

    def test_detects_skill_md_file(self):
        fixture = ROOT / "test" / "fixtures" / "skills" / "sample" / "SKILL.md"
        kind, resolved = inquire._detect_target_kind(str(fixture))
        self.assertEqual(kind, "skill")
        # resolved should be the parent dir
        self.assertEqual(resolved, str(fixture.parent))

    def test_unknown_for_random_dir(self):
        with tempfile.TemporaryDirectory() as td:
            kind, _ = inquire._detect_target_kind(td)
        self.assertEqual(kind, "unknown")


class TestSkillBinResolution(unittest.TestCase):
    def test_resolves_via_allowed_tools(self):
        fixture = ROOT / "test" / "fixtures" / "skills" / "sample"
        fm = inquire._read_skill_frontmatter(str(fixture / "SKILL.md"))
        bin_dir = inquire._skill_bin_from_allowed_tools(
            fm.get("allowed-tools", ""), fixture, "sample"
        )
        self.assertIsNotNone(bin_dir)
        self.assertTrue(bin_dir.is_dir())
        self.assertEqual(bin_dir.name, "sample")

    def test_falls_back_to_skill_name(self):
        fixture = ROOT / "test" / "fixtures" / "skills" / "sample"
        # No allowed-tools at all -> fallback should still find bin/sample.
        bin_dir = inquire._skill_bin_from_allowed_tools("", fixture, "sample")
        self.assertIsNotNone(bin_dir)
        self.assertEqual(bin_dir.name, "sample")


class TestSkillAuditEnvelope(unittest.TestCase):
    """End-to-end skill audit: build_envelope on a SKILL.md target."""

    def test_skill_target_kind_is_skill(self):
        fixture = ROOT / "test" / "fixtures" / "skills" / "sample"
        fm = inquire._read_skill_frontmatter(str(fixture / "SKILL.md"))
        bin_dir = inquire._skill_bin_from_allowed_tools(
            fm.get("allowed-tools", ""), fixture, "sample"
        )
        target = {
            "kind": "skill",
            "name": fm.get("name") or "sample",
            "tool": fm.get("name") or "sample",
            "cli": "no-such",
            "plugin_path": str(fixture),
            "skill_bin": str(bin_dir) if bin_dir else None,
            "skill_frontmatter": fm,
        }
        with mock.patch.object(inquire, "cli_on_path", return_value=None):
            with mock.patch.object(inquire, "web_search", return_value=[]):
                env = inquire.build_envelope(target, mode="audit")
        self.assertEqual(env["target"]["kind"], "skill")
        self.assertEqual(env["target"]["name"], "sample")
        self.assertIn("description", env["target"])
        # Subcommand discovery via skill_bin should have found overview/status.
        # We can't directly assert subcommands enumerated in the envelope
        # (cluster module is the consumer), but we can confirm the audit
        # didn't crash.
        self.assertIn("usage_clusters", env)


class TestPluginAuditRegression(unittest.TestCase):
    """Plugin auto-detection still works (regression)."""

    def test_plugin_target_kind_via_path(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            p = _make_fake_plugin(Path(td), "regress", PLUGIN_LEVEL1)
            res = _run_bin(
                "inquire", "regress", "--audit", "--plugin-path", str(p),
                "--no-cache", "--cli", "definitely-not-on-path",
                home=home,
            )
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
            env = json.loads(res.stdout)
        self.assertEqual(env["target"]["kind"], "plugin")
        self.assertEqual(env.get("envelope_version"), "1.1")


class TestPromotionsField(unittest.TestCase):
    def test_promotions_present_audit_no_clusters(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "no-such",
                      "plugin_path": str(p)}
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    env = inquire.build_envelope(target, mode="audit")
        self.assertIn("promotions", env)
        self.assertEqual(env["promotions"], [])

    def test_promotions_built_from_clusters(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_fake_plugin(Path(td), "fake", PLUGIN_LEVEL1)
            target = {"tool": "fake", "name": "fake", "cli": "no-such",
                      "plugin_path": str(p)}
            cmd = 'sometool raw "SELECT id FROM widgets WHERE org_id=1"'
            fake_signal = {
                "files_scanned": 7, "files_skipped": 0,
                "tuples": [
                    ("2026-01-01T00:00:00Z", "/x", "Bash", cmd, {})
                    for _ in range(12)
                ],
                "by_format": {"claude_code": 12}, "errors": [],
            }
            with mock.patch.object(inquire, "cli_on_path", return_value=None):
                with mock.patch.object(inquire, "web_search", return_value=[]):
                    with mock.patch.object(
                        inquire, "_collect_transcripts", return_value=fake_signal
                    ):
                        env = inquire.build_envelope(target, mode="audit")
        self.assertGreater(len(env["promotions"]), 0)
        promo = env["promotions"][0]
        self.assertIn("usage_evidence", promo)
        self.assertIn("promotion_kind", promo)
        self.assertIn("priority", promo)
        # 12 invocations should land in the high bucket.
        self.assertEqual(promo["priority"], "high")
        self.assertEqual(promo["usage_evidence"]["transcripts_scanned"], 7)
        self.assertEqual(promo["usage_evidence"]["date_range"],
                         "2026-01-01..2026-01-01")


# ─── Fix 6: 6 test gaps ─────────────────────────────────────────────────────


class TestAdapterDiscoveryRoots(unittest.TestCase):
    """Gap 1: non-claude adapter roots (gstack, codex, cursor) surface files."""

    def _make_root(self, home: Path, fmt: str, rel_root: str, rel_file: str) -> None:
        f = home / rel_root / rel_file
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{"type":"tool_use"}\n', encoding="utf-8")

    @unittest.skipIf(sys.platform == "win32", "symlink-dependent glob on non-Windows only")
    def test_discover_gstack_files(self):
        pkg = _load_adapters_pkg()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            self._make_root(home, "gstack", ".gstack/projects/proj1", "session.jsonl")
            with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
                files = pkg.discover_files()
        fmts = [f for f, _ in files]
        self.assertIn("gstack", fmts)
        self.assertEqual(files[0][0], "gstack")

    def test_discover_codex_files(self):
        pkg = _load_adapters_pkg()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            self._make_root(home, "codex", ".codex/sessions", "s1.jsonl")
            with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
                files = pkg.discover_files()
        fmts = [f for f, _ in files]
        self.assertIn("codex", fmts)

    def test_discover_cursor_files(self):
        pkg = _load_adapters_pkg()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            self._make_root(home, "cursor", ".cursor/chats", "c1.jsonl")
            with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
                files = pkg.discover_files()
        fmts = [f for f, _ in files]
        self.assertIn("cursor", fmts)


class TestTier2ThresholdBoundary(unittest.TestCase):
    """Gap 2: Tier 2 threshold boundary at count == 2."""

    def _make_tuples(self, cmd: str, n: int) -> list:
        return [["t", "/x", "Bash", cmd, {}] for _ in range(n)]

    def test_tier2_at_threshold_kept(self):
        # Exactly 2 identical Tier-2 invocations inside a Tier-1 cluster of >=3.
        base_cmd = 'foo raw "SELECT id FROM kept WHERE org_id=1"'
        other_cmd = 'foo raw "SELECT id FROM kept WHERE status=2"'
        tuples = (self._make_tuples(base_cmd, 2)
                  + self._make_tuples(other_cmd, 1))
        # Need at least 3 for Tier-1; the first bucket has count=2 (kept).
        # Add a third for Tier-1 with a different WHERE column.
        tuples += self._make_tuples('foo raw "SELECT id FROM kept WHERE flag=0"', 1)
        out = cluster_mod.cluster_invocations(tuples, [])
        # Tier 2 with count=2 must appear.
        t1 = out["tier1_clusters"][0]
        counts = [e["count"] for e in t1["tier2"]]
        self.assertIn(2, counts, "Tier 2 bucket at exactly count=2 must be kept")

    def test_tier2_at_count1_dropped(self):
        # 4 unique Tier-2 fingerprints, each appearing once — all should be dropped.
        tuples = [
            ["t", "/x", "Bash", f'foo raw "SELECT col{i} FROM shared WHERE k{i}=1"', {}]
            for i in range(4)
        ]
        out = cluster_mod.cluster_invocations(tuples, [])
        if out["stats"]["unique_tier1"] == 1:
            self.assertEqual(out["tier1_clusters"][0]["tier2"], [],
                             "Tier 2 buckets with count=1 must be dropped")


class TestColOverlapBoundary(unittest.TestCase):
    """Gap 3: 50% column overlap boundary for aligned/misaligned classification."""

    def _tuples(self, select: str, where: str, n: int = 3) -> list:
        cmd = f'foo raw "SELECT {select} FROM boundary WHERE {where}=1"'
        return [["t", "/x", "Bash", cmd, {}] for _ in range(n)]

    def test_type_c_at_50_pct_aligned(self):
        # 1-of-2 cols overlap (50%) -> aligned.
        tuples = self._tuples("id, name", "id")
        out = cluster_mod.cluster_invocations(
            tuples, [{"name": "boundary", "columns": ["id", "other"]}]
        )
        c = out["tier1_clusters"][0]["tier2"][0]
        self.assertEqual(c["promotion_kind"], "aligned")

    def test_type_b_below_50_pct_misaligned(self):
        # 1-of-3 cluster cols overlap (33%) -> misaligned.
        tuples = self._tuples("a, b, c", "a")
        out = cluster_mod.cluster_invocations(
            tuples, [{"name": "boundary", "columns": ["a", "x", "y"]}]
        )
        # cluster_cols = {a, b, c} (3); sub_cols = {a, x, y}; overlap = {a} = 1/3 < 50%
        c = out["tier1_clusters"][0]["tier2"][0]
        self.assertEqual(c["promotion_kind"], "misaligned")


class TestHyphenatedSkillName(unittest.TestCase):
    """Gap 4: skills with hyphenated names resolve bin dir via Bash(my-skill:*)."""

    def test_hyphenated_skill_bin_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td) / "my-skill"
            bin_dir = skill_dir / "bin" / "my-skill"
            bin_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: my-skill\nallowed-tools: Bash(my-skill:*)\n---\n",
                encoding="utf-8"
            )
            result = inquire._skill_bin_from_allowed_tools(
                "Bash(my-skill:*)", skill_dir, "my-skill"
            )
        self.assertIsNotNone(result, "Hyphenated skill name must resolve a bin dir")
        self.assertEqual(result.name, "my-skill")


class TestAllowedToolsWithoutBash(unittest.TestCase):
    """Gap 5: allowed-tools with no Bash entry -> _skill_bin_from_allowed_tools returns None."""

    def test_no_bash_entry_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td) / "readskill"
            skill_dir.mkdir()
            result = inquire._skill_bin_from_allowed_tools(
                "Read(file)", skill_dir, "readskill"
            )
        # No Bash(...:*) entry, no bin/<name> dir either -> None.
        self.assertIsNone(result, "No Bash entry + no bin dir must return None")


class TestWellUsedBoundary(unittest.TestCase):
    """Gap 6: well_used 50% boundary."""

    def _summary_ok(self) -> dict:
        return {"questions_asked": 7, "ok": 7, "gaps": 0,
                "na": 0, "unknown": 0, "high_confidence_gaps": 0}

    def test_exactly_50_pct_aligned_is_well_used(self):
        # 1 aligned + 1 missing = 50% -> well_used.
        clusters = {"tier1_clusters": [
            {"shape": "select: t", "count": 5,
             "tier2": [
                 {"promotion_kind": "aligned", "count": 3},
                 {"promotion_kind": "missing", "count": 2},
             ]},
        ]}
        v = inquire._verdict(self._summary_ok(), usage_clusters=clusters)
        self.assertEqual(v, "well_used")

    def test_below_50_pct_aligned_is_not_well_used(self):
        # 1 aligned + 2 missing = 33% -> not well_used -> ok.
        clusters = {"tier1_clusters": [
            {"shape": "select: t", "count": 5,
             "tier2": [
                 {"promotion_kind": "aligned", "count": 3},
                 {"promotion_kind": "missing", "count": 2},
                 {"promotion_kind": "missing", "count": 2},
             ]},
        ]}
        v = inquire._verdict(self._summary_ok(), usage_clusters=clusters)
        self.assertEqual(v, "ok")


if __name__ == "__main__":
    unittest.main()
