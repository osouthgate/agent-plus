"""agent-plus-meta migrations package.

Each migration module here exposes:

    def migrate(workspace: pathlib.Path) -> dict:
        return {"status": "ok" | "skipped" | "failed",
                "message": "...",
                "changes": [...]}

The runner (in `_subcommands/upgrade.py`) walks this directory and applies
every module whose destination version is in (LAST_VERSION, LATEST_VERSION].
History is persisted at ~/.agent-plus/migrations.json keyed by migration id
(e.g. "v0_13_5").

Idempotent: re-running on an already-migrated workspace MUST return
{"status": "skipped", "message": "already applied"}.

Empty on day one (v0.13.5) — the runner ships, the directory exists, the
contract is documented. The first breaking change has somewhere to land.
"""
