---
name: vercel-remote
description: Read-first wrapper around the Vercel REST API. Single-call project overviews (deployments, domains, env NAMES-only, warnings) for incident triage. Use whenever the user wants the state of a Vercel project — what deployed, what's failing, which env vars exist, which domains are verified — without you chaining `vercel list`, `vercel inspect`, `vercel env ls`, `vercel domains ls` per project. Also covers deployment logs via `logs` and async deploys via Deploy Hooks with `--wait`.
when_to_use: Trigger on phrases like "what's happening on vercel", "is prod up", "vercel status", "why is <project> broken", "latest deployment on <project>", "env vars on <project>", "vercel overview", "check domains on <project>", "tail the logs on <deployment>", "trigger a vercel deploy".
allowed-tools: Bash(vercel-remote:*) Bash(python3 *vercel-remote*:*)
---

# vercel-remote

Project-scoped CLI that wraps the Vercel REST API into a read-first, JSON-output overview tool. Stdlib-only Python 3 (no pip installs, no venvs). Designed for agent-driven incident triage — one call returns the full project/deployments/domains/env-names picture so you don't burn tool calls chaining per-resource requests.

Lives at `${CLAUDE_SKILL_DIR}/../../bin/vercel-remote`; the plugin auto-adds `bin/` to PATH, so just run `vercel-remote ...`.

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

vercel-remote deployments list [--project <name>] [--state ready|error|building|queued|canceled] [--limit 20]
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

Every payload carries a top-level `tool: {name, version}` field so agents can self-diagnose version drift from the output alone. Run `vercel-remote --version` to check the installed version directly.

## Offloading large responses with `--output`

Vercel responses get large fast — `logs` on a chatty deployment, `overview` with high `--limit`, `deployments list` across many deployments. Pulling all of that through the model's context is wasteful when you only need a slice.

**Pass `--output <path>` before the subcommand** (it's a top-level flag, like `--pretty`):

```bash
vercel-remote --output /tmp/logs.json logs my-app-xyz.vercel.app --since 24h
vercel-remote --output /tmp/deps.json deployments list --project my-app --limit 50
```

Instead of printing the full payload, stdout returns a compact envelope:

```json
{
  "tool": {"name": "vercel-remote", "version": "..."},
  "savedTo": "/tmp/logs.json",
  "bytes": 48320,
  "fileLineCount": 1204,
  "payloadKeys": ["deployment", "entries"],
  "payloadShape": {
    "deployment": {"type": "string", "length": 24},
    "entries": {"type": "list", "length": 412,
      "sample": {"type": "dict", "keys": 5,
        "shape": {"level": {"type": "string", "length": 5},
                  "message": {"type": "string", "length": 187},
                  "timestamp": {"type": "number"}}}}
  }
}
```

**How to act on it:**

1. Check `payloadShape` to see where the data lives and how much there is. In the example above, the agent immediately knows there are 412 log entries with `{level, message, timestamp}` fields — no second call needed to discover shape.
2. Use `Read` with offset/limit to pull the slice you need. `fileLineCount` is the upper bound.
3. For list-shaped responses (`projects list`, `deployments list`), the envelope has `payloadType: "list"` + `payloadLength` + `sampleShape` describing the first item.

**`--shape-depth <1|2|3>`** controls recursion depth. Default is `3` (two layers deep — surfaces patterns like `deployments[0].meta`). Drop to `1` for a minimal envelope.

**When NOT to use `--output`:** small responses (`projects resolve`, `env list` — NAMES are already tiny), or when you need the data in the same turn to act on. The envelope points at the file; it doesn't carry the data.

## Piping to `jq`

Output is JSON-first, so pipe freely to `jq` for focused extraction:

```bash
# Failing deployments only
vercel-remote overview --project my-app | jq '.deployments[] | select(.state == "ERROR")'

# Unverified domain names
vercel-remote domains list --project my-app | jq '.[] | select(.verified == false) | .name'

# Env var names (no values) as a flat list
vercel-remote env list --project my-app --env production | jq -r '.names[]'
```

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

## When NOT to use this — fall back to the `vercel` CLI or the Vercel API directly

**This wrapper's scope is deliberately narrow:** projects (list/resolve), `overview`, deployments (list/show/trigger via Deploy Hook), `logs`, domains (list/verify), and env (list NAMES / set / remove). Anything outside that surface is not here and won't be — use the `vercel` CLI (already authed on the user's machine) or `curl https://api.vercel.com/...` with `Authorization: Bearer $VERCEL_TOKEN` instead.

Specific cases where you should skip `vercel-remote` and go straight to `vercel` or the raw API:

- **Creating, renaming, or deleting a project**, or configuring its Git integration / framework preset / build-and-output settings. → `vercel project add`, `vercel project rm`, or `POST/PATCH/DELETE /v9/projects[/{id}]`. `vercel-remote` only reads and resolves projects.
- **DNS records CRUD, domain transfers, or buying a domain.** `domains list`/`verify` check *project-attached* domains; they don't touch the account-level domain registry. → `vercel domains ...` or `/v4/domains`, `/v2/domains/{domain}/records`.
- **Edge Config, Blob, KV/Redis, or Postgres storage** (reading/writing items, creating stores, rotating tokens). → `vercel env pull` + the relevant storage SDK, or the `/v1/edge-config/*`, `/v1/blob/*`, storage integration endpoints directly.
- **Cron jobs, firewall rules, deployment protection, preview comments, web analytics config, log drains, integrations/marketplace, team/user management, billing/invoices.** None of these are wrapped. → dashboard, `vercel` CLI, or the corresponding `/v1/...` endpoint.
- **Deploying from local files** (not via a pre-configured Deploy Hook). `deployments trigger` only fires Deploy Hook URLs. → `vercel deploy` / `vercel --prod`, or `POST /v13/deployments` with a file-upload payload.
- **Tailing logs live / runtime (non-build) logs.** `logs` is a one-shot snapshot of build/function logs for a specific deployment. → `vercel logs <url> --follow` for a live tail, or the runtime logs drain.

**Don't get stuck in a loop.** If a `vercel-remote` command errors with "unknown subcommand" / "not supported", or the user's request obviously needs a write or resource the wrapper doesn't expose, immediately switch to `vercel` or `curl` against `api.vercel.com` rather than re-trying `vercel-remote` with different flags. The wrapper exists to make *reading* faster and safer, not to replace the CLI or the REST API.
