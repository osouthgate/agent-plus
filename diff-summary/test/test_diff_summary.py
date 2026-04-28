"""Unit tests for diff-summary. Stdlib unittest only — no pytest deps."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path


def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "diff-summary"
    loader = SourceFileLoader("diff_summary", str(bin_path))
    spec = importlib.util.spec_from_loader("diff_summary", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ds = _load_module()


def _run_cli(*argv: str) -> dict:
    """Call main(argv) and capture stdout JSON."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        ds.main(list(argv))
    return json.loads(buf.getvalue())


# ──────────────────────────── git helpers ────────────────────────────


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=30, check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {args} failed: {proc.stderr}")
    return proc


def _init_repo(cwd: Path) -> None:
    _git(cwd, "init", "-q", "-b", "main")
    _git(cwd, "config", "user.email", "test@example.com")
    _git(cwd, "config", "user.name", "Test")
    _git(cwd, "config", "commit.gpgsign", "false")


def _commit(cwd: Path, msg: str = "x") -> None:
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", msg, "--allow-empty")


def _write(cwd: Path, rel: str, content: str) -> Path:
    p = cwd / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _write_bytes(cwd: Path, rel: str, content: bytes) -> Path:
    p = cwd / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


# ──────────────────────────── envelope ────────────────────────────


class TestEnvelope(unittest.TestCase):
    def test_tool_meta_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "a.py", "x = 1\n")
            _commit(cwd, "init")
            _write(cwd, "a.py", "x = 2\n")
            out = _run_cli("--path", str(cwd))
            self.assertEqual(out["tool"]["name"], "diff-summary")
            self.assertIn("version", out["tool"])

    def test_version_flag(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            buf = io.StringIO()
            with redirect_stdout(buf):
                ds.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_non_git_dir_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit) as ctx:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    ds.main(["--path", td])
            self.assertEqual(ctx.exception.code, 1)


# ──────────────────────────── modes ────────────────────────────


class TestModes(unittest.TestCase):
    def test_working_tree_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "a.py", "x = 1\n")
            _commit(cwd, "init")
            _write(cwd, "a.py", "x = 2\nfoo = 3\n")
            out = _run_cli("--path", str(cwd))
            self.assertEqual(out["mode"], "working")
            self.assertEqual(out["stats"]["files"], 1)
            self.assertGreaterEqual(out["stats"]["insertions"], 1)

    def test_staged_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "a.py", "x = 1\n")
            _commit(cwd, "init")
            _write(cwd, "a.py", "x = 2\n")
            _git(cwd, "add", "a.py")
            out = _run_cli("--path", str(cwd), "--staged")
            self.assertEqual(out["mode"], "staged")
            self.assertEqual(out["stats"]["files"], 1)

    def test_base_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "a.py", "x = 1\n")
            _commit(cwd, "init")
            _git(cwd, "checkout", "-q", "-b", "feature")
            _write(cwd, "b.py", "y = 1\n")
            _commit(cwd, "add b")
            out = _run_cli("--path", str(cwd), "--base", "main")
            self.assertEqual(out["mode"], "base")
            self.assertEqual(out["base"], "main")
            paths = {f["path"] for f in out["files"]}
            self.assertIn("b.py", paths)

    def test_base_mode_unknown_ref_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "a.py", "x\n")
            _commit(cwd, "init")
            with self.assertRaises(SystemExit) as ctx:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    ds.main(["--path", str(cwd), "--base", "no-such-branch"])
            self.assertEqual(ctx.exception.code, 1)

    def test_range_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "a.py", "x = 1\n")
            _commit(cwd, "first")
            sha1 = _git(cwd, "rev-parse", "HEAD").stdout.strip()
            _write(cwd, "a.py", "x = 2\n")
            _commit(cwd, "second")
            sha2 = _git(cwd, "rev-parse", "HEAD").stdout.strip()
            out = _run_cli("--path", str(cwd), "--range", f"{sha1}..{sha2}")
            self.assertEqual(out["mode"], "range")
            self.assertEqual(out["stats"]["files"], 1)


