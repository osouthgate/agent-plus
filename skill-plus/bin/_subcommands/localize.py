"""skill-plus localize — move a global skill into the project scope.

Symmetric mirror of globalize. Source `~/.claude/skills/<name>/`,
destination `<repo>/.claude/skills/<name>/`. Default is dry-run.

Helpers (`_git_toplevel`) are injected by the bin shell.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

_HELPERS_PATH = Path(__file__).resolve().parent / "_scope_helpers.py"
_spec = importlib.util.spec_from_file_location("_skill_plus_scope_helpers_l", _HELPERS_PATH)
_helpers = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
assert _spec and _spec.loader
_spec.loader.exec_module(_helpers)  # type: ignore[union-attr]

is_legal_skill_name = _helpers.is_legal_skill_name
project_skill_dir = _helpers.project_skill_dir
global_skill_dir = _helpers.global_skill_dir


def _stderr(msg: str) -> None:
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    except UnicodeEncodeError:
        sys.stderr.write(msg.encode("ascii", "replace").decode("ascii") + "\n")
        sys.stderr.flush()


def localize_action(name: str, *, dry_run: bool, keep_local: bool, force: bool) -> dict:
    """Internal helper — also reused by team-sync. Returns the payload dict
    (without the `tool` envelope)."""
    if not is_legal_skill_name(name):
        return {
            "verdict": "error_invalid_name",
            "name": name,
            "error": f"'{name}' is not a legal skill directory name",
            "dry_run": dry_run,
        }

    top = _git_toplevel()  # injected
    if top is None:
        return {
            "verdict": "error_no_git_repo",
            "name": name,
            "error": "localize requires a git repository (project destination must be inside a repo)",
            "dry_run": dry_run,
        }
    project = top.resolve()

    source = global_skill_dir(name)
    destination = project_skill_dir(project, name)

    if not source.is_dir():
        _stderr(f"  ! source missing: {source}")
        return {
            "verdict": "error_source_missing",
            "name": name,
            "source": str(source),
            "destination": str(destination),
            "error": f"global skill not found at {source}",
            "dry_run": dry_run,
        }

    if destination.exists() and not force:
        _stderr(f"  ! destination exists: {destination} (pass --force to overwrite)")
        return {
            "verdict": "error_destination_exists",
            "name": name,
            "source": str(source),
            "destination": str(destination),
            "error": f"project skill already exists at {destination}",
            "dry_run": dry_run,
        }

    if dry_run:
        verb = "would_copy" if keep_local else "would_move"
        _stderr(f"  [dry-run] {verb} {source} -> {destination}")
        return {
            "verdict": verb,
            "name": name,
            "source": str(source),
            "destination": str(destination),
            "keep_local": keep_local,
            "force": force,
            "dry_run": True,
        }

    destination.parent.mkdir(parents=True, exist_ok=True)
    if force and destination.exists():
        shutil.rmtree(str(destination))

    if keep_local:
        shutil.copytree(str(source), str(destination))
        verdict = "copied"
    else:
        shutil.move(str(source), str(destination))
        verdict = "moved"

    _stderr(f"  ok {verdict}: {source} -> {destination}")
    return {
        "verdict": verdict,
        "name": name,
        "source": str(source),
        "destination": str(destination),
        "keep_local": keep_local,
        "force": force,
        "dry_run": False,
    }


def run(args, emit_fn) -> int:
    payload = localize_action(
        args.name,
        dry_run=bool(args.dry_run),
        keep_local=bool(getattr(args, "keep_local", False)),
        force=bool(getattr(args, "force", False)),
    )
    emit_fn(payload)
    return 0 if not payload["verdict"].startswith("error_") else 1
