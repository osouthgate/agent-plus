---
name: railway-ops
description: Read-first wrapper around the Railway CLI. Single-call env overviews (services, deploy status, recent errors/warnings, env var NAMES-only) for fast incident triage. Use whenever the user wants to see the state of a Railway environment — what's running, what's broken, which env vars exist on a service — without you having to chain `railway list`, `railway service status`, `railway logs`, `railway variables` per service.
when_to_use: Trigger on phrases like "what's happening on railway", "show me prod", "railway status", "is the api up", "why is staging broken", "what's failing on railway", "env vars on <service>", "which env vars does api have", "is redis running", "show me recent errors on <service>", "give me a snapshot of production", "railway overview".
allowed-tools: Bash(.claude/skills/railway-ops/bin/railway-ops:*)
---

# railway-ops

Project-scoped CLI that wraps the Railway CLI into a read-first, JSON-output overview tool. Stdlib-only Python 3. Designed for incident triage — one call returns the full project/services/errors/envs picture so you don't burn context chaining per-service `railway` invocations.

The binary lives at `.claude/skills/railway-ops/bin/railway-ops` — invoke it by that path from the repo root (or any subdirectory of `osdb/`).

## Prerequisites

- **`railway` CLI** installed and on PATH (`railway --version` must succeed).
- **Authenticated** — `railway login` must have run (`railway whoami` must return a user).
- **Project linked** — `railway link` must have been run in this repo (or pass `--env <name>` explicitly).

The skill bails with a clear message if any of these preconditions are missing.

## When to reach for this

- User asks **"what's happening in prod"** — run `overview --env production --pretty` and you get services, deploy status, recent errors, and env var names in one shot.
- User asks **"why is <service> broken"** — run `errors <service> --env production --pretty` for focused error/warning triage.
- User asks **"what env vars does <service> have"** — run `envs <service>` to get NAMES only. Values never touch stdout.
- User says **"show me Railway"** / **"Railway status"** — run `status` to get the project/env/whoami context.

## Triage recipes

**DB incident on a known service** — skip `overview`, go straight to the focused command with a high limit:

```bash
.claude/skills/railway-ops/bin/railway-ops errors <service> --env production --since 24h --limit 50 --pretty
```

`overview` caps each service's errors[] at 20 and is noisy when you already know which service is on fire. `errors` pulls 10× the log lines, caps errors/warnings at 50 by default, and emits a bucketed `errorKinds` summary (fingerprint → count) so a flood of 800 identical FK violations can't hide behind the truncation. Read `errorTotal` and `errorKinds` first — they reveal scale before you read any individual line.

**Scanning the whole env** — use `overview`, but always check `summary.errors` and each service's `errorTotal` / `errorKinds` before trusting the per-service `errors[]` list. `errors[]` is truncated; the kinds buckets are not.

## Commands

All commands emit a single JSON document to stdout (the "JSON-first contract"). Use `--pretty` for indented output when a human is reading; default is compact JSON for piping into `jq` or agent tooling.

```bash
# Single-call snapshot — the headline feature. Project, env, per-service
# deploy status, recent errors + warnings (last 24h by default), env var
# NAMES per service. Runs per-service fetches in parallel.
.claude/skills/railway-ops/bin/railway-ops overview --env production --pretty
.claude/skills/railway-ops/bin/railway-ops overview --env staging --since 1h --pretty

# Narrow to a single service (case-insensitive substring match against name)
.claude/skills/railway-ops/bin/railway-ops overview --env production --service api --pretty

# Override the per-service errors/warnings cap (default 20)
.claude/skills/railway-ops/bin/railway-ops overview --env production --limit 50 --pretty

# whoami + linked project + available environments
.claude/skills/railway-ops/bin/railway-ops status --pretty

# One service's errors + warnings (deeper than overview — bigger --limit)
.claude/skills/railway-ops/bin/railway-ops errors api --env production --since 2h --limit 50 --pretty

# Env var NAMES only for one service. Values are stripped at parse time and
# never reach stdout or stderr.
.claude/skills/railway-ops/bin/railway-ops envs api --env production --pretty

# Short per-service deploy-status list
.claude/skills/railway-ops/bin/railway-ops services --env production --pretty

# All Railway projects visible to this account
.claude/skills/railway-ops/bin/railway-ops projects --pretty
```

## Hard safety rules (non-negotiable)

1. **Env var VALUES never touch stdout or stderr.** The tool calls `railway variables --json`, parses the `{KEY: VALUE}` dict, keeps only the keys, and drops the dict before emitting. If the agent needs a specific value for a troubleshooting task, the user runs `railway variables` directly — the skill exists specifically to prevent accidental value leakage into conversation transcripts.
2. **Read-only.** Write subcommands (`up`, `deploy`, `redeploy`, `restart`, `down`, `delete`, `init`, `link`, `unlink`, `add`, `scale`) are rejected before reaching the `railway` binary.
3. **Prerequisites are checked at startup.** If `railway --version` fails, or `railway whoami` doesn't show a logged-in user, the tool exits with a clear remediation message.

