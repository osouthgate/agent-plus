---
name: agent-plus
description: Workspace bootstrap for the agent-plus plugin collection. Creates `.agent-plus/` (one shared dir with skill-feedback), reports which sibling-plugin env vars are set (names only), and caches resolved project IDs / service handles into `services.json` so subsequent calls don't re-discover them. Use at session start, when env config changes, or when an agent asks "is X configured here?".
when_to_use: Trigger at session start to bootstrap context. Trigger when the user asks "is hermes configured here?", "what env vars do I need?", "what github repo is this?", "what vercel projects do I have?", "what supabase projects can I see?", "what railway projects am I in?", "what linear teams am I on?", "set up agent-plus", "init agent-plus", or after the user edits `.env`. Trigger `agent-plus list` when the user asks "what plugins are installed", "show me agent-plus tools", "what can agent-plus do", "which plugin should I use for X", or any tool-discovery question — it returns marketplace.json + a per-plugin headline-commands preview in one call so you don't have to open every README. Trigger `agent-plus extensions` when the user asks "add an agent-plus extension", "register a custom refresh script", "list my extensions", "validate extensions config", or wants to plug a custom data source into `refresh` without modifying the meta plugin. Skip for actual plugin operations — once `agent-plus refresh` has cached identity, switch to the per-plugin CLI (`github-remote overview`, `vercel-remote overview`, `supabase-remote sql`, `railway-ops overview`, `linear-remote issues`, `langfuse-remote health`, etc.) for real work.
allowed-tools: Bash(agent-plus:*)
---

# agent-plus

The meta plugin. Three subcommands, JSON output only. Use at session start to give the agent day-one context — installed plugin set, env-var readiness, resolved project IDs — without grep-mining the project for every fact.

The binary lives at `${CLAUDE_SKILL_DIR}/../../bin/agent-plus`; the plugin loader auto-adds `bin/` to PATH, so call it as `agent-plus`.

## When to reach for this

**At session start, run `agent-plus envcheck` once.** It tells you which plugins are configured in this workspace without you having to read every `.env` and README. If the user asks for a service you can see in `set:` use the corresponding plugin; otherwise, surface what's missing.

After running `agent-plus refresh` once per session, treat `services.json` as the single source of truth for project IDs and service handles. Don't re-resolve.

```bash
# Session-start bootstrap (run once)
agent-plus init                      # idempotent; creates .agent-plus/ if missing
agent-plus envcheck                  # which plugin env vars are set?
agent-plus refresh                   # resolves github + vercel identity into services.json

# "Is X configured?" questions
agent-plus envcheck --pretty | jq '.plugins["hermes-remote"].ready'
```

## Headline commands

```bash
agent-plus init       [--dir PATH] [--pretty]
agent-plus envcheck   [--dir PATH] [--env-file PATH] [--pretty]
agent-plus refresh    [--dir PATH] [--env-file PATH] [--plugin <name>]
                      [--no-extensions | --extensions-only] [--pretty]
agent-plus list       [--dir PATH] [--names-only] [--pretty]
agent-plus extensions list|validate|add|remove [--dir PATH] [--pretty]
agent-plus --version
```

