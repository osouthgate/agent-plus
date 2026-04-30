# agent-plus-meta

> Part of [**agent-plus**](../README.md) · siblings: [`repo-analyze`](../repo-analyze) · [`diff-summary`](../diff-summary) · [`skill-feedback`](../skill-feedback) · [`skill-plus`](../skill-plus)

Every fresh Claude Code session re-mines the same workspace facts: grep `.env`, `ls` plugin dirs, `git remote -v`, hand-roll `gh repo view`, paste a Vercel project ID, repeat. Session transcripts showed **~67 grep + ~60 ls operations** during cold-start context-gathering — most of them recovering facts the previous session also recovered.

`agent-plus-meta init` + `agent-plus-meta refresh` collapses that into two tool calls and writes the results to `.agent-plus/` so the next session reads them off disk. `list` covers the whole stack — github, vercel, supabase, railway, linear, langfuse — so one refresh resolves the infra surface. Stdlib-only Python 3, no SaaS, no SDK.

## Headline commands

```bash
agent-plus-meta init       [--dir PATH] [--pretty] [--non-interactive] [--auto]
agent-plus-meta envcheck   [--dir PATH] [--env-file PATH] [--pretty]
agent-plus-meta refresh    [--dir PATH] [--env-file PATH] [--plugin <name>]
                      [--no-extensions | --extensions-only] [--pretty]
agent-plus-meta list       [--dir PATH] [--names-only] [--pretty]
agent-plus-meta extensions list|validate|add|remove [--dir PATH] [--pretty]
agent-plus-meta marketplace init|search|prefer ...
agent-plus-meta upgrade-check [--force] [--snooze 24h|48h|7d|never] [--clear-snooze]
                              [--timeout SEC] [--no-telemetry] [--json]
agent-plus-meta upgrade    [--non-interactive] [--auto] [--user-choice yes|always|snooze|never]
                              [--rollback] [--dry-run] [--no-telemetry] [--json]
agent-plus-meta --version
```

All commands emit JSON wrapped in the standard `tool: {name, version}` envelope.

### `init`

Persona-aware onboarding wizard. Detects user state, picks one of three first-run branches, offers cross-repo session mining, and ends with a coherent doctor verdict. Also performs the legacy idempotent workspace bootstrap (`.agent-plus/manifest.json`, `services.json`, `env-status.json`) — re-running leaves existing files untouched and reports `skipped:[...]`.

#### Three branches

State detection inspects: presence of `~/.claude/projects/` history, presence of `<repo>/.claude/skills/`, count of plugins with all required env vars set, presence of `.agent-plus/manifest.json`, and whether cwd is "homeless" (no git toplevel, no project markers, at or above home). The wizard then picks one branch with a deterministic priority `skill_author > returning > new`:

- **NEW** — runs `repo-analyze <project>` as the first win.
- **RETURNING** — runs `agent-plus-meta doctor` first to confirm the install on a fresh machine.
- **SKILL-AUTHOR** — runs `skill-plus list --include-global` to surface project + global skills with collision flags.

#### Cross-repo discovery

After the first-win, the wizard walks `~/.claude/projects/`, decodes each subdir back to a repo path (handles both Windows `C--dev-foo` and POSIX `-Users-bob-foo` encodings), filters dead paths and entries older than 30 days, and offers the top 4 by recency. Selection prompt accepts comma-separated indices (`1,3`), `[a]ll`, `[n]one`, or `[m]anual` for paste-in. Manual paste validates each path exists; warns when no `.git/` and no project markers are present but accepts anyway. Each accepted repo is scanned via `skill-plus scan --all-projects --project <path>` with progress streamed per-repo.

> **Homeless context.** When cwd has no git toplevel and no project markers and is at or above your home dir, the NEW branch skips the local first-win (no `repo-analyze` against `~`) and the wizard pivots to cross-repo discovery first. If `~/.claude/projects/` is also empty, the wizard ends gracefully at the doctor step.

#### Doctor finale