## `overview` output shape

```json
{
  "project": "loamdb",
  "projectId": "5199ef24-...",
  "env": "production",
  "since": "24h",
  "filter": null,
  "summary": {
    "services": 3,
    "failures": 0,
    "errors": 2,
    "warnings": 5
  },
  "services": [
    {
      "name": "api",
      "id": "e2b67796-...",
      "status": "SUCCESS",
      "stopped": false,
      "latestDeploy": { "id": "ddf2184d-...", "status": "SUCCESS" },
      "errors": [
        { "timestamp": "...", "level": "error", "message": "...", "module": "..." }
      ],
      "warnings": [ ... ],
      "errorTotal": 847,
      "warningTotal": 12,
      "errorKinds": {
        "insert or update on relationship_evidence row <n> violates FK": 847
      },
      "warningKinds": { "slow query took <n>ms": 12 },
      "truncated": true,
      "envVarNames": ["ALLOWED_ORIGINS", "DATABASE_URL", "OPENAI_API_KEY", ...]
    },
    { "name": "Redis", ... },
    { "name": "loamdb-postgres", ... }
  ]
}
```

- `filter` is `null` when no `--service` filter was applied, otherwise `{"service": "<name>"}`.
- `summary` is a roll-up across the (possibly filtered) `services` array: `services` count, `failures` (services whose latest deploy is not `SUCCESS` and not stopped), plus **pre-cap** totals of `errors` and `warnings` (so a flood of identical errors is reflected here even though each service's `errors[]` is truncated).
- `errorTotal` / `warningTotal` on each service snapshot are pre-cap counts. `errors[]` and `warnings[]` remain capped and deduped for readability; always compare the two to tell whether truncation is hiding something.
- `errorKinds` / `warningKinds` are fingerprint-bucketed counts (top 10 by frequency). UUIDs, numbers, hex, and quoted strings are normalised away before bucketing, so "FK violation on row 12345" and "FK violation on row 67890" collapse into one bucket. Read these before trusting the individual lines — a truncated `errors[]` of 20 can misrepresent 800 identical failures.
- `truncated` is `true` when the pre-cap total exceeds what's shown in `errors[]`/`warnings[]`.
- Field names are stable across releases; changes are additive. Prefer `jq` against these keys rather than parsing human output.

### Log classification

- Pulls last ~500 lines per service via `railway logs --json --since <dur>`.
- Classifies by the `level` field (Pino/stdlib logger convention) first, falling back to regex on the `message` text (`/\b(error|fatal|panic|exception|traceback|unhandled)\b/i` for errors, `/\b(warn|warning)\b/i` for warnings).
- Dedupes consecutive identical messages. Caps each bucket at `--limit` (default 20) most-recent entries per service. The `errors` subcommand uses its own `--limit` (default 50) for deeper single-service triage.
- **Truncation is visible:** `errors[]` / `warnings[]` are capped, but `errorTotal` / `warningTotal` / `errorKinds` / `warningKinds` are computed across every classified line, so you can always tell how much the cap is hiding.

## Architecture note

Env var **values never touch stdout**. The only code path that reads them is `strip_env_values()` which runs `json.loads` on the CLI output, extracts the keys, and explicitly `del`s the parsed dict. There is no branch anywhere in the program that emits a value. The unit tests pin this invariant — `test_strip_env_values_leaks_no_substring` asserts that the concatenated stdout output of the builder contains zero characters from any input value.

Parallelism: per-service log + variable fetches are fanned out across a thread pool (max 8 workers). Total wall time for the user's prod environment (5 services) is typically <15s.

## Flags worth knowing

- `--pretty` — indent JSON output (accepted on every subcommand).
- `--env <name>` — target a specific Railway environment (production, staging, …). Defaults to the linked env when omitted.
- `--since <duration>` — `30s` / `5m` / `2h` / `1d` / `1w` / ISO 8601. Only affects log fetches.
- `--log-lines N` (overview) — override per-service log line count pulled from `railway logs` (default 500).
- `--service <name>` (overview) — case-insensitive substring match; narrows `services` and re-scopes `summary` to just the matched services.
- `--limit N` (overview) — per-service cap on the deduped errors/warnings buckets (default 20).
- `--limit N` (errors) — cap on errors/warnings buckets for the single-service `errors` command (default 50).

## Testing

Unit tests in `.claude/skills/railway-ops/test/` use Python's stdlib `unittest`. No Railway account needed — all tests stub the subprocess runner.

```bash
python -m unittest discover .claude/skills/railway-ops/test
```

## How to use it with Claude

When the user asks about Railway state, reach for `overview` first. It's the cheapest one-call answer. Only drop to `errors <service>` if the user is focused on a single service, or `envs <service>` if they want to know which env vars exist. Never run `overview` in a tight loop — each call fans out to ~2 subprocess calls per service.
