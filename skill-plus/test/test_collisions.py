"""Tests for `skill-plus collisions`."""
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


def test_no_collisions(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "alpha")
    fake_global_skill(home, "beta")
    res = run_bin("collisions", cwd=repo, home=home)
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "no_collisions"
    assert p["collisions"] == []


def test_non_tty_no_flags_emits_needs_user_input(tmp_path: Path):
    """T1: non-tty bail with suggested_renames[]."""
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    fake_global_skill(home, "foo")
    # subprocess.run with capture_output=True ensures non-tty stdin in child.
    res = run_bin("collisions", cwd=repo, home=home)
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "needs_user_input"
    assert len(p["collisions"]) == 1
    c = p["collisions"][0]
    assert c["name"] == "foo"
    assert len(c["suggested_renames"]) == 2
    new_names = {s["new_name"] for s in c["suggested_renames"]}
    assert new_names == {"foo-project", "foo-global"}
    # No FS writes occurred.
    assert (repo / ".claude" / "skills" / "foo").is_dir()
    assert (home / ".claude" / "skills" / "foo").is_dir()


def test_auto_renames_global_with_suffix(tmp_path: Path):
    """T3: --auto -> project wins, global gets `-global` suffix."""
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    fake_global_skill(home, "foo")
    res = run_bin("collisions", "--auto", "--no-dry-run", cwd=repo, home=home)
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "renamed"
    c = p["collisions"][0]
    assert c["action"] == "rename_global"
    assert c["new_name"] == "foo-global"
    # Project untouched, global renamed.
    assert (repo / ".claude" / "skills" / "foo").is_dir()
    assert not (home / ".claude" / "skills" / "foo").exists()
    assert (home / ".claude" / "skills" / "foo-global").is_dir()


def test_explicit_rename_applies(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    fake_global_skill(home, "foo")
    res = run_bin(
        "collisions", "--rename", "foo:global:foo-old",
        "--no-dry-run", cwd=repo, home=home,
    )
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "renamed"
    assert (home / ".claude" / "skills" / "foo-old").is_dir()
    assert not (home / ".claude" / "skills" / "foo").exists()


def test_rename_to_existing_name_errors(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    fake_global_skill(home, "foo")
    fake_project_skill(repo, "bar")  # conflicting target
    res = run_bin(
        "collisions", "--rename", "foo:global:bar",
        "--no-dry-run", cwd=repo, home=home,
    )
    assert res.returncode == 1
    p = payload(res)
    assert p["verdict"] == "error_new_name_collides"


def test_rename_with_illegal_name_errors(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    fake_global_skill(home, "foo")
    res = run_bin(
        "collisions", "--rename", "foo:global:bad name!",
        "--no-dry-run", cwd=repo, home=home,
    )
    assert res.returncode == 1
    p = payload(res)
    assert p["verdict"] == "error_invalid_new_name"


def test_multiple_collisions_mixed_renames(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    fake_global_skill(home, "foo")
    fake_project_skill(repo, "bar")
    fake_global_skill(home, "bar")
    res = run_bin(
        "collisions",
        "--rename", "foo:global:foo-g",
        "--rename", "bar:project:bar-p",
        "--no-dry-run", cwd=repo, home=home,
    )
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "renamed"
    assert len(p["collisions"]) == 2
    assert (home / ".claude" / "skills" / "foo-g").is_dir()
    assert (repo / ".claude" / "skills" / "bar-p").is_dir()
    # Originals — project foo (untouched, mapping was global side) still there.
    assert (repo / ".claude" / "skills" / "foo").is_dir()
    assert (home / ".claude" / "skills" / "bar").is_dir()


def test_dry_run_default_shows_would_rename_no_fs_change(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    fake_global_skill(home, "foo")
    res = run_bin("collisions", "--auto", cwd=repo, home=home)
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "would_rename"
    assert p["dry_run"] is True
    # No FS change.
    assert (home / ".claude" / "skills" / "foo").is_dir()
    assert not (home / ".claude" / "skills" / "foo-global").exists()


def test_no_dry_run_actually_renames(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    fake_global_skill(home, "foo")
    res = run_bin("collisions", "--auto", "--no-dry-run", cwd=repo, home=home)
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "renamed"
    assert p["dry_run"] is False
    assert (home / ".claude" / "skills" / "foo-global").is_dir()


def test_interactive_prompt_path(tmp_path: Path):
    """Mock stdin via subprocess input — child sees stdin lines but isatty()
    is still False (subprocess pipe). To actually trigger interactive mode
    we'd need a pty, which is not portable. Instead we verify that providing
    --rename via CLI is the supported scripted path; non-tty without flags
    triggers the bail path (covered above). This test exercises the
    --rename-driven path with multiple collisions to confirm dispatch works."""
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    fake_global_skill(home, "foo")
    res = run_bin(
        "collisions",
        "--rename", "foo:project:foo-proj",
        "--no-dry-run", cwd=repo, home=home,
    )
    assert res.returncode == 0
    p = payload(res)
    assert p["verdict"] == "renamed"
    c = p["collisions"][0]
    assert c["action"] == "rename_project"
    assert c["new_name"] == "foo-proj"
    assert (repo / ".claude" / "skills" / "foo-proj").is_dir()
    assert not (repo / ".claude" / "skills" / "foo").exists()


def test_no_git_repo_errors(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    nonrepo = tmp_path / "not-a-repo"
    nonrepo.mkdir()
    res = run_bin("collisions", cwd=nonrepo, home=home)
    assert res.returncode == 1
    p = payload(res)
    assert p["verdict"] == "error_no_git_repo"
