# railway-ops

Read-first wrapper around the [Railway CLI](https://docs.railway.app/reference/cli-api) for fast environment inspection during incident triage. Stdlib-only Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

## Why

Not a replacement for the Railway dashboard or CLI. This exists so an AI agent (or a human in a hurry) gets **one-call situational awareness** across a project's services instead of stitching together five separate `railway` invocations.

**Measured wins**

- `overview` fans out to every service in parallel (~8 worker threads): logs + deploy status + env var names per service, all in one call. A 5-service project resolves in **~8s** instead of **~40s** sequential.
- Log lines are classified server-side — `error` / `warning` / other — deduped and capped at 20-most-recent per service. Claude sees structured buckets, not raw log blobs.
- **Env var values never touch output.** `railway variables` exposes values; this CLI parses them in `strip_env_values`, keeps names, drops the dict. A canary-value no-leak test asserts no value substring can appear in any output path. Designed specifically to prevent accidental leakage into AI agent transcripts.
- **Read-only by design.** `railway up`, `railway redeploy`, and variable writes are not wrapped. If you need to write, use `railway` directly.

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
    { "name": "api",      "status": "active", "errors": [], "warnings": [ ... 1 ... ], "envVarNames": [ ... ] },
    { "name": "postgres", "status": "FAILED", "errors": [ ... 19 ... ], "envVarNames": [ ... ] }
  ]
}
```

## Architecture

- Shells out to `railway` via `subprocess.run` with a 30s timeout per call.
- Services queried in parallel via `multiprocessing.ThreadPool` (8 workers).
- Env var parsing isolated in `strip_env_values`, covered by canary-value no-leak tests.
- Log classification is regex-based: `/\b(error|ERROR|FATAL|panic|exception|traceback)\b/` → errors, `/\b(warn|WARN|warning)\b/i` → warnings. Each bucket deduped and capped at 20 most-recent entries to keep output bounded.

## Tests

```bash
python -m unittest discover test
```

18 tests covering strip_env_values (including canary-value no-leak invariant), log classification, bucket dedupe+cap, and end-to-end snapshot shape with stubbed subprocess.