Calls `cmd_doctor` in-process and renders pretty output inline. Wrapped in try/except — if doctor itself raises, the wizard prints a fallback hint (`Run agent-plus-meta doctor manually`) and continues to envelope emission. Never crashes the wizard.

#### `--non-interactive --auto` mode

Designed for agent harnesses (e.g. Claude Code's Bash tool calling the wizard on the user's behalf). Skips all prompts, picks the branch deterministically, runs the first-win, scans every auto-discovered cross-repo path silently (no manual paste), and emits the frozen JSON envelope on stdout. Exits 0 even on recoverable errors so the calling agent can parse the envelope rather than diff stderr.

```bash
agent-plus-meta init --non-interactive --auto
```

v0.13.0 ships the `agent-plus-installer` skill that packages the install + auto-init flow for Claude Code; see [skills/agent-plus-installer/SKILL.md](./skills/agent-plus-installer/SKILL.md).

#### JSON envelope (frozen v0.12.0 public contract)

> Note on `tool.version`: the schema below is **frozen at v0.12.0** — field
> names, types, and ordering will not change without a major bump. The
> `tool.version` field, however, reflects whichever build is running
> (`0.15.1` today). The literal `"0.12.0"` in the examples is illustrative;
> at runtime you'll see the current plugin version.

```json
{
  "tool": {"name": "agent-plus-meta", "version": "0.12.0"},
  "verdict": "success" | "warn" | "error",
  "branch_chosen": "new" | "returning" | "skill_author",
  "tie_break_reason": null | "string",
  "detection": {
    "has_claude_projects_history": bool,
    "has_skills": bool,
    "env_vars_ready_count": int,
    "agent_plus_already_init": bool,
    "homeless": bool
  },
  "cross_repo_offered": ["/abs/path", ...],
  "cross_repo_accepted": ["/abs/path", ...],
  "cross_repo_results": [
    {"path": "...", "candidates_found": int, "status": "ok" | "skipped" | "failed", "reason": "..."}
  ],
  "doctor_verdict": "healthy" | "degraded" | "broken",
  "doctor_summary": {
    "primitives_installed": int,
    "primitives_total": int,
    "envcheck_ready": int,
    "envcheck_total": int,
    "marketplaces_installed": int,
    "stale_services_count": int
  },
  "first_win_command": "string" | null,
  "first_win_result": "ok" | "failed" | "skipped",
  "ttl_total_ms": int,
  "errors": [
    {"code": "string", "message": "...", "hint": "...", "recoverable": bool}
  ]
}
```

Frozen for v0.12.0; additive changes may land in v0.13.x+ without breaking; renames or removals require a major bump.

The legacy fields (`workspace`, `source`, `created`, `skipped`, `suggested_skills`) are preserved at the top level for back-compat with pre-v0.12.0 callers.

#### Stable error codes

| Code | When it fires | Recoverable | Hint |
|---|---|---|---|
| `consent_required` | An action requires explicit user consent that wasn't given | yes | re-run interactively |
| `cross_repo_scan_failed` | A per-repo `skill-plus scan` returned non-zero or errored | yes | re-run, or scan manually with `skill-plus` |
| `cross_repo_interrupted` | User Ctrl+C during cross-repo selection or loop | yes | re-run; completed scans are preserved |
| `stack_detect_unreadable_marker` | A stack-detect marker file is present but unreadable | yes | suggestion is silently skipped |
| `doctor_unreachable` | The doctor finale call raised | yes | run `agent-plus-meta doctor` manually |
| `skill_plus_missing` | `skill-plus` not on PATH for first-win or cross-repo scan | yes | `claude plugin install skill-plus@agent-plus` |
| `auto_tie_break` | `--auto` resolved an ambiguous state via deterministic tie-break | yes | inspect `tie_break_reason` in envelope |
| `install_sh_curl_failed` | (install.sh) failed to download a plugin | yes | re-run install.sh, check network |

