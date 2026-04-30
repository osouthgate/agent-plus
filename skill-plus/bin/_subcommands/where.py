"""skill-plus where — read-only three-tier scope resolver (v0.14.0).

Walks project + global + plugin-cache tiers and reports every location
the named skill is defined. Read-only; never writes.

Helpers (`_git_toplevel`) are injected by the bin shell at module load time.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load sibling _scope_helpers module without forcing a package layout.
_HELPERS_PATH = Path(__file__).resolve().parent / "_scope_helpers.py"
_spec = importlib.util.spec_from_file_location("_skill_plus_scope_helpers", _HELPERS_PATH)
_helpers = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
assert _spec and _spec.loader
_spec.loader.exec_module(_helpers)  # type: ignore[union-attr]

find_locations = _helpers.find_locations
resolution_hint = _helpers.resolution_hint
is_legal_skill_name = _helpers.is_legal_skill_name


def run(args, emit_fn) -> int:
    name = args.name

    if not is_legal_skill_name(name):
        emit_fn({
            "verdict": "error_invalid_name",
            "name": name,
            "error": f"'{name}' is not a legal skill directory name (allowed: a-z A-Z 0-9 _ -)",
            "dry_run": True,
        })
        return 1

    # Resolve project (optional — `where` works without a repo).
    top = _git_toplevel()  # injected
    project: Path | None = top.resolve() if top is not None else None

    locations = find_locations(name, project)
    hint = resolution_hint(locations)
    collision = len(locations) > 1

    if not locations:
        sys.stderr.write(f"  ! skill '{name}' not found in any scope (project, global, plugin)\n")
        sys.stderr.flush()
    else:
        sys.stderr.write(
            f"  i found {len(locations)} location(s) for '{name}'; resolution: {hint}\n"
        )
        sys.stderr.flush()

    payload = {
        "verdict": "found" if locations else "not_found",
        "name": name,
        "locations": locations,
        "resolution_hint": hint,
        "collision": collision,
        "dry_run": True,  # always read-only
    }
    emit_fn(payload)
    return 0
