# agent-plus-meta

> Part of [**agent-plus**](../README.md) · siblings: [`repo-analyze`](../repo-analyze) · [`diff-summary`](../diff-summary) · [`skill-feedback`](../skill-feedback) · [`skill-plus`](../skill-plus)
>
> The framework's meta plugin. Renamed from `agent-plus` → `agent-plus-meta` in 0.11.0 to resolve the naming collision with the framework itself. CLI commands changed from `agent-plus init/refresh/...` to `agent-plus-meta init/refresh/...`.

Every fresh Claude Code session re-mines the same workspace facts: grep `.env`, `ls` plugin dirs, `git remote -v`, hand-roll `gh repo view`, paste a Vercel project ID, repeat. Session transcripts showed **~67 grep + ~60 ls operations** during cold-start context-gathering — most of them recovering facts the previous session also recovered.

`agent-plus-meta init` + `agent-plus-meta refresh` collapses that into two tool calls and writes the results to `.agent-plus/` so the next session reads them off disk. `list` covers the whole stack — github, vercel, supabase, railway, linear, langfuse — so one refresh resolves the infra surface. Stdlib-only Python 3, no SaaS, no SDK.

## Headline commands

```bash
agent-plus-meta init       [--dir PATH] [--pretty]
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

Creates `.agent-plus/` with three empty-but-valid JSON files. Idempotent — re-running leaves existing files untouched and reports `skipped:[...]`.

```bash
$ agent-plus-meta init --pretty
{
  "tool": {"name": "agent-plus-meta", "version": "0.11.0"},
  "workspace": "/path/to/repo/.agent-plus",
  "source": "git",
  "created": ["manifest.json", "services.json", "env-status.json"],
  "skipped": []
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

## Resolution

**Workspace dir** (highest first; identical to `skill-feedback` so both plugins share one `.agent-plus/`):

1. `--dir PATH` (CLI flag)
2. `<git-toplevel>/.agent-plus/` (cwd is in a git repo)
3. `<cwd>/.agent-plus/` (cwd contains an existing `.agent-plus/`)
4. `~/.agent-plus/` (last-resort fallback for read paths; `init` defaults to cwd in non-git projects)

The `source` field on every payload tells you which rule fired.

**Env (.env autoload)**, highest precedence first:

1. `--env-file <path>`
2. project `.env.local` / `.env` (walked up from cwd)
3. shell env

Each plugin's required env-vars are checked by canonical prefix (`HERMES_*`, `COOLIFY_*`, `LANGFUSE_*`, …) — spec hardcoded in `bin/agent-plus#PLUGIN_ENV_SPEC` to keep drift visible.

## Extensions

Without this, every workspace is locked to the 6 plugins that ship with `refresh`. With it, you drop a script into `extensions.json` and `agent-plus-meta refresh` aggregates your own data the same way it aggregates the built-ins — same envelope, same `services.<name>` slot.

### The contract

Each extension's stdout MUST be a single JSON object:

```json
{
  "status": "ok" | "unconfigured" | "partial" | "error",
  "...": "any other fields you want, passed through verbatim"
}
```

The orchestrator wraps script output as `{plugin: "<name>", source: "extension", ...your fields...}` and merges into `services.<name>`. Non-JSON output, JSON arrays, non-zero exit, and timeouts all become `{status: "error", reason: "...", stderr_tail: "<last 500 chars>"}`. Scripts run with `cwd=<repo root>` and inherit the host env (so `gh`, `vercel`, etc. work). The orchestrator never echoes env values into output.

### Worked example

```python
#!/usr/bin/env python3
# .agent-plus/scripts/refresh-releases.py
import json, subprocess
proc = subprocess.run(
    ["gh", "api", "repos/osouthgate/agent-plus/releases", "--paginate=false"],
    capture_output=True, text=True, timeout=10,
)
if proc.returncode != 0:
    print(json.dumps({"status": "error", "reason": proc.stderr[:200]}))
else:
    releases = json.loads(proc.stdout)[:5]
    print(json.dumps({
        "status": "ok",
        "latest": [{"tag": r["tag_name"], "name": r["name"]} for r in releases],
        "count": len(releases),
    }))
```

Register it once:

```bash
agent-plus-meta extensions add --name releases \
    --command python3 \
    --command-arg=.agent-plus/scripts/refresh-releases.py \
    --description "GitHub releases summary" \
    --timeout 15
```

Every `agent-plus-meta refresh` then populates `services.releases` alongside the built-ins.

### Managing extensions

```bash
agent-plus-meta extensions list                       # show registered + on-disk check
agent-plus-meta extensions validate                   # dry-run validate (no script execution)
agent-plus-meta extensions add --name X --command Y   # append (atomic; rejects collisions)
agent-plus-meta extensions remove --name X            # remove (atomic; also drops services.<name>)

agent-plus-meta refresh --no-extensions               # skip extensions
agent-plus-meta refresh --extensions-only             # run only extensions
```

`extensions list` and `agent-plus-meta list` surface `command_hash` (sha256 of argv[0]) rather than the command itself — paths often contain usernames. Disabled extensions (`"enabled": false`) load but skip at refresh time. Names colliding with built-in plugin names are rejected at add/load time. `extensions remove` returns `services_cleaned: bool` so callers can confirm stale handler output didn't linger.

## What it doesn't do

- **`refresh` is data-driven.** Each wrapper declares a `refresh_handler` block in its `plugin.json`; plugins without one are silently skipped. Extensions add your own.
- **No SessionStart hook.** Bootstrap is on the agent / user; `init` is idempotent so it's cheap to run on every session start.
- **No edit / generic file ops.** `.agent-plus/*.json` is plain JSON. Use `jq` or an editor.
- **No secret values anywhere.** Names only; values stay in your shell / `.env`. Verified by canary test.

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

`bin/agent-plus` is one stdlib Python 3 file. Copy to `$PATH`, run.

## Tests

```bash
python3 -m pytest agent-plus/test/ -v
```

## License

MIT, inherits the root [LICENSE](../LICENSE).