Interactive runs print errors as Tier-1 lines (`<problem> - <cause> - <fix>`) on stderr. `--auto` runs return the same errors as structured entries in `envelope.errors[]`.

#### Observability

Each wizard run appends one JSON line to `<workspace>/.agent-plus/init.log`:

```json
{"ts": "...", "branch_chosen": "skill_author", "detection": {...}, "cross_repo_accepted": ["..."], "doctor_verdict": "healthy"}
```

Useful for debugging "why did init pick this branch" or "did the user accept cross-repo this time".

```bash
$ agent-plus-meta init --pretty
{
  "tool": {"name": "agent-plus-meta", "version": "0.12.0"},
  "workspace": "/path/to/repo/.agent-plus",
  "source": "git",
  "created": ["manifest.json", "services.json", "env-status.json"],
  "skipped": [],
  "verdict": "success",
  "branch_chosen": "new",
  "detection": {"has_claude_projects_history": false, "has_skills": false, ...},
  "doctor_verdict": "healthy",
  ...
}
```

### `envcheck`

Walks every known plugin's required env-var prefixes. Reports which are set, which are missing, per-plugin readiness. **Names only — values never land on disk** (canary-tested). Result is also written to `.agent-plus/env-status.json`.

```bash
$ agent-plus-meta envcheck --pretty
{
  "tool": {"name": "agent-plus-meta", "version": "0.11.0"},
  "workspace": "/path/to/repo/.agent-plus",
  "source": "git",
  "checked": ["COOLIFY_API_KEY", "COOLIFY_URL", "GITHUB_TOKEN", ...],
  "set": ["GITHUB_TOKEN", "VERCEL_TOKEN"],
  "missing": ["LINEAR_API_KEY", "SUPABASE_ACCESS_TOKEN", ...],
  "missing_optional": ["GITHUB_REPO", ...],
  "plugins": {
    "github-remote": {"set": ["GITHUB_TOKEN"], "missing_required": [], "ready": true, ...},
    "vercel-remote": {"set": ["VERCEL_TOKEN"], "missing_required": [], "ready": true, ...},
    "linear-remote": {"set": [], "missing_required": ["LINEAR_API_KEY"], "ready": false, ...},
    ...
  },
  "checkedAt": "2026-04-27T12:00:00Z",
  "written_to": "/path/to/repo/.agent-plus/env-status.json"
}
```

### `refresh`

Resolves project / repo identity for the lightest-cost endpoints in **github-remote, vercel-remote, supabase-remote, railway-ops, linear-remote, langfuse-remote** plus any registered extensions. Caches the result into `.agent-plus/services.json`. Each handler hits one read-only endpoint and keeps NAMES + IDs + URLs only — tokens never appear in stdout, stderr, or services.json (canary-tested).

```bash
$ agent-plus-meta refresh --pretty
{
  "tool": {"name": "agent-plus-meta", "version": "0.11.0"},
  "services": {
    "github-remote": {
      "plugin": "github-remote", "owner": "osouthgate", "repo": "agent-plus",
      "status": "ok", "default_branch": "main", "repo_id": 123456789
    },
    "vercel-remote": {
      "plugin": "vercel-remote", "status": "ok",
      "projects": [{"name": "my-app", "id": "prj_..."}], "count": 1
    }
  },
  "refreshedAt": "2026-04-27T12:00:00Z",
  "written_to": "/path/to/repo/.agent-plus/services.json"
}
```

### `list`

One call returns every plugin in `.claude-plugin/marketplace.json` plus a 400-char headline-commands preview pulled from each plugin's README. Cross-references `env-status.json` (when present) to surface a `ready` flag per plugin.

```bash
$ agent-plus-meta list --pretty
{
  "tool": {"name": "agent-plus-meta", "version": "0.11.0"},
  "plugins": [
    {
      "name": "github-remote",
      "description": "Read-first GitHub REST API wrapper — ...",
      "headline_commands": "## Headline commands\n\n```bash\ngithub-remote pr ...",
      "source": "./github-remote",
      "ready": true
    },
    ...
  ],
  "count": 12
}

