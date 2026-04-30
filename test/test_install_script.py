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

    def test_unattended_flag_accepted(self) -> None:
        # --unattended composed with --dry-run keeps tests offline. The flag
        # must be accepted (rc=0); semantics are exercised at runtime, not here.
        proc = subprocess.run(
            ["sh", str(SCRIPT), "--unattended", "--dry-run"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0,
                         msg=f"--unattended --dry-run failed: stderr={proc.stderr!r}")

    def test_no_init_flag_accepted(self) -> None:
        proc = subprocess.run(
            ["sh", str(SCRIPT), "--no-init", "--dry-run"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0,
                         msg=f"--no-init --dry-run failed: stderr={proc.stderr!r}")

    def test_unattended_with_init_chain_dryrun_mentions_init(self) -> None:
        # Proves the chain into `agent-plus-meta init` is wired even though
        # dry-run suppresses the actual call.
        proc = subprocess.run(
            ["sh", str(SCRIPT), "--unattended", "--dry-run"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("agent-plus-meta init", proc.stdout,
                      msg=f"chain target not surfaced in dry-run output: {proc.stdout!r}")

    def test_dryrun_without_no_init_does_not_actually_chain(self) -> None:
        # --dry-run alone (no --no-init) must NOT execute init — the chain is
        # short-circuited under dry-run regardless of --no-init. We assert
        # absence of the live "Running" prefix used in the non-dry path.
        proc = subprocess.run(
            ["sh", str(SCRIPT), "--dry-run"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("Running agent-plus-meta init", proc.stdout,
                         msg=f"dry-run unexpectedly invoked init: {proc.stdout!r}")

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
