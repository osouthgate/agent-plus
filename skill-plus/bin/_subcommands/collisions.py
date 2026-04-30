"""skill-plus collisions — detect and resolve project/global skill name collisions.

Detects collisions between `<repo>/.claude/skills/` and `~/.claude/skills/`,
then offers renames in one of four UX modes:
  - interactive (default tty): prompt user per collision
  - non-tty bail: emit verdict=needs_user_input + suggested_renames[]
  - --rename <name>:<scope>:<new-name> (repeatable): scripted resolution
  - --auto: project wins, global gets `-global` suffix (T3)

Helpers (`_git_toplevel`) are injected by the bin shell.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

_HELPERS_PATH = Path(__file__).resolve().parent / "_scope_helpers.py"
_spec = importlib.util.spec_from_file_location("_skill_plus_scope_helpers_c", _HELPERS_PATH)
_helpers = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
assert _spec and _spec.loader
_spec.loader.exec_module(_helpers)  # type: ignore[union-attr]

is_legal_skill_name = _helpers.is_legal_skill_name
project_skills_root = _helpers.project_skills_root
global_skills_root = _helpers.global_skills_root


def _stderr(msg: str) -> None:
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    except UnicodeEncodeError:
        sys.stderr.write(msg.encode("ascii", "replace").decode("ascii") + "\n")
        sys.stderr.flush()


def _scan_skills(skills_dir: Path) -> set[str]:
    if not skills_dir.is_dir():
        return set()
    out: set[str] = set()
    try:
        for entry in skills_dir.iterdir():
            if entry.is_dir() and (entry / "SKILL.md").exists():
                out.add(entry.name)
    except OSError:
        return set()
    return out


def _detect_collisions(project: Path) -> list[dict]:
    proot = project_skills_root(project)
    groot = global_skills_root()
    p_names = _scan_skills(proot)
    g_names = _scan_skills(groot)
    collisions: list[dict] = []
    for name in sorted(p_names & g_names):
        collisions.append({
            "name": name,
            "project_path": str(proot / name),
            "global_path": str(groot / name),
            "action": "pending",
            "new_name": None,
            "suggested_renames": [
                {"scope": "project", "new_name": f"{name}-project"},
                {"scope": "global", "new_name": f"{name}-global"},
            ],
        })
    return collisions


def _parse_rename_flag(spec: str) -> tuple[str, str, str] | None:
    """Parse `<name>:<scope>:<new-name>` (also accepts `=` separator on first
    field per plan example: `foo=global:foo-global`)."""
    # Normalize: allow `name=scope:new` or `name:scope:new`.
    if "=" in spec and spec.count(":") >= 1:
        # `name=scope:new`
        head, _, tail = spec.partition("=")
        if ":" not in tail:
            return None
        scope, _, new_name = tail.partition(":")
        return head.strip(), scope.strip(), new_name.strip()
    parts = spec.split(":")
    if len(parts) != 3:
        return None
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def _all_existing_names(project: Path) -> set[str]:
    return _scan_skills(project_skills_root(project)) | _scan_skills(global_skills_root())


def _do_rename(
    name: str,
    scope: str,
    new_name: str,
    *,
    project: Path,
    dry_run: bool,
) -> dict:
    """Rename one side of a collision. Returns a dict describing the action."""
    if scope == "project":
        src = project_skills_root(project) / name
        dst = project_skills_root(project) / new_name
    elif scope == "global":
        src = global_skills_root() / name
        dst = global_skills_root() / new_name
    else:
        return {
            "ok": False,
            "error": f"invalid scope '{scope}' (expected 'project' or 'global')",
        }

    if not src.is_dir():
        return {"ok": False, "error": f"source missing: {src}"}
    if dst.exists():
        return {"ok": False, "error": f"destination already exists: {dst}"}

    if dry_run:
        return {
            "ok": True,
            "verdict": "would_rename",
            "scope": scope,
            "old_path": str(src),
            "new_path": str(dst),
        }
    shutil.move(str(src), str(dst))
    return {
        "ok": True,
        "verdict": "renamed",
        "scope": scope,
        "old_path": str(src),
        "new_path": str(dst),
    }


def _interactive_resolve(collisions: list[dict]) -> tuple[bool, str | None]:
    """Returns (proceed, error). Mutates each collision dict in place to set
    `action` and `new_name`. Reads stdin via input(). Caller must guard tty."""
    for c in collisions:
        name = c["name"]
        prompt = (
            f"skill '{name}' exists in both project and global. "
            "Rename which? [p=project, g=global, s=skip] > "
        )
        try:
            sys.stderr.write(prompt)
            sys.stderr.flush()
            choice = input().strip().lower()
        except EOFError:
            return False, "stdin closed during interactive resolution"
        if choice in ("s", "skip", ""):
            c["action"] = "skip"
            c["new_name"] = None
            continue
        if choice in ("p", "project"):
            scope = "project"
        elif choice in ("g", "global"):
            scope = "global"
        else:
            return False, f"unrecognized choice '{choice}' (expected p/g/s)"
        # Ask for new name.
        sys.stderr.write(f"new name for '{name}' on {scope} side > ")
        sys.stderr.flush()
        try:
            new_name = input().strip()
        except EOFError:
            return False, "stdin closed during interactive resolution"
        if not is_legal_skill_name(new_name):
            return False, f"illegal new name '{new_name}'"
        c["action"] = f"rename_{scope}"
        c["new_name"] = new_name
    return True, None


def run(args, emit_fn) -> int:  # noqa: C901 — straight-line UX dispatcher
    dry_run = bool(args.dry_run)
    auto = bool(getattr(args, "auto", False))
    explicit_renames: list[str] = list(getattr(args, "rename", []) or [])

    top = _git_toplevel()  # injected
    if top is None:
        emit_fn({
            "verdict": "error_no_git_repo",
            "error": "collisions requires a git repository (project scope)",
            "collisions": [],
            "dry_run": dry_run,
        })
        return 1
    project = top.resolve()

    collisions = _detect_collisions(project)

    if not collisions:
        _stderr("  i no collisions detected between project and global scopes")
        emit_fn({
            "verdict": "no_collisions",
            "collisions": [],
            "dry_run": dry_run,
        })
        return 0

    # Resolve --rename flags into a {name: (scope, new_name)} map.
    rename_map: dict[str, tuple[str, str]] = {}
    if explicit_renames:
        for spec in explicit_renames:
            parsed = _parse_rename_flag(spec)
            if parsed is None:
                emit_fn({
                    "verdict": "error_invalid_rename_flag",
                    "error": f"could not parse --rename '{spec}' (expected name:scope:new-name)",
                    "collisions": collisions,
                    "dry_run": dry_run,
                })
                return 1
            n, s, new_n = parsed
            if s not in ("project", "global"):
                emit_fn({
                    "verdict": "error_invalid_rename_scope",
                    "error": f"--rename scope must be 'project' or 'global' (got '{s}')",
                    "collisions": collisions,
                    "dry_run": dry_run,
                })
                return 1
            if not is_legal_skill_name(new_n):
                emit_fn({
                    "verdict": "error_invalid_new_name",
                    "error": f"new name '{new_n}' is not a legal skill directory name",
                    "collisions": collisions,
                    "dry_run": dry_run,
                })
                return 1
            rename_map[n] = (s, new_n)

    # Choose UX mode.
    use_auto = auto
    use_explicit = bool(rename_map)
    use_interactive = (not use_auto) and (not use_explicit) and sys.stdin.isatty()

    if use_auto:
        # T3: project wins. Global side gets `-global` suffix.
        for c in collisions:
            new_name = f"{c['name']}-global"
            c["action"] = "rename_global"
            c["new_name"] = new_name
    elif use_explicit:
        # Apply mapped renames; un-mapped collisions stay pending.
        for c in collisions:
            mapping = rename_map.get(c["name"])
            if mapping is None:
                c["action"] = "skip"
                c["new_name"] = None
                continue
            scope, new_n = mapping
            c["action"] = f"rename_{scope}"
            c["new_name"] = new_n
    elif use_interactive:
        ok, err = _interactive_resolve(collisions)
        if not ok:
            emit_fn({
                "verdict": "error_interactive_failed",
                "error": err or "interactive resolution failed",
                "collisions": collisions,
                "dry_run": dry_run,
            })
            return 1
    else:
        # Non-tty bail (T1).
        _stderr("  ! collisions detected; not a tty — emitting suggested renames")
        emit_fn({
            "verdict": "needs_user_input",
            "collisions": collisions,
            "dry_run": dry_run,
        })
        return 0

    # Validate planned new names against existing names + each other.
    existing = _all_existing_names(project)
    planned_new: set[str] = set()
    for c in collisions:
        if c["action"] == "skip" or c["new_name"] is None:
            continue
        nn = c["new_name"]
        if nn in existing:
            emit_fn({
                "verdict": "error_new_name_collides",
                "error": f"planned new name '{nn}' collides with an existing skill",
                "collisions": collisions,
                "dry_run": dry_run,
            })
            return 1
        if nn in planned_new:
            emit_fn({
                "verdict": "error_duplicate_new_name",
                "error": f"two collisions would rename to the same new name '{nn}'",
                "collisions": collisions,
                "dry_run": dry_run,
            })
            return 1
        planned_new.add(nn)

    # Execute (or dry-run) each rename.
    any_action = False
    for c in collisions:
        if c["action"] == "skip" or c["new_name"] is None:
            continue
        scope = c["action"].replace("rename_", "")
        result = _do_rename(
            c["name"], scope, c["new_name"],
            project=project, dry_run=dry_run,
        )
        if not result.get("ok"):
            emit_fn({
                "verdict": "error_rename_failed",
                "error": result.get("error", "rename failed"),
                "collisions": collisions,
                "dry_run": dry_run,
            })
            return 1
        any_action = True

    if not any_action:
        verdict = "no_action"
    else:
        verdict = "would_rename" if dry_run else "renamed"

    emit_fn({
        "verdict": verdict,
        "collisions": collisions,
        "dry_run": dry_run,
    })
    return 0
