"""Unit tests for linear-remote. Stdlib unittest only — no pytest, no Linear account."""

from __future__ import annotations

import importlib.util
import io
import json
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "linear-remote"
    loader = SourceFileLoader("linear_remote", str(bin_path))
    spec = importlib.util.spec_from_loader("linear_remote", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


lr = _load_module()


# ──────────────────────────── _scrub ────────────────────────────


class TestScrub(unittest.TestCase):
    def test_strips_top_level_secrets(self) -> None:
        obj = {"id": "x", "apiKey": "lin_api_xxx", "token": "t", "webhookSecret": "ws"}
        out = lr._scrub(obj)
        self.assertEqual(out["id"], "x")
        self.assertEqual(out["apiKey"], "[REDACTED]")
        self.assertEqual(out["token"], "[REDACTED]")
        self.assertEqual(out["webhookSecret"], "[REDACTED]")

    def test_strips_nested_secrets(self) -> None:
        obj = {
            "integration": {"apiKey": "lin_api_xxx", "provider": "github"},
            "webhook": {"secret": "super-secret", "url": "https://example.com"},
        }
        out = lr._scrub(obj)
        self.assertEqual(out["integration"]["apiKey"], "[REDACTED]")
        self.assertEqual(out["integration"]["provider"], "github")
        self.assertEqual(out["webhook"]["secret"], "[REDACTED]")
        self.assertEqual(out["webhook"]["url"], "https://example.com")

    def test_strips_secrets_in_list(self) -> None:
        obj = {"keys": [{"name": "ci", "apiKey": "lin_api_a"}, {"name": "bot", "apiKey": "lin_api_b"}]}
        out = lr._scrub(obj)
        for item in out["keys"]:
            self.assertEqual(item["apiKey"], "[REDACTED]")
            self.assertIn(item["name"], ("ci", "bot"))

    def test_primitives_pass_through(self) -> None:
        self.assertEqual(lr._scrub("hello"), "hello")
        self.assertEqual(lr._scrub(42), 42)
        self.assertEqual(lr._scrub(None), None)


# ──────────────────────────── canary no-leak ────────────────────────────


class TestCanaryNoLeak(unittest.TestCase):
    """Asserts a known canary secret never appears in any emitted output."""

    CANARY = "lin_api_CANARY_DO_NOT_LEAK_7d1c9f"

    def _capture_emit(self, obj) -> str:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            lr.emit_json(obj, pretty=True)
        return buf.getvalue()

    def test_canary_not_in_api_key_field(self) -> None:
        obj = {"integration": {"apiKey": self.CANARY, "name": "ci"}}
        out = self._capture_emit(obj)
        self.assertNotIn(self.CANARY, out)
        self.assertIn("ci", out)

    def test_canary_not_in_webhook_secret(self) -> None:
        obj = {"webhooks": [{"url": "https://ex.com", "webhookSecret": self.CANARY}]}
        out = self._capture_emit(obj)
        self.assertNotIn(self.CANARY, out)

    def test_canary_not_in_deeply_nested(self) -> None:
        obj = {"a": [{"b": {"c": [{"token": self.CANARY}, {"apiKey": self.CANARY}]}}]}
        out = self._capture_emit(obj)
        self.assertNotIn(self.CANARY, out)


# ──────────────────────────── markdown helpers ────────────────────────────


class TestNormaliseMarkdown(unittest.TestCase):
    def test_strips_html_comments(self) -> None:
        text = "Hello\n<!-- todo: remove -->\nWorld"
        out = lr._normalise_markdown(text)
        self.assertNotIn("<!--", out)
        self.assertIn("Hello", out)
        self.assertIn("World", out)

    def test_strips_multiline_html_comments(self) -> None:
        text = "Start\n<!--\nmulti\nline\n-->\nEnd"
        out = lr._normalise_markdown(text)
        self.assertNotIn("<!--", out)
        self.assertNotIn("multi", out)

    def test_preserves_team_autolinks(self) -> None:
        text = "See LOA-229 and ABC-12 for context."
        out = lr._normalise_markdown(text)
        self.assertIn("LOA-229", out)
        self.assertIn("ABC-12", out)

    def test_preserves_task_lists(self) -> None:
        text = "- [ ] todo\n- [x] done"
        out = lr._normalise_markdown(text)
        self.assertIn("- [ ]", out)
        self.assertIn("- [x]", out)

    def test_strips_frontmatter(self) -> None:
        text = "---\nteam: LOA\nlabels: [bug]\n---\n# Title\n\nBody here."
        out = lr._normalise_markdown(text)
        self.assertNotIn("team:", out)
        self.assertNotIn("---", out)
        self.assertIn("Title", out)
        self.assertIn("Body here", out)

    def test_empty_passes_through(self) -> None:
        self.assertEqual(lr._normalise_markdown(""), "")


class TestFrontmatter(unittest.TestCase):
    def test_parses_scalars(self) -> None:
        text = '---\nteam: LOA\nproject: "Agent Plus"\npriority: 2\n---\nbody'
        meta, body = lr._parse_frontmatter(text)
        self.assertEqual(meta["team"], "LOA")
        self.assertEqual(meta["project"], "Agent Plus")
        self.assertEqual(meta["priority"], "2")
        self.assertEqual(body.strip(), "body")

    def test_parses_inline_list(self) -> None:
        text = "---\nlabels: [bug, prod, urgent]\n---\n"
        meta, _ = lr._parse_frontmatter(text)
        self.assertEqual(meta["labels"], ["bug", "prod", "urgent"])

    def test_parses_multiline_list(self) -> None:
        text = "---\nlabels:\n  - bug\n  - prod\n---\n"
        meta, _ = lr._parse_frontmatter(text)
        self.assertEqual(meta["labels"], ["bug", "prod"])

    def test_no_frontmatter(self) -> None:
        text = "# Title\n\nBody"
        meta, body = lr._parse_frontmatter(text)
        self.assertEqual(meta, {})
        self.assertEqual(body, text)

    def test_empty_frontmatter(self) -> None:
        text = "---\n---\n# Title"
        meta, body = lr._parse_frontmatter(text)
        self.assertEqual(meta, {})
        self.assertIn("Title", body)


class TestExtractTitleBody(unittest.TestCase):
    def test_h1_is_title(self) -> None:
        text = "# My Issue\n\nThis is the body."
        title, body = lr._extract_title_and_body(text)
        self.assertEqual(title, "My Issue")
        self.assertEqual(body, "This is the body.")

    def test_no_h1_uses_first_line(self) -> None:
        text = "My Issue\n\nBody follows."
        title, body = lr._extract_title_and_body(text)
        self.assertEqual(title, "My Issue")
        self.assertIn("Body follows.", body)

    def test_h1_only(self) -> None:
        title, body = lr._extract_title_and_body("# Title Only")
        self.assertEqual(title, "Title Only")
        self.assertEqual(body, "")


# ──────────────────────────── ID format ────────────────────────────


class TestIdFormat(unittest.TestCase):
    def test_uuid_recognised(self) -> None:
        self.assertTrue(lr._is_uuid("12345678-1234-1234-1234-123456789012"))
        self.assertTrue(lr._is_uuid("ABCDEF12-ABCD-ABCD-ABCD-ABCDEF123456"))

    def test_uuid_rejects_non_uuid(self) -> None:
        self.assertFalse(lr._is_uuid("LOA-229"))
        self.assertFalse(lr._is_uuid("not-a-uuid"))
        self.assertFalse(lr._is_uuid(""))

    def test_human_key_recognised(self) -> None:
        self.assertTrue(lr._is_human_key("LOA-229"))
        self.assertTrue(lr._is_human_key("ABC-1"))
        self.assertTrue(lr._is_human_key("TEAM2-99"))

    def test_human_key_rejects(self) -> None:
        self.assertFalse(lr._is_human_key("loa-229"))  # lowercase
        self.assertFalse(lr._is_human_key("LOA"))       # no number
        self.assertFalse(lr._is_human_key("LOA229"))    # no hyphen
        self.assertFalse(lr._is_human_key(""))


# ──────────────────────────── config ────────────────────────────


class TestConfig(unittest.TestCase):
    def test_require_api_key_dies_without(self) -> None:
        with self.assertRaises(SystemExit):
            lr.require_api_key({})

    def test_require_api_key_returns(self) -> None:
        self.assertEqual(lr.require_api_key({"LINEAR_API_KEY": "lin_api_xxx"}), "lin_api_xxx")

    def test_die_on_missing_points_to_linear_settings(self) -> None:
        # Capture stderr to verify the error hint.
        import sys
        buf = io.StringIO()
        with patch.object(sys, "stderr", buf):
            with self.assertRaises(SystemExit):
                lr.require_api_key({})
        msg = buf.getvalue()
        self.assertIn("linear.app/settings/api", msg)
        self.assertIn("LINEAR_API_KEY", msg)


# ──────────────────────────── name-resolve ambiguity ────────────────────────────


class TestResolveAmbiguity(unittest.TestCase):
    """Simulated resolve scenarios — verifies ambiguity fails with candidates."""

    def setUp(self) -> None:
        # Reset per-process caches to keep tests isolated.
        lr._teams_cache.clear()
        lr._states_cache.clear()
        lr._labels_cache.clear()
        lr._users_cache.clear()
        lr._projects_cache.clear()

    def test_team_ambiguous_substring_fails_with_candidates(self) -> None:
        # Prime cache with two teams that both contain "core".
        cfg = {"LINEAR_API_KEY": "fake", "LINEAR_TEAM_ID": ""}
        lr._teams_cache[lr._cache_key(cfg)] = [
            {"id": "u1", "key": "COR", "name": "Core Team"},
            {"id": "u2", "key": "COE", "name": "Core Engineering"},
        ]
        import sys
        buf = io.StringIO()
        with patch.object(sys, "stderr", buf):
            with self.assertRaises(SystemExit):
                lr.resolve_team("core", cfg)
        err = buf.getvalue()
        self.assertIn("Ambiguous", err)
        self.assertIn("Core Team", err)
        self.assertIn("Core Engineering", err)

    def test_team_exact_key_match_wins(self) -> None:
        cfg = {"LINEAR_API_KEY": "fake"}
        lr._teams_cache[lr._cache_key(cfg)] = [
            {"id": "u1", "key": "LOA", "name": "Loa Team"},
            {"id": "u2", "key": "LOB", "name": "Lob Team"},
        ]
        result = lr.resolve_team("LOA", cfg)
        self.assertEqual(result["id"], "u1")

    def test_state_not_found_lists_available(self) -> None:
        cfg = {"LINEAR_API_KEY": "fake"}
        lr._states_cache[(lr._cache_key(cfg), "tid")] = [
            {"id": "s1", "name": "Todo", "type": "unstarted"},
            {"id": "s2", "name": "In Progress", "type": "started"},
        ]
        import sys
        buf = io.StringIO()
        with patch.object(sys, "stderr", buf):
            with self.assertRaises(SystemExit):
                lr.resolve_state("Backlog", "tid", cfg)
        err = buf.getvalue()
        self.assertIn("Todo", err)
        self.assertIn("In Progress", err)

    def test_label_ambiguous_fails(self) -> None:
        cfg = {"LINEAR_API_KEY": "fake"}
        lr._labels_cache[(lr._cache_key(cfg), None)] = [
            {"id": "l1", "name": "bug-frontend"},
            {"id": "l2", "name": "bug-backend"},
        ]
        import sys
        buf = io.StringIO()
        with patch.object(sys, "stderr", buf):
            with self.assertRaises(SystemExit):
                lr.resolve_labels(["bug"], None, cfg)
        err = buf.getvalue()
        self.assertIn("bug-frontend", err)
        self.assertIn("bug-backend", err)


# ──────────────────────────── --from-markdown integration ────────────────────────────


class TestFromMarkdownParsing(unittest.TestCase):
    """Does not hit the API — verifies frontmatter + title extraction for the
    --from-markdown path."""

    def test_full_frontmatter_and_h1(self) -> None:
        text = (
            "---\n"
            "team: LOA\n"
            "project: Agent Plus\n"
            "labels: [bug, prod]\n"
            "priority: 2\n"
            "assignee: alice@example.com\n"
            "---\n"
            "# Redis connection leak\n"
            "\n"
            "The connection pool isn't being reaped.\n"
            "<!-- internal note: caused by ORM pool exhaustion -->\n"
            "\n"
            "See LOA-200 for related work.\n"
        )
        meta, body = lr._parse_frontmatter(text)
        self.assertEqual(meta["team"], "LOA")
        self.assertEqual(meta["project"], "Agent Plus")
        self.assertEqual(meta["labels"], ["bug", "prod"])
        self.assertEqual(meta["priority"], "2")
        self.assertEqual(meta["assignee"], "alice@example.com")
        title, body_only = lr._extract_title_and_body(body)
        self.assertEqual(title, "Redis connection leak")
        self.assertIn("LOA-200", body_only)
        # HTML comment only stripped by _normalise_markdown, so check that path:
        normalised = lr._normalise_markdown(body)
        self.assertNotIn("<!--", normalised)
        self.assertIn("LOA-200", normalised)

    def test_no_frontmatter_only_h1(self) -> None:
        text = "# Just a title\n\nBody content."
        meta, body = lr._parse_frontmatter(text)
        self.assertEqual(meta, {})
        title, body_only = lr._extract_title_and_body(body)
        self.assertEqual(title, "Just a title")
        self.assertIn("Body content", body_only)


# ──────────────────────────── body @file loading ────────────────────────────


class TestBodyArg(unittest.TestCase):
    def test_plain_body_passes_through(self) -> None:
        self.assertEqual(lr._load_body_arg("just a string"), "just a string")

    def test_at_path_loads_file(self) -> None:
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".md", encoding="utf-8") as f:
            f.write("file content here")
            path = f.name
        try:
            result = lr._load_body_arg(f"@{path}")
            self.assertEqual(result, "file content here")
        finally:
            Path(path).unlink()

    def test_at_missing_file_dies(self) -> None:
        with self.assertRaises(SystemExit):
            lr._load_body_arg("@/nonexistent/path/xyz.md")


