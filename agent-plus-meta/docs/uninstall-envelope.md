# `agent-plus-meta uninstall` — JSON envelope schema

**Stability:** frozen public contract as of v0.15.0. The envelope *shape*
is unchanged since then; the prompt/TTY gating semantics (which runs
prompt, what `interactive` records, when `--purge` is refused) were
tightened in v0.20.x -- see "Script mode (`--json`), prompts, and TTY
semantics" below.

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

**Note (2026-07, additive, non-breaking):** the envelope also carries a `nextSteps` (camelCase) field -- the cross-plugin funnel-chain contract shared by every agent-plus-meta subcommand (see `README.md`'s "nextSteps[] chaining"). It is distinct from this doc's `next_steps` (snake_case, uninstall-specific reinstall pointer) and follows the `"<runnable command> -- <why>"` shape. Because a real (non-dry-run, confirmed) removal deletes this bin along with the rest of the default tier, `nextSteps` deliberately does NOT chain into another `agent-plus-meta` command in that case -- it surfaces an outstanding `claude plugin uninstall ...` hint if one exists, else a `git status` sanity check. A dry-run or a declined confirmation (nothing actually removed yet) still points back at `agent-plus-meta uninstall`.

## Script mode (`--json`), prompts, and TTY semantics

**The one-line rule: `--json` implies non-interactive for the scope
confirmation; purge always requires a real TTY.**

Since v0.20.x:

- `--json` is accepted **both before and after the subcommand**
  (`agent-plus-meta --json uninstall` == `agent-plus-meta uninstall
  --json`). Both spellings force the JSON envelope onto stdout even when
  stdout is an interactive terminal. (Previously only the top-level
  position worked; the flag after the subcommand was parsed into a dest
  the dispatcher never read, so the documented script-mode invocation
  silently behaved as if the flag were absent.)
- The y/N **scope confirmation** prompts only when ALL of these hold: no
  `--non-interactive`/`--auto`, no `--json`, and stdin is a TTY. When the
  prompt is skipped for any of those reasons, the run proceeds with
  exactly the auto-confirmed semantics `--non-interactive` has always had
  (the explicit flag set / script context IS the confirmation). Scripts
  that used to pipe `y`/`n` into the prompt must select scope with flags
  instead: a piped `n` no longer declines, because non-TTY runs never
  read the pipe.
- The envelope's **`interactive` field records the actual gating**: `true`
  iff the y/N prompt could fire this run (interactive terminal, no
  suppressing flags). It is no longer a restatement of the flag set.
- **`--purge` requires a real interactive terminal.** When `--purge` is
  combined with `--json`, `--non-interactive`/`--auto`, or a non-TTY
  stdin, the run is refused up front: exit code 1, nothing removed, no
  uninstall envelope on stdout; a structured error envelope prints to
  stderr instead (the standard host error shape):

  ```json
  {"tool": {"name": "agent-plus-meta", "version": "..."},
   "error": "purge requires an interactive terminal; run without --purge or confirm at a TTY",
   "problem": "...", "cause": "...", "fix": "...", "cmd": "uninstall"}
  ```

  This is enforcement of the existing documented principle ("`--auto`
  does NOT bypass the purge confirmation"), not a new door: previously
  the PURGE prompt fired anyway, which hung forever on a held-open
  non-TTY stdin and produced a prompt nobody could answer at EOF.
- **`--dry-run --purge` remains allowed in every mode** (piped, `--json`,
  `--non-interactive`): a dry-run never prompts and never removes, so
  scripts can still preview the purge manifest.

## Enums

### `mode`

| Value          | Meaning                                                       |
|----------------|---------------------------------------------------------------|
| `default`      | No scope flags. 5 primitive bins only.                        |
| `workspace`    | `--workspace`. Adds `<repo>/.agent-plus/` and `~/.agent-plus/`. |
| `marketplaces` | `--marketplaces`. Adds marketplace state directories.         |
| `all`          | `--all` (or `--workspace --marketplaces`). Bins + workspace + marketplaces. |
| `purge`        | `--purge`. `all` + every other agent-plus state we own. Requires a real TTY (see "Script mode" above). |

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

The `--purge` non-TTY refusal (v0.20.x) is deliberately NOT an
`errors[].code` entry: no uninstall envelope is produced at all in that
case. It surfaces as the process-level structured error envelope on
stderr (`error`/`problem`/`cause`/`fix`/`cmd`) with exit code 1 -- the
same refusal shape `init --dir <unwritable>` uses.
