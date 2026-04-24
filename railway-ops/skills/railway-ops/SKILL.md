---
name: railway-ops
description: Read-first wrapper around the Railway CLI. Single-call env overviews (services, deploy status, recent errors/warnings, env var NAMES-only) for fast incident triage. Use whenever the user wants to see the state of a Railway environment — what's running, what's broken, which env vars exist on a service — without you having to chain `railway list`, `railway service status`, `railway logs`, `railway variables` per service.
when_to_use: Trigger on phrases like "what's happening on railway", "show me prod", "railway status", "is the api up", "why is staging broken", "what's failing on railway", "env vars on <service>", "which env vars does api have", "is redis running", "show me recent errors on <service>", "give me a snapshot of production", "railway overview".
allowed-tools: Bash(railway-ops:*) Bash(python3 *railway-ops*:*)
---

# railway-ops

Project-scoped CLI that wraps the Railway CLI into a read-first, JSON-output overview tool. Stdlib-only Python 3. Designed for incident triage — one call returns the full project/services/errors/envs picture so you don't burn context chaining per-service `railway` invocations.

Lives at `${CLAUDE_SKILL_DIR}/../../bin/railway-ops`; the plugin auto-adds `bin/` to PATH, so just run `railway-ops ...`.

## Prerequisites

- **`railway` CLI** installed and on PATH (`railway --version` must succeed).
- **Authenticated** — `railway login` must have run (`railway whoami` must return a user).
- **Project linked** — `railway link` must have been run in this repo (or pass `--env <name>` explicitly).
- **Optional: `RAILWAY_API_TOKEN`** (or `RAILWAY_TOKEN`) env var — unlocks deploy history via Railway's GraphQL API, giving you `activeDeploy` (currently serving) separately from `latestDeploy` (most recent attempt), plus commit SHA, PR number, branch, and timestamps on each. Without a token the skill still works — it just falls back to a single CLI-sourced deploy with only `id`/`status`.

The skill bails with a clear message if any of these preconditions are missing.

## When to reach for this

- User asks **"what's happening in prod"** — run `overview --env production --pretty` and you get services, deploy status, recent errors, and env var names in one shot.
- User asks **"why is <service> broken"** — run `errors <service> --env production --pretty` for focused error/warning triage.
- User asks **"what env vars does <service> have"** — run `envs <service>` to get NAMES only. Values never touch stdout.
- User says **"show me Railway"** / **"Railway status"** — run `status` to get the project/env/whoami context.

## Triage recipes

**DB incident on a known service** — skip `overview`, go straight to the focused command with a high limit:

```bash
railway-ops errors <service> --env production --since 24h --limit 50 --pretty
```

`overview` caps each service's errors[] at 20 and is noisy when you already know which service is on fire. `errors` pulls 10× the log lines, caps errors/warnings at 50 by default, and emits a bucketed `errorKinds` summary (fingerprint → count) so a flood of 800 identical FK violations can't hide behind the truncation. Read `errorTotal` and `errorKinds` first — they reveal scale before you read any individual line.

**Scanning the whole env** — use `overview`, but always check `summary.errors` and each service's `errorTotal` / `errorKinds` before trusting the per-service `errors[]` list. `errors[]` is truncated; the kinds buckets are not.

**"Is prod serving or broken?"** — check both `activeDeploy` AND `latestDeploy`. `activeDeploy=SUCCESS` 23 hours ago + `latestDeploy=FAILED` 8 minutes ago means traffic is fine, someone just tried to ship and the build failed — completely different triage from "prod is down." If `activeDeploy` is `null`, the GraphQL path wasn't available (no `RAILWAY_API_TOKEN`) or the service has never had a successful deploy.

**"Why did the failed deploy fail?"** — when `latestDeploy.status` is FAILED/CRASHED/ERRORED AND it's a different deploy from `activeDeploy`, the overview auto-includes a `buildLogTail` (last ~30 build-log lines), a `buildErrorKinds` fingerprint summary, and `buildLineCount` right on the `latestDeploy` object. One call tells the whole story: active is still serving, this newer attempt failed, here's why. Look at `buildErrorKinds` first — `"Build Failed: failed to compute cache key: ... not found": 1` pinpoints the Docker layer that broke without scrolling.