# ──────────────────────────── tool metadata ────────────────────────────


class TestToolMeta(unittest.TestCase):
    def test_injects_tool_field_on_dict(self) -> None:
        wrapped = lr._with_tool_meta({"issue": "x"})
        self.assertIn("tool", wrapped)
        self.assertEqual(wrapped["tool"]["name"], "linear-remote")
        self.assertIn("version", wrapped["tool"])
        # Tool field is first (dict insertion order) so agents see it up-top.
        self.assertEqual(list(wrapped.keys())[0], "tool")
        self.assertEqual(wrapped["issue"], "x")

    def test_non_dict_passes_through(self) -> None:
        self.assertEqual(lr._with_tool_meta([1, 2, 3]), [1, 2, 3])
        self.assertEqual(lr._with_tool_meta("str"), "str")
        self.assertIsNone(lr._with_tool_meta(None))

    def test_existing_tool_field_preserved(self) -> None:
        payload = {"tool": {"name": "other", "version": "9.9"}, "data": 1}
        wrapped = lr._with_tool_meta(payload)
        # Don't overwrite — caller already set it.
        self.assertEqual(wrapped["tool"]["name"], "other")

    def test_plugin_version_reads_manifest(self) -> None:
        v = lr._plugin_version()
        self.assertIsInstance(v, str)
        self.assertNotEqual(v, "")

    def test_emit_json_includes_tool_field(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            lr.emit_json({"id": "LOA-1"}, pretty=False)
        parsed = json.loads(buf.getvalue())
        self.assertIn("tool", parsed)
        self.assertEqual(parsed["tool"]["name"], "linear-remote")
        self.assertEqual(parsed["id"], "LOA-1")


if __name__ == "__main__":
    unittest.main()
