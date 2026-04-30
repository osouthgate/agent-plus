"""skill-plus team-sync — share a global skill with the team via the repo.

Equivalent to `localize <name>` plus an emitted commit-message hint.
Does NOT invoke git. Caller decides whether to commit.

Helpers (`_git_toplevel`) are injected by the bin shell.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Reuse the localize action.
_LOCALIZE_PATH = Path(__file__).resolve().parent / "localize.py"
_spec = importlib.util.spec_from_file_location("_skill_plus_localize_for_ts", _LOCALIZE_PATH)
_localize_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
assert _spec and _spec.loader
# Inject the same helpers list the bin shell injects, so localize_action's
# `_git_toplevel` lookup resolves. We borrow from this module's globals which
# are themselves injected by the bin loader.
_localize_mod.__dict__["_git_toplevel"] = None  # placeholder; rewired in run()
_spec.loader.exec_module(_localize_mod)  # type: ignore[union-attr]


_COMMIT_HINT_TEMPLATE = (
    "chore(skills): share {name} via repo (was global)\n"
    "\n"
    "Was at ~/.claude/skills/{name}/, now at .claude/skills/{name}/\n"
    "so teammates pick it up automatically."
)


def run(args, emit_fn) -> int:
    # Wire the injected `_git_toplevel` into the localize module so its
    # internal call resolves correctly.
    _localize_mod.__dict__["_git_toplevel"] = _git_toplevel  # injected

    payload = _localize_mod.localize_action(
        args.name,
        dry_run=bool(args.dry_run),
        keep_local=False,
        force=bool(getattr(args, "force", False)),
    )

    # team-sync always emits a commit_hint, even on error (so wizard can
    # show what the user *would* have committed). Hint is data-only.
    payload["commit_hint"] = _COMMIT_HINT_TEMPLATE.format(name=args.name)

    if not payload["verdict"].startswith("error_"):
        try:
            sys.stderr.write(
                "  i tip: commit the new .claude/skills/" + args.name + "/ to share with the team\n"
            )
            sys.stderr.flush()
        except UnicodeEncodeError:
            pass

    emit_fn(payload)
    return 0 if not payload["verdict"].startswith("error_") else 1
