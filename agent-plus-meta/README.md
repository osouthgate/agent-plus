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

A future v0.13.0 `agent-plus-installer` skill will package the install + auto-init flow for Claude Code with a SKILL.md trigger spec.

#### JSON envelope (frozen v0.12.0 public contract)

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