# ──────────────────────────── role classification ────────────────────────────


class TestRoles(unittest.TestCase):
    def test_role_predicates(self) -> None:
        # Direct test of the classifier — no git needed.
        cases = [
            ("src/foo.py", "source"),
            ("tests/test_foo.py", "test"),
            ("src/foo.test.ts", "test"),
            ("src/__tests__/foo.ts", "test"),
            ("spec/bar_spec.rb", "test"),
            ("supabase/migrations/20240101_init.sql", "migration"),
            ("db/migrate/V1__init.sql", "migration"),
            ("alembic/versions/abc123.py", "migration"),
            ("dist/main.js", "generated"),
            ("build/x.py", "generated"),
            (".next/static.js", "generated"),
            ("pnpm-lock.yaml", "generated"),
            ("Cargo.lock", "generated"),
            ("go.sum", "generated"),
            ("Dockerfile", "build"),
            ("Makefile", "build"),
            (".github/workflows/ci.yml", "build"),
            ("Dockerfile.dev", "build"),
            ("config.yaml", "config"),
            (".env.local", "config"),
            ("tsconfig.json", "config"),
            ("jest.config.js", "config"),
            ("README.md", "doc"),
            ("docs/intro.md", "doc"),
            ("LICENSE", "doc"),
            ("tests/fixtures/sample.json", "fixture"),  # fixtures dir wins... but tests dir wins first actually
            ("seeds/users.sql", "fixture"),
            ("seed_users.sql", "fixture"),
            ("src/util.ts", "source"),
        ]
        for path, expected in cases:
            with self.subTest(path=path):
                # tests/fixtures/sample.json — test dir comes first in priority.
                # accept either based on priority order.
                got = ds._classify_role(path)
                if path == "tests/fixtures/sample.json":
                    # test predicate fires first
                    self.assertEqual(got, "test")
                else:
                    self.assertEqual(got, expected)

    def test_classify_in_diff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "src/foo.py", "x = 1\n")
            _write(cwd, "tests/test_foo.py", "def test_x(): pass\n")
            _write(cwd, "README.md", "# x\n")
            _commit(cwd, "init")
            _write(cwd, "src/foo.py", "x = 2\n")
            _write(cwd, "tests/test_foo.py", "def test_x(): assert True\n")
            _write(cwd, "README.md", "# x\nupdated\n")
            out = _run_cli("--path", str(cwd))
            roles = {f["path"]: f["role"] for f in out["files"]}
            self.assertEqual(roles["src/foo.py"], "source")
            self.assertEqual(roles["tests/test_foo.py"], "test")
            self.assertEqual(roles["README.md"], "doc")


# ──────────────────────────── risk tiers ────────────────────────────


