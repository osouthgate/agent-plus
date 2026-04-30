"""Shared helpers for scope-topology subcommands (v0.14.0).

Used by globalize, localize, where, team-sync, collisions. Stdlib only.
Helpers like `_git_toplevel` are NOT injected here — modules that need
them call back through their host subcommand which receives them via
the bin loader's namespace injection. Most helpers in this file are
pure (path math, name validation), so they don't need injection.
"""
from __future__ import annotations

import re
from pathlib import Path

# A legal Claude-skill directory name. Mirrors what `scaffold` accepts.
_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def is_legal_skill_name(name: str) -> bool:
    return bool(name) and bool(_SKILL_NAME_RE.match(name))


def project_skills_root(project: Path) -> Path:
    """`<repo>/.claude/skills/`."""
    return project / ".claude" / "skills"


def global_skills_root(home: Path | None = None) -> Path:
    """`~/.claude/skills/`."""
    return (home or Path.home()) / ".claude" / "skills"


def plugins_cache_root(home: Path | None = None) -> Path:
    """`~/.claude/plugins/cache/`."""
    return (home or Path.home()) / ".claude" / "plugins" / "cache"


def project_skill_dir(project: Path, name: str) -> Path:
    return project_skills_root(project) / name


def global_skill_dir(name: str, home: Path | None = None) -> Path:
    return global_skills_root(home) / name


def _read_skill_version(skill_dir: Path) -> str | None:
    """Best-effort: read `version:` from SKILL.md frontmatter. Returns None
    if absent or unparsable. Stdlib-only — no YAML dep."""
    skill_md = skill_dir / "SKILL.md"
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for raw in lines[1:]:
        if raw.strip() == "---":
            break
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        if key.strip() == "version":
            v = val.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            return v or None
    return None


def find_plugin_locations(name: str, home: Path | None = None) -> list[dict]:
    """Walk `~/.claude/plugins/cache/**/skills/<name>/`. Returns a list of
    {scope: "plugin", path, plugin, version} dicts. Empty list if cache
    root absent. The plugin name + version come from the parent
    `.claude-plugin/plugin.json` if present; otherwise from the cache
    subdirectory name."""
    root = plugins_cache_root(home)
    if not root.is_dir():
        return []
    out: list[dict] = []
    # Glob pattern: <cache>/<plugin>/skills/<name>/SKILL.md
    # Plugin layout in cache may also nest deeper, so use rglob over the
    # cache root looking for `skills/<name>/SKILL.md`.
    for skill_md in root.rglob("SKILL.md"):
        try:
            skill_dir = skill_md.parent
            if skill_dir.name != name:
                continue
            # Confirm grandparent dir is named "skills" — guards against
            # arbitrary SKILL.md files inside the cache.
            if skill_dir.parent.name != "skills":
                continue
        except OSError:
            continue
        # Resolve plugin manifest. Walk up looking for .claude-plugin/plugin.json.
        plugin_name = None
        plugin_version = None
        cur: Path = skill_dir.parent.parent  # parent of skills/
        # Search a few levels upward for the plugin manifest.
        for _ in range(4):
            manifest = cur / ".claude-plugin" / "plugin.json"
            if manifest.is_file():
                import json as _json
                try:
                    data = _json.loads(manifest.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        plugin_name = data.get("name") or plugin_name
                        plugin_version = data.get("version") or plugin_version
                except (OSError, _json.JSONDecodeError):
                    pass
                break
            if cur.parent == cur:
                break
            cur = cur.parent
        if plugin_name is None:
            # Best fallback: directory name two levels up from <name>/ (e.g. cache/<plugin>/skills/<name>/)
            plugin_name = skill_dir.parent.parent.name
        version = _read_skill_version(skill_dir) or plugin_version
        out.append({
            "scope": "plugin",
            "path": str(skill_dir),
            "plugin": plugin_name,
            "version": version,
        })
    return out


def find_locations(
    name: str,
    project: Path | None,
    home: Path | None = None,
) -> list[dict]:
    """Return location dicts across project, global, plugin tiers."""
    locs: list[dict] = []
    if project is not None:
        pdir = project_skill_dir(project, name)
        if pdir.is_dir() and (pdir / "SKILL.md").exists():
            locs.append({
                "scope": "project",
                "path": str(pdir),
                "version": _read_skill_version(pdir),
            })
    gdir = global_skill_dir(name, home)
    if gdir.is_dir() and (gdir / "SKILL.md").exists():
        locs.append({
            "scope": "global",
            "path": str(gdir),
            "version": _read_skill_version(gdir),
        })
    locs.extend(find_plugin_locations(name, home))
    return locs


def resolution_hint(locations: list[dict]) -> str:
    """Claude Code's documented loader preference: project > global > plugin."""
    if not locations:
        return "unknown"
    scopes = [l["scope"] for l in locations]
    for preferred in ("project", "global", "plugin"):
        if preferred in scopes:
            return preferred
    return "unknown"
