"""Tests for `skill-plus team-sync`."""
from __future__ import annotations

import subprocess
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
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    return repo, home


def test_dry_run_emits_commit_hint(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_global_skill(home, "foo")
    res = run_bin("team-sync", "foo", cwd=repo, home=home)
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "would_move"
    assert "commit_hint" in p
    assert "foo" in p["commit_hint"]
    assert "chore(skills)" in p["commit_hint"]


def test_no_dry_run_performs_localize(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_global_skill(home, "foo")
    res = run_bin("team-sync", "foo", "--no-dry-run", cwd=repo, home=home)
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "moved"
    assert (repo / ".claude" / "skills" / "foo" / "SKILL.md").is_file()
    assert not (home / ".claude" / "skills" / "foo").exists()


def test_global_source_missing_errors(tmp_path: Path):
    repo, home = _setup(tmp_path)
    res = run_bin("team-sync", "ghost", cwd=repo, home=home)
    assert res.returncode == 1
    p = payload(res)
    assert p["verdict"] == "error_source_missing"
    # commit_hint still emitted even on error
    assert "commit_hint" in p


def test_project_destination_exists_errors(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_global_skill(home, "foo")
    fake_project_skill(repo, "foo")
    res = run_bin("team-sync", "foo", "--no-dry-run", cwd=repo, home=home)
    assert res.returncode == 1
    p = payload(res)
    assert p["verdict"] == "error_destination_exists"


def test_commit_hint_template(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_global_skill(home, "my-skill")
    res = run_bin("team-sync", "my-skill", cwd=repo, home=home)
    p = payload(res)
    hint = p["commit_hint"]
    assert "chore(skills): share my-skill via repo (was global)" in hint
    assert "~/.claude/skills/my-skill/" in hint
    assert ".claude/skills/my-skill/" in hint
    assert "teammates pick it up automatically" in hint


def test_no_git_repo_errors(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    fake_global_skill(home, "foo")
    nonrepo = tmp_path / "not-a-repo"
    nonrepo.mkdir()
    res = run_bin("team-sync", "foo", cwd=nonrepo, home=home)
    assert res.returncode == 1
    p = payload(res)
    assert p["verdict"] == "error_no_git_repo"
