"""Unit tests for agent-plus. Stdlib unittest only — no pytest, no network.

Run with:
    python3 -m unittest agent-plus/test/test_agent_plus.py
or:
    python3 -m pytest agent-plus/test/
"""

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
    bin_path = here.parent.parent / "bin" / "agent-plus"
    loader = SourceFileLoader("agent_plus", str(bin_path))
    spec = importlib.util.spec_from_loader("agent_plus", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ap = _load_module()
BIN = Path(__file__).resolve().parent.parent / "bin" / "agent-plus"


def _run(*args: str, env: dict | None = None, cwd: str | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True,
        env={**os.environ, **(env or {})},
        cwd=cwd,
        timeout=15,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ─── envelope ────────────────────────────────────────────────────────────────


class TestEnvelope(unittest.TestCase):
    """Pattern #6: every JSON output carries `tool: {name, version}`."""

    def test_tool_meta_shape(self) -> None:
        meta = ap._tool_meta()
        self.assertEqual(meta["name"], "agent-plus")
        self.assertIsInstance(meta["version"], str)

    def test_with_tool_meta_dict(self) -> None:
        out = ap._with_tool_meta({"foo": "bar"})
        self.assertIn("tool", out)
        self.assertEqual(out["tool"]["name"], "agent-plus")
        self.assertEqual(out["foo"], "bar")

    def test_with_tool_meta_passthrough_for_non_dict(self) -> None:
        # Non-dicts pass through unchanged.
        self.assertEqual(ap._with_tool_meta(["a", "b"]), ["a", "b"])
        self.assertEqual(ap._with_tool_meta("hi"), "hi")

    def test_envelope_does_not_overwrite_existing_tool_key(self) -> None:
        out = ap._with_tool_meta({"tool": "preset", "x": 1})
        self.assertEqual(out["tool"], "preset")


# ─── --version ───────────────────────────────────────────────────────────────


class TestVersionFlag(unittest.TestCase):
    def test_version_exits_zero_and_prints_version(self) -> None:
        rc, out, err = _run("--version")
        self.assertEqual(rc, 0, msg=f"stderr={err!r}")
        self.assertTrue(out.strip(), "version flag printed nothing")
        # Manifest version is 0.1.0 in v0.1.0 — but don't hardcode in case
        # of bumps; just assert it's a non-empty string.


# ─── init ────────────────────────────────────────────────────────────────────


class TestInit(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _init(self) -> dict:
        rc, out, err = _run("init", "--dir", str(self.dir))
        self.assertEqual(rc, 0, msg=f"init failed: {err!r}")
        return json.loads(out)

    def test_init_creates_three_files_when_absent(self) -> None:
        result = self._init()
        ws = Path(result["workspace"])
        self.assertTrue(ws.is_dir())
        for f in ("manifest.json", "services.json", "env-status.json"):
            self.assertTrue((ws / f).is_file(), f"missing {f}")
        self.assertEqual(sorted(result["created"]),
                         ["env-status.json", "manifest.json", "services.json"])
        self.assertEqual(result["skipped"], [])

    def test_init_envelope_present(self) -> None:
        result = self._init()
        self.assertIn("tool", result)
        self.assertEqual(result["tool"]["name"], "agent-plus")

    def test_init_idempotent_skips_existing_files(self) -> None:
        self._init()
        result2 = self._init()
        self.assertEqual(result2["created"], [])
        self.assertEqual(sorted(result2["skipped"]),
                         ["env-status.json", "manifest.json", "services.json"])

    def test_init_manifest_shape(self) -> None:
        self._init()
        ws = self.dir / ".agent-plus"
        manifest = json.loads((ws / "manifest.json").read_text())
        self.assertEqual(manifest["plugins"], [])
        self.assertIn("version", manifest)
        self.assertIn("createdAt", manifest)

    def test_init_env_status_shape(self) -> None:
        self._init()
        ws = self.dir / ".agent-plus"
        es = json.loads((ws / "env-status.json").read_text())
        self.assertEqual(es["checked"], [])
        self.assertEqual(es["missing"], [])
        self.assertIsNone(es["checkedAt"])

    def test_init_services_shape(self) -> None:
        self._init()
        ws = self.dir / ".agent-plus"
        svc = json.loads((ws / "services.json").read_text())
        self.assertEqual(svc, {"services": {}})

    def test_init_returns_tool_created_skipped(self) -> None:
        result = self._init()
        for k in ("tool", "created", "skipped"):
            self.assertIn(k, result)


# ─── envcheck ────────────────────────────────────────────────────────────────


class TestEnvcheck(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        # Init the workspace so envcheck can persist.
        _run("init", "--dir", str(self.dir))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _envcheck(self, env_extra: dict | None = None) -> dict:
        # Strip every var the spec cares about so subprocess inherits a clean
        # state and we control which are set in this test.
        clean_env: dict[str, str] = {}
        for k, v in os.environ.items():
            # keep core OS vars; drop everything our spec cares about so we
            # can deterministically assert what's set.
            if any(k.startswith(prefix) for prefix in (
                "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
            )):
                continue
            clean_env[k] = v
        if env_extra:
            clean_env.update(env_extra)
        # Run from a tempdir so .env in the real repo doesn't pollute.
        rc, out, err = _run(
            "envcheck", "--dir", str(self.dir),
            env=clean_env, cwd=str(self.dir),
        )
        # We don't reset os.environ here because subprocess gets its own env.
        self.assertEqual(rc, 0, msg=f"envcheck failed: {err!r}")
        # We deliberately overwrite env for the subprocess; need a way to
        # clear inherited values. Re-spawn with the cleaned env above.
        return json.loads(out)

    def test_envcheck_identifies_missing_when_unset(self) -> None:
        result = self._envcheck()
        # Nothing in our hardcoded list should be set.
        # LINEAR_API_KEY is required and should appear in `missing`.
        self.assertIn("LINEAR_API_KEY", result["missing"])

    def test_envcheck_identifies_set_var(self) -> None:
        result = self._envcheck(env_extra={"LINEAR_API_KEY": "lin_test_value"})
        self.assertIn("LINEAR_API_KEY", result["set"])
        self.assertNotIn("LINEAR_API_KEY", result["missing"])

    def test_envcheck_persists_to_env_status_json(self) -> None:
        self._envcheck(env_extra={"LINEAR_API_KEY": "lin_test"})
        es_path = self.dir / ".agent-plus" / "env-status.json"
        es = json.loads(es_path.read_text())
        self.assertIn("LINEAR_API_KEY", es["set"])
        self.assertIsNotNone(es["checkedAt"])

    def test_envcheck_envelope_present(self) -> None:
        result = self._envcheck()
        self.assertEqual(result["tool"]["name"], "agent-plus")

    def test_envcheck_per_plugin_ready(self) -> None:
        result = self._envcheck(env_extra={
            "LINEAR_API_KEY": "lin_test",
        })
        plugins = result["plugins"]
        self.assertTrue(plugins["linear-remote"]["ready"])
        self.assertFalse(plugins["vercel-remote"]["ready"])
        # skill-feedback has no env requirement → always ready.
        self.assertTrue(plugins["skill-feedback"]["ready"])

    def test_envcheck_railway_binary_check_present(self) -> None:
        result = self._envcheck()
        binary = result["plugins"]["railway-ops"]["binary"]
        self.assertIsNotNone(binary)
        self.assertEqual(binary["binary"], "railway")
        self.assertIn("on_path", binary)


# ─── canary: no env-var VALUES leak into output ─────────────────────────────


class TestCanaryNoLeak(unittest.TestCase):
    """Pattern #5: env-var names land in output, but VALUES never do.

    We use a recognisable canary value across multiple env vars, run
    envcheck, and assert the canary string is nowhere in stdout, the
    persisted JSON file, or the program payload.
    """

    CANARY = "CANARY_AGENTPLUS_DO_NOT_LEAK_7e3b1c"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        _run("init", "--dir", str(self.dir))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_canary_not_in_envcheck_stdout(self) -> None:
        env_extra = {
            "GITHUB_TOKEN": self.CANARY,
            "VERCEL_TOKEN": self.CANARY,
            "LINEAR_API_KEY": self.CANARY,
            "SUPABASE_ACCESS_TOKEN": self.CANARY,
            "OPENROUTER_API_KEY": self.CANARY,
            "HCLOUD_TOKEN": self.CANARY,
            "COOLIFY_API_KEY": self.CANARY,
            "LANGFUSE_SECRET_KEY": self.CANARY,
            "HERMES_CHAT_API_KEY": self.CANARY,
        }
        # Build a clean env — we want only the canary to be the source of
        # secret-shaped strings flowing through the process.
        clean: dict[str, str] = {}
        for k, v in os.environ.items():
            if any(k.startswith(prefix) for prefix in (
                "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
            )):
                continue
            clean[k] = v
        clean.update(env_extra)

        rc, out, err = _run("envcheck", "--dir", str(self.dir),
                            "--pretty", env=clean, cwd=str(self.dir))
        self.assertEqual(rc, 0, msg=err)
        self.assertNotIn(self.CANARY, out, "canary leaked into stdout")
        self.assertNotIn(self.CANARY, err, "canary leaked into stderr")

        # And not on disk either.
        es_path = self.dir / ".agent-plus" / "env-status.json"
        text_on_disk = es_path.read_text()
        self.assertNotIn(self.CANARY, text_on_disk,
                         "canary leaked into env-status.json")

        # Names should appear, but not values.
        self.assertIn("GITHUB_TOKEN", text_on_disk)
        self.assertIn("LINEAR_API_KEY", text_on_disk)


# ─── refresh (no network) ────────────────────────────────────────────────────


class TestRefreshUnconfigured(unittest.TestCase):
    """When tokens are missing, refresh should record `unconfigured` /
    `partial` rather than crash or prompt."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        _run("init", "--dir", str(self.dir))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_refresh_vercel_unconfigured_without_token(self) -> None:
        # Strip VERCEL_TOKEN from env.
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith("VERCEL_")}
        rc, out, err = _run(
            "refresh", "--plugin", "vercel-remote", "--dir", str(self.dir),
            env=clean, cwd=str(self.dir),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        vercel = result["services"]["vercel-remote"]
        self.assertEqual(vercel["status"], "unconfigured")

    def test_refresh_writes_services_json(self) -> None:
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith("VERCEL_")}
        _run("refresh", "--plugin", "vercel-remote", "--dir", str(self.dir),
             env=clean, cwd=str(self.dir))
        svc_path = self.dir / ".agent-plus" / "services.json"
        svc = json.loads(svc_path.read_text())
        self.assertIn("vercel-remote", svc["services"])
        self.assertIn("refreshedAt", svc)


# ─── workspace resolution ────────────────────────────────────────────────────


class TestWorkspaceResolution(unittest.TestCase):
    def test_resolve_with_dir_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws, src = ap.resolve_workspace(td)
            self.assertEqual(ws, Path(td).resolve() / ".agent-plus")
            self.assertEqual(src, "flag")

    def test_resolve_for_read_falls_back_to_home(self) -> None:
        with patch.object(ap, "_git_toplevel", return_value=None):
            with tempfile.TemporaryDirectory() as td:
                # Ensure cwd has no .agent-plus dir.
                old_cwd = os.getcwd()
                os.chdir(td)
                try:
                    ws, src = ap._resolve_for_read(None)
                    # Read fallback is ~/.agent-plus
                    self.assertEqual(src, "home")
                    self.assertEqual(ws, (Path.home() / ".agent-plus").resolve())
                finally:
                    os.chdir(old_cwd)


# ─── plugin spec sanity ──────────────────────────────────────────────────────


class TestPluginSpec(unittest.TestCase):
    """The hardcoded PLUGIN_ENV_SPEC must cover every plugin in the
    repo. If a new plugin is added without updating the spec, this test
    fires to remind the author."""

    def test_known_plugins_present(self) -> None:
        expected = {
            "coolify-remote", "hcloud-remote", "hermes-remote",
            "langfuse-remote", "openrouter-remote", "railway-ops",
            "supabase-remote", "vercel-remote", "github-remote",
            "linear-remote", "skill-feedback",
        }
        self.assertEqual(set(ap.PLUGIN_ENV_SPEC), expected)

    def test_each_entry_has_required_and_optional_lists(self) -> None:
        for name, spec in ap.PLUGIN_ENV_SPEC.items():
            self.assertIn("required", spec, f"{name} missing required")
            self.assertIn("optional", spec, f"{name} missing optional")
            self.assertIsInstance(spec["required"], list)
            self.assertIsInstance(spec["optional"], list)


# ─── new refresh handlers (mocked endpoints, no network) ────────────────────


class _FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess used by railway tests."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestRefreshSupabase(unittest.TestCase):
    def test_ok_with_token(self) -> None:
        body = [
            {"name": "proj-a", "id": "abc123", "region": "us-east-1",
             "organization_id": "org_1"},
            {"name": "proj-b", "id": "def456", "region": "eu-west-1",
             "organization_id": "org_2"},
        ]
        with patch.object(ap, "_http_get_json", return_value=(200, body)):
            out = ap._refresh_supabase({"SUPABASE_ACCESS_TOKEN": "sbp_abc"})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["projects"][0]["name"], "proj-a")
        self.assertEqual(out["projects"][0]["region"], "us-east-1")

    def test_unconfigured_without_token(self) -> None:
        out = ap._refresh_supabase({})
        self.assertEqual(out["status"], "unconfigured")
        self.assertIn("SUPABASE_ACCESS_TOKEN", out["reason"])

    def test_canary_no_leak(self) -> None:
        canary = "CANARY-12345-SUPABASE-DO-NOT-LEAK"
        body = [{"name": "p", "id": "x", "region": "r", "organization_id": "o"}]
        with patch.object(ap, "_http_get_json", return_value=(200, body)):
            out = ap._refresh_supabase({"SUPABASE_ACCESS_TOKEN": canary})
        self.assertNotIn(canary, json.dumps(out))


class TestRefreshRailway(unittest.TestCase):
    def test_ok(self) -> None:
        fake_out = json.dumps([
            {"name": "alpha", "id": "p1"},
            {"name": "beta", "id": "p2"},
        ])
        with patch.object(ap.shutil, "which", return_value="C:/fake/railway.exe"):
            with patch.object(ap.subprocess, "run", return_value=_FakeProc(0, fake_out, "")):
                out = ap._refresh_railway({})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["projects"][0]["name"], "alpha")

    def test_unconfigured_when_railway_missing(self) -> None:
        with patch.object(ap.shutil, "which", return_value=None):
            out = ap._refresh_railway({})
        self.assertEqual(out["status"], "unconfigured")
        self.assertIn("railway", out["reason"].lower())

    def test_unconfigured_when_railway_unauthed(self) -> None:
        with patch.object(ap.shutil, "which", return_value="C:/fake/railway.exe"):
            with patch.object(ap.subprocess, "run",
                              return_value=_FakeProc(1, "", "Unauthorized: please log in")):
                out = ap._refresh_railway({})
        self.assertEqual(out["status"], "unconfigured")


class TestRefreshLinear(unittest.TestCase):
    def test_ok_with_key(self) -> None:
        body = {
            "data": {
                "viewer": {"id": "u1", "name": "Olive", "email": "o@example.com"},
                "teams": {"nodes": [
                    {"id": "t1", "key": "ENG", "name": "Engineering"},
                    {"id": "t2", "key": "OPS", "name": "Ops"},
                ]},
            },
        }
        with patch.object(ap, "_http_request_json", return_value=(200, body)):
            out = ap._refresh_linear({"LINEAR_API_KEY": "lin_test"})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["viewer"]["name"], "Olive")
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["teams"][0]["key"], "ENG")

    def test_unconfigured_without_key(self) -> None:
        out = ap._refresh_linear({})
        self.assertEqual(out["status"], "unconfigured")
        self.assertIn("LINEAR_API_KEY", out["reason"])

    def test_canary_no_leak(self) -> None:
        canary = "CANARY-12345-LINEAR-DO-NOT-LEAK"
        body = {"data": {"viewer": {"id": "u", "name": "n", "email": "e"},
                          "teams": {"nodes": []}}}
        with patch.object(ap, "_http_request_json", return_value=(200, body)):
            out = ap._refresh_linear({"LINEAR_API_KEY": canary})
        self.assertNotIn(canary, json.dumps(out))

    def test_canary_no_leak_on_error_path(self) -> None:
        canary = "CANARY-12345-LINEAR-ERR-DO-NOT-LEAK"
        with patch.object(ap, "_http_request_json",
                          return_value=(401, {"errors": [{"message": "Unauthorized"}]})):
            out = ap._refresh_linear({"LINEAR_API_KEY": canary})
        self.assertEqual(out["status"], "error")
        self.assertNotIn(canary, json.dumps(out))


class TestRefreshLangfuse(unittest.TestCase):
    def test_ok_with_keys_default_base(self) -> None:
        with patch.object(ap, "_http_get_json",
                          return_value=(200, {"status": "OK", "version": "3.10.0"})):
            out = ap._refresh_langfuse({
                "LANGFUSE_PUBLIC_KEY": "pk-lf-x",
                "LANGFUSE_SECRET_KEY": "sk-lf-y",
            })
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["base_url"], "https://cloud.langfuse.com")
        self.assertEqual(out["health_status"], "OK")

    def test_base_url_precedence_uses_explicit_base(self) -> None:
        with patch.object(ap, "_http_get_json",
                          return_value=(200, {"status": "OK"})):
            out = ap._refresh_langfuse({
                "LANGFUSE_PUBLIC_KEY": "pk",
                "LANGFUSE_SECRET_KEY": "sk",
                "LANGFUSE_BASE_URL": "https://lf.example.com/",
                "LANGFUSE_HOST": "https://other.example.com",
            })
        self.assertEqual(out["base_url"], "https://lf.example.com")

    def test_unconfigured_without_keys(self) -> None:
        out = ap._refresh_langfuse({})
        self.assertEqual(out["status"], "unconfigured")

    def test_canary_no_leak(self) -> None:
        canary = "CANARY-12345-LANGFUSE-DO-NOT-LEAK"
        with patch.object(ap, "_http_get_json", return_value=(200, {"status": "OK"})):
            out = ap._refresh_langfuse({
                "LANGFUSE_PUBLIC_KEY": "pk-not-secret",
                "LANGFUSE_SECRET_KEY": canary,
            })
        self.assertNotIn(canary, json.dumps(out))


# ─── canary on all four new handlers via subprocess ─────────────────────────


class TestRefreshCanaryAcrossAllNewHandlers(unittest.TestCase):
    """Pattern 5 across the wider stack: even when refresh fails (no network /
    no railway CLI / etc.), no token value should ever appear in stdout,
    stderr, or services.json."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        _run("init", "--dir", str(self.dir))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_clean(self, plugin: str, env_extra: dict[str, str]) -> tuple[str, str, str]:
        clean: dict[str, str] = {}
        for k, v in os.environ.items():
            if any(k.startswith(prefix) for prefix in (
                "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
            )):
                continue
            clean[k] = v
        clean.update(env_extra)
        rc, out, err = _run(
            "refresh", "--plugin", plugin, "--dir", str(self.dir),
            env=clean, cwd=str(self.dir),
        )
        self.assertEqual(rc, 0, msg=f"refresh {plugin} crashed: {err!r}")
        return out, err, (self.dir / ".agent-plus" / "services.json").read_text()

    def test_canary_supabase_subprocess(self) -> None:
        canary = "CANARY-12345-SUPABASE-DO-NOT-LEAK"
        out, err, disk = self._run_clean(
            "supabase-remote", {"SUPABASE_ACCESS_TOKEN": canary},
        )
        for blob, label in ((out, "stdout"), (err, "stderr"), (disk, "services.json")):
            self.assertNotIn(canary, blob, f"canary leaked into {label}")

    def test_canary_linear_subprocess(self) -> None:
        canary = "CANARY-12345-LINEAR-DO-NOT-LEAK"
        out, err, disk = self._run_clean(
            "linear-remote", {"LINEAR_API_KEY": canary},
        )
        for blob, label in ((out, "stdout"), (err, "stderr"), (disk, "services.json")):
            self.assertNotIn(canary, blob, f"canary leaked into {label}")

    def test_canary_langfuse_subprocess(self) -> None:
        canary = "CANARY-12345-LANGFUSE-DO-NOT-LEAK"
        out, err, disk = self._run_clean(
            "langfuse-remote", {
                "LANGFUSE_PUBLIC_KEY": "pk-not-secret",
                "LANGFUSE_SECRET_KEY": canary,
                # Point at an unroutable URL so we never actually hit cloud.
                "LANGFUSE_BASE_URL": "http://127.0.0.1:1/",
            },
        )
        for blob, label in ((out, "stdout"), (err, "stderr"), (disk, "services.json")):
            self.assertNotIn(canary, blob, f"canary leaked into {label}")

    def test_railway_unconfigured_does_not_crash(self) -> None:
        # Just check the subprocess form doesn't crash when railway might be
        # absent or unauthed in CI.
        out, err, _disk = self._run_clean("railway-ops", {})
        result = json.loads(out)
        railway = result["services"]["railway-ops"]
        self.assertIn(railway["status"], ("ok", "unconfigured", "error"))


# ─── list ─────────────────────────────────────────────────────────────────────


class TestList(unittest.TestCase):
    def test_list_returns_envelope_and_plugins(self) -> None:
        rc, out, err = _run("list")
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["tool"]["name"], "agent-plus")
        self.assertIsInstance(result["plugins"], list)
        self.assertGreaterEqual(result["count"], 11)

    def test_list_each_plugin_has_name_and_description(self) -> None:
        rc, out, _ = _run("list")
        result = json.loads(out)
        for entry in result["plugins"]:
            self.assertIn("name", entry)
            self.assertIn("description", entry)
            self.assertIn("source", entry)
            # headline_commands may be None but the key must be present.
            self.assertIn("headline_commands", entry)

    def test_list_names_only(self) -> None:
        rc, out, err = _run("list", "--names-only")
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["tool"]["name"], "agent-plus")
        self.assertIsInstance(result["plugins"], list)
        for n in result["plugins"]:
            self.assertIsInstance(n, str)
        self.assertIn("agent-plus", result["plugins"])

    def test_list_extracts_some_headline_for_known_plugin(self) -> None:
        # agent-plus's own README has a "Headline commands" section, so
        # the preview should be non-null and contain the words.
        rc, out, _ = _run("list")
        result = json.loads(out)
        agentplus = next(p for p in result["plugins"] if p["name"] == "agent-plus")
        self.assertIsNotNone(agentplus["headline_commands"])
        self.assertIn("Headline commands", agentplus["headline_commands"])

    def test_list_handles_missing_readme(self) -> None:
        # Build a fake repo with one plugin that has no README.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
                "name": "fake",
                "plugins": [
                    {"name": "no-readme", "source": "./no-readme",
                     "description": "a plugin without a README"},
                ],
            }), encoding="utf-8")
            (root / "no-readme").mkdir()
            # Initialise as git repo so _git_toplevel resolves to root.
            subprocess.run(["git", "init"], cwd=str(root),
                           capture_output=True, check=False, timeout=10)
            rc, out, err = _run("list", cwd=str(root))
            self.assertEqual(rc, 0, msg=err)
            result = json.loads(out)
            self.assertEqual(result["count"], 1)
            entry = result["plugins"][0]
            self.assertEqual(entry["name"], "no-readme")
            self.assertIsNone(entry["headline_commands"])

    def test_list_includes_ready_when_envstatus_present(self) -> None:
        # Run envcheck first so env-status.json populates `ready`.
        with tempfile.TemporaryDirectory() as td:
            ws_dir = Path(td)
            _run("init", "--dir", str(ws_dir))
            # Strip env so we get deterministic ready states.
            clean: dict[str, str] = {}
            for k, v in os.environ.items():
                if any(k.startswith(prefix) for prefix in (
                    "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                    "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
                )):
                    continue
                clean[k] = v
            _run("envcheck", "--dir", str(ws_dir), env=clean, cwd=str(ws_dir))
            rc, out, err = _run("list", "--dir", str(ws_dir),
                                env=clean, cwd=str(ws_dir))
            self.assertEqual(rc, 0, msg=err)
            result = json.loads(out)
            # At least one plugin should have a `ready` field now.
            with_ready = [p for p in result["plugins"] if "ready" in p]
            self.assertGreaterEqual(len(with_ready), 1)


