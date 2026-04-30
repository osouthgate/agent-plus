"""Tests for `skill-plus where`."""
from __future__ import annotations

import subprocess
from pathlib import Path

from _scope_fixtures import (
    fake_project_skill,
    fake_global_skill,
    fake_plugin_skill,
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


def test_project_only_one_location_no_collision(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    res = run_bin("where", "foo", cwd=repo, home=home)
    p = payload(res)
    assert p["verdict"] == "found"
    assert len(p["locations"]) == 1
    assert p["locations"][0]["scope"] == "project"
    assert p["resolution_hint"] == "project"
    assert p["collision"] is False


def test_global_only_scope_global(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_global_skill(home, "foo")
    res = run_bin("where", "foo", cwd=repo, home=home)
    p = payload(res)
    assert len(p["locations"]) == 1
    assert p["locations"][0]["scope"] == "global"
    assert p["resolution_hint"] == "global"


def test_project_plus_global_collision(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    fake_global_skill(home, "foo")
    res = run_bin("where", "foo", cwd=repo, home=home)
    p = payload(res)
    assert len(p["locations"]) == 2
    assert p["collision"] is True
    assert p["resolution_hint"] == "project"


def test_plugin_installed_includes_plugin_and_version(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_plugin_skill(home, "agent-plus", "foo", plugin_version="1.2.3")
    res = run_bin("where", "foo", cwd=repo, home=home)
    p = payload(res)
    assert len(p["locations"]) == 1
    loc = p["locations"][0]
    assert loc["scope"] == "plugin"
    assert loc["plugin"] == "agent-plus"
    # skill version from frontmatter takes precedence over plugin manifest
    assert loc["version"] == "0.1.0"


def test_all_three_tiers_resolution_project(tmp_path: Path):
    repo, home = _setup(tmp_path)
    fake_project_skill(repo, "foo")
    fake_global_skill(home, "foo")
    fake_plugin_skill(home, "agent-plus", "foo")
    res = run_bin("where", "foo", cwd=repo, home=home)
    p = payload(res)
    assert len(p["locations"]) == 3
    assert p["collision"] is True
    assert p["resolution_hint"] == "project"
    scopes = sorted(l["scope"] for l in p["locations"])
    assert scopes == ["global", "plugin", "project"]


def test_nonexistent_skill_empty_locations(tmp_path: Path):
    repo, home = _setup(tmp_path)
    res = run_bin("where", "ghost", cwd=repo, home=home)
    p = payload(res)
    assert p["verdict"] == "not_found"
    assert p["locations"] == []
    assert p["collision"] is False
    assert p["resolution_hint"] == "unknown"


def test_plugin_cache_missing_does_not_crash(tmp_path: Path):
    repo, home = _setup(tmp_path)
    # No plugin cache directory at all.
    fake_project_skill(repo, "foo")
    res = run_bin("where", "foo", cwd=repo, home=home)
    assert res.returncode == 0
    p = payload(res)
    assert len(p["locations"]) == 1
