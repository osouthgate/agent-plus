#!/usr/bin/env python3
"""Dump the env var names declared by `agent-plus-meta`'s `PLUGIN_ENV_SPEC`.

Single source of truth: the bin owns the env-var contract. The pre-commit
hook reads this script's output to know which env vars to strip when
running tests under clean env. This keeps the hook and the contract
in sync — when a new plugin lands with new required env vars, this
script picks them up automatically.

Output: space-separated env var names on stdout, one line. Stable
ordering (sorted) so diffs are clean.

Stdlib only. Exits 0 on success; 2 if the bin can't be loaded (the hook
treats this as a hard failure — silently skipping the strip is the bug
class the hook exists to prevent).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    bin_path = repo_root / "agent-plus-meta" / "bin" / "agent-plus-meta"

    if not bin_path.is_file():
        sys.stderr.write(
            f"_dump_plugin_env_vars.py: cannot find {bin_path}\n"
            "  (run from inside an agent-plus checkout)\n"
        )
        return 2

    # The bin has no .py extension, so importlib.util.spec_from_file_location
    # can't infer the loader. Use SourceFileLoader explicitly — same trick
    # the test files use to load the bin as a module.
    from importlib.machinery import SourceFileLoader

    try:
        loader = SourceFileLoader("agent_plus_meta", str(bin_path))
        spec = importlib.util.spec_from_loader("agent_plus_meta", loader)
        if spec is None:
            raise ImportError("spec_from_loader returned None")
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001 — bin can fail to load for any reason
        sys.stderr.write(
            f"_dump_plugin_env_vars.py: failed to load {bin_path}: {e}\n"
        )
        return 2

    plugin_env_spec = getattr(mod, "PLUGIN_ENV_SPEC", None)
    if not isinstance(plugin_env_spec, dict):
        sys.stderr.write(
            "_dump_plugin_env_vars.py: PLUGIN_ENV_SPEC missing or not a dict\n"
        )
        return 2

    names: set[str] = set()
    for plugin, entry in plugin_env_spec.items():
        if not isinstance(entry, dict):
            continue
        for key in ("required", "optional"):
            for var in entry.get(key) or []:
                if isinstance(var, str) and var:
                    names.add(var)

    print(" ".join(sorted(names)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
