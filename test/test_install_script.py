"""Sanity tests for the top-level install.sh.

Stdlib unittest only — no pytest fixtures, no network. Verifies:
  1. The script is syntactically valid POSIX shell (`sh -n`).
  2. `--dry-run` exits 0 and mentions all five framework primitives.
  3. An unknown flag is rejected with a non-zero exit.

Run with:
    python3 -m pytest test/test_install_script.py
or:
    python3 -m unittest test/test_install_script.py
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "install.sh"

PRIMITIVES = (
    "agent-plus-meta",
    "repo-analyze",
    "diff-summary",
    "skill-feedback",
    "skill-plus",
)


def _have_sh() -> bool:
    return shutil.which("sh") is not None


@unittest.skipUnless(_have_sh(), "POSIX `sh` not on PATH")
class TestInstallScript(unittest.TestCase):
    def test_script_exists(self) -> None:
        self.assertTrue(SCRIPT.is_file(), f"missing {SCRIPT}")

    def test_script_parses_as_posix_sh(self) -> None:
        proc = subprocess.run(
            ["sh", "-n", str(SCRIPT)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(
            proc.returncode, 0,
            msg=f"sh -n failed: stderr={proc.stderr!r}",
        )

    def test_dry_run_lists_all_primitives(self) -> None:
        proc = subprocess.run(
            ["sh", str(SCRIPT), "--dry-run"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0,
                         msg=f"dry-run failed: stderr={proc.stderr!r}")
        for name in PRIMITIVES:
            self.assertIn(name, proc.stdout,
                          msg=f"primitive {name!r} not mentioned in dry-run output")

    def test_unknown_flag_exits_nonzero(self) -> None:
        proc = subprocess.run(
            ["sh", str(SCRIPT), "--this-does-not-exist"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_install_dir_override_honored_in_dry_run(self) -> None:
        # AGENT_PLUS_INSTALL_DIR env override should appear in dry-run target paths
        # so users can confirm where files would land before committing to a write.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            env = os.environ.copy()
            env["AGENT_PLUS_INSTALL_DIR"] = td
            proc = subprocess.run(
                ["sh", str(SCRIPT), "--dry-run"],
                capture_output=True, text=True, timeout=15, env=env,
            )
            self.assertEqual(proc.returncode, 0,
                             msg=f"dry-run with override failed: stderr={proc.stderr!r}")
            self.assertIn(td, proc.stdout,
                          msg=f"override dir {td!r} not surfaced in dry-run output")


if __name__ == "__main__":
    unittest.main()
