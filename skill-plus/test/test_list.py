"""Tests for `skill-plus list` — auditing skills against the contract."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-plus"


def _run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True, timeout=30,
        cwd=str(cwd) if cwd else None, env=e,
    )


# ─── fixtures ─────────────────────────────────────────────────────────────────

GOOD_SKILL_MD = """---
description: A well-formed skill that exercises every required field of the contract.
when_to_use: Use this when you want a fully-passing reference for the skill-plus list audit.
allowed_tools: Bash, Read
---

# good-skill

Reference body.

## Killer command

```
good-skill foo --bar
```

## Do NOT use this for

- random unrelated tasks

## Safety rules

- read-only by default
"""

GOOD_SKILL_PY = """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

def main():
    print(json.dumps({"ok": True}))

if __name__ == "__main__":
    main()
"""

PARTIAL_SKILL_MD = """---
description: Partial skill missing the safety section and the Windows launcher.
when_to_use: Use this to confirm the audit flags a skill that is mostly but not entirely compliant.
allowed_tools: Bash
---

# partial-skill

## Killer command

```
partial-skill go
```

## Do NOT use this for

- anything that needs safety rules
"""

BROKEN_SKILL_MD = """no frontmatter at all, just body text

## Killer command

something
"""

NON_STDLIB_PY = """#!/usr/bin/env python3
import os
import sys
import requests   # third-party
from pathlib import Path
import json
"""


def _make_skill(skills_dir: Path, name: str, skill_md: str, *,
                 launcher_py_content: str | None = None,
                 with_cmd: bool = True,
                 launcher_as_py_extension: bool = False) -> Path:
    skill_dir = skills_dir / name
    bin_dir = skill_dir / "bin"
    bin_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    if launcher_py_content is not None:
        if launcher_as_py_extension:
            (bin_dir / f"{name}.py").write_text(launcher_py_content, encoding="utf-8")
            # Also drop a posix launcher so the posixLauncher check passes.
            (bin_dir / name).write_text("#!/bin/sh\nexec python3 \"$(dirname \"$0\")/" + name + ".py\" \"$@\"\n", encoding="utf-8")
        else:
            (bin_dir / name).write_text(launcher_py_content, encoding="utf-8")

    if with_cmd:
        (bin_dir / f"{name}.cmd").write_text("@echo off\r\npython %~dp0" + name + " %*\r\n", encoding="utf-8")
    return skill_dir


@pytest.fixture
def project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)

    _make_skill(skills_dir, "good-skill", GOOD_SKILL_MD,
                launcher_py_content=GOOD_SKILL_PY, with_cmd=True)
    _make_skill(skills_dir, "partial-skill", PARTIAL_SKILL_MD,
                launcher_py_content=GOOD_SKILL_PY, with_cmd=False)
    _make_skill(skills_dir, "broken-skill", BROKEN_SKILL_MD,
                launcher_py_content=GOOD_SKILL_PY, with_cmd=True)
    return tmp_path


# ─── tests ────────────────────────────────────────────────────────────────────

def _payload(res: subprocess.CompletedProcess[str]) -> dict:
    assert res.returncode == 0, f"rc={res.returncode} stderr={res.stderr}"
    return json.loads(res.stdout)


def test_no_skills_dir_returns_clean_envelope(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    res = _run("list", "--project", str(tmp_path))
    payload = _payload(res)
    assert payload["skills"] == []
    assert payload["skillsTotal"] == 0
    assert payload["note"] == "no .claude/skills/ directory"
    assert payload["tool"]["name"] == "skill-plus"


def test_envelope_shape(project: Path):
    res = _run("list", "--project", str(project))
    payload = _payload(res)
    assert payload["project"] == str(project.resolve())
    assert payload["skillsDir"].endswith(os.path.join(".claude", "skills"))
    assert payload["skillsTotal"] == 3
    assert len(payload["skills"]) == 3
    for s in payload["skills"]:
        assert {"name", "path", "passed", "total", "score",
                "frontmatter", "body", "bin", "nonStdlibImports"} <= set(s.keys())


def test_sort_worst_first(project: Path):
    res = _run("list", "--project", str(project))
    payload = _payload(res)
    scores = [s["score"] for s in payload["skills"]]
    assert scores == sorted(scores), "skills should be sorted ascending by score"
    # broken-skill has the lowest score (no frontmatter at all).
    assert payload["skills"][0]["name"] == "broken-skill"
    assert payload["skills"][-1]["name"] == "good-skill"


def test_good_skill_passes_everything(project: Path):
    res = _run("list", "--project", str(project))
    payload = _payload(res)
    good = next(s for s in payload["skills"] if s["name"] == "good-skill")
    assert all(good["frontmatter"].values()), good["frontmatter"]
    assert all(good["body"].values()), good["body"]
    assert all(good["bin"].values()), good["bin"]
    assert good["passed"] == good["total"]
    assert good["score"] == 1.0


def test_partial_skill_flags_safety_and_cmd(project: Path):
    res = _run("list", "--project", str(project))
    payload = _payload(res)
    partial = next(s for s in payload["skills"] if s["name"] == "partial-skill")
    assert partial["body"]["killerCommand"] is True
    assert partial["body"]["doNotUseFor"] is True
    assert partial["body"]["safety"] is False
    assert partial["bin"]["windowsLauncher"] is False
    assert partial["bin"]["posixLauncher"] is True
    # frontmatter intact
    assert all(partial["frontmatter"].values())


def test_broken_skill_records_parse_error(project: Path):
    res = _run("list", "--project", str(project))
    payload = _payload(res)
    broken = next(s for s in payload["skills"] if s["name"] == "broken-skill")
    assert "frontmatterParseError" in broken
    assert broken["frontmatter"] == {
        "description": False, "whenToUse": False, "allowedTools": False,
    }


def test_non_stdlib_imports_detected(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    _make_skill(skills_dir, "third-party-skill", GOOD_SKILL_MD,
                launcher_py_content=NON_STDLIB_PY, with_cmd=True)
    res = _run("list", "--project", str(tmp_path))
    payload = _payload(res)
    skill = payload["skills"][0]
    assert "requests" in skill["nonStdlibImports"]
    # stdlib imports must NOT show up
    for std in ("os", "sys", "json", "pathlib"):
        assert std not in skill["nonStdlibImports"]


def test_lenient_frontmatter_key_forms(tmp_path: Path):
    """`when-to-use` (kebab) and `allowed-tools` (kebab) are accepted."""
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    md = """---
description: Skill using kebab-case frontmatter keys to test lenient parsing.
when-to-use: Use this to verify the audit accepts when-to-use as a synonym for when_to_use.
allowed-tools: Bash, Read
---

