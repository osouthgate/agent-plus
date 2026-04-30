# `agent-plus-meta uninstall` — JSON envelope schema

**Stability:** frozen public contract as of v0.15.0.

The `agent-plus-meta uninstall` subcommand emits a JSON envelope on stdout
when invoked with `--json` (and to a lesser extent always, when wired
through the host `_with_tool_meta` wrapper). This document is the source
of truth for the envelope's shape.

## Schema

```jsonc
{
  "tool": {"name": "agent-plus-meta", "version": "0.15.0"},
  "action": "uninstall",
  "mode": "default | workspace | marketplaces | all | purge",
  "dry_run": false,
  "interactive": true,
  "user_confirmed": true,
  "install_dir": "/home/user/.local/bin",
  "paths": [
    {
      "path": "/home/user/.local/bin/agent-plus-meta",
      "kind": "primitive_bin",
      "scope": "default",
      "status": "removed"
    },
    {
      "path": "/home/user/repo/.agent-plus",
      "kind": "workspace",
      "scope": "workspace",
      "status": "skipped",
      "note": "Pass --workspace to remove."
    },
    {
      "path": "/home/user/.agent-plus/marketplaces/alice-agent-plus-skills",
      "kind": "marketplace_state",
      "scope": "marketplaces",
      "slug": "alice/agent-plus-skills",
      "status": "skipped",
      "note": "Pass --marketplaces to remove."
    },
    {
      "path": "/home/user/.claude/plugins/cache/github-remote@agent-plus",
      "kind": "claude_plugin",
      "scope": "out_of_scope",
      "status": "kept",
      "hint": "claude plugin uninstall github-remote@agent-plus"
    }
  ],
  "summary": {
    "removed": 5,
    "missing": 0,
    "skipped": 2,
    "kept": 3,
    "errors": 0
  },
  "claude_plugin_hints": [
    "claude plugin uninstall github-remote@agent-plus"
  ],
  "next_steps": [
    "Re-install: curl -fsSL https://raw.githubusercontent.com/osouthgate/agent-plus/main/install.sh | sh"
  ],
  "errors": []
}
```

## Enums

### `mode`

| Value          | Meaning                                                       |
|----------------|---------------------------------------------------------------|
| `default`      | No scope flags. 5 primitive bins only.                        |
| `workspace`    | `--workspace`. Adds `<repo>/.agent-plus/` and `~/.agent-plus/`. |
| `marketplaces` | `--marketplaces`. Adds marketplace state directories.         |
| `all`          | `--all` (or `--workspace --marketplaces`). Bins + workspace + marketplaces. |
| `purge`        | `--purge`. `all` + every other agent-plus state we own.       |

### `kind`

| Value               | Implemented | Notes                                     |
|---------------------|-------------|-------------------------------------------|
| `primitive_bin`     | yes         | One of the 5 framework bins.              |
| `workspace`         | yes         | `~/.agent-plus/` or `<repo>/.agent-plus/`.|
| `marketplace_state` | yes         | A marketplace registration directory.     |
| `marketplace_registry` | reserved | Reserved for v0.16+ registry-level state. |
| `claude_plugin`     | yes (list-only) | Out-of-scope. Hint surfaced; never deleted. |
| `claude_session`    | reserved    | Reserved. Sessions are user-owned; never touched. |
| `user_skill`        | reserved    | Reserved. User skills are never touched.  |
| `feedback_log`      | reserved    | Reserved for `~/.agent-plus/skill-feedback/`. |
| `analytics`         | reserved    | Reserved for `~/.agent-plus/analytics/`.  |
| `settings_hook`     | reserved    | v0.16+. Future Claude Code `SessionStart` / `UserPromptSubmit` hooks. |
| `daemon_pid`        | reserved    | v0.16+. Future long-running helper PID files. |
| `migration_state`   | reserved    | v0.16+. The `migrations/` history file.   |

### `scope`

| Value          | Meaning                                                |
|----------------|--------------------------------------------------------|
| `default`      | Removed under any scope.                               |
| `workspace`    | Removed only when `--workspace`/`--all`/`--purge` set. |
| `marketplaces` | Removed only when `--marketplaces`/`--all`/`--purge` set. |
| `purge`        | Removed only under `--purge`.                          |
| `out_of_scope` | Never touched; surfaced for transparency / hints.      |

### `status`

| Value          | Meaning                                                |
|----------------|--------------------------------------------------------|
| `removed`      | Existed; we deleted it this run.                       |
| `missing`      | Not there to begin with (idempotent re-run, or never installed). |
| `skipped`      | Out of this run's flag scope.                          |
| `kept`         | User-owned territory we deliberately don't touch.       |
| `error`        | Tried to remove; OS or permission issue. `error` field carries the cause. |
| `would_remove` | Pre-execution intermediate (visible in `--dry-run`).   |

## Compatibility rules

- Adding new fields to the envelope is **non-breaking**.
- Adding new enum values to `kind` is **non-breaking** (the schema reserves
  several slots ahead of implementation for this exact reason).
- Adding new enum values to `mode`, `scope`, or `status` is non-breaking
  but consumers should treat unknown values as fall-through.
- Renaming or removing **any** of `tool`, `action`, `mode`, `paths`,
  `summary`, `status`, `kind`, `scope`, `dry_run`, or `user_confirmed` is
  a **breaking change** requiring a major version bump.

## Stable error codes

Surfaced in `errors[].code`:

| Code                          | When                                              |
|-------------------------------|---------------------------------------------------|
| `uninstall_partial_failure`   | One or more removals failed. `recoverable: true`. |