class TestRisk(unittest.TestCase):
    def test_large_source_no_test_is_high(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "src/foo.py", "x = 1\n")
            _commit(cwd, "init")
            big = "\n".join(f"line{i} = {i}" for i in range(300)) + "\n"
            _write(cwd, "src/foo.py", big)
            out = _run_cli("--path", str(cwd))
            f = out["files"][0]
            self.assertEqual(f["risk"], "high")
            self.assertIn("large-change", f["riskReasons"])

    def test_small_source_with_test_is_low_or_medium(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "src/foo.py", "x = 1\n")
            _write(cwd, "tests/test_foo.py", "def test_x(): pass\n")
            _commit(cwd, "init")
            _write(cwd, "src/foo.py", "x = 2\n")
            _write(cwd, "tests/test_foo.py", "def test_x(): assert True\n")
            out = _run_cli("--path", str(cwd))
            src = next(f for f in out["files"] if f["path"] == "src/foo.py")
            self.assertIn(src["risk"], ("low", "medium"))
            # If low, no-test-changes shouldn't be a reason
            if src["risk"] == "low":
                self.assertNotIn("no-test-changes", src["riskReasons"])

    def test_env_file_is_high_secret_risk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "README.md", "x\n")
            _commit(cwd, "init")
            _write(cwd, ".env", "SECRET=CANARY-VALUE\n")
            _git(cwd, "add", "-N", ".env")
            out = _run_cli("--path", str(cwd))
            f = next(x for x in out["files"] if x["path"] == ".env")
            self.assertEqual(f["risk"], "high")
            self.assertIn("secret-risk-path", f["riskReasons"])
            self.assertIn(".env", out["summary"]["secretsRiskFiles"])
            # Pattern 5 canary: secret value must NOT appear anywhere in
            # the default (no --include-patches) output.
            self.assertNotIn("CANARY-VALUE", json.dumps(out))

    def test_migration_is_high(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "README.md", "x\n")
            _commit(cwd, "init")
            _write(cwd, "supabase/migrations/20240101_init.sql", "CREATE TABLE x(a INT);\n")
            _git(cwd, "add", "-N", "supabase/migrations/20240101_init.sql")
            out = _run_cli("--path", str(cwd))
            f = next(x for x in out["files"] if "migrations" in x["path"])
            self.assertEqual(f["role"], "migration")
            self.assertEqual(f["risk"], "high")

    def test_workflow_change_is_high(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "README.md", "x\n")
            _commit(cwd, "init")
            _write(cwd, ".github/workflows/ci.yml", "name: ci\non: push\n")
            _git(cwd, "add", "-N", ".github/workflows/ci.yml")
            out = _run_cli("--path", str(cwd))
            f = next(x for x in out["files"] if ".github" in x["path"])
            self.assertEqual(f["risk"], "high")
            self.assertIn("ci-workflow-change", f["riskReasons"])

    def test_config_change_is_medium(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "tsconfig.json", '{"compilerOptions":{}}\n')
            _commit(cwd, "init")
            _write(cwd, "tsconfig.json", '{"compilerOptions":{"strict":true}}\n')
            out = _run_cli("--path", str(cwd))
            f = out["files"][0]
            self.assertEqual(f["role"], "config")
            self.assertEqual(f["risk"], "medium")


# ──────────────────────────── public-API detection ────────────────────────────


class TestPublicAPI(unittest.TestCase):
    def test_export_in_index_ts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "src/index.ts", "// hi\n")
            _commit(cwd, "init")
            _write(cwd, "src/index.ts", "export function foo() { return 1; }\n")
            out = _run_cli("--path", str(cwd))
            f = out["files"][0]
            self.assertTrue(f["publicApiTouched"])

    def test_well_known_entrypoint_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "src/lib.rs", "// x\n")
            _commit(cwd, "init")
            _write(cwd, "src/lib.rs", "// y\n")
            out = _run_cli("--path", str(cwd))
            f = out["files"][0]
            self.assertTrue(f["publicApiTouched"])

    def test_pub_fn_rust(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "src/util.rs", "// hi\n")
            _commit(cwd, "init")
            _write(cwd, "src/util.rs", "pub fn helper() {}\n")
            out = _run_cli("--path", str(cwd))
            f = out["files"][0]
            self.assertTrue(f["publicApiTouched"])

    def test_capitalized_func_go(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "pkg/x.go", "package pkg\n")
            _commit(cwd, "init")
            _write(cwd, "pkg/x.go", "package pkg\nfunc Public() {}\n")
            out = _run_cli("--path", str(cwd))
            f = out["files"][0]
            self.assertTrue(f["publicApiTouched"])


# ──────────────────────────── co-changed test ────────────────────────────


