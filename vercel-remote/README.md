# vercel-remote

Read-first wrapper around the [Vercel REST API](https://vercel.com/docs/rest-api) for fast project inspection and incident triage. Stdlib-only Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

## Why

Without this, an agent investigating a Vercel project chains four calls — `vercel list` / `vercel inspect` / `vercel env ls` / `vercel domains ls` — parses human output, copy/pastes 22-char project IDs between invocations, and still has no single JSON blob it can reason over. That's 4 tool calls and ~1,500 extra input tokens before the agent has any useful context. `vercel-remote overview` replaces that with one call.

**Measured wins**

- `overview` aggregates project info, the last 10 deployments (with commit metadata projected from `meta.githubCommitSha` / `meta.githubCommitMessage`), domain verification summary, and env var NAMES in a single API round-trip from the CLI's perspective. Payload is bounded and capped so context budgeting is predictable.
- **Env var values never touch output.** The `env list` command returns names only, and every API response walks through `_scrub()` before emission — masking `password`, `token`, `githubToken`, `secret`, `value`, `encryptedValue`, and related fields across any nested response shape. A canary-value no-leak test asserts a known secret substring cannot appear in any output path.
- **Deploy Hooks, not `/v13/deployments`.** `deployments trigger` uses Vercel Deploy Hooks (pre-configured POST URLs) — the correct flow for an agent. The file-upload `POST /v13/deployments` path is a footgun for agents and is deliberately not wrapped.
- **Name-resolved.** `--project my-app` works; you never copy `prj_xxxxxxxxxxxxxxxx` between commands.
- **`--wait` contract is explicit.** 15-min default for deploy triggers, 5-min for domain verification, 30s for env changes. On timeout: non-zero exit with partial JSON containing the last-known state. Never hangs indefinitely.

## Install

### Recommended — marketplace install

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install vercel-remote@agent-plus
```

Adds `vercel-remote` to PATH and loads the skill so Claude reaches for it automatically.

### Session-only (dev / try-before-install)

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/vercel-remote
```

### Standalone — no Claude Code

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/vercel-remote/bin/vercel-remote
chmod +x vercel-remote
./vercel-remote projects list --pretty
```

## Prerequisites

- **`VERCEL_TOKEN`** with appropriate scope. Get one at https://vercel.com/account/tokens.
- **Python 3.9+** (stdlib only).

Config precedence (highest first):
1. `--token` / `--team` CLI flags
2. `--env-file <path>`
3. `.env.local` / `.env` walked up from cwd (closest wins)
4. Shell env

Only `VERCEL_*` prefixed vars are read.

## Usage

`--output <path>` is available on every subcommand (pass it before the subcommand): writes the full JSON payload to disk and prints a compact envelope (`savedTo`, `bytes`, `payloadKeys`, plus head/tail previews for log-shaped payloads). Use for large responses (`logs`, `overview` across many deployments) that are wasteful to route through the model's context window.

```bash
# One-call snapshot — project + recent deployments + domains + env NAMES + warnings
vercel-remote overview --project my-app --pretty

# Save a big log dump to disk; stdout returns just a summary envelope
vercel-remote --output /tmp/dep.json logs my-app-xyz.vercel.app --since 24h

# List projects
vercel-remote projects list --pretty

# Resolve a name → full project object
vercel-remote projects resolve my-app

# Recent deployments, error state only (--state: ready|error|building|queued|canceled)
vercel-remote deployments list --project my-app --state error --limit 5

# Inspect a specific deployment (ID or URL)
vercel-remote deployments show my-app-xyz.vercel.app

# Trigger a deploy via Deploy Hook, wait up to 15 min for completion
vercel-remote deployments trigger --hook-url https://api.vercel.com/v1/integrations/deploy/... --wait

# Build / runtime logs (last hour, errors only)
vercel-remote logs my-app-xyz.vercel.app --since 1h --errors-only

# Domain verification
vercel-remote domains list --project my-app
vercel-remote domains verify mydomain.com --project my-app --wait

# Env vars — NAMES only on list
vercel-remote env list --project my-app --env production
vercel-remote env set MY_KEY some-value --project my-app --env production
vercel-remote env remove MY_KEY --project my-app --env production
```

## Team scoping

If `VERCEL_TEAM_ID` is set, every API call appends `?teamId=…`. Without it, team-scoped resources return 404. The plugin handles this plumbing centrally in `_api()` — you never need to pass `--team` on individual commands.

## What it doesn't do

- **Team / user CRUD** — only team *scoping* (via `VERCEL_TEAM_ID`) is supported; team management is out of scope.
- **Billing and invoices** — use the dashboard.
- **Edge Config authoring** — use `vercel edge-config` directly.
- **Framework-specific build logic** — this wraps the API, not the build.

Use the `vercel` CLI for those.

## Gotchas the plugin collapses

- **Vercel CLI `--target` collides with the env-var `target` concept.** This plugin uses `--env production|preview|development` for env var commands (matches agent-plus convention) and keeps `--target` only for deploy-level flags.
- **Vercel has multiple log endpoints with shifting semantics.** The `logs` command normalises against `/v2/deployments/{id}/events` with since-filtering. If Vercel ships a new log API, only `fetch_logs()` changes.
- **Env `POST` returns 409 when the key exists.** The plugin detects this and surfaces a clear message directing you to `env remove` first.
- **Deploy Hooks are secrets-in-URL.** The URL itself authorises the deploy — treat it like a token. Don't commit it; don't log it.

## License

MIT. See repo root.
