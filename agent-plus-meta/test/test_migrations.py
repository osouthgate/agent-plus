"""Tests for the migration runner contract (v0.13.5).

Empty migrations dir on day one; the runner ships, the directory exists,
and the contract is documented in agent-plus-meta/migrations/README.md.
These tests cover the runner's three documented states:

  - empty dir → no-op, returns []
  - module exposes migrate(workspace) callable → applied
  - module raises → caught, recorded as failed, error envelope returned
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


def _load_host():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "agent-plus-meta"
    loader = SourceFileLoader("agent_plus", str(bin_path))
    spec = importlib.util.spec_from_loader("agent_plus", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


HOST = _load_host()
_HERE = Path(__file__).resolve()
_BIN_DIR = _HERE.parent.parent / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))
from _subcommands import upgrade as up  # noqa: E402

up.bind(HOST)


class _TempHome(unittest.TestCase):
    def setUp(self):
        up.bind(HOST)
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._patch_home = patch.object(Path, "home", return_value=self.home)
        self._patch_home.start()
        self.workspace = self.home / ".agent-plus"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._patch_home.stop()
        self._tmp.cleanup()


class TestMigrationsEmpty(_TempHome):
    def test_empty_dir_returns_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            mig_dir = Path(td)
            results, err = up._run_migrations(
                from_version="0.13.0", to_version="0.13.5",
                mig_dir=mig_dir, workspace=self.workspace,
            )
        self.assertEqual(results, [])
        self.assertIsNone(err)

    def test_real_migrations_dir_is_empty_on_day_one(self):
        # Sanity: agent-plus-meta/migrations/ ships empty in v0.13.5
        # (only __init__.py + README.md). The runner finds zero v*.py
        # modules.
        mig_dir = up._migrations_dir()
        self.assertTrue(mig_dir.is_dir(), f"missing {mig_dir}")
        results, err = up._run_migrations(
            from_version="0.13.0", to_version="0.13.5",
            mig_dir=mig_dir, workspace=self.workspace,
        )
        self.assertEqual(results, [])
        self.assertIsNone(err)


class TestMigrationsCallable(_TempHome):
    def test_callable_contract_applied_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            mig_dir = Path(td)
            (mig_dir / "v0_13_5.py").write_text(
                "from pathlib import Path\n"
                "def migrate(workspace):\n"
                "    (workspace / 'migration_marker.txt').write_text('done', encoding='utf-8')\n"
                "    return {'status': 'ok', 'message': 'applied', 'changes': []}\n",
                encoding="utf-8",
            )
            results, err = up._run_migrations(
                from_version="0.13.0", to_version="0.13.5",
                mig_dir=mig_dir, workspace=self.workspace,
            )
            self.assertIsNone(err)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["status"], "ok")
            self.assertTrue((self.workspace / "migration_marker.txt").is_file())

            # Second run: should be skipped via history.
            results2, err2 = up._run_migrations(
                from_version="0.13.0", to_version="0.13.5",
                mig_dir=mig_dir, workspace=self.workspace,
            )
            self.assertIsNone(err2)
            self.assertEqual(results2[0]["status"], "skipped_already_applied")


class TestMigrationsFailure(_TempHome):
    def test_exception_caught_and_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            mig_dir = Path(td)
            (mig_dir / "v0_13_5.py").write_text(
                "def migrate(workspace):\n"
                "    raise RuntimeError('kaboom')\n",
                encoding="utf-8",
            )
            results, err = up._run_migrations(
                from_version="0.13.0", to_version="0.13.5",
                mig_dir=mig_dir, workspace=self.workspace,
            )
        self.assertIsNotNone(err)
        self.assertEqual(err["code"], up.ERR_MIGRATION_FAILED)
        self.assertEqual(results[0]["status"], "failed")

    def test_missing_migrate_attr_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            mig_dir = Path(td)
            (mig_dir / "v0_13_5.py").write_text(
                "# no migrate function defined\n", encoding="utf-8",
            )
            results, err = up._run_migrations(
                from_version="0.13.0", to_version="0.13.5",
                mig_dir=mig_dir, workspace=self.workspace,
            )
        self.assertIsNotNone(err)
        self.assertEqual(err["code"], up.ERR_MIGRATION_FAILED)


if __name__ == "__main__":
    unittest.main()