# ─── refresh handler registry ────────────────────────────────────────────────


class TestRefreshHandlerRegistry(unittest.TestCase):
    def test_all_six_handlers_registered(self) -> None:
        expected = {
            "github-remote", "vercel-remote",
            "supabase-remote", "railway-ops",
            "linear-remote", "langfuse-remote",
        }
        self.assertEqual(set(ap.REFRESH_HANDLERS), expected)


# ─── extensions ─────────────────────────────────────────────────────────────


def _write_ext_script(tmp: Path, name: str, body: str) -> Path:
    """Write a small Python script the extension can invoke. We use python3
    as the executable so tests are portable across Windows/POSIX (no bash)."""
    p = tmp / name
    p.write_text(body, encoding="utf-8")
    return p


def _write_extensions_json(workspace: Path, exts: list[dict]) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    p = workspace / "extensions.json"
    p.write_text(json.dumps({"extensions": exts}, indent=2), encoding="utf-8")
    return p


class TestExtensionConfigValidation(unittest.TestCase):
    def test_valid_minimal(self) -> None:
        err = ap._validate_extension_config({
            "name": "my-ext", "command": ["python3", "x.py"],
        })
        self.assertIsNone(err)

    def test_name_must_be_slug(self) -> None:
        err = ap._validate_extension_config({"name": "Bad Name", "command": ["x"]})
        self.assertIsNotNone(err)
        self.assertIn("slug", err.lower())

    def test_name_starts_with_letter(self) -> None:
        err = ap._validate_extension_config({"name": "1bad", "command": ["x"]})
        self.assertIsNotNone(err)

    def test_name_too_long(self) -> None:
        err = ap._validate_extension_config({
            "name": "a" * 40, "command": ["x"],
        })
        self.assertIsNotNone(err)

    def test_name_collision_with_builtin_rejected(self) -> None:
        err = ap._validate_extension_config({
            "name": "github-remote", "command": ["x"],
        })
        self.assertIsNotNone(err)
        self.assertIn("collides", err)

    def test_command_must_be_list(self) -> None:
        err = ap._validate_extension_config({"name": "x", "command": "ls"})
        self.assertIsNotNone(err)

    def test_command_must_be_non_empty(self) -> None:
        err = ap._validate_extension_config({"name": "x", "command": []})
        self.assertIsNotNone(err)

    def test_command_elements_must_be_strings(self) -> None:
        err = ap._validate_extension_config({"name": "x", "command": ["ok", 123]})
        self.assertIsNotNone(err)

    def test_timeout_must_be_int(self) -> None:
        err = ap._validate_extension_config({
            "name": "x", "command": ["a"], "timeout_seconds": "30",
        })
        self.assertIsNotNone(err)

    def test_timeout_bool_rejected(self) -> None:
        # bool is a subclass of int in Python — explicitly guard against it.
        err = ap._validate_extension_config({
            "name": "x", "command": ["a"], "timeout_seconds": True,
        })
        self.assertIsNotNone(err)

    def test_enabled_must_be_bool(self) -> None:
        err = ap._validate_extension_config({
            "name": "x", "command": ["a"], "enabled": "yes",
        })
        self.assertIsNotNone(err)


class TestExtensionRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cwd = self.dir
        self.cwd.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_run_extension_success(self) -> None:
        script = _write_ext_script(self.dir, "ok.py",
            "import json; print(json.dumps({'status':'ok','foo':'bar'}))")
        out = ap._run_extension(
            {"name": "test-ok", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["plugin"], "test-ok")
        self.assertEqual(out["source"], "extension")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["foo"], "bar")

    def test_run_extension_non_json(self) -> None:
        script = _write_ext_script(self.dir, "bad.py", "print('not json')")
        out = ap._run_extension(
            {"name": "bad", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["status"], "error")
        self.assertIn("not valid JSON", out["reason"])

    def test_run_extension_json_array_rejected(self) -> None:
        # Array on stdout is a contract violation: must be a JSON object.
        script = _write_ext_script(self.dir, "arr.py", "print('[1,2,3]')")
        out = ap._run_extension(
            {"name": "arr", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["status"], "error")
        self.assertIn("not a JSON object", out["reason"])

    def test_run_extension_nonzero_exit(self) -> None:
        script = _write_ext_script(self.dir, "fail.py",
            "import sys; sys.stderr.write('boom\\n'); sys.exit(7)")
        out = ap._run_extension(
            {"name": "fail", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["status"], "error")
        self.assertIn("exited 7", out["reason"])
        self.assertIn("boom", out.get("stderr_tail", ""))

    def test_run_extension_timeout(self) -> None:
        script = _write_ext_script(self.dir, "slow.py",
            "import time; time.sleep(5)")
        out = ap._run_extension(
            {"name": "slow", "command": [sys.executable, str(script)],
             "timeout_seconds": 1},
            cwd=self.cwd,
        )
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason"], "timeout")

    def test_run_extension_stderr_tail_capped(self) -> None:
        # stderr longer than 500 chars must be truncated.
        script = _write_ext_script(self.dir, "loud.py",
            "import sys; sys.stderr.write('x' * 2000); sys.exit(1)")
        out = ap._run_extension(
            {"name": "loud", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["status"], "error")
        self.assertLessEqual(len(out["stderr_tail"]), 500)

    def test_extension_cannot_overwrite_plugin_or_source(self) -> None:
        # If a script tries to set plugin/source, the orchestrator wins.
        script = _write_ext_script(self.dir, "evil.py",
            "import json; print(json.dumps({"
            "'plugin':'github-remote','source':'builtin','status':'ok'}))")
        out = ap._run_extension(
            {"name": "evil", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["plugin"], "evil")
        self.assertEqual(out["source"], "extension")
        self.assertEqual(out["status"], "ok")

    def test_extension_default_status_ok(self) -> None:
        # Script omits status — orchestrator fills in "ok" by default.
        script = _write_ext_script(self.dir, "nostatus.py",
            "import json; print(json.dumps({'count':3}))")
        out = ap._run_extension(
            {"name": "nostatus", "command": [sys.executable, str(script)],
             "timeout_seconds": 10},
            cwd=self.cwd,
        )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["count"], 3)


class TestExtensionsRefreshIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / ".agent-plus"
        _run("init", "--dir", str(self.root))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _clean_env(self, extra: dict | None = None) -> dict:
        clean: dict[str, str] = {}
        for k, v in os.environ.items():
            if any(k.startswith(prefix) for prefix in (
                "GITHUB_", "VERCEL_", "COOLIFY_", "HCLOUD_", "HERMES_",
                "LANGFUSE_", "OPENROUTER_", "SUPABASE_", "LINEAR_",
            )):
                continue
            clean[k] = v
        if extra:
            clean.update(extra)
        return clean

    def test_refresh_runs_extension(self) -> None:
        script = _write_ext_script(self.root, "ext1.py",
            "import json; print(json.dumps({'status':'ok','msg':'hello'}))")
        _write_extensions_json(self.workspace, [
            {"name": "ext1", "command": [sys.executable, str(script)]},
        ])
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertIn("ext1", result["services"])
        ext1 = result["services"]["ext1"]
        self.assertEqual(ext1["status"], "ok")
        self.assertEqual(ext1["source"], "extension")
        self.assertEqual(ext1["plugin"], "ext1")
        self.assertEqual(ext1["msg"], "hello")
        self.assertIn("ext1", result["extensions_ran"])

    def test_no_extensions_flag_skips(self) -> None:
        script = _write_ext_script(self.root, "ext1.py",
            "import json; print(json.dumps({'status':'ok'}))")
        _write_extensions_json(self.workspace, [
            {"name": "skipme", "command": [sys.executable, str(script)]},
        ])
        rc, out, err = _run(
            "refresh", "--no-extensions", "--plugin", "vercel-remote",
            "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertNotIn("skipme", result["services"])

    def test_extensions_only_skips_builtins(self) -> None:
        script = _write_ext_script(self.root, "ext1.py",
            "import json; print(json.dumps({'status':'ok'}))")
        _write_extensions_json(self.workspace, [
            {"name": "only-me", "command": [sys.executable, str(script)]},
        ])
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertIn("only-me", result["services"])
        # No built-in handlers ran.
        for builtin in ("github-remote", "vercel-remote", "linear-remote"):
            self.assertNotIn(builtin, result["services"])

    def test_disabled_extension_skipped(self) -> None:
        script = _write_ext_script(self.root, "ext1.py",
            "import json; print(json.dumps({'status':'ok'}))")
        _write_extensions_json(self.workspace, [
            {"name": "off", "command": [sys.executable, str(script)],
             "enabled": False},
        ])
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertNotIn("off", result["services"])

    def test_refresh_default_runs_extensions_too(self) -> None:
        # No --extensions-only / --no-extensions / --plugin: both fire.
        script = _write_ext_script(self.root, "ext1.py",
            "import json; print(json.dumps({'status':'ok','tag':'default-run'}))")
        _write_extensions_json(self.workspace, [
            {"name": "default-ext", "command": [sys.executable, str(script)]},
        ])
        rc, out, err = _run(
            "refresh", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertIn("default-ext", result["services"])
        self.assertEqual(result["services"]["default-ext"]["tag"], "default-run")
        # And built-ins also ran.
        self.assertIn("vercel-remote", result["services"])

    def test_canary_no_env_leak_into_extension_output(self) -> None:
        """Pattern 5 canary: even when the host env has a token, that value
        must not surface in refresh output unless the script itself emits it."""
        canary = "CANARY-12345-DO-NOT-LEAK"
        script = _write_ext_script(self.root, "ext1.py",
            "import json; print(json.dumps({'status':'ok','note':'hi'}))")
        _write_extensions_json(self.workspace, [
            {"name": "canary-ext", "command": [sys.executable, str(script)]},
        ])
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env({"GITHUB_TOKEN": canary}),
            cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertNotIn(canary, out, "canary leaked into stdout")
        self.assertNotIn(canary, err, "canary leaked into stderr")
        services_disk = (self.workspace / "services.json").read_text()
        self.assertNotIn(canary, services_disk)

    def test_extension_stderr_does_not_propagate_beyond_tail(self) -> None:
        # Script prints a fake token-shaped string to its OWN stderr. That ends
        # up in stderr_tail (because the script chose to emit it). But the
        # orchestrator must not propagate stdout/stderr text outside the
        # bounded tail field.
        canary = "TOKEN=ABCXYZ123"
        script = _write_ext_script(self.root, "leaker.py",
            f"import sys; sys.stderr.write({canary!r}); sys.exit(2)")
        _write_extensions_json(self.workspace, [
            {"name": "leaker", "command": [sys.executable, str(script)]},
        ])
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        leaker = result["services"]["leaker"]
        self.assertEqual(leaker["status"], "error")
        # stderr_tail SHOULD contain the canary (script chose to emit it).
        self.assertIn(canary, leaker.get("stderr_tail", ""))
        # But err (orchestrator's stderr) must be clean.
        self.assertNotIn(canary, err)

    def test_timeout_via_subprocess(self) -> None:
        script = _write_ext_script(self.root, "slow.py",
            "import time; time.sleep(5)")
        _write_extensions_json(self.workspace, [
            {"name": "slowpoke", "command": [sys.executable, str(script)],
             "timeout_seconds": 1},
        ])
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        slow = result["services"]["slowpoke"]
        self.assertEqual(slow["status"], "error")
        self.assertEqual(slow["reason"], "timeout")

    def test_malformed_extensions_file_surfaces_error(self) -> None:
        (self.workspace / "extensions.json").write_text("not json{",
                                                         encoding="utf-8")
        rc, out, err = _run(
            "refresh", "--extensions-only", "--dir", str(self.root),
            env=self._clean_env(), cwd=str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertIn("extension_errors", result)


# ─── extensions subcommand ─────────────────────────────────────────────────


class TestExtensionsSubcommand(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / ".agent-plus"
        _run("init", "--dir", str(self.root))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_list_empty(self) -> None:
        rc, out, err = _run("extensions", "list", "--dir", str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["extensions"], [])

    def test_add_then_list(self) -> None:
        rc, out, err = _run(
            "extensions", "add", "--name", "myext", "--command", sys.executable,
            "--command-arg=-c", "--command-arg=print('{}')",
            "--description", "test ext", "--dir", str(self.root),
        )
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["added"], "myext")
        self.assertTrue((self.workspace / "extensions.json").is_file())

        rc, out, err = _run("extensions", "list", "--dir", str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["extensions"][0]["name"], "myext")
        self.assertEqual(result["extensions"][0]["description"], "test ext")
        # Command path itself NOT in output — only the hash.
        self.assertNotIn(sys.executable, out)
        self.assertEqual(len(result["extensions"][0]["command_hash"]), 64)  # sha256

    def test_add_rejects_collision_with_builtin(self) -> None:
        rc, out, err = _run(
            "extensions", "add", "--name", "github-remote",
            "--command", "python3", "--dir", str(self.root),
        )
        # Returns 0 but with error in payload (envelope-wrapped).
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)
        self.assertIn("collides", result["error"])

    def test_add_rejects_invalid_name(self) -> None:
        rc, out, _ = _run(
            "extensions", "add", "--name", "BadName",
            "--command", "python3", "--dir", str(self.root),
        )
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)

    def test_add_then_add_same_name_rejected(self) -> None:
        _run("extensions", "add", "--name", "dupe", "--command", "python3",
             "--dir", str(self.root))
        rc, out, _ = _run("extensions", "add", "--name", "dupe",
                          "--command", "python3", "--dir", str(self.root))
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)
        self.assertIn("already registered", result["error"])

    def test_remove(self) -> None:
        _run("extensions", "add", "--name", "togo", "--command", "python3",
             "--dir", str(self.root))
        rc, out, err = _run("extensions", "remove", "--name", "togo",
                            "--dir", str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["removed"], "togo")
        # Now list confirms it's gone.
        rc, out, _ = _run("extensions", "list", "--dir", str(self.root))
        result = json.loads(out)
        self.assertEqual(result["count"], 0)

    def test_remove_nonexistent_errors(self) -> None:
        rc, out, _ = _run("extensions", "remove", "--name", "ghost",
                          "--dir", str(self.root))
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)

    def test_validate_clean(self) -> None:
        # Use a script that exists.
        script = self.root / "real.py"
        script.write_text("# noop\n", encoding="utf-8")
        _write_extensions_json(self.workspace, [
            {"name": "good", "command": [sys.executable, "real.py"]},
        ])
        rc, out, err = _run("extensions", "validate", "--dir", str(self.root),
                            cwd=str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertTrue(result["ok"])

    def test_validate_reports_missing_command(self) -> None:
        _write_extensions_json(self.workspace, [
            {"name": "broken", "command": ["does-not-exist-xyz.py"]},
        ])
        rc, out, err = _run("extensions", "validate", "--dir", str(self.root),
                            cwd=str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        # Warning, not error — file just doesn't exist on disk.
        self.assertTrue(any(it["level"] == "warning" for it in result["issues"]))

    def test_validate_reports_collision_on_disk_load(self) -> None:
        _write_extensions_json(self.workspace, [
            {"name": "github-remote", "command": ["python3"]},
        ])
        rc, out, _ = _run("extensions", "validate", "--dir", str(self.root))
        result = json.loads(out)
        self.assertFalse(result["ok"])
        self.assertTrue(any("collides" in it["message"] for it in result["issues"]))

    def test_add_refuses_when_extensions_json_malformed(self) -> None:
        (self.workspace / "extensions.json").write_text("[broken",
                                                         encoding="utf-8")
        rc, out, _ = _run(
            "extensions", "add", "--name", "newone",
            "--command", "python3", "--dir", str(self.root),
        )
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)


class TestListIntegrationWithExtensions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / ".agent-plus"
        _run("init", "--dir", str(self.root))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_list_includes_extensions_array(self) -> None:
        _write_extensions_json(self.workspace, [
            {"name": "ext-one", "command": [sys.executable],
             "description": "first ext"},
        ])
        rc, out, err = _run("list", "--dir", str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertIn("extensions", result)
        self.assertEqual(result["extensions_count"], 1)
        ext = result["extensions"][0]
        self.assertEqual(ext["name"], "ext-one")
        self.assertEqual(ext["description"], "first ext")
        self.assertEqual(len(ext["command_hash"]), 64)
        # command itself NOT in output.
        self.assertNotIn(sys.executable, out)

    def test_list_names_only_includes_extensions(self) -> None:
        _write_extensions_json(self.workspace, [
            {"name": "ext-a", "command": ["a"]},
            {"name": "ext-b", "command": ["b"]},
        ])
        rc, out, err = _run("list", "--names-only", "--dir", str(self.root))
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(sorted(result["extensions"]), ["ext-a", "ext-b"])


# ─── marketplace init ──────────────────────────────────────────────────────


class TestMarketplaceInit(unittest.TestCase):
    """Phase 1 / Slice 1: `agent-plus marketplace init <user>/<name>`."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _init(self, slug: str, *, extra: list[str] | None = None,
              cwd: str | None = None) -> tuple[int, str, str]:
        args = ["marketplace", "init", slug]
        if extra:
            args.extend(extra)
        return _run(*args, cwd=cwd or str(self.cwd))

    def test_happy_path_scaffolds_all_files(self) -> None:
        rc, out, err = self._init("testuser/agent-plus-skills")
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(result["tool"]["name"], "agent-plus")
        m = result["marketplace"]
        self.assertEqual(m["owner"], "testuser")
        self.assertEqual(m["name"], "agent-plus-skills")
        self.assertEqual(sorted(m["files_written"]),
                         [".gitignore", "CHANGELOG.md", "LICENSE",
                          "README.md", "marketplace.json"])
        target = Path(m["path"])
        self.assertTrue(target.is_dir())
        for f in ("marketplace.json", "README.md", "LICENSE",
                  ".gitignore", "CHANGELOG.md"):
            self.assertTrue((target / f).is_file(), f"missing {f}")

    def test_marketplace_json_shape(self) -> None:
        rc, out, _ = self._init("testuser/agent-plus-skills")
        self.assertEqual(rc, 0)
        target = Path(json.loads(out)["marketplace"]["path"])
        mj = json.loads((target / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(mj["name"], "agent-plus-skills")
        self.assertEqual(mj["owner"], "testuser")
        self.assertEqual(mj["version"], "0.1.0")
        self.assertEqual(mj["agent_plus_version"], ">=0.5")
        self.assertEqual(mj["surface"], "claude-code")
        self.assertEqual(mj["skills"], [])
        self.assertEqual(mj["homepage"], "https://github.com/testuser/agent-plus-skills")
        self.assertEqual(mj["license"], "MIT")

    def test_license_is_mit(self) -> None:
        rc, out, _ = self._init("testuser/agent-plus-skills")
        self.assertEqual(rc, 0)
        target = Path(json.loads(out)["marketplace"]["path"])
        license_text = (target / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("testuser", license_text)
        self.assertIn("Permission is hereby granted", license_text)

    def test_readme_mentions_owner_and_name(self) -> None:
        rc, out, _ = self._init("testuser/agent-plus-skills")
        target = Path(json.loads(out)["marketplace"]["path"])
        readme = (target / "README.md").read_text(encoding="utf-8")
        self.assertIn("testuser", readme)
        self.assertIn("agent-plus-skills", readme)

    def test_next_steps_printed_not_executed(self) -> None:
        rc, out, _ = self._init("testuser/agent-plus-skills")
        result = json.loads(out)
        steps = result["marketplace"]["next_steps"]
        self.assertEqual(len(steps), 3)
        self.assertTrue(any("gh repo create" in s for s in steps))
        self.assertTrue(any("agent-plus-skills" in s and "add-topic" in s for s in steps))
        self.assertTrue(any("claude-code" in s and "add-topic" in s for s in steps))

    def test_default_target_is_cwd_plus_name(self) -> None:
        rc, out, _ = self._init("testuser/agent-plus-skills")
        result = json.loads(out)
        expected = (self.cwd / "agent-plus-skills").resolve()
        self.assertEqual(Path(result["marketplace"]["path"]), expected)

    def test_path_override(self) -> None:
        custom = self.cwd / "my-custom-dir"
        rc, out, err = self._init("testuser/agent-plus-skills",
                                  extra=["--path", str(custom)])
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertEqual(Path(result["marketplace"]["path"]), custom.resolve())
        self.assertTrue((custom / "marketplace.json").is_file())

    def test_name_mismatch_rejected(self) -> None:
        rc, out, _ = self._init("testuser/wrong-name")
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)
        self.assertIn("agent-plus-skills", result["error"])
        self.assertIn("wrong-name", result["error"])

    def test_invalid_slug_rejected(self) -> None:
        rc, out, _ = self._init("not-a-slug")
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)
        self.assertIn("slug", result["error"].lower())

    def test_existing_non_empty_dir_rejected(self) -> None:
        target = self.cwd / "agent-plus-skills"
        target.mkdir()
        (target / "preexisting.txt").write_text("hi", encoding="utf-8")
        rc, out, _ = self._init("testuser/agent-plus-skills")
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIn("error", result)
        self.assertIn("non-empty", result["error"])
        # Existing file untouched.
        self.assertEqual((target / "preexisting.txt").read_text(encoding="utf-8"), "hi")
        self.assertFalse((target / "marketplace.json").is_file())

    def test_existing_empty_dir_accepted(self) -> None:
        target = self.cwd / "agent-plus-skills"
        target.mkdir()
        rc, out, err = self._init("testuser/agent-plus-skills")
        self.assertEqual(rc, 0, msg=err)
        result = json.loads(out)
        self.assertIn("marketplace", result)
        self.assertTrue((target / "marketplace.json").is_file())

    def test_envelope_present(self) -> None:
        rc, out, _ = self._init("testuser/agent-plus-skills")
        result = json.loads(out)
        self.assertIn("tool", result)
        self.assertEqual(result["tool"]["name"], "agent-plus")
        self.assertIsInstance(result["tool"]["version"], str)

    def test_pretty_flag_emits_indented_json(self) -> None:
        rc, out, err = self._init("testuser/agent-plus-skills", extra=["--pretty"])
        self.assertEqual(rc, 0, msg=err)
        # Pretty output has newlines + indentation.
        self.assertIn("\n  ", out)


if __name__ == "__main__":
    unittest.main()
