"""Tests for skill-plus promote subcommand."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-plus"


def _run(*args: str, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    # Isolate user config (~/.agent-plus/skill-plus/config.json) per test by
    # pointing HOME / USERPROFILE at a tmp dir if caller passes it via env.
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True, timeout=30,
        cwd=str(cwd) if cwd else None, env=e,
    )


def _isolated_home_env(home: Path) -> dict:
    # Both HOME (POSIX) and USERPROFILE (Windows) — Path.home() consults either.
    return {"HOME": str(home), "USERPROFILE": str(home)}


# ─── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)
    return repo


def _write_valid_skill(repo: Path, name: str = "my-skill", *,
                       version: str = "0.3.0",
                       obviates_inline: bool = True) -> Path:
    """Default fixture skill carries an inline frontmatter `obviates` list so
    the contract validation passes."""
    skill_dir = repo / ".claude" / "skills" / name
    (skill_dir / "bin").mkdir(parents=True)
    if obviates_inline:
        obv_line = 'obviates: [foo-thing, bar-thing]\n'
    else:
        obv_line = "obviates:\n  - foo-thing\n  - bar-thing\n"
    (skill_dir / "SKILL.md").write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: Does the one thing reliably.\n"
        f"when_to_use: When you want the one thing done.\n"
        f"version: {version}\n"
        f"{obv_line}"
        f"allowed-tools: Bash\n"
        f"---\n\n"
        f"## Killer command\n\n"
        f"```\n{name} --do-it\n```\n\n"
        f"## Do NOT use this for\n\n- other things\n",
        encoding="utf-8",
    )
    (skill_dir / "bin" / name).write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    return skill_dir


def _make_marketplace(tmp_path: Path, repo_id: str = "me/agent-plus-skills",
                     skills: list | None = None,
                     extra_keys: dict | None = None) -> Path:
    """Create a fake marketplace clone matching the live shape."""
    leaf = repo_id.split("/")[-1]
    clone = tmp_path / "clones" / leaf
    clone.mkdir(parents=True)
    owner, name = repo_id.split("/", 1)
    payload = {
        "name": name,
        "owner": owner,
        "version": "0.1.0",
        "agent_plus_version": ">=0.5",
        "surface": "claude-code",
        "skills": skills if skills is not None else [],
    }
    if extra_keys:
        payload.update(extra_keys)
    (clone / "marketplace.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return clone


# ─── tests ────────────────────────────────────────────────────────────────────

def test_skill_not_found(git_repo: Path, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    res = _run("promote", "ghost-skill", "--to", "me/agent-plus-skills",
               "--pretty", cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 2, res.stdout
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "skill_not_found"


def test_skill_not_promotable_missing_killer(git_repo: Path, tmp_path: Path):
    skill = git_repo / ".claude" / "skills" / "broken"
    (skill / "bin").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: broken\n"
        "description: Does the one thing reliably.\n"
        "when_to_use: Whenever needed.\n"
        "---\n\n"
        "Body without the required section.\n",
        encoding="utf-8",
    )
    (skill / "bin" / "broken").write_text("#!/bin/sh\n", encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()
    res = _run("promote", "broken", "--to", "me/agent-plus-skills",
               "--pretty", cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 2, res.stdout
    payload = json.loads(res.stdout)
    assert payload["error"] == "skill_not_promotable"
    assert "killer_command_section" in payload["missing"]


def test_no_destination_no_config(git_repo: Path, tmp_path: Path):
    _write_valid_skill(git_repo)
    home = tmp_path / "home"
    home.mkdir()
    res = _run("promote", "my-skill", "--pretty",
               cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 2, res.stdout
    payload = json.loads(res.stdout)
    assert payload["error"] == "no_destination"


def test_dry_run_default_with_to_and_path(git_repo: Path, tmp_path: Path):
    _write_valid_skill(git_repo)
    clone = _make_marketplace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    res = _run("promote", "my-skill",
               "--to", "me/agent-plus-skills",
               "--marketplace-path", str(clone),
               "--pretty",
               cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["dryRun"] is True
    entry = payload["marketplaceEntryToAdd"]
    assert entry["name"] == "my-skill"
    assert entry["path"] == "my-skill/"
    assert entry["version"] == "0.3.0"
    assert entry["obviates"] == ["foo-thing", "bar-thing"]
    # Source still in place.
    assert (git_repo / ".claude" / "skills" / "my-skill").is_dir()
    # Nothing copied.
    assert not (clone / "my-skill").exists()
    # marketplace.json skills still empty.
    mp = json.loads((clone / "marketplace.json").read_text(encoding="utf-8"))
    assert mp["skills"] == []


def test_marketplace_mismatch(git_repo: Path, tmp_path: Path):
    _write_valid_skill(git_repo)
    # Clone exists but its marketplace.json identifies a different repo.
    clone = _make_marketplace(tmp_path, repo_id="other/some-other-marketplace")
    home = tmp_path / "home"
    home.mkdir()

    res = _run("promote", "my-skill",
               "--to", "me/agent-plus-skills",
               "--marketplace-path", str(clone),
               "--pretty",
               cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 2, res.stdout
    payload = json.loads(res.stdout)
    assert payload["error"] == "marketplace_mismatch"
    assert payload["expected"] == "me/agent-plus-skills"
    assert payload["found"] == "other/some-other-marketplace"


def test_no_dry_run_copies_updates_removes(git_repo: Path, tmp_path: Path):
    _write_valid_skill(git_repo)
    clone = _make_marketplace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    res = _run("promote", "my-skill",
               "--to", "me/agent-plus-skills",
               "--marketplace-path", str(clone),
               "--no-dry-run", "--pretty",
               cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["dryRun"] is False
    assert payload["action"] == "promoted"
    assert payload["sourceRemoved"] is True
    assert payload["marketplaceUpdated"] is True
    assert payload["filesCopied"] >= 2  # SKILL.md + bin launcher

    # Destination has the skill.
    assert (clone / "my-skill" / "SKILL.md").is_file()
    assert (clone / "my-skill" / "bin" / "my-skill").is_file()
    # Source removed.
    assert not (git_repo / ".claude" / "skills" / "my-skill").exists()
    # marketplace.json updated.
    mp = json.loads((clone / "marketplace.json").read_text(encoding="utf-8"))
    assert len(mp["skills"]) == 1
    entry = mp["skills"][0]
    assert entry["name"] == "my-skill"
    assert entry["path"] == "my-skill/"
    assert entry["version"] == "0.3.0"
    assert entry["obviates"] == ["foo-thing", "bar-thing"]
    # Top-level key order matches the live marketplace shape.
    assert list(mp.keys())[:6] == [
        "name", "owner", "version", "agent_plus_version", "surface", "skills",
    ]


def test_no_dry_run_keep_local(git_repo: Path, tmp_path: Path):
    _write_valid_skill(git_repo)
    clone = _make_marketplace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    res = _run("promote", "my-skill",
               "--to", "me/agent-plus-skills",
               "--marketplace-path", str(clone),
               "--no-dry-run", "--keep-local", "--pretty",
               cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["sourceRemoved"] is False
    # Source kept.
    assert (git_repo / ".claude" / "skills" / "my-skill" / "SKILL.md").is_file()
    # Destination present.
    assert (clone / "my-skill" / "SKILL.md").is_file()


def test_skill_not_promotable_missing_obviates(git_repo: Path, tmp_path: Path):
    """A skill with no frontmatter `obviates` AND no body 'Obviates' /
    'When NOT to use this' section must fail validation."""
    skill = git_repo / ".claude" / "skills" / "no-obv"
    (skill / "bin").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: no-obv\n"
        "description: Does the one thing reliably.\n"
        "when_to_use: Whenever needed.\n"
        "---\n\n"
        "## Killer command\n\n"
        "```\nno-obv --do-it\n```\n",
        encoding="utf-8",
    )
    (skill / "bin" / "no-obv").write_text("#!/bin/sh\n", encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()
    res = _run("promote", "no-obv", "--to", "me/agent-plus-skills",
               "--pretty", cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 2, res.stdout
    payload = json.loads(res.stdout)
    assert payload["error"] == "skill_not_promotable"
    assert "obviates" in payload["missing"]


def test_obviates_block_style_frontmatter(git_repo: Path, tmp_path: Path):
    """Frontmatter block-style obviates list must be parsed."""
    _write_valid_skill(git_repo, name="block-skill", obviates_inline=False)
    clone = _make_marketplace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    res = _run("promote", "block-skill",
               "--to", "me/agent-plus-skills",
               "--marketplace-path", str(clone),
               "--pretty",
               cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["marketplaceEntryToAdd"]["obviates"] == ["foo-thing", "bar-thing"]


def test_obviates_from_body_section(git_repo: Path, tmp_path: Path):
    """Without a frontmatter obviates field, the body 'Do NOT use this for'
    section bullets must be picked up."""
    name = "body-obv"
    skill = git_repo / ".claude" / "skills" / name
    (skill / "bin").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: Does the one thing reliably.\n"
        f"when_to_use: Whenever needed.\n"
        f"---\n\n"
        f"## Killer command\n\n"
        f"```\n{name}\n```\n\n"
        f"## Do NOT use this for\n\n- alpha\n- beta\n\nMore prose.\n",
        encoding="utf-8",
    )
    (skill / "bin" / name).write_text("#!/bin/sh\n", encoding="utf-8")
    clone = _make_marketplace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    res = _run("promote", name,
               "--to", "me/agent-plus-skills",
               "--marketplace-path", str(clone),
               "--pretty",
               cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["marketplaceEntryToAdd"]["obviates"] == ["alpha", "beta"]


def test_marketplace_malformed_missing_skills(git_repo: Path, tmp_path: Path):
    """A marketplace.json without a `skills` array must be rejected."""
    _write_valid_skill(git_repo)
    clone = tmp_path / "clones" / "agent-plus-skills"
    clone.mkdir(parents=True)
    (clone / "marketplace.json").write_text(
        json.dumps({
            "name": "agent-plus-skills",
            "owner": "me",
            "version": "0.1.0",
            # NB: no `skills` array — old `plugins` shape, or just malformed.
            "plugins": [],
        }, indent=2),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    res = _run("promote", "my-skill",
               "--to", "me/agent-plus-skills",
               "--marketplace-path", str(clone),
               "--pretty",
               cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 2, res.stdout
    payload = json.loads(res.stdout)
    assert payload["error"] == "marketplace_malformed"


def test_unknown_keys_preserved(git_repo: Path, tmp_path: Path):
    """Unknown top-level keys in marketplace.json must be preserved on write."""
    _write_valid_skill(git_repo)
    clone = _make_marketplace(tmp_path, extra_keys={"customField": "keep-me"})
    home = tmp_path / "home"
    home.mkdir()
    res = _run("promote", "my-skill",
               "--to", "me/agent-plus-skills",
               "--marketplace-path", str(clone),
               "--no-dry-run", "--pretty",
               cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 0, res.stdout + res.stderr
    mp = json.loads((clone / "marketplace.json").read_text(encoding="utf-8"))
    assert mp.get("customField") == "keep-me"
    # Known keys still come first in the canonical order.
    keys = list(mp.keys())
    assert keys.index("skills") < keys.index("customField")


def test_destination_exists(git_repo: Path, tmp_path: Path):
    _write_valid_skill(git_repo)
    clone = _make_marketplace(tmp_path)
    # Pre-create the destination directory.
    (clone / "my-skill").mkdir()
    (clone / "my-skill" / "SKILL.md").write_text("preexisting", encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()
    res = _run("promote", "my-skill",
               "--to", "me/agent-plus-skills",
               "--marketplace-path", str(clone),
               "--pretty",
               cwd=git_repo, env=_isolated_home_env(home))
    assert res.returncode == 2, res.stdout
    payload = json.loads(res.stdout)
    assert payload["error"] == "destination_exists"