**"Errors since my current deploy came up"** — `errors <service> --since-deploy` scopes to logs from the active deploy's `createdAt` onward, so you don't see noise from a previous version. Needs `RAILWAY_API_TOKEN`; falls back to `--since` with a stderr warning if unavailable.

## Commands

All commands emit a single JSON document to stdout (the "JSON-first contract"). Use `--pretty` for indented output when a human is reading; default is compact JSON for piping into `jq` or agent tooling.

```bash
# Single-call snapshot — the headline feature. Project, env, per-service
# deploy status, recent errors + warnings (last 24h by default), env var
# NAMES per service. Runs per-service fetches in parallel.
railway-ops overview --env production --pretty
railway-ops overview --env staging --since 1h --pretty

# Narrow to a single service (case-insensitive substring match against name)
railway-ops overview --env production --service api --pretty

# Override the per-service errors/warnings cap (default 20)
railway-ops overview --env production --limit 50 --pretty

# whoami + linked project + available environments
railway-ops status --pretty

# One service's errors + warnings (deeper than overview — bigger --limit)
railway-ops errors api --env production --since 2h --limit 50 --pretty

# Scope to logs since the active deploy came up (needs RAILWAY_API_TOKEN)
railway-ops errors api --env production --since-deploy --pretty

# Env var NAMES only for one service. Values are stripped at parse time and
# never reach stdout or stderr.
railway-ops envs api --env production --pretty

# Short per-service deploy-status list
railway-ops services --env production --pretty

# All Railway projects visible to this account
railway-ops projects --pretty
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
      "activeDeploy": {
        "id": "ddf2184d-...",
        "status": "SUCCESS",
        "createdAt": "2026-04-23T14:00:00Z",
        "updatedAt": "2026-04-23T14:02:30Z",
        "staticUrl": "https://api-production.up.railway.app",
        "commitSha": "abc123",
        "commitMessage": "release: v0.0.4.5",
        "prNumber": 639,
        "branch": "main"
      },
      "latestDeploy": {
        "id": "c8000cda-...",
        "status": "FAILED",
        "createdAt": "2026-04-24T13:45:00Z",
        "updatedAt": "2026-04-24T13:45:15Z",
        "staticUrl": null,
        "commitSha": "def456",
        "commitMessage": "hotfix: stop dropping relationship_evidence FK",
        "prNumber": 651,
        "branch": "brockenhurst/hotfix/relationship-evidence-fk-race",
        "buildLogTail": [
          { "timestamp": "...", "message": "[err] [builder 4/6] COPY package.json ./" },
          { "timestamp": "...", "message": "[err] Build Failed: ... \"/package.json\": not found" }
        ],
        "buildErrorKinds": {
          "Build Failed: failed to compute cache key: ... not found": 1
        },
        "buildLineCount": 42
      },
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
- **Postgres-aware classification** runs first: lines matching `<timestamp> UTC [<pid>] <LEVEL>:` are classified by the embedded `LEVEL` (pg's `LOG`, `STATEMENT`, `DETAIL`, `HINT`, `NOTICE` → ignored; `ERROR`, `FATAL`, `PANIC` → error; `WARNING` → warning). Without this, Railway's "all stderr is level=error" envelope buries the real signal under hundreds of routine `LOG: checkpoint complete` lines.
- Then classifies by the `level` field (Pino/stdlib logger convention), falling back to regex on the `message` text (`/\b(error|fatal|panic|exception|traceback|unhandled)\b/i` for errors, `/\b(warn|warning)\b/i` for warnings).
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

Unit tests in `railway-ops/test/` use Python's stdlib `unittest`. No Railway account needed — all tests stub the subprocess runner. Run from the repo root:

```bash
python -m unittest discover railway-ops/test
```

## How to use it with Claude

When the user asks about Railway state, reach for `overview` first. It's the cheapest one-call answer. Only drop to `errors <service>` if the user is focused on a single service, or `envs <service>` if they want to know which env vars exist. Never run `overview` in a tight loop — each call fans out to ~2 subprocess calls per service.
