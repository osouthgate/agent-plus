# railway-ops

Read-first wrapper around the [Railway CLI](https://docs.railway.app/reference/cli-api) for fast environment inspection during incident triage.

Not a replacement for the Railway dashboard or CLI. This exists so an AI agent (or a human in a hurry) can get one-call situational awareness across a project's services — what's running, what's failing, what env vars exist (names only) — without stitching together five separate `railway` invocations.

Stdlib-only Python 3. No `pip install` required.

## Install

```bash
claude --plugin-dir /path/to/agent-plus/railway-ops
```

Or install the whole `agent-plus` repo as a marketplace and enable `railway-ops` from there.

## Prerequisites

- `railway` CLI on PATH — install: https://docs.railway.app/reference/cli-api#installation
- Authed: `railway login`
- Linked to a project in whichever directory you run from: `railway link`

The CLI checks both at startup and exits with a clear message if either is missing.

## Safety rules

1. **Env var values never land in output.** `railway variables` exposes values; this CLI parses and strips them. Only names are returned. Designed specifically to prevent accidental value leakage into AI agent transcripts.
2. **Read-only.** `railway up`, `railway redeploy`, and variable writes are not wrapped. If you need to write, use `railway` directly.

## Commands

```bash
# One-call snapshot — project, env, services, deploy status, recent errors/warnings, env var NAMES
railway-ops overview [--env production|staging] [--since 24h] [--pretty]

# Current linked context (project/env/service + whoami)
railway-ops status [--pretty]

# Focus on one service's errors
railway-ops errors <service> [--env <name>] [--limit 50] [--pretty]

# Env var NAMES for one service (values never appear)
railway-ops envs <service> [--env <name>]

# Short list of services + deploy status
railway-ops services [--env <name>] [--pretty]

# Project list
railway-ops projects
```

## Example output

```bash
$ railway-ops overview --env production --pretty
{
  "project": "loamdb",
  "env": "production",
  "services": [
    {
      "name": "api",
      "status": "active",
      "latestDeploy": { "id": "...", "status": "SUCCESS", "createdAt": "..." },
      "errors": [],
      "warnings": [
        { "timestamp": "2026-04-23T13:45:12Z", "line": "[WARN] security.auth_failure ..." }
      ],
      "envVarNames": ["DATABASE_URL", "OPENAI_API_KEY", "LANGFUSE_PUBLIC_KEY", "..."]
    },
    {
      "name": "postgres",
      "status": "FAILED",
      "errors": [ ... 19 entries ... ],
      ...
    }
  ]
}
```

## Architecture

The CLI shells out to `railway` via `subprocess.run` with a 30s timeout per call. Services are queried in parallel via `multiprocessing.ThreadPool` so a 5-service project resolves in ~8s rather than 40s sequential.

Env var parsing is isolated in one function (`strip_env_values`) covered by canary-value tests — a no-leak invariant asserts no value substring ever appears in the output.

Log classification is regex-based: lines matching `/\b(error|ERROR|FATAL|panic|exception|traceback)\b/` are errors; `/\b(warn|WARN|warning)\b/i` are warnings. Each bucket is deduped (near-identical consecutive lines collapsed) and capped at 20 most-recent entries to keep output bounded.

## Tests

```bash
python -m unittest discover test
```

18 tests covering strip_env_values (including canary-value no-leak invariant), log classification, bucket dedupe+cap, and end-to-end snapshot shape with stubbed subprocess.
