"""Unit tests for repo-analyze. Stdlib unittest only — no pytest."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path


def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "repo-analyze"
    loader = SourceFileLoader("repo_analyze", str(bin_path))
    spec = importlib.util.spec_from_loader("repo_analyze", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ra = _load_module()


def _run_cli(*argv: str) -> dict:
    """Call main(argv) and capture stdout JSON."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        ra.main(list(argv))
    return json.loads(buf.getvalue())


# ──────────────────────────── helpers ────────────────────────────


class _Repo:
    """Helper to fabricate a fake repo under a tmp dir."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, rel: str, content: str = "") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def write_bytes(self, rel: str, content: bytes) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p


# ──────────────────────────── envelope tests ────────────────────────────


class TestEnvelope(unittest.TestCase):
    def test_tool_meta_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = _run_cli("--path", td)
            self.assertEqual(out["tool"]["name"], "repo-analyze")
            self.assertIn("version", out["tool"])

    def test_version_flag(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            buf = io.StringIO()
            with redirect_stdout(buf):
                ra.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_with_tool_meta_passthrough(self) -> None:
        # Lists pass through unchanged.
        self.assertEqual(ra._with_tool_meta([1, 2]), [1, 2])
        # Dicts get tool injected.
        out = ra._with_tool_meta({"foo": "bar"})
        self.assertEqual(out["tool"]["name"], "repo-analyze")
        self.assertEqual(out["foo"], "bar")


# ──────────────────────────── language detection ────────────────────────────


class TestLanguages(unittest.TestCase):
    def test_python_project(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("main.py", "print('hi')\n")
            r.write("lib/util.py", "def f():\n    pass\n")
            out = _run_cli("--path", td)
            self.assertIn("python", out["languages"])
            self.assertEqual(out["languages"]["python"]["files"], 2)

    def test_node_project(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("package.json", json.dumps({
                "name": "demo",
                "version": "1.0.0",
                "dependencies": {"next": "^14.0.0", "react": "^18.0.0"},
            }))
            r.write("src/page.tsx", "export default () => null;\n")
            out = _run_cli("--path", td)
            self.assertIn("typescript", out["languages"])

    def test_rust_project(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("Cargo.toml", '[package]\nname = "demo"\nversion = "0.1.0"\n[dependencies]\naxum = "0.7"\n')
            r.write("src/main.rs", "fn main() {}\n")
            out = _run_cli("--path", td)
            self.assertIn("rust", out["languages"])
            self.assertIsNotNone(out["deps"]["rust"])
            self.assertIn("axum", out["deps"]["rust"]["runtime"])

    def test_go_project(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("go.mod", "module example.com/demo\n\ngo 1.21\n\nrequire (\n  github.com/gin-gonic/gin v1.9.0\n)\n")
            r.write("main.go", "package main\nfunc main() {}\n")
            out = _run_cli("--path", td)
            self.assertIn("go", out["languages"])
            self.assertIsNotNone(out["deps"]["go"])
            self.assertIn("github.com/gin-gonic/gin", out["deps"]["go"]["runtime"])

    def test_multi_language_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("script.py", "x = 1\n")
            r.write("app.js", "console.log(1);\n")
            out = _run_cli("--path", td)
            self.assertIn("python", out["languages"])
            self.assertIn("javascript", out["languages"])

    def test_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = _run_cli("--path", td)
            self.assertEqual(out["languages"], {})
            self.assertEqual(out["frameworks"], [])
            self.assertEqual(out["entrypoints"], [])
            self.assertEqual(out["tree"]["totalFiles"], 0)

    def test_binary_files_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_bytes("logo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 1024)
            r.write_bytes("data.bin", b"\x00\x01\x02" * 100)
            r.write("real.py", "print('x')\n")
            out = _run_cli("--path", td)
            # python counted; binaries (no recognized extension) absent.
            self.assertIn("python", out["languages"])
            # png/bin extensions aren't in LANG_BY_EXT so they don't count anyway.
            self.assertNotIn("png", out["languages"])

    def test_binary_with_known_ext_skipped(self) -> None:
        # A .py file that's actually binary should be skipped by _count_lines.
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_bytes("fake.py", b"\x00\x00\x00garbage")
            r.write("real.py", "x = 1\n")
            out = _run_cli("--path", td)
            self.assertEqual(out["languages"]["python"]["files"], 1)


# ──────────────────────────── boring suspects ────────────────────────────


class TestSkipDirs(unittest.TestCase):
    def test_node_modules_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("real.js", "const x = 1;\n")
            r.write("node_modules/big-pkg/index.js", "module.exports = {};\n")
            r.write("node_modules/big-pkg/util.ts", "export const x = 1;\n")
            out = _run_cli("--path", td, "--tree-mode", "full")
            # only one js file should be found
            self.assertEqual(out["languages"]["javascript"]["files"], 1)
            for entry in out["tree"]["entries"]:
                self.assertNotIn("node_modules", entry["path"])

    def test_pycache_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("a.py", "x = 1\n")
            r.write("__pycache__/a.cpython-311.pyc", "junk\n")
            out = _run_cli("--path", td, "--tree-mode", "full")
            self.assertEqual(out["languages"]["python"]["files"], 1)
            for entry in out["tree"]["entries"]:
                self.assertNotIn("__pycache__", entry["path"])

    def test_dist_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("src/main.ts", "export {};\n")
            r.write("dist/main.js", "compiled\n")
            r.write("build/x.py", "x = 1\n")
            r.write(".next/static.js", "compiled\n")
            out = _run_cli("--path", td, "--tree-mode", "full")
            self.assertEqual(out["languages"].get("typescript", {}).get("files"), 1)
            self.assertNotIn("javascript", out["languages"])
            for entry in out["tree"]["entries"]:
                self.assertFalse(entry["path"].startswith("dist/"))
                self.assertFalse(entry["path"].startswith("build/"))
                self.assertFalse(entry["path"].startswith(".next/"))


# ──────────────────────────── framework detection ────────────────────────────


class TestFrameworks(unittest.TestCase):
    def test_nextjs_detected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("package.json", json.dumps({
                "name": "x", "dependencies": {"next": "^14.0.0", "react": "^18.0.0"}}))
            out = _run_cli("--path", td)
            names = {f["name"] for f in out["frameworks"]}
            self.assertIn("Next.js", names)
            self.assertIn("React", names)

    def test_fastapi_detected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("pyproject.toml",
                    '[project]\nname = "x"\nversion = "0"\ndependencies = ["fastapi>=0.100", "pydantic"]\n')
            out = _run_cli("--path", td)
            names = {f["name"] for f in out["frameworks"]}
            self.assertIn("FastAPI", names)
            self.assertIn("Pydantic", names)

    def test_axum_detected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("Cargo.toml",
                    '[package]\nname = "x"\nversion = "0.1"\n[dependencies]\naxum = "0.7"\ntokio = "1"\n')
            out = _run_cli("--path", td)
            names = {f["name"] for f in out["frameworks"]}
            self.assertIn("Axum", names)
            self.assertIn("Tokio", names)

    def test_tailwind_config_only_medium(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("tailwind.config.js", "module.exports = {};\n")
            out = _run_cli("--path", td)
            tw = [f for f in out["frameworks"] if f["name"] == "TailwindCSS"]
            self.assertEqual(len(tw), 1)
            self.assertEqual(tw[0]["confidence"], "medium")

    def test_framework_confidence_high_and_medium(self) -> None:
        # Explicit coverage: manifest-confirmed frameworks should report
        # confidence="high"; config-only fallbacks should report "medium".
        # Same repo exercises both branches in one call.
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("package.json", json.dumps({
                "name": "x",
                "dependencies": {"next": "^14.0.0", "react": "^18.0.0"},
            }))
            r.write("tailwind.config.js", "module.exports = {};\n")
            out = _run_cli("--path", td)
            by_name = {f["name"]: f for f in out["frameworks"]}
            # High-confidence path: manifest dep present.
            self.assertIn("Next.js", by_name)
            self.assertEqual(by_name["Next.js"]["confidence"], "high")
            self.assertIn("React", by_name)
            self.assertEqual(by_name["React"]["confidence"], "high")
            # Medium-confidence path: tailwind detected from config file
            # alone (no `tailwindcss` dep declared in package.json).
            self.assertIn("TailwindCSS", by_name)
            self.assertEqual(by_name["TailwindCSS"]["confidence"], "medium")


# ──────────────────────────── build tools ────────────────────────────


class TestBuildTools(unittest.TestCase):
    def test_pnpm_and_docker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("pnpm-lock.yaml", "")
            r.write("Dockerfile", "FROM alpine\n")
            out = _run_cli("--path", td)
            names = {b["name"] for b in out["buildTools"]}
            self.assertIn("pnpm", names)
            self.assertIn("Docker", names)

    def test_cargo_and_uv(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("Cargo.lock", "")
            r.write("uv.lock", "")
            out = _run_cli("--path", td)
            names = {b["name"] for b in out["buildTools"]}
            self.assertIn("cargo", names)
            self.assertIn("uv", names)


# ──────────────────────────── deps + entrypoints ────────────────────────────


class TestDeps(unittest.TestCase):
    def test_node_deps_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            deps = {f"pkg{i}": "^1" for i in range(60)}
            r.write("package.json", json.dumps({"name": "x", "dependencies": deps}))
            out = _run_cli("--path", td)
            self.assertEqual(len(out["deps"]["node"]["runtime"]), 50)
            self.assertTrue(out["deps"]["node"]["truncated"])

    def test_requirements_txt_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("requirements.txt", "fastapi>=0.100\n# comment\nflask\n")
            out = _run_cli("--path", td)
            runtime = out["deps"]["python"]["runtime"]
            self.assertIn("fastapi", runtime)
            self.assertIn("flask", runtime)


class TestEntrypoints(unittest.TestCase):
    def test_django_manage_py(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("manage.py", "#!/usr/bin/env python\n")
            out = _run_cli("--path", td)
            kinds = {e["kind"] for e in out["entrypoints"]}
            self.assertIn("django-manage", kinds)

    def test_next_app_page(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("src/app/page.tsx", "export default () => null;\n")
            out = _run_cli("--path", td)
            kinds = {e["kind"] for e in out["entrypoints"]}
            self.assertIn("next-page", kinds)

    def test_node_main_field(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("package.json", json.dumps({"name": "x", "main": "lib/index.js"}))
            out = _run_cli("--path", td)
            paths = {e["path"] for e in out["entrypoints"]}
            self.assertIn("lib/index.js", paths)


# ──────────────────────────── tree ────────────────────────────


class TestTree(unittest.TestCase):
    def test_max_tree_files_truncates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            for i in range(50):
                r.write(f"f{i}.py", "x = 1\n")
            out = _run_cli("--path", td, "--tree-mode", "full", "--max-tree-files", "10")
            self.assertLessEqual(len(out["tree"]["entries"]), 10)
            self.assertTrue(out["tree"]["truncated"])

    def test_default_tree_includes_dirs_first(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("zzz.py", "x = 1\n")
            r.write("aaa/inner.py", "x = 1\n")
            out = _run_cli("--path", td, "--tree-mode", "full")
            entries = out["tree"]["entries"]
            # First entry at depth 1 should be a dir.
            top = [e for e in entries if e["depth"] == 1]
            self.assertEqual(top[0]["type"], "dir")

    def test_compact_tree_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("src/app.py", "x = 1\n")
            r.write("src/util.py", "y = 2\n")
            r.write("README.md", "# hi\n")
            out = _run_cli("--path", td)  # compact is default
            tree = out["tree"]
            self.assertEqual(tree["mode"], "compact")
            self.assertIn("totalFiles", tree)
            self.assertIn("folders", tree)
            folders_by_path = {f["folder"]: f for f in tree["folders"]}
            self.assertIn("src/", folders_by_path)
            src = folders_by_path["src/"]
            self.assertEqual(src["files"], 2)
            self.assertIn("py", src["types"])

    def test_compact_tree_skips_excluded_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("src/app.py", "x = 1\n")
            r.write("node_modules/pkg/index.js", "x = 1\n")
            r.write("dist/bundle.js", "compiled\n")
            out = _run_cli("--path", td)  # compact is default
            folder_paths = [f["folder"] for f in out["tree"]["folders"]]
            for path in folder_paths:
                self.assertFalse(path.startswith("node_modules/"), f"node_modules leaked: {path}")
                self.assertFalse(path.startswith("dist/"), f"dist leaked: {path}")


# ──────────────────────────── README ────────────────────────────


class TestReadme(unittest.TestCase):
    def test_readme_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("README.md",
                    "# My Project\n\nA short description.\n\n## Install\n\nfoo\n\n## Usage\n\nbar\n")
            out = _run_cli("--path", td)
            self.assertEqual(out["readme"]["title"], "My Project")
            self.assertIn("short description", out["readme"]["firstParagraph"])
            self.assertEqual(out["readme"]["headings"], ["Install", "Usage"])

    def test_no_readme_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("README.md", "# Hi\n")
            out = _run_cli("--path", td, "--no-readme")
            self.assertNotIn("readme", out)

    def test_no_readme_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = _run_cli("--path", td)
            self.assertIsNone(out["readme"])


# ──────────────────────────── --output offload ────────────────────────────


class TestOutput(unittest.TestCase):
    def test_output_writes_file_and_returns_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("a.py", "x = 1\n")
            outpath = Path(td) / "out.json"
            envelope = _run_cli("--path", td, "--output", str(outpath))
            self.assertTrue(outpath.exists())
            self.assertEqual(envelope["payloadPath"], str(outpath.resolve()))
            self.assertIn("payloadKeys", envelope)
            self.assertIn("payloadShape", envelope)
            self.assertIn("languages", envelope["payloadShape"])
            # File on disk has full payload.
            full = json.loads(outpath.read_text())
            self.assertIn("languages", full)


# ──────────────────────────── Pattern 5 (no leakage) ────────────────────────────


class TestNoLeakage(unittest.TestCase):
    def test_unknown_package_json_fields_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("package.json", json.dumps({
                "name": "demo",
                "version": "1.0.0",
                "dependencies": {"react": "^18"},
                "npm-publish-token": "leak-me-please",
                "publishConfig": {"_authToken": "secret-leak"},
                "npmRegistry": "https://leak.example.com",
            }))
            outpath = Path(td) / "out.json"
            _run_cli("--path", td, "--output", str(outpath))
            text = outpath.read_text()
            self.assertNotIn("leak-me-please", text)
            self.assertNotIn("secret-leak", text)
            self.assertNotIn("npm-publish-token", text)
            self.assertNotIn("publishConfig", text)
            self.assertNotIn("npmRegistry", text)
            # Sanity: the field we DO read survived.
            data = json.loads(text)
            self.assertEqual(data["deps"]["node"]["runtime"], ["react"])

    def test_arbitrary_file_contents_not_echoed(self) -> None:
        # Pattern 5 invariant is broader than the package.json allowlist —
        # repo-analyze must never echo arbitrary file CONTENTS into output.
        # It only reports paths, languages, and counts.
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write(".env", "DB_PASSWORD=CANARY-ENV-AAAA\n")
            r.write("src/secrets.py", "API_KEY = 'CANARY-SRC-BBBB'\n")
            r.write("config/credentials.json",
                    json.dumps({"token": "CANARY-CFG-CCCC"}))
            r.write("README.md", "# demo\nNot a secret.\n")
            outpath = Path(td) / "out.json"
            _run_cli("--path", td, "--output", str(outpath))
            text = outpath.read_text()
            self.assertNotIn("CANARY-ENV-AAAA", text)
            self.assertNotIn("CANARY-SRC-BBBB", text)
            self.assertNotIn("CANARY-CFG-CCCC", text)
            # And exercise the stdout path too (no --output) since it goes
            # through different write code.
            stdout_payload = _run_cli("--path", td)
            stdout_text = json.dumps(stdout_payload)
            self.assertNotIn("CANARY-ENV-AAAA", stdout_text)
            self.assertNotIn("CANARY-SRC-BBBB", stdout_text)
            self.assertNotIn("CANARY-CFG-CCCC", stdout_text)


# ──────────────────────────── agent-plus enrichment ────────────────────────────


class TestAgentPlusEnrichment(unittest.TestCase):
    def test_services_json_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write(".agent-plus/services.json", json.dumps({
                "services": {
                    "github-remote": {"status": "ok", "owner": "x", "repo": "y", "repo_id": 1234567},
                    "vercel-remote": {"status": "unconfigured"},
                },
            }))
            out = _run_cli("--path", td)
            self.assertIsNotNone(out["agentPlusServices"])
            services = out["agentPlusServices"]["services"]
            self.assertEqual(services["github-remote"]["status"], "ok")
            self.assertEqual(services["vercel-remote"]["status"], "unconfigured")
            # Should NOT contain repo_id or owner — names + status only.
            text = json.dumps(out["agentPlusServices"])
            self.assertNotIn("1234567", text)
            self.assertNotIn("repo_id", text)

    def test_services_json_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = _run_cli("--path", td)
            self.assertIsNone(out["agentPlusServices"])


# ──────────────────────────── parsers ────────────────────────────


class TestParsers(unittest.TestCase):
    def test_strip_pep508(self) -> None:
        self.assertEqual(ra._strip_pep508("fastapi>=0.100,<1.0"), "fastapi")
        self.assertEqual(ra._strip_pep508("requests==2.31.0"), "requests")
        self.assertEqual(ra._strip_pep508("  flask  "), "flask")

    def test_parse_go_mod_inline(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "go.mod"
            p.write_text("module example.com/x\n\nrequire github.com/foo/bar v1.0.0\n")
            parsed = ra._parse_go_mod(p)
            self.assertEqual(parsed["module"], "example.com/x")
            self.assertIn("github.com/foo/bar", parsed["dependencies"])

    def test_parse_gemfile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Gemfile"
            p.write_text("source 'https://rubygems.org'\ngem 'rails', '~> 7.0'\ngem 'pg'\n")
            self.assertEqual(ra._parse_gemfile(p), ["rails", "pg"])


# ──────────────────────────── shape descriptor ────────────────────────────


class TestShape(unittest.TestCase):
    def test_shape_value_basic(self) -> None:
        self.assertEqual(ra._shape_value("hi", 1)["type"], "string")
        self.assertEqual(ra._shape_value(42, 1)["type"], "number")
        self.assertEqual(ra._shape_value(True, 1)["type"], "boolean")
        self.assertEqual(ra._shape_value(None, 1)["type"], "null")
        self.assertEqual(ra._shape_value([], 1)["type"], "list")
        self.assertEqual(ra._shape_value({}, 1)["type"], "dict")

    def test_payload_shape_excludes_tool(self) -> None:
        shape = ra._payload_shape({"tool": {"name": "x"}, "data": [1, 2]}, depth=2)
        self.assertNotIn("tool", shape)
        self.assertIn("data", shape)


# ──────────────────────────── stamp ────────────────────────────


class TestStamp(unittest.TestCase):
    def test_write_stamp_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ra._write_stamp(root)
            stamp = root / ".agent-plus" / "repo-analyze.stamp"
            self.assertTrue(stamp.exists())
            content = stamp.read_text(encoding="utf-8").strip()
            # Should be an ISO-8601 timestamp.
            self.assertRegex(content, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_write_stamp_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ra._write_stamp(root)
            ra._write_stamp(root)
            stamp = root / ".agent-plus" / "repo-analyze.stamp"
            self.assertTrue(stamp.exists())

    def test_stamp_written_on_successful_analyze(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write("main.py", "x = 1\n")
            _run_cli("--path", td)
            # Stamp should exist under td/.agent-plus/ (no git repo in tmpdir,
            # so git_root falls back to root itself).
            stamp = Path(td) / ".agent-plus" / "repo-analyze.stamp"
            self.assertTrue(stamp.exists(), "stamp not written after successful run")

    def test_stamp_not_written_on_die(self) -> None:
        """die() exits before emit(), so stamp must not be written on error."""
        with tempfile.TemporaryDirectory() as td:
            bad_path = Path(td) / "does_not_exist"
            with self.assertRaises(SystemExit) as ctx:
                ra.main(["--path", str(bad_path)])
            self.assertNotEqual(ctx.exception.code, 0)
            stamp = Path(td) / ".agent-plus" / "repo-analyze.stamp"
            self.assertFalse(stamp.exists(), "stamp must not be written on error exit")


if __name__ == "__main__":
    unittest.main()