$ agent-plus-meta list --names-only
{"tool": {...}, "plugins": ["agent-plus", "hermes-remote", ...], "count": 12}
```

### `marketplace init`

Scaffold a `<user>/agent-plus-skills` marketplace repo. The `name` portion of the slug **must** be `agent-plus-skills` for v1 — fixed convention so `gh search repos topic:agent-plus-skills` is unambiguous. Default target dir is `<cwd>/<name>/`; override with `--path`. Refuses to scaffold into a non-empty directory. Never runs `gh` itself — prints suggested invocations only.

```bash
$ agent-plus-meta marketplace init osouthgate/agent-plus-skills --pretty
{
  "tool": {"name": "agent-plus-meta", "version": "0.11.0"},
  "marketplace": {
    "path": "/path/to/agent-plus-skills",
    "owner": "osouthgate", "name": "agent-plus-skills",
    "files_written": ["marketplace.json", "README.md", "LICENSE", ".gitignore", "CHANGELOG.md"],
    "git_initialized": true,
    "next_steps": [
      "gh repo create osouthgate/agent-plus-skills --public --source . --remote origin",
      "gh repo edit osouthgate/agent-plus-skills --add-topic agent-plus-skills",
      "gh repo edit osouthgate/agent-plus-skills --add-topic claude-code"
    ]
  }
}
```

`init` also pins `core.autocrlf=false` on the new repo's local config (best-effort) so Windows checkouts don't CRLF-mangle skill-bin / JSON-manifest writes.

### `marketplace search [query]`

Discover marketplaces published under the `agent-plus-skills` topic on GitHub. Shells to `gh search repos --topic agent-plus-skills --json ... --limit 30`, ranks by `stars + recency_boost (max(0, 30 - days_since_update) * 2)` so a freshly-updated 5-star repo can outrank a stale 30-star one.

```bash
$ agent-plus-meta marketplace search database --pretty
{
  "tool": {"name": "agent-plus-meta", "version": "0.11.0"},
  "ok": true, "query": "database",
  "results": [
    {"slug": "alice/agent-plus-skills", "name": "agent-plus-skills", "owner": "alice",
     "description": "Postgres + ClickHouse skills", "stars": 12, "updatedAt": "2026-04-22T...",
     "url": "https://github.com/alice/agent-plus-skills", "score": 28.0}
  ]
}
```

Refuses cleanly when `gh` isn't on PATH (`error: gh_not_installed`). Translates timeouts and non-zero exits into envelope errors. User query is never interpolated into a shell string — list-form `subprocess.run` only.

### `marketplace prefer <user>/<repo> --skill <name>`

Per-skill collision resolution. When two installed marketplaces ship a skill of the same name, this records which marketplace wins. Recorded atomically in `~/.agent-plus/preferences.json`. `agent-plus-meta refresh` consults the preference on collisions and surfaces a `collisions: [{skill, candidates, chosen, reason: "first_wins" | "preference"}]` slot in the envelope. Without a preference, behaviour is deterministic first-wins (sorted iteration).

```bash
agent-plus-meta marketplace prefer alice/agent-plus-skills --skill repo-analyze
agent-plus-meta marketplace prefer --list --pretty
agent-plus-meta marketplace prefer --clear --skill repo-analyze
```

### `upgrade-check`

Cached probe — answers "is there a newer agent-plus available?" without ever blocking a workflow. Reads the single-root [`VERSION`](../VERSION) file at `raw.githubusercontent.com/osouthgate/agent-plus/main/VERSION` (tag-bound, NOT derived from any plugin's `plugin.json`).

```bash
agent-plus-meta upgrade-check                  # default: read cache, probe network on miss
agent-plus-meta upgrade-check --force          # bypass cache, always probe
agent-plus-meta upgrade-check --snooze 24h     # advance the ladder: 24h → 48h → 7d → never
agent-plus-meta upgrade-check --clear-snooze   # reset the snooze ladder to none
agent-plus-meta upgrade-check --timeout 5      # network timeout in seconds (default 3, max 10)
```

Cache lives at `~/.agent-plus/upgrade/cache.json` — TTL 60min for `up_to_date`, 720min for `upgrade_available`. Snooze state at `~/.agent-plus/upgrade/snooze.json`. Both files reset automatically when a new latest version is detected.

The probe is best-effort: any network failure (timeout, DNS, HTTP non-2xx, malformed body) degrades to verdict `unknown` and the workflow proceeds. Next probe in ~60min retries.

#### `upgrade-check` envelope (frozen at v0.13.5)

```json
{
  "tool": {"name": "agent-plus-meta", "version": "0.13.5"},
  "verdict": "up_to_date" | "upgrade_available" | "just_upgraded" | "unknown",
  "current_version": "0.13.5",
  "latest_version": "0.13.5" | null,
  "version_source": "root_VERSION_file",
  "cache":   {"hit": true, "age_sec": 0, "ttl_sec": 3600},
  "snooze":  {"active": false, "expires_ts": null, "ladder_step": "none"},
  "config":  {"update_check": true, "silent_upgrade": false},
  "network": {"attempted": false, "ok": false, "elapsed_ms": 0, "error": null},
  "ttl_total_ms": 3,
  "errors": []
}
```

Frozen for v0.13.5. Additive enum widening (e.g., new `verdict` values) is non-breaking; field renames or removals require a major bump.

### `upgrade`

The action. Detects how agent-plus is installed (`global` for `~/.local/bin` or `$AGENT_PLUS_INSTALL_DIR`; `git_local` for clones), takes a per-bin `.bak` snapshot at `~/.agent-plus/.bak/<UTC-timestamp>/<bin>.bak`, downloads each of the 5 framework primitives from GitHub raw, atomically replaces them, runs any pending migrations, and gates on a post-test `cmd_doctor()` call. Verdict `broken` triggers automatic rollback from the `.bak` set.

```bash
agent-plus-meta upgrade                        # interactive 4-option prompt
agent-plus-meta upgrade --non-interactive --auto       # pick the recommended option silently
agent-plus-meta upgrade --user-choice yes              # explicit choice (also: always | snooze | never)
agent-plus-meta upgrade --rollback                     # restore the most recent .bak set; no upgrade
agent-plus-meta upgrade --dry-run                      # show what would happen, change nothing
```

#### 4-option prompt (interactive)

The same shape as gstack's upgrade prompt:

| Choice  | Action |
|---------|--------|
| **Yes**     | Apply this upgrade now. |
| **Always**  | Sets `silent_upgrade: true` and applies. Future patch bumps land silently; minor/major still prompts. |
| **Snooze**  | Advances the ladder (24h → 48h → 7d → never), no upgrade. |
| **Never**   | Sets `update_check: false`, no upgrade. Re-enable with `agent-plus-meta upgrade-check --clear-snooze` plus a config edit. |

#### `silent_upgrade` config vs `--auto` CLI flag

These are two distinct concepts (the names look similar; they aren't):

- **`silent_upgrade: bool`** in `~/.agent-plus/config.json` (default `false`) means *"skip the upgrade prompt entirely on patch bumps."* Hardcoded patch-only in v0.13.5 — minor and major bumps always prompt regardless. Set by choosing **Always** in the prompt, or by hand-editing `config.json`.
- **`--auto`** CLI flag means *"non-interactive, pick the recommended option."* Used by automation (`install.sh --upgrade --auto`, `agent-plus-installer` skill). Under `--auto`, the recommended pick is **Yes** by default — or **Always** when `silent_upgrade=true` AND the bump is a patch (per T5).

#### Migrations

Each migration module under `agent-plus-meta/migrations/v*.py` exposes:

```python
from pathlib import Path
def migrate(workspace: Path) -> dict:
    return {"status": "ok" | "skipped" | "failed", "message": "...", "changes": [...]}