class TestCoChangedTest(unittest.TestCase):
    def test_source_with_matching_test_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "src/foo.py", "x = 1\n")
            _write(cwd, "tests/test_foo.py", "x = 1\n")
            _commit(cwd, "init")
            _write(cwd, "src/foo.py", "x = 2\n")
            _write(cwd, "tests/test_foo.py", "x = 2\n")
            out = _run_cli("--path", str(cwd))
            src = next(f for f in out["files"] if f["path"] == "src/foo.py")
            self.assertNotIn("no-test-changes", src["riskReasons"])

    def test_source_alone_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "src/bar.py", "x = 1\n")
            _commit(cwd, "init")
            _write(cwd, "src/bar.py", "x = 2\n")
            out = _run_cli("--path", str(cwd))
            src = out["files"][0]
            self.assertIn("no-test-changes", src["riskReasons"])
            self.assertIn("src/bar.py", out["summary"]["sourceFilesWithoutTestChanges"])


# ──────────────────────────── moved lines ────────────────────────────


class TestMovedLines(unittest.TestCase):
    def test_block_moved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            block = "\n".join(f"line{i} content here" for i in range(50)) + "\n"
            _write(cwd, "src/old.py", block)
            _write(cwd, "src/new.py", "# placeholder\n")
            _commit(cwd, "init")
            _write(cwd, "src/old.py", "# emptied\n")
            _write(cwd, "src/new.py", "# placeholder\n" + block)
            out = _run_cli("--path", str(cwd))
            self.assertGreaterEqual(out["stats"]["movedLinesEstimate"], 40)


# ──────────────────────────── renames + binary ────────────────────────────


class TestRenameBinary(unittest.TestCase):
    def test_rename_detected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            content = "line one\nline two\nline three\nline four\nline five\n"
            _write(cwd, "src/old_name.py", content)
            _commit(cwd, "init")
            _git(cwd, "mv", "src/old_name.py", "src/new_name.py")
            out = _run_cli("--path", str(cwd), "--staged")
            f = out["files"][0]
            self.assertEqual(f["status"], "renamed")
            self.assertEqual(f["renamedFrom"], "src/old_name.py")
            self.assertEqual(f["path"], "src/new_name.py")

    def test_binary_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "README.md", "x\n")
            _commit(cwd, "init")
            _write_bytes(cwd, "logo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 1024)
            _git(cwd, "add", "-N", "logo.png")
            out = _run_cli("--path", str(cwd))
            f = next(x for x in out["files"] if x["path"] == "logo.png")
            self.assertTrue(f["binary"])
            self.assertEqual(f["insertions"], 0)
            self.assertEqual(f["deletions"], 0)


# ──────────────────────────── filtering + truncation ────────────────────────────


class TestFiltering(unittest.TestCase):
    def test_max_files_truncates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            for i in range(10):
                _write(cwd, f"f{i}.py", f"x = {i}\n")
            _commit(cwd, "init")
            for i in range(10):
                _write(cwd, f"f{i}.py", f"x = {i}{i}\n")
            out = _run_cli("--path", str(cwd), "--max-files", "5")
            self.assertEqual(len(out["files"]), 5)
            self.assertTrue(out["truncated"])

    def test_public_api_only_filter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "src/index.ts", "// x\n")
            _write(cwd, "src/other.ts", "// y\n")
            _commit(cwd, "init")
            _write(cwd, "src/index.ts", "export function f() {}\n")
            _write(cwd, "src/other.ts", "// changed\n")
            out = _run_cli("--path", str(cwd), "--public-api-only")
            paths = [f["path"] for f in out["files"]]
            self.assertIn("src/index.ts", paths)
            self.assertNotIn("src/other.ts", paths)

    def test_risk_filter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "README.md", "x\n")
            _write(cwd, "src/foo.py", "y = 1\n")
            _commit(cwd, "init")
            _write(cwd, "README.md", "x\nupdated\n")  # doc → low
            _write(cwd, "src/foo.py", "y = 2\n")     # source no test → medium
            out = _run_cli("--path", str(cwd), "--risk", "medium")
            paths = [f["path"] for f in out["files"]]
            self.assertIn("src/foo.py", paths)
            self.assertNotIn("README.md", paths)


