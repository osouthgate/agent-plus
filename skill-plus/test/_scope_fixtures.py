"""Shared fixtures for v0.14.0 scope-topology tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-plus"


SKILL_MD_TEMPLATE = """---
description: A test skill named {name} used by scope-topology tests.
when_to_use: Use this in scope-topology tests to exercise globalize/localize/where.
allowed_tools: Bash, Read
version: 0.1.0
---

# {name}

## Killer command

```
{name} go
```

## Do NOT use this for

- anything outside the test fixture

## Safety rules

- read-only by default
"""


def _write_skill(skill_dir: Path, name: str, *, version: str = "0.1.0") -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    bin_dir = skill_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    md = SKILL_MD_TEMPLATE.format(name=name).replace(
        "version: 0.1.0", f"version: {version}"
    )
    (skill_dir / "SKILL.md").write_text(md, encoding="utf-8")
    (bin_dir / name).write_text(
        "#!/bin/sh\necho hi\n", encoding="utf-8"
    )
    return skill_dir


def fake_project_skill(repo_root: Path, name: str, *, version: str = "0.1.0") -> Path:
    """Create <repo_root>/.claude/skills/<name>/. Initializes git if needed."""
    if not (repo_root / ".git").exists():
        repo_root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=str(repo_root), check=True)
    skill_dir = repo_root / ".claude" / "skills" / name
    return _write_skill(skill_dir, name, version=version)


def fake_global_skill(home: Path, name: str, *, version: str = "0.1.0") -> Path:
    """Create <home>/.claude/skills/<name>/."""
    skill_dir = home / ".claude" / "skills" / name
    return _write_skill(skill_dir, name, version=version)


def fake_plugin_skill(home: Path, plugin: str, name: str, *,
                      plugin_version: str = "1.0.0",
                      skill_version: str = "0.1.0") -> Path:
    """Create <home>/.claude/plugins/cache/<plugin>/skills/<name>/ with manifest."""
    plugin_root = home / ".claude" / "plugins" / "cache" / plugin
    plugin_root.mkdir(parents=True, exist_ok=True)
    manifest_dir = plugin_root / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"name": plugin, "version": plugin_version}),
        encoding="utf-8",
    )
    skill_dir = plugin_root / "skills" / name
    return _write_skill(skill_dir, name, version=skill_version)


def run_bin(*args: str, cwd: Path | None = None,
            env: dict[str, str] | None = None,
            home: Path | None = None,
            input_text: str | None = None,
            timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run skill-plus as a subprocess. If `home` is set, it overrides
    HOME and USERPROFILE in the child environment so Path.home() resolves
    inside tmp_path."""
    e = os.environ.copy()
    if env:
        e.update(env)
    if home is not None:
        e["HOME"] = str(home)
        e["USERPROFILE"] = str(home)
    # Force non-tty stdin unless caller supplies input. Subprocess inherits
    # stdin by default, which can be a tty when pytest runs in a terminal —
    # that breaks the non-tty-bail tests. Passing input="" or input_text
    # both make stdin a pipe (not a tty).
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True, timeout=timeout,
        cwd=str(cwd) if cwd else None, env=e,
        input=input_text if input_text is not None else "",
    )


def payload(res: subprocess.CompletedProcess[str]) -> dict:
    """Parse JSON stdout, asserting we got valid JSON. Allow non-zero exit
    so callers can inspect error envelopes."""
    assert res.stdout, f"empty stdout; stderr={res.stderr!r}"
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"could not parse stdout as JSON ({exc}): "
            f"stdout={res.stdout!r} stderr={res.stderr!r}"
        )
