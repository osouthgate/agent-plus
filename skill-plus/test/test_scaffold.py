"""Tests for skill-plus scaffold subcommand."""
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
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True, timeout=30,
        cwd=str(cwd) if cwd else None, env=e,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)
    return tmp_path


def _scaffold_full(repo: Path, name: str = "my-skill", **extra) -> subprocess.CompletedProcess:
    args = [
        "scaffold", name,
        "--description", "A short skill that does the one thing.",
        "--when-to-use", "When you need to do the one thing repeatedly.",
        "--killer-command", "do-the-thing --flag",
        "--do-not-use-for", "Things outside the one thing;Other unrelated tasks",
    ]
    for k, v in extra.items():
        args.extend([k, v] if v is not None else [k])
    return _run(*args, "--pretty", cwd=repo)


def test_scaffold_writes_all_four_files(git_repo: Path):
    res = _scaffold_full(git_repo)
    assert res.returncode == 0, res.stderr + res.stdout
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["name"] == "my-skill"

    skill_dir = git_repo / ".claude" / "skills" / "my-skill"
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "bin" / "my-skill").is_file()
    assert (skill_dir / "bin" / "my-skill.cmd").is_file()
    assert (skill_dir / "bin" / "my-skill.py").is_file()

    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "## Killer command" in skill_md
    assert "do-the-thing --flag" in skill_md
    assert "## Do NOT use this for" in skill_md
    assert "## Safety rules" in skill_md
    assert "Things outside the one thing" in skill_md
    assert "Other unrelated tasks" in skill_md


def test_scaffold_frontmatter_has_required_keys(git_repo: Path):
    _scaffold_full(git_repo)
    skill_md = (git_repo / ".claude/skills/my-skill/SKILL.md").read_text(encoding="utf-8")
    head = skill_md.split("---", 2)
    assert len(head) >= 3, "expected --- delimited frontmatter"
    fm = head[1]
    assert "name:" in fm
    assert "description:" in fm
    assert "when_to_use:" in fm
    assert "allowed-tools:" in fm


def test_scaffold_python_entry_is_valid_python(git_repo: Path):
    _scaffold_full(git_repo)
    p = git_repo / ".claude/skills/my-skill/bin/my-skill.py"
    src = p.read_text(encoding="utf-8")
    compile(src, str(p), "exec")  # raises SyntaxError if broken


def test_scaffold_python_entry_runs_version_end_to_end(git_repo: Path):
    _scaffold_full(git_repo)
    p = git_repo / ".claude/skills/my-skill/bin/my-skill.py"
    res = subprocess.run([sys.executable, str(p), "--version"],
                         capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, res.stderr
    assert "my-skill" in res.stdout


def test_scaffold_python_entry_do_subcommand_emits_envelope(git_repo: Path):
    _scaffold_full(git_repo)
    p = git_repo / ".claude/skills/my-skill/bin/my-skill.py"
    res = subprocess.run([sys.executable, str(p), "do"],
                         capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["tool"] == {"name": "my-skill", "version": "0.1.0"}
    assert payload["ok"] is True
    assert payload["command"] == "do"


@pytest.mark.parametrize("bad", ["Foo", "1foo", "foo_bar", "a" * 100])
def test_invalid_names_rejected(git_repo: Path, bad: str):
    res = _run(
        "scaffold", bad,
        "--description", "x" * 20,
        "--when-to-use", "x" * 20,
        "--killer-command", "do-it",
        "--do-not-use-for", "nope",
        "--pretty",
        cwd=git_repo,
    )
    assert res.returncode == 2
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "invalid_name"


def test_refuses_if_exists_unless_force(git_repo: Path):
    res = _scaffold_full(git_repo)
    assert res.returncode == 0
    res2 = _scaffold_full(git_repo)
    assert res2.returncode == 2
    payload = json.loads(res2.stdout)
    assert payload["error"] == "skill_exists"

    # --force overwrites
    args = [
        "scaffold", "my-skill",
        "--description", "A different description here.",
        "--when-to-use", "When you need to do the one thing repeatedly.",
        "--killer-command", "do-the-thing --flag",
        "--do-not-use-for", "nope;other",
        "--force", "--pretty",
    ]
    res3 = _run(*args, cwd=git_repo)
    assert res3.returncode == 0, res3.stderr + res3.stdout
    payload = json.loads(res3.stdout)
    assert payload["ok"] is True


def test_missing_slots_reports_which(git_repo: Path):
    res = _run(
        "scaffold", "x-skill",
        "--description", "short",  # < 10 chars
        "--pretty",
        cwd=git_repo,
    )
    assert res.returncode == 2
    payload = json.loads(res.stdout)
    assert payload["error"] == "missing_required_slots"
    missing = set(payload["missing"])
    assert "description" in missing  # too short
    assert "when_to_use" in missing
    assert "killer_command" in missing
    assert "do_not_use_for" in missing


def test_from_candidate_seeds_killer_command(git_repo: Path, monkeypatch):
    # Write a fake candidates.jsonl at the project state root.
    state = git_repo / ".agent-plus" / "skill-plus"
    state.mkdir(parents=True, exist_ok=True)
    cand = {
        "id": "abc123",
        "key": "railway logs",
        "count": 8,
        "sessions": ["s1", "s2", "s3"],
        "examples": ["railway logs --service api --since 5m", "railway logs --service web"],
    }
    (state / "candidates.jsonl").write_text(json.dumps(cand) + "\n", encoding="utf-8")

    res = _run(
        "scaffold", "probe-errors",
        "--from-candidate", "abc123",
        "--when-to-use", "When investigating service errors quickly.",
        "--do-not-use-for", "Anything that mutates state",
        "--pretty",
        cwd=git_repo,
    )
    assert res.returncode == 0, res.stderr + res.stdout
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["fromCandidate"] == "abc123"

    skill_md = (git_repo / ".claude/skills/probe-errors/SKILL.md").read_text(encoding="utf-8")
    assert "railway logs --service api --since 5m" in skill_md
    # Description seeded from default template
    assert "railway logs" in skill_md
    assert "8 uses" in skill_md


def test_from_candidate_unknown_id_errors(git_repo: Path):
    res = _run(
        "scaffold", "ghost",
        "--from-candidate", "nope-not-there",
        "--when-to-use", "When investigating service errors quickly.",
        "--do-not-use-for", "anything",
        "--pretty",
        cwd=git_repo,
    )
    assert res.returncode == 2
    payload = json.loads(res.stdout)
    assert payload["error"] == "candidate_not_found"