# ──────────────────────────── --include-patches ────────────────────────────


class TestPatches(unittest.TestCase):
    def test_patch_field_populated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "a.py", "x = 1\n")
            _commit(cwd, "init")
            _write(cwd, "a.py", "x = 2\n")
            out = _run_cli("--path", str(cwd), "--include-patches")
            f = out["files"][0]
            self.assertIn("patch", f)
            self.assertIn("diff --git", f["patch"])

    def test_patch_field_omitted_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "a.py", "x = 1\n")
            _commit(cwd, "init")
            _write(cwd, "a.py", "x = 2\n")
            out = _run_cli("--path", str(cwd))
            f = out["files"][0]
            self.assertNotIn("patch", f)


# ──────────────────────────── output offload ────────────────────────────


class TestOutput(unittest.TestCase):
    def test_output_writes_file_and_returns_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "a.py", "x = 1\n")
            _commit(cwd, "init")
            _write(cwd, "a.py", "x = 2\n")
            outpath = Path(td) / "out.json"
            envelope = _run_cli("--path", str(cwd), "--output", str(outpath))
            self.assertTrue(outpath.exists())
            self.assertEqual(envelope["payloadPath"], str(outpath.resolve()))
            self.assertIn("payloadKeys", envelope)
            self.assertIn("payloadShape", envelope)
            self.assertIn("files", envelope["payloadShape"])
            full = json.loads(outpath.read_text())
            self.assertIn("files", full)


# ──────────────────────────── Pattern 5: secret canary ────────────────────────────


class TestNoLeakage(unittest.TestCase):
    def test_env_content_not_echoed_without_include_patches(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "README.md", "x\n")
            _commit(cwd, "init")
            _write(cwd, ".env", "SECRET=CANARY-VALUE-9876\n")
            _git(cwd, "add", "-N", ".env")
            outpath = Path(td) / "out.json"
            _run_cli("--path", str(cwd), "--output", str(outpath))
            text = outpath.read_text()
            # path is flagged
            self.assertIn(".env", text)
            # value is NOT echoed (default mode = no patches)
            self.assertNotIn("CANARY-VALUE-9876", text)

    def test_env_content_not_echoed_in_stdout_mode(self) -> None:
        # Same canary, but exercising the stdout path (no --output) since
        # the offload path and the inline path go through different code.
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "README.md", "x\n")
            _commit(cwd, "init")
            _write(cwd, ".env", "TOKEN=CANARY-STDOUT-7654\n")
            _git(cwd, "add", "-N", ".env")
            out = _run_cli("--path", str(cwd))
            self.assertNotIn("CANARY-STDOUT-7654", json.dumps(out))

    def test_env_patch_suppressed_even_with_include_patches(self) -> None:
        # The user opts into raw patches, but secret-risk paths must still
        # have their patch bodies suppressed (replaced with patchOmitted).
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _init_repo(cwd)
            _write(cwd, "README.md", "x\n")
            _commit(cwd, "init")
            _write(cwd, ".env", "API_KEY=CANARY-PATCH-1111\n")
            _write(cwd, "src/app.py", "def hello():\n    return 'hi'\n")
            _git(cwd, "add", "-N", ".env", "src/app.py")
            out = _run_cli("--path", str(cwd), "--include-patches")
            text = json.dumps(out)
            # Secret value never reaches output, even with patches on.
            self.assertNotIn("CANARY-PATCH-1111", text)
            # Non-secret file's patch IS included (sanity: opt-in still works).
            app_entry = next(f for f in out["files"] if f["path"] == "src/app.py")
            self.assertIn("patch", app_entry)
            # .env entry has the suppression marker, not a patch.
            env_entry = next(f for f in out["files"] if f["path"] == ".env")
            self.assertNotIn("patch", env_entry)
            self.assertEqual(env_entry.get("patchOmitted"), "secretsRiskFiles")


if __name__ == "__main__":
    unittest.main()
