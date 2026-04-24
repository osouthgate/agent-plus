# railway-ops

Read-first wrapper around the [Railway CLI](https://docs.railway.app/reference/cli-api) for fast environment inspection during incident triage. Stdlib-only Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

## Why

Not a replacement for the Railway dashboard or CLI. This exists so an AI agent (or a human in a hurry) gets **one-call situational awareness** across a project's services instead of stitching together five separate `railway` invocations.

**Measured wins**

- `overview` fans out to every service in parallel (~8 worker threads): logs + deploy status + env var names per service, all in one call. A 5-service project resolves in **~8s** instead of **~40s** sequential.
- Log lines are classified server-side — `error` / `warning` / other — deduped and capped per service (default 20, configurable via `--limit`). Claude sees structured buckets, not raw log blobs.
- **Env var values never touch output.** `railway variables` exposes values; this CLI parses them in `strip_env_values`, keeps names, drops the dict. A canary-value no-leak test asserts no value substring can appear in any output path. Designed specifically to prevent accidental leakage into AI agent transcripts.
- **Read-only by design.** `railway up`, `railway redeploy`, and variable writes are not wrapped. If you need to write, use `railway` directly.
- **JSON-first contract.** Every command emits a single JSON document on stdout. Field names are stable across releases (additive changes only). Human-readable output is opt-in via `--pretty`; the default is compact JSON for piping into `jq`, scripts, or agent tooling.

## Install

### Recommended — marketplace install

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install railway-ops@agent-plus
```

Adds `railway-ops` to PATH and loads the skill so Claude reaches for it automatically.

### Session-only (dev / try-before-install)

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/railway-ops
```

`--plugin-dir` loads for the current shell only; nothing persisted.

### Standalone — no Claude Code

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/railway-ops/bin/railway-ops
chmod +x railway-ops
./railway-ops overview
```

## Prerequisites

- `railway` CLI on PATH — install: https://docs.railway.app/reference/cli-api#installation
- Authed: `railway login`
- Linked to a project in whichever directory you run from: `railway link`

The CLI checks both at startup and exits with a clear message if either is missing.

## Commands

All commands emit JSON on stdout. `--pretty` switches to indented JSON for humans; omit it for compact output suitable for `jq` / pipelines.

```bash
# One-call snapshot — project, env, services, deploy status, recent errors/warnings, env var NAMES
# --service narrows the snapshot to a single service (focused triage).
# --limit caps errors/warnings per service (default 20).
railway-ops overview [--env production|staging] [--service <name>] \
                     [--since 24h] [--log-lines 500] [--limit 20] [--pretty]

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

## JSON contract

`overview` emits a stable top-level shape:

| Field        | Type            | Notes                                                                 |
|--------------|-----------------|-----------------------------------------------------------------------|
| `project`    | string          | Linked project name                                                   |
| `projectId`  | string \| null  | Railway project id when available                                     |
| `env`        | string          | Resolved environment, or `"<linked>"` when no `--env` is passed       |
| `since`      | string          | The `--since` window used for log queries                             |
| `filter`     | object \| null  | `{ "service": "<name>" }` when `--service` is passed, else `null`     |
| `summary`    | object          | `{ services, failures, errors, warnings }` rollup across services     |
| `services`   | array<object>   | Per-service snapshots: `name`, `status`, `errors`, `warnings`, `envVarNames` |

Contract rules:

- Fields listed above are guaranteed to be present (non-breaking additions allowed in future releases).
- `summary` always returns all four counters, even when every value is `0`.
- Env var *values* are never emitted — only `envVarNames`. Enforced by canary-value no-leak tests.
- `--pretty` only affects whitespace; field names and nesting are identical to the compact output.

## Example output

```bash
$ railway-ops overview --env production --pretty
{
  "project": "loamdb",
  "projectId": "proj-abc123",
  "env": "production",
  "since": "24h",
  "filter": null,
  "summary": {
    "services": 2,
    "failures": 1,
    "errors": 19,
    "warnings": 1
  },
  "services": [
    { "name": "api",      "status": "active", "errors": [], "warnings": [ ... 1 ... ], "envVarNames": [ ... ] },
    { "name": "postgres", "status": "FAILED", "errors": [ ... 19 ... ], "envVarNames": [ ... ] }
  ]
}
```

## Architecture

- Shells out to `railway` via `subprocess.run` with a 30s timeout per call.
- Services queried in parallel via `concurrent.futures.ThreadPoolExecutor` (8 workers).
- Env var parsing isolated in `strip_env_values`, covered by canary-value no-leak tests.
- Log classification is regex-based: `/\b(error|ERROR|FATAL|panic|exception|traceback)\b/` → errors, `/\b(warn|WARN|warning)\b/i` → warnings. Each bucket is deduped and capped at `--limit` most-recent entries (default 20) to keep output bounded.

## Tests

```bash
python -m unittest discover -s test -p "test_*.py"
```

33 tests covering:

- `strip_env_values` (including canary-value no-leak invariants across object, list, and `KEY=VALUE` shapes).
- Log classification, bucket dedupe + cap, and plain-text vs JSON log handling.
- End-to-end snapshot shape with a stubbed `railway` subprocess runner.
- **Schema contract tests** for `overview` JSON output: top-level keys, `summary` rollup math, `filter` narrowing (case-insensitive, empty-match), `bucket_cap` plumb-through, per-service key stability, and JSON round-trip safety.
- Argparse contract: ensures `overview` exposes `--service` and `--limit` with stable defaults.
