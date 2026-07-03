from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run_py(bin_path: Path, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(bin_path), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _commit_count(cwd: Path) -> int:
    r = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        return 0
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


@pytest.mark.parametrize(
    "fixture",
    ["osdb", "rainshift", "tinker-tailor"],
)
def test_diff_summary_range_json(plugin_bin, repo_root: Path, fixture: str) -> None:
    root = repo_root / "evals" / "fixtures" / fixture
    if not root.is_dir():
        pytest.skip(f"missing fixture directory {root}")
    if _commit_count(root) < 2:
        pytest.skip(f"{fixture}: fewer than 2 commits")

    b = plugin_bin("diff-summary")
    r = _run_py(
        b,
        ["--path", ".", "--range", "HEAD~1..HEAD", "--json"],
        cwd=root,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["tool"]["name"] == "diff-summary"
    assert data["tool"].get("version")
    assert "savedTo" not in json.dumps(data)

    files = data.get("files")
    assert isinstance(files, list)
    for entry in files:
        assert "path" in entry
        assert "role" in entry
        assert "risk" in entry