```

Idempotent. History persists at `~/.agent-plus/migrations.json` keyed by id (filename stem, e.g. `v0_13_5`). Empty on day one — see [migrations/README.md](./migrations/README.md) for the full contract.

#### `upgrade` envelope (frozen at v0.13.5)

```json
{
  "tool": {"name": "agent-plus-meta", "version": "0.13.5"},
  "verdict": "success" | "noop" | "warn" | "error" | "rolled_back",
  "from_version": "0.13.5",
  "to_version": "0.13.5",
  "install_type_detected": "global" | "git_local" | "unknown",
  "bins_replaced": [
    {"name": "agent-plus-meta", "from": "0.13.5", "to": "0.13.5",
     "status": "ok" | "skipped" | "failed",
     "backup_path": "/abs/path/to/.bak"}
  ],
  "migrations_applied": [
    {"id": "v0_13_5", "status": "ok" | "skipped_already_applied" | "failed",
     "duration_ms": 12}
  ],
  "post_test": {"doctor_verdict": "healthy" | "degraded" | "broken",
                "rollback_triggered": false},
  "user_choice": "yes" | "always" | "snooze" | "never" | "auto",
  "ttl_total_ms": 142,
  "errors": []
}
```

#### Stable error codes (v0.13.5)

Four codes, mirroring v0.12.0's pattern:

1. `upgrade_check_network_failed` — curl/DNS/HTTP failure on the probe. Recoverable; next probe retries.
2. `upgrade_partial_failure` — one or more primitives failed to replace. Auto-rollback fires.
3. `upgrade_migration_failed` — a migration script raised. Rollback triggered.
4. `upgrade_rollback_required` — post-upgrade doctor returned `broken`. Bins restored from `.bak`.

User-declined runs are NOT errors — verdict `noop` plus `user_choice: "snooze" | "never"` carries that signal. Telemetry derives the declined-rate from `user_choice` distribution.

### `uninstall`

Safe-by-default uninstall. Default scope removes ONLY the 5 framework primitive bins. Workspace, marketplaces, plugins, sessions, and skills are KEPT and listed with hints. Opt-in flags escalate scope explicitly.

```bash
agent-plus-meta uninstall                 # 5 bins only — interactive y/N
agent-plus-meta uninstall --workspace     # also remove .agent-plus/ workspaces
agent-plus-meta uninstall --marketplaces  # also unregister marketplace state
agent-plus-meta uninstall --all           # bins + workspace + marketplaces
agent-plus-meta uninstall --purge         # all + everything we own; ALWAYS prompts 'PURGE'
agent-plus-meta uninstall --dry-run       # preview manifest, remove nothing
agent-plus-meta uninstall --json          # JSON envelope only (script mode)
```

The `install.sh --uninstall` shim delegates to this command when `agent-plus-meta` is reachable. When it's not (broken/partial install), `install.sh` falls back to a self-contained POSIX shell path that removes the 5 bins only — expanded scopes are refused with exit 3 in fallback mode.

#### Flag matrix

| Flag                 | Adds to default                                            |
|----------------------|------------------------------------------------------------|
| (none)               | — 5 bins only.                                             |
| `--workspace`        | `<repo>/.agent-plus/` AND `~/.agent-plus/`                  |
| `--marketplaces`     | `~/.agent-plus/marketplaces/<owner>-<name>/` registrations  |
| `--all`              | bins + workspace + marketplaces (does NOT include `--purge`) |
| `--purge`            | `--all` PLUS any other agent-plus state we own. ALWAYS prompts `PURGE`. |
| `--dry-run`          | preview only; remove nothing                                |
| `--non-interactive`  | skip y/N prompt; does NOT bypass `--purge` confirmation     |
| `--json`             | suppress human preview; emit JSON envelope on stdout        |
| `--install-dir PATH` | override `$INSTALL_DIR` (defaults to `AGENT_PLUS_INSTALL_DIR` or `~/.local/bin`) |

#### What the uninstall NEVER touches

- `~/.claude/projects/` — Claude Code session history (user-owned).
- `~/.claude/skills/` — your authored skills (user-owned).
- `<repo>/.claude/skills/` — per-repo authored skills (user-owned).
- `~/.claude/plugins/cache/` — Claude Code plugin cache. We list `@agent-plus`-tagged entries with `claude plugin uninstall <name>@agent-plus` hints, but we never delete from this path. Claude Code owns it.

#### `uninstall` envelope (frozen at v0.15.0)

Public contract; full schema reference in [docs/uninstall-envelope.md](./docs/uninstall-envelope.md).

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
    {"path": "...", "kind": "primitive_bin", "scope": "default", "status": "removed"},
    {"path": "...", "kind": "workspace", "scope": "workspace", "status": "skipped",
     "note": "Pass --workspace to remove."},
    {"path": "...", "kind": "marketplace_state", "scope": "marketplaces",
     "slug": "alice/agent-plus-skills", "status": "skipped"},
    {"path": "...", "kind": "claude_plugin", "scope": "out_of_scope", "status": "kept",
     "hint": "claude plugin uninstall github-remote@agent-plus"}
  ],
  "summary": {"removed": 5, "missing": 0, "skipped": 2, "kept": 3, "errors": 0},
  "claude_plugin_hints": ["claude plugin uninstall github-remote@agent-plus"],
  "next_steps": ["Re-install: curl -fsSL .../install.sh | sh"],
  "errors": []
}
```

