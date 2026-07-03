"""Sanity tests for the top-level install.sh.

Stdlib unittest only — no pytest fixtures, no network. Verifies:
  1. The script is syntactically valid POSIX shell (`sh -n`).
  2. `--dry-run` exits 0 and mentions all five framework primitives.
  3. An unknown flag is rejected with a non-zero exit.

Run with (from repo root):
    python3 -m unittest discover -s test -p "test_install_script.py" -v
or:
    python3 -m unittest test/test_install_script.py
On Windows without POSIX sh on PATH, the suite skips every test (expected); run under WSL, Git Bash, or Linux CI for full coverage.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tarfile
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


def _build_release_fixture(fixture_dir: Path, version: str, *, bad_checksum: bool = False) -> None:
    """Seed `fixture_dir` with a minimal but valid agent-plus release payload:
    `agent-plus-<version>.tar.gz` (top-level `agent-plus-<version>/` prefix,
    one placeholder-content directory per primitive) plus a matching
    `SHA256SUMS`. Content fidelity of the real plugin trees is already
    covered by test_install_sh_round_trip_via_source_dir -- this fixture only
    needs to exist so install_from_src() doesn't report MISSING primitives.

    Used by the checksum-verification tests via AGENT_PLUS_ASSET_BASE_URL
    (a file:// URL), so verification is exercised fully offline.

    With bad_checksum=True, SHA256SUMS records a deliberately wrong digest
    so callers can exercise the mismatch / hard-fail path.
    """
    root = f"agent-plus-{version}"
    tar_name = f"{root}.tar.gz"
    tar_path = fixture_dir / tar_name
    with tarfile.open(tar_path, "w:gz") as tf:
        root_info = tarfile.TarInfo(name=root)
        root_info.type = tarfile.DIRTYPE
        root_info.mode = 0o755
        tf.addfile(root_info)
        for prim in PRIMITIVES:
            dir_info = tarfile.TarInfo(name=f"{root}/{prim}")
            dir_info.type = tarfile.DIRTYPE
            dir_info.mode = 0o755
            tf.addfile(dir_info)
            data = f"placeholder for {prim}\n".encode("utf-8")
            file_info = tarfile.TarInfo(name=f"{root}/{prim}/.placeholder")
            file_info.size = len(data)
            tf.addfile(file_info, io.BytesIO(data))

    digest = "0" * 64 if bad_checksum else hashlib.sha256(tar_path.read_bytes()).hexdigest()
    (fixture_dir / "SHA256SUMS").write_text(
        f"{digest}  {tar_name}\n", encoding="utf-8",
    )


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

    # --- release-asset sha256 verification (v0.21.0+) -----------------------
    #
    # All of these set AGENT_PLUS_VERSION (so resolve_tag() never touches the
    # network) and AGENT_PLUS_ASSET_BASE_URL to a local file:// fixture dir
    # (a test-only override -- precedent: --source-dir), so the whole
    # download-verify-extract path runs fully offline.

    def test_checksum_verification_success_allows_install(self) -> None:
        # Seed a fixture release (tarball + matching SHA256SUMS) and confirm
        # install.sh downloads the asset tarball, verifies it, and proceeds
        # to a normal install.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            fixture_dir = tdp / "fixture"
            fixture_dir.mkdir()
            version = "0.99.1"
            _build_release_fixture(fixture_dir, version)
            install_dir = tdp / "bin"
            prefix = tdp / "share"
            env = os.environ.copy()
            env["AGENT_PLUS_VERSION"] = version
            env["AGENT_PLUS_ASSET_BASE_URL"] = fixture_dir.as_uri()
            env["AGENT_PLUS_INSTALL_DIR"] = str(install_dir)
            env["AGENT_PLUS_PREFIX"] = str(prefix)
            proc = subprocess.run(
                ["sh", str(SCRIPT), "--no-init"],
                capture_output=True, text=True, timeout=30, env=env,
            )
            self.assertEqual(proc.returncode, 0,
                             msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
            self.assertIn("Checksum verified", proc.stdout,
                          msg=f"verification-success message missing: {proc.stdout!r}")
            self.assertNotIn("install_sh_checksum_failed", proc.stderr)
            for name in PRIMITIVES:
                self.assertTrue((install_dir / name).is_file(),
                                msg=f"wrapper missing: {name}")
                self.assertTrue((prefix / name).is_dir(),
                                msg=f"tree missing: {name}")

    def test_checksum_mismatch_hard_fails(self) -> None:
        # A SHA256SUMS entry that doesn't match the downloaded tarball must
        # hard-fail (exit 1) with the greppable marker, and must NOT install
        # anything.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            fixture_dir = tdp / "fixture"
            fixture_dir.mkdir()
            version = "0.99.2"
            _build_release_fixture(fixture_dir, version, bad_checksum=True)
            install_dir = tdp / "bin"
            prefix = tdp / "share"
            env = os.environ.copy()
            env["AGENT_PLUS_VERSION"] = version
            env["AGENT_PLUS_ASSET_BASE_URL"] = fixture_dir.as_uri()
            env["AGENT_PLUS_INSTALL_DIR"] = str(install_dir)
            env["AGENT_PLUS_PREFIX"] = str(prefix)
            proc = subprocess.run(
                ["sh", str(SCRIPT), "--no-init"],
                capture_output=True, text=True, timeout=30, env=env,
            )
            self.assertEqual(proc.returncode, 1,
                             msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
            self.assertIn("[install_sh_checksum_failed]", proc.stderr)
            for name in PRIMITIVES:
                self.assertFalse((prefix / name).is_dir(),
                                 msg=f"tree unexpectedly installed after checksum failure: {name}")

    def test_checksum_mismatch_hard_fails_even_when_unattended(self) -> None:
        # Contract: a checksum mismatch is an integrity failure, not a
        # "partial install" -- --unattended must NOT downgrade it to exit 0.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            fixture_dir = tdp / "fixture"
            fixture_dir.mkdir()
            version = "0.99.3"
            _build_release_fixture(fixture_dir, version, bad_checksum=True)
            install_dir = tdp / "bin"
            prefix = tdp / "share"
            env = os.environ.copy()
            env["AGENT_PLUS_VERSION"] = version
            env["AGENT_PLUS_ASSET_BASE_URL"] = fixture_dir.as_uri()
            env["AGENT_PLUS_INSTALL_DIR"] = str(install_dir)
            env["AGENT_PLUS_PREFIX"] = str(prefix)
            proc = subprocess.run(
                ["sh", str(SCRIPT), "--no-init", "--unattended"],
                capture_output=True, text=True, timeout=30, env=env,
            )
            self.assertEqual(proc.returncode, 1,
                             msg=f"--unattended must not mask a checksum failure: "
                                 f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
            self.assertIn("[install_sh_checksum_failed]", proc.stderr)

    def test_no_verify_skips_verification_even_with_bad_checksum(self) -> None:
        # AGENT_PLUS_NO_VERIFY=1 must skip the checksum step entirely --
        # proven by seeding a deliberately WRONG checksum and confirming the
        # install still proceeds. (A matching checksum wouldn't distinguish
        # "verified and passed" from "verification never ran".)
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            fixture_dir = tdp / "fixture"
            fixture_dir.mkdir()
            version = "0.99.4"
            _build_release_fixture(fixture_dir, version, bad_checksum=True)
            install_dir = tdp / "bin"
            prefix = tdp / "share"
            env = os.environ.copy()
            env["AGENT_PLUS_VERSION"] = version
            env["AGENT_PLUS_ASSET_BASE_URL"] = fixture_dir.as_uri()
            env["AGENT_PLUS_INSTALL_DIR"] = str(install_dir)
            env["AGENT_PLUS_PREFIX"] = str(prefix)
            env["AGENT_PLUS_NO_VERIFY"] = "1"
            proc = subprocess.run(
                ["sh", str(SCRIPT), "--no-init"],
                capture_output=True, text=True, timeout=30, env=env,
            )
            self.assertEqual(proc.returncode, 0,
                             msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
            self.assertIn("AGENT_PLUS_NO_VERIFY=1", proc.stderr)
            self.assertNotIn("install_sh_checksum_failed", proc.stderr)
            for name in PRIMITIVES:
                self.assertTrue((prefix / name).is_dir(),
                                msg=f"tree missing despite NO_VERIFY: {name}")

    def test_dryrun_mentions_verification_for_release_tag(self) -> None:
        import os
        env = os.environ.copy()
        env["AGENT_PLUS_VERSION"] = "v0.99.5"
        proc = subprocess.run(
            ["sh", str(SCRIPT), "--dry-run"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("verification:", proc.stdout)
        self.assertIn("SHA256SUMS", proc.stdout)

    def test_dryrun_mentions_no_verify_skip(self) -> None:
        import os
        env = os.environ.copy()
        env["AGENT_PLUS_VERSION"] = "v0.99.6"
        env["AGENT_PLUS_NO_VERIFY"] = "1"
        proc = subprocess.run(
            ["sh", str(SCRIPT), "--dry-run"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("verification: skipped (AGENT_PLUS_NO_VERIFY=1)", proc.stdout)

    def test_dryrun_mentions_main_unverified(self) -> None:
        import os
        env = os.environ.copy()
        env["AGENT_PLUS_VERSION"] = "main"
        proc = subprocess.run(
            ["sh", str(SCRIPT), "--dry-run"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("unverified by design", proc.stdout)


if __name__ == "__main__":
    unittest.main()
