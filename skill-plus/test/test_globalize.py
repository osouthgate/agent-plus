"""Tests for `skill-plus globalize`."""
from __future__ import annotations

from pathlib import Path

from _scope_fixtures import (
    fake_project_skill,
    fake_global_skill,
    run_bin,
    payload,
)


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    home.mkdir()
    return repo, home


def test_dry_run_default_emits_would_move(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    res = run_bin("globalize", "foo", cwd=repo, home=home)
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "would_move"
    assert p["dry_run"] is True
    # Source still exists, destination not created
    assert (repo / ".claude" / "skills" / "foo").is_dir()
    assert not (home / ".claude" / "skills" / "foo").exists()


def test_no_dry_run_actually_moves(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    res = run_bin("globalize", "foo", "--no-dry-run", cwd=repo, home=home)
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "moved"
    assert p["dry_run"] is False
    assert not (repo / ".claude" / "skills" / "foo").exists()
    assert (home / ".claude" / "skills" / "foo" / "SKILL.md").is_file()


def test_keep_local_copies(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    res = run_bin("globalize", "foo", "--no-dry-run", "--keep-local",
                  cwd=repo, home=home)
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "copied"
    # Both sides exist.
    assert (repo / ".claude" / "skills" / "foo" / "SKILL.md").is_file()
    assert (home / ".claude" / "skills" / "foo" / "SKILL.md").is_file()


def test_destination_exists_without_force_errors(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    fake_global_skill(home, "foo")
    res = run_bin("globalize", "foo", "--no-dry-run", cwd=repo, home=home)
    assert res.returncode == 1
    p = payload(res)
    assert p["verdict"] == "error_destination_exists"
    # Source still exists, destination unchanged.
    assert (repo / ".claude" / "skills" / "foo").is_dir()
    assert (home / ".claude" / "skills" / "foo").is_dir()


def test_destination_exists_with_force_overwrites(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo", version="2.0.0")
    fake_global_skill(home, "foo", version="0.0.1")
    res = run_bin("globalize", "foo", "--no-dry-run", "--force",
                  cwd=repo, home=home)
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "moved"
    # Source removed, destination has the v2 fixture (newer one wins).
    assert not (repo / ".claude" / "skills" / "foo").exists()
    text = (home / ".claude" / "skills" / "foo" / "SKILL.md").read_text(encoding="utf-8")
    assert "version: 2.0.0" in text


def test_source_missing_errors(tmp_path: Path):
    repo, home = _setup(tmp_path)
    repo.mkdir()
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    res = run_bin("globalize", "ghost", cwd=repo, home=home)
    assert res.returncode == 1
    p = payload(res)
    assert p["verdict"] == "error_source_missing"


def test_no_git_repo_errors(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    nonrepo = tmp_path / "not-a-repo"
    nonrepo.mkdir()
    res = run_bin("globalize", "foo", cwd=nonrepo, home=home)
    assert res.returncode == 1
    p = payload(res)
    assert p["verdict"] == "error_no_git_repo"


def test_invalid_name_errors(tmp_path: Path):
    repo, home = _setup(tmp_path)
    repo.mkdir()
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    res = run_bin("globalize", "bad/name", cwd=repo, home=home)
    assert res.returncode == 1
    p = payload(res)
    assert p["verdict"] == "error_invalid_name"