All commands emit JSON wrapped in the standard `tool: {name, version}` envelope (pattern #6).

## Workspace dir resolution

Resolution order (highest precedence first) — **identical to skill-feedback so both plugins share one `.agent-plus/`**:

1. `--dir PATH` (CLI flag)
2. `<git-toplevel>/.agent-plus/` (cwd is in a git repo)
3. `<cwd>/.agent-plus/` (cwd contains an existing `.agent-plus/`)
4. `~/.agent-plus/` (last-resort fallback for read paths)

Run `agent-plus envcheck` and look at the `source` field to see which rule fired.

## What lands on disk

`.agent-plus/` after `init`:

```
.agent-plus/
├── manifest.json      # plugins list (populated by future refresh)
├── services.json      # resolved IDs/names from refresh (NAMES + IDs only)
└── env-status.json    # last envcheck result (NAMES only, no values)
```

**Pattern 5 — strip values.** Env-var values never appear in stdout, never on disk. Only the variable NAMES (e.g. `GITHUB_TOKEN`, `VERCEL_TOKEN`). Same contract as `railway-ops`.

## Extensions (user-defined refresh handlers)

`agent-plus refresh` runs both built-in handlers AND any user extensions registered in `<workspace>/extensions.json` by default. The contract: each extension's stdout must be a single JSON object with a `status` field (`ok` | `unconfigured` | `partial` | `error`); all other fields pass through verbatim. The orchestrator wraps it as `{plugin: "<name>", source: "extension", ...}` and merges into `services.<name>`. Names that collide with built-in plugins are rejected. See `agent-plus/README.md#Extensions` for the worked example. **agent-plus only orchestrates; the user owns the extension scripts themselves.**

```bash
agent-plus extensions add --name X --command python3 --command-arg=script.py
agent-plus extensions list
agent-plus extensions validate
agent-plus refresh --no-extensions     # skip them
agent-plus refresh --extensions-only   # only run them
```

## What `refresh` covers

Cheapest identity probe per built-in plugin, NAMES + IDs + URLs only. User extensions also run by default unless `--no-extensions` is set:

- **`github-remote`** — parses `git config --get remote.origin.url`, hits `GET /repos/{owner}/{repo}` if a token is available (env or `gh auth token`), captures `default_branch` + `repo_id`.
- **`vercel-remote`** — if `VERCEL_TOKEN` is set, hits `GET /v9/projects?limit=20`, captures `[{name, id}]`. If not set, records `unconfigured` (never prompts).
- **`supabase-remote`** — if `SUPABASE_ACCESS_TOKEN` is set, hits `GET https://api.supabase.com/v1/projects`, captures `[{name, id, region, organization_id}]` (cap 20).
- **`railway-ops`** — shells out to `railway list --json` (no env passed in — railway-ops itself defers to the CLI's auth state). Captures `[{name, id}]`. Records `unconfigured` if `railway` isn't on PATH or isn't authed.
- **`linear-remote`** — if `LINEAR_API_KEY` is set, POSTs a tiny GraphQL `{ viewer { id name email } teams(first: 20) { nodes { id key name } } }`. Auth header is the raw key — NO `Bearer` prefix.
- **`langfuse-remote`** — if both `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set, hits `GET <base>/api/public/health` with Basic auth. Base URL precedence: `LANGFUSE_BASE_URL > LANGFUSE_HOST > https://cloud.langfuse.com`. Multi-instance health is `langfuse-remote health --all`'s job.

Plugins still out of scope for refresh (envcheck still reports them): coolify-remote, hcloud-remote, hermes-remote, openrouter-remote, skill-feedback.

## When NOT to use this — fall back to the underlying plugin

- **Actual plugin operations.** This is workspace bootstrap only. To deploy a Hermes app, use `hermes-remote`. To trigger a Vercel deploy, use `vercel-remote`. Don't loop on `agent-plus` for real work.
- **Reading or editing config files directly.** `.agent-plus/*.json` is plain JSON. Use `cat`, `jq`, or open in an editor — `agent-plus` does not expose a generic file edit surface.
- **Refreshing plugins beyond the wired six.** coolify-remote, hcloud-remote, hermes-remote, openrouter-remote, skill-feedback don't have a refresh handler yet — use the per-plugin CLI directly.

## Privacy + safety contract

- **NAMES only.** Env-var names land on disk; values do not. Verified by a canary test.
- **Read-mostly.** `init` writes three small JSON files; `envcheck` overwrites `env-status.json`; `refresh` overwrites `services.json` (merging by plugin key). No deletes, no destructive ops.
- **Network only on `refresh`.** `init` and `envcheck` are local. `refresh` makes at most one HTTP GET per supported plugin.
- **Token never echoed.** `refresh` reads `GITHUB_TOKEN` / `VERCEL_TOKEN` to authenticate but never copies them into stdout, stderr, or `services.json`.

## Design rules (agent-plus patterns)

1. **Aggregate server-side.** `init` creates three files in one call. `envcheck` walks every plugin in one call.
2. **Resolve by name, not ID.** `refresh` caches `(name, id)` pairs so subsequent agent calls can pass `--project my-app` without seeing the 22-char ID.
3. **`--json` is the default.** `--pretty` for indented.
4. **Strip values the agent shouldn't see.** Env-var NAMES only; secret values never enter the output stream.
5. **Self-diagnosing output.** Every payload carries `tool: {name, version}` from the manifest.
6. **Stay in your lane.** This is a bootstrap plugin. For actual plugin operations, use the plugin directly.

## Example session-start bootstrap

```bash
agent-plus init --pretty
# {"tool":{"name":"agent-plus","version":"0.1.0"},
#  "workspace":"/path/to/repo/.agent-plus",
#  "source":"git",
#  "created":["manifest.json","services.json","env-status.json"],
#  "skipped":[]}

agent-plus envcheck --pretty
# {... "set":["GITHUB_TOKEN","VERCEL_TOKEN"],
#      "missing":["LINEAR_API_KEY","SUPABASE_ACCESS_TOKEN", ...],
#      "plugins":{"github-remote":{"ready":true,...}, ...}}

agent-plus refresh --pretty
# {... "services":{"github-remote":{"owner":"osouthgate","repo":"agent-plus",
#                                   "default_branch":"main", ...},
#                  "vercel-remote":{"projects":[{"name":"...","id":"..."}], ...}}}
```
