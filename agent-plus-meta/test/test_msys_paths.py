"""Unit tests for the MSYS/Git-Bash path normalisation in bin/agent-plus-meta.

`git rev-parse --show-toplevel` under Git Bash on Windows can emit
`/c/dev/foo`; `Path("/c/dev/foo")` on Windows silently mis-roots to
`C:\\c\\dev\\foo` (real past incident -- see TODOS.md "Windows path audit").
`_git_toplevel` must route git output through `_msys_to_windows` before
`Path()`. Follows the platform-patching idiom of test_agent_plus.py's
`test_msys_detection_helper_handles_git_prefix`.

Stdlib unittest only; discoverable by both pytest and
`python -m unittest discover`.
"""

from __future__ import annotations

import importlib.util
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


def _load_host():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "agent-plus-meta"
    loader = SourceFileLoader("agent_plus_msys", str(bin_path))
    spec = importlib.util.spec_from_loader("agent_plus_msys", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


HOST = _load_host()


class _FakeProc:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class TestMsysToWindows(unittest.TestCase):
    """Helper-level: MSYS form -> C:/..., POSIX passthrough, Windows form
    passthrough, non-Windows no-op."""

    def test_msys_drive_form_converted_on_windows(self) -> None:
        with patch.object(HOST.sys, "platform", "win32"):
            self.assertEqual(HOST._msys_to_windows("/c/dev/foo"), "C:/dev/foo")
            self.assertEqual(HOST._msys_to_windows("/d/x"), "D:/x")
            # Drive letter is upper-cased.
            self.assertEqual(HOST._msys_to_windows("/C/dev/foo"), "C:/dev/foo")

    def test_posix_multi_char_root_passthrough(self) -> None:
        # `/home/...` must NOT be treated as a drive: the pattern requires a
        # single letter followed by `/`.
        with patch.object(HOST.sys, "platform", "win32"):
            self.assertEqual(
                HOST._msys_to_windows("/home/user/repo"), "/home/user/repo")

    def test_windows_form_passthrough(self) -> None:
        with patch.object(HOST.sys, "platform", "win32"):
            self.assertEqual(HOST._msys_to_windows("C:/dev/foo"), "C:/dev/foo")
            self.assertEqual(
                HOST._msys_to_windows("C:\\dev\\foo"), "C:\\dev\\foo")

    def test_bare_drive_without_trailing_path_passthrough(self) -> None:
        # `/c` alone doesn't match (needs a `/` after the letter). Accepted
        # behaviour of the shared idiom; documents the edge rather than
        # changing it.
        with patch.object(HOST.sys, "platform", "win32"):
            self.assertEqual(HOST._msys_to_windows("/c"), "/c")

    def test_non_windows_platform_is_noop(self) -> None:
        with patch.object(HOST.sys, "platform", "linux"):
            self.assertEqual(HOST._msys_to_windows("/c/dev/foo"), "/c/dev/foo")


class TestGitToplevelMsysWrap(unittest.TestCase):
    """_git_toplevel must normalise git's stdout before Path()."""

    def test_git_toplevel_normalizes_msys_output(self) -> None:
        fake = _FakeProc("/c/fake/repo\n")
        with patch.object(HOST.sys, "platform", "win32"), \
             patch.object(HOST.subprocess, "run", return_value=fake):
            top = HOST._git_toplevel()
        self.assertEqual(top, Path("C:/fake/repo"))

    def test_git_toplevel_posix_output_passthrough_on_linux(self) -> None:
        fake = _FakeProc("/home/user/repo\n")
        with patch.object(HOST.sys, "platform", "linux"), \
             patch.object(HOST.subprocess, "run", return_value=fake):
            top = HOST._git_toplevel()
        self.assertEqual(top, Path("/home/user/repo"))

    def test_git_toplevel_none_on_nonzero_rc(self) -> None:
        fake = _FakeProc("", returncode=128)
        with patch.object(HOST.subprocess, "run", return_value=fake):
            self.assertIsNone(HOST._git_toplevel())

    def test_git_toplevel_none_on_empty_stdout(self) -> None:
        fake = _FakeProc("\n")
        with patch.object(HOST.subprocess, "run", return_value=fake):
            self.assertIsNone(HOST._git_toplevel())


if __name__ == "__main__":
    unittest.main(verbosity=2)