The `kind` enum reserves slots for future additive use (`settings_hook`, `daemon_pid`, `migration_state`) so v0.16+ can extend without breaking the contract. Adding fields is non-breaking; renaming or removing `tool/action/mode/paths/summary/status/kind/scope` requires a major bump.

#### Stable error codes (v0.15.0)

`uninstall_partial_failure` — one or more removals failed (permission, locked file). Recoverable; check filesystem permissions and re-run.

#### `--purge` is the one-way door

Every other mode is recoverable via re-install. `--purge` removes user data we own (`.agent-plus/` workspace, feedback logs, marketplace state). It always prompts for the literal word `PURGE` — even under `--non-interactive`. Typing anything else aborts. The friction is intentional. PATH cleanup is NOT performed; if you added `~/.local/bin` to your shell rc, removing it is up to you.

## What it doesn't do

- **`refresh` is data-driven.** Each wrapper declares a `refresh_handler` block in its `plugin.json`; plugins without one are silently skipped. Extensions add your own.
- **No SessionStart hook.** Bootstrap is on the agent / user; `init` is idempotent so it's cheap to run on every session start.
- **No edit / generic file ops.** `.agent-plus/*.json` is plain JSON. Use `jq` or an editor.
- **No secret values anywhere.** Names only; values stay in your shell / `.env`. Verified by canary test.

## Extensions

Plug a custom data source into `refresh` without modifying the meta plugin: drop a script into `.agent-plus/extensions.json` and `agent-plus-meta refresh` aggregates it the same way it aggregates the built-ins — same envelope, same `services.<name>` slot.

```bash
agent-plus-meta extensions list      # see what's wired
agent-plus-meta extensions validate  # dry-run
agent-plus-meta extensions add --name X --command python3 --command-arg=script.py
agent-plus-meta extensions remove --name X
```

Authoring contract + env-pass-through rules + examples: [`docs/extensions.md`](./docs/extensions.md).

## Resolution

Workspace dir, layered `.env` precedence, and per-plugin env-var spec: see [`docs/resolution.md`](./docs/resolution.md). Defaults work for the common case; only read when you hit a precedence question.

## Install

### Marketplace

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install agent-plus-meta@agent-plus
```

### Session-only

```bash
claude --plugin-dir ./agent-plus
```

### Standalone

`bin/agent-plus-meta` is one stdlib Python 3 file. Copy to `$PATH`, run.

## Tests

```bash
python3 -m pytest agent-plus-meta/test/ -v
```

## License

MIT, inherits the root [LICENSE](../LICENSE).
