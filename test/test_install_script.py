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


def _safe_path() -> str:
    """Build a PATH containing core utilities (rm, sh, etc.) but with no
    `agent-plus-meta` binary on it. Used by the install.sh delegation tests
    so we exercise the candidate-path / fallback branches deterministically.
    """
    import os
    sh_path = shutil.which("sh")
    candidates: list[str] = []
    if sh_path:
        candidates.append(str(Path(sh_path).parent))
    # Common system locations that hold rm and friends.
    for d in ("/usr/bin", "/bin", "/usr/local/bin"):
        if Path(d).is_dir():
            candidates.append(d)
    # On Windows + Git Bash, /usr/bin maps under the Git install.
    git_usr_bin = Path("C:/Program Files/Git/usr/bin")
    if git_usr_bin.is_dir():
        candidates.append(str(git_usr_bin))
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return os.pathsep.join(out) if out else "/usr/bin"


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

    def test_dryrun_mentions_tarball_url(self) -> None:
        proc = subprocess.run(
            ["sh", str(SCRIPT), "--dry-run"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("archive/refs/", proc.stdout,
                      msg=f"tarball URL not surfaced: {proc.stdout!r}")

    def test_dryrun_mentions_prefix_and_install_dir(self) -> None:
        proc = subprocess.run(
            ["sh", str(SCRIPT), "--dry-run"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("prefix:", proc.stdout)
        self.assertIn("install dir:", proc.stdout)

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


    def test_install_sh_uninstall_delegates_when_bin_present(self) -> None:
        # Stage a fake agent-plus-meta in INSTALL_DIR. install.sh --uninstall
        # should `exec` it. We capture argv via a stub bin that prints them.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fake_bin = Path(td) / "agent-plus-meta"
            fake_bin.write_text(
                "#!/bin/sh\necho FAKE-APM \"$@\"\n", encoding="utf-8",
            )
            os.chmod(fake_bin, 0o755)
            env = os.environ.copy()
            env["AGENT_PLUS_INSTALL_DIR"] = td
            # Strip PATH so `command -v agent-plus-meta` doesn't pick up a real
            # one — we want the candidate-path branch under
            # AGENT_PLUS_INSTALL_DIR to fire.
            # Keep coreutils (rm/sh/etc.) reachable but ensure no real
            # `agent-plus-meta` binary is on PATH. We rebuild PATH from
            # canonical system bins only.
            env["PATH"] = _safe_path()
            proc = subprocess.run(
                ["sh", str(SCRIPT), "--uninstall", "--dry-run"],
                capture_output=True, text=True, timeout=15, env=env,
            )
            self.assertEqual(proc.returncode, 0,
                             msg=f"delegate failed: stderr={proc.stderr!r}")
            self.assertIn("FAKE-APM", proc.stdout,
                          msg=f"stub bin not exec'd: stdout={proc.stdout!r}")
            self.assertIn("uninstall", proc.stdout)
            self.assertIn("--dry-run", proc.stdout)

    def test_install_sh_uninstall_fallback_when_bin_missing(self) -> None:
        # No fake bin staged → fallback. Stage primitive bins to be removed.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            for name in PRIMITIVES:
                (Path(td) / name).write_text("stub", encoding="utf-8")
            env = os.environ.copy()
            env["AGENT_PLUS_INSTALL_DIR"] = td
            # Also point PREFIX somewhere we know is empty — we only stage
            # wrappers in this test, no plugin trees.
            env["AGENT_PLUS_PREFIX"] = str(Path(td) / "prefix-empty")
            # Keep coreutils (rm/sh/etc.) reachable but ensure no real
            # `agent-plus-meta` binary is on PATH. We rebuild PATH from
            # canonical system bins only.
            env["PATH"] = _safe_path()
            proc = subprocess.run(
                ["sh", str(SCRIPT), "--uninstall"],
                capture_output=True, text=True, timeout=15, env=env,
            )
            self.assertEqual(proc.returncode, 0,
                             msg=f"fallback failed: stderr={proc.stderr!r}")
            self.assertIn("fallback mode", proc.stdout)
            for name in PRIMITIVES:
                self.assertFalse(
                    (Path(td) / name).is_file(),
                    msg=f"primitive {name} not removed by fallback",
                )

    def test_install_sh_round_trip_via_source_dir(self) -> None:
        # End-to-end dogfood: install from the live tree via --source-dir,
        # confirm wrappers + plugin trees land, version reports correctly.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            install_dir = Path(td) / "bin"
            prefix = Path(td) / "share"
            env = os.environ.copy()
            env["AGENT_PLUS_INSTALL_DIR"] = str(install_dir)
            env["AGENT_PLUS_PREFIX"] = str(prefix)
            proc = subprocess.run(
                ["sh", str(SCRIPT),
                 "--no-init",
                 f"--source-dir={REPO_ROOT}"],
                capture_output=True, text=True, timeout=60, env=env,
            )
            self.assertEqual(proc.returncode, 0,
                             msg=f"round-trip failed: stderr={proc.stderr!r} stdout={proc.stdout!r}")
            for name in PRIMITIVES:
                wrapper = install_dir / name
                tree = prefix / name
                self.assertTrue(wrapper.is_file(),
                                msg=f"wrapper missing: {wrapper}")
                self.assertTrue(tree.is_dir(),
                                msg=f"tree missing: {tree}")
                # Verify the real bin landed in the tree.
                real_bin = tree / "bin" / name
                self.assertTrue(real_bin.is_file(),
                                msg=f"real bin missing: {real_bin}")
            # _subcommands/ landed for agent-plus-meta + skill-plus.
            self.assertTrue((prefix / "agent-plus-meta" / "bin"
                             / "_subcommands" / "init.py").is_file())
            self.assertTrue((prefix / "skill-plus" / "bin"
                             / "_subcommands" / "where.py").is_file())
            # plugin.json landed.
            self.assertTrue((prefix / "agent-plus-meta" / ".claude-plugin"
                             / "plugin.json").is_file())

    def test_install_sh_uninstall_fallback_refuses_workspace_flag(self) -> None:
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            env = os.environ.copy()
            env["AGENT_PLUS_INSTALL_DIR"] = td
            # Keep coreutils (rm/sh/etc.) reachable but ensure no real
            # `agent-plus-meta` binary is on PATH. We rebuild PATH from
            # canonical system bins only.
            env["PATH"] = _safe_path()
            proc = subprocess.run(
                ["sh", str(SCRIPT), "--uninstall", "--workspace"],
                capture_output=True, text=True, timeout=15, env=env,
            )
            self.assertEqual(proc.returncode, 3,
                             msg=f"expected exit 3, got {proc.returncode}; "
                                 f"stderr={proc.stderr!r}")
            self.assertIn("re-install", proc.stderr.lower() + proc.stdout.lower())


if __name__ == "__main__":
    unittest.main()