## Killer command

go

## When NOT to use

- bad cases

## Safety

- nothing destructive
"""
    _make_skill(skills_dir, "kebab-skill", md,
                launcher_py_content=GOOD_SKILL_PY, with_cmd=True)
    res = _run("list", "--project", str(tmp_path))
    payload = _payload(res)
    skill = payload["skills"][0]
    assert all(skill["frontmatter"].values()), skill["frontmatter"]
    assert all(skill["body"].values()), skill["body"]


# ─── --include-global tests (v0.12.0) ────────────────────────────────────────


def _make_global_skill(home: Path, name: str, skill_md: str = GOOD_SKILL_MD) -> Path:
    """Create a skill under <home>/.claude/skills/<name>/."""
    global_skills = home / ".claude" / "skills"
    global_skills.mkdir(parents=True, exist_ok=True)
    return _make_skill(global_skills, name, skill_md,
                       launcher_py_content=GOOD_SKILL_PY, with_cmd=True)


def test_include_global_default_off_yields_only_project_scope(project: Path,
                                                              tmp_path: Path):
    fake_home = tmp_path / "fake-home"
    _make_global_skill(fake_home, "global-only-skill")
    res = _run("list", "--project", str(project),
               env={"HOME": str(fake_home), "USERPROFILE": str(fake_home)})
    payload = _payload(res)
    # Without --include-global, the global skill is invisible. Existing
    # envelope shape (no `scope`/`scopes`/`collisions` keys) is preserved
    # so back-compat with all pre-v0.12.0 consumers holds.
    assert payload["skillsTotal"] == 3
    assert "scopes" not in payload
    assert "collisions" not in payload
    for s in payload["skills"]:
        assert "scope" not in s


def test_include_global_walks_both_scopes(project: Path, tmp_path: Path):
    fake_home = tmp_path / "fake-home"
    _make_global_skill(fake_home, "global-only-skill")
    res = _run("list", "--include-global", "--project", str(project),
               env={"HOME": str(fake_home), "USERPROFILE": str(fake_home)})
    payload = _payload(res)
    # 3 project + 1 global
    assert payload["skillsTotal"] == 4
    assert payload["scopes"]["project"]["count"] == 3
    assert payload["scopes"]["global"]["count"] == 1
    # Every row carries a scope tag.
    by_name = {s["name"]: s for s in payload["skills"]}
    assert by_name["global-only-skill"]["scope"] == "global"
    assert by_name["good-skill"]["scope"] == "project"
    assert payload["collisions"] == []  # no name overlap


def test_include_global_flags_collisions(project: Path, tmp_path: Path):
    fake_home = tmp_path / "fake-home"
    # Same name in both scopes → collision.
    _make_global_skill(fake_home, "good-skill")
    _make_global_skill(fake_home, "another-global")
    res = _run("list", "--include-global", "--project", str(project),
               env={"HOME": str(fake_home), "USERPROFILE": str(fake_home)})
    payload = _payload(res)
    assert payload["collisions"] == ["good-skill"]
    # Both rows for `good-skill` carry collision: true.
    matches = [s for s in payload["skills"] if s["name"] == "good-skill"]
    assert len(matches) == 2
    assert all(s.get("collision") is True for s in matches)
    # Non-colliding rows do NOT carry the flag.
    non_colliding = [s for s in payload["skills"] if s["name"] == "another-global"]
    assert len(non_colliding) == 1
    assert "collision" not in non_colliding[0]


def test_include_global_with_no_global_dir_is_empty(project: Path, tmp_path: Path):
    fake_home = tmp_path / "fake-home-empty"
    fake_home.mkdir()  # exists but no .claude/skills/ underneath
    res = _run("list", "--include-global", "--project", str(project),
               env={"HOME": str(fake_home), "USERPROFILE": str(fake_home)})
    payload = _payload(res)
    assert payload["scopes"]["global"]["count"] == 0
    assert payload["scopes"]["project"]["count"] == 3
    assert payload["collisions"] == []
