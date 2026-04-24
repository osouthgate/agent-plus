---
name: vercel-remote
description: Read-first wrapper around the Vercel REST API. Single-call project overviews (deployments, domains, env NAMES-only, warnings) for incident triage. Use whenever the user wants the state of a Vercel project — what deployed, what's failing, which env vars exist, which domains are verified — without you chaining `vercel list`, `vercel inspect`, `vercel env ls`, `vercel domains ls` per project. Also covers deployment logs via `logs` and async deploys via Deploy Hooks with `--wait`.
when_to_use: Trigger on phrases like "what's happening on vercel", "is prod up", "vercel status", "why is <project> broken", "latest deployment on <project>", "env vars on <project>", "vercel overview", "check domains on <project>", "tail the logs on <deployment>", "trigger a vercel deploy".
allowed-tools: Bash(.claude/skills/vercel-remote/bin/vercel-remote:*)
---

# vercel-remote

Project-scoped CLI that wraps the Vercel REST API into a read-first, JSON-output overview tool. Stdlib-only Python 3 (no pip installs, no venvs). Designed for agent-driven incident triage — one call returns the full project/deployments/domains/env-names picture so you don't burn tool calls chaining per-resource requests.

The binary lives at `.claude/skills/vercel-remote/bin/vercel-remote` — invoke by that path.

## Prerequisites

- **`VERCEL_TOKEN`** set (project `.env` / `.env.local` or shell env). Get one at https://vercel.com/account/tokens.
- **Optional:** `VERCEL_TEAM_ID` for team-scoped projects. Every API call appends `?teamId=…` when set.
- **Optional:** `VERCEL_PROJECT` to omit `--project` on every call.

The CLI bails with a clear missing-config message if `VERCEL_TOKEN` is absent.

## When to reach for this

- User asks **"what's happening on <project>"** → run `overview --project <name> --pretty`. One call → project info + recent deployments (with commit metadata) + domain health + env var NAMES + top-level warnings.
- User asks **"why is prod broken"** → run `deployments list --project <name> --state error --limit 5` then `logs <deployment-url> --errors-only`.
- User asks **"what env vars does <project> have"** → run `env list --project <name>`. Names only — values never touch output.
- User asks **"trigger a deploy"** → run `deployments trigger --hook-url <hook> --wait`. Waits up to 15 min for build completion.
- User asks **"is my domain verified"** → run `domains list --project <name>` or `domains verify <domain> --project <name> --wait`.

## Headline commands

```bash
vercel-remote projects list [--pretty]
vercel-remote projects resolve <name-or-id>

vercel-remote overview --project <name> [--limit 10] [--pretty]

vercel-remote deployments list [--project <name>] [--state ready|error|building] [--limit 20]
vercel-remote deployments show <deployment-or-url>
vercel-remote deployments trigger --hook-url <url> [--wait] [--timeout 900]

vercel-remote logs <deployment-or-url> [--since 1h] [--errors-only] [--limit 100]

vercel-remote domains list --project <name>
vercel-remote domains verify <domain> --project <name> [--wait] [--timeout 300]

vercel-remote env list --project <name> [--env production|preview|development]
vercel-remote env set <KEY> <VALUE> --project <name> [--env ...] [--wait]
vercel-remote env remove <KEY> --project <name> [--env ...] [--wait]
```

All list/show commands emit JSON to stdout. Use `--pretty` for indentation.

## Design rules (agent-plus patterns)

1. **Aggregate server-side.** `overview` returns project + deployments + domains + env names in one call — you don't chain four requests.
2. **Resolve by name.** Pass `--project my-app`, not `prj_xxxxxxxxxxxxxxxx`. The CLI resolves internally via `/v9/projects/{idOrName}`.
3. **`--wait` on every async flow.** `deployments trigger`, `domains verify`, `env set/remove` support `--wait` with per-command sensible timeouts (15m / 5m / 30s). On timeout: non-zero exit with partial JSON including the last-known state.
4. **`--json` is the default.** No human-prose output paths. Pipe to `jq` freely.
5. **Zero env-value leakage.** `env list` returns NAMES only. Every API response walks through `_scrub()` before emission, which masks `password`, `token`, `githubToken`, `value`, `secret`, `encryptedValue`, and related keys. A canary-value test (`test/test_vercel_remote.py`) asserts a known secret substring never appears in any output path.

## Config precedence (highest first)

1. `--token` / `--team` CLI flags
2. `--env-file <path>` if passed
3. `.env.local` / `.env` walked up from cwd (closest wins)
4. Shell env

Only `VERCEL_*` prefixed vars are picked up.

## Safety

- **Read-only by default.** Write commands (`env set`, `env remove`, `deployments trigger`) are explicit subcommands.
- **No `deployments trigger` via the file-upload path.** The command uses Vercel **Deploy Hooks** (pre-configured webhook URLs, stored as a project secret) — see https://vercel.com/docs/deployments/deploy-hooks. Pass the hook URL via `--hook-url`.
- **Team scoping is mandatory when `VERCEL_TEAM_ID` is set.** Every API call appends `?teamId=…`. Without it, team-scoped resources return 404.

## Error message contract

Every error path emits problem + cause + fix + link:

- Missing token → "Set in project `.env` or `.env.local` (keys prefixed `VERCEL_`), or `~/.claude/settings.json`. Get one: https://vercel.com/account/tokens"
- 401 → "Token invalid or expired. Regenerate: https://vercel.com/account/tokens"
- 403 → "Token lacks required scope. Regenerate with appropriate scope."
- 429 → "Rate-limited by Vercel API. Retry after a few seconds." (`Retry-After` is honoured automatically; you only see this on exhausted retries.)
- 404 with team scope → notes the team-ID prefix so you can verify `VERCEL_TEAM_ID`.

## What it doesn't do

Deliberately out of scope for v1:

- Team / user CRUD (team *scoping* is fully supported via `VERCEL_TEAM_ID`; only team management is deferred).
- Billing and invoices.
- Edge Config authoring.
- Framework-specific build logic.

Use the `vercel` CLI or the dashboard for those. This plugin is read-first operational triage plus the minimum write surface needed by agents (env set/remove, deploy hook trigger).
