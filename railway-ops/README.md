# railway-ops

Read-first wrapper around the [Railway CLI](https://docs.railway.app/reference/cli-api) for fast environment inspection during incident triage. Stdlib-only Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

## Why

Not a replacement for the Railway dashboard or CLI. This exists so an AI agent (or a human in a hurry) gets **one-call situational awareness** across a project's services instead of stitching together five separate `railway` invocations.

**Measured wins**

- `overview` fans out to every service in parallel (~8 worker threads): logs + deploy status + env var names per service, all in one call. A 5-service project resolves in **~8s** instead of **~40s** sequential.
- **activeDeploy vs latestDeploy.** The Railway CLI only exposes one deploy per service; when a newer build failed on top of a serving SUCCESS, the CLI would collapse that into "everything's broken." With `RAILWAY_API_TOKEN` set, the tool queries Railway's GraphQL API to split the two out with timestamps, commit SHA, PR number, and branch on each. Triage signal: `activeDeploy=SUCCESS` + `latestDeploy=FAILED` = traffic fine, someone just tried to ship.
- **Build logs attached to failed deploys.** When `latestDeploy` is FAILED and distinct from `activeDeploy`, `overview` auto-attaches `buildLogTail` (last ~30 build-log lines), `buildErrorKinds` (fingerprint→count), and `buildLineCount` to that deploy. One call tells the whole story — no second request needed to see why the build broke.
- **Postgres-aware log classification.** Messages matching `<ts> UTC [<pid>] <LEVEL>:` are classified by the embedded pg level, so routine `LOG: checkpoint complete` lines don't flood the error bucket even though Railway stamps them as stderr.
- Log lines are classified server-side — `error` / `warning` / other — deduped and capped per service (default 20, configurable via `--limit`). Pre-cap `errorTotal` and fingerprint-bucketed `errorKinds` make truncation self-documenting: a flood of 800 identical FK violations shows up as one bucket with count=800, not 20 silent lines.
- **Env var values never touch output.** `railway variables` exposes values; this CLI parses them in `strip_env_values`, keeps names, drops the dict. A canary-value no-leak test asserts no value substring can appear in any output path.
- **Read-only by design.** `railway up`, `railway redeploy`, and variable writes are not wrapped. If you need to write, use `railway` directly.
- **Self-diagnosing output.** Every JSON response carries a top-level `tool: {name, version}` field read from the plugin manifest. Agents can see their binary version from the output alone — no extra subprocess call to detect version drift.
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
- **Optional**: `RAILWAY_API_TOKEN` (or `RAILWAY_TOKEN`) env var. Unlocks deploy history via Railway's GraphQL API — needed for `activeDeploy` / `latestDeploy` split, build-log tails on failed deploys, `errors --since-deploy`, and the `build-logs` subcommand's auto-resolve path. Without it the tool still works on a single-deploy fallback from the CLI.

The CLI checks preconditions at startup and exits with a clear message if any are missing.

## Commands

All commands emit JSON on stdout. `--pretty` switches to indented JSON for humans; omit it for compact output suitable for `jq` / pipelines. `--env` accepts short forms (`prod` → `production`, `stag` → `staging`); an ambiguous substring raises a clear error rather than silently picking one.

`--output <path>` (available on every subcommand) writes the full JSON payload to disk and prints only a compact envelope on stdout — `savedTo`, `bytes`, `payloadKeys`, `payloadShape` (type + size per key, recursive), and (for log-shaped payloads) head+tail line previews. Use it when the response is large enough that routing it through an agent's context window is wasteful (`build-logs` with high `--lines`, `overview` for many services). The envelope is the agent's cue to decide whether to `Read` the file at all, and `payloadShape` points at which top-level key (and nested slice) to drill into. `--shape-depth <1|2|3>` controls recursion depth (default `3`).

```bash
# One-call snapshot — project, env, services, active+latest deploys with commit meta,
# recent errors/warnings with fingerprint buckets, env var NAMES. When latestDeploy
# is FAILED and distinct from activeDeploy, it's auto-enriched with buildLogTail
# and buildErrorKinds so you see WHY the build failed in the same response.
railway-ops overview [--env production|prod] [--service <name>] \
                     [--since 24h] [--log-lines 500] [--limit 20] [--pretty]

# Current linked context (project/env/service + whoami)
railway-ops status [--pretty]

# Focus on one service's runtime errors
# --since-deploy scopes to logs since the active deploy's createdAt (needs token)
railway-ops errors <service> [--env <name>] [--limit 50] [--since-deploy] [--pretty]

# Build-stream logs for a failed deploy
# Without --deployment: resolves the most recent FAILED deploy via GraphQL
# With --deployment: skips GraphQL, goes straight to `railway logs --deployment <id>`
railway-ops build-logs <service> [--deployment <id>] [--env <name>] [--lines 200] [--pretty]

# Env var NAMES for one service (values never appear)
railway-ops envs <service> [--env <name>]

# Short list of services + deploy status
railway-ops services [--env <name>] [--pretty]

# Project list
railway-ops projects

# Print binary version and exit
railway-ops --version
```

## JSON contract

`overview` emits a stable top-level shape:

| Field        | Type            | Notes                                                                 |
|--------------|-----------------|-----------------------------------------------------------------------|
| `tool`       | object          | `{ name: "railway-ops", version: "<semver>" }` — self-diagnosing version drift |
| `project`    | string          | Linked project name                                                   |
| `projectId`  | string \| null  | Railway project id when available                                     |
| `env`        | string          | Resolved environment, or `"<linked>"` when no `--env` is passed       |
| `since`      | string          | The `--since` window used for log queries                             |
| `filter`     | object \| null  | `{ "service": "<name>" }` when `--service` is passed, else `null`     |
| `summary`    | object          | `{ services, failures, errors, warnings }` rollup across services (pre-cap totals) |
| `services`   | array<object>   | Per-service snapshots (see below)                                     |

Per-service snapshot shape:

| Field          | Type            | Notes                                                                 |
|----------------|-----------------|-----------------------------------------------------------------------|
| `name`         | string          | Service name                                                          |
| `id`           | string          | Railway service id                                                    |
| `status`       | string          | Latest attempt's status (SUCCESS / FAILED / CRASHED / …)              |
| `stopped`      | bool            | Whether the service is stopped                                        |
| `activeDeploy` | object \| null  | Currently-serving SUCCESS deploy (needs `RAILWAY_API_TOKEN`)          |
| `latestDeploy` | object          | Most recent attempt (any status). If FAILED + distinct from active, auto-includes `buildLogTail`, `buildErrorKinds`, `buildLineCount` |
| `errors`       | array<object>   | Capped (`--limit`) deduped error lines                                |
| `warnings`     | array<object>   | Capped deduped warning lines                                          |
| `errorTotal`   | int             | Pre-cap error count — reflects floods the `errors[]` list can't       |
| `warningTotal` | int             | Pre-cap warning count                                                 |
| `errorKinds`   | object          | Top-10 fingerprint → count buckets (normalises UUIDs/nums/quoted strs) |
| `warningKinds` | object          | Same shape for warnings                                               |
| `truncated`    | bool            | True when `errorTotal > len(errors)` or `warningTotal > len(warnings)` |
| `envVarNames`  | array<string>   | Env var NAMES only — values are stripped at parse time                |

Deploy object shape (`activeDeploy`, `latestDeploy`):

| Field           | Type              | Notes                                            |
|-----------------|-------------------|--------------------------------------------------|
| `id`            | string            | Deployment id                                    |
| `status`        | string            | SUCCESS / FAILED / CRASHED / SKIPPED / …         |
| `createdAt`     | string \| null    | ISO 8601 — deploy start                          |
| `updatedAt`     | string \| null    | ISO 8601 — deploy finish                         |
| `staticUrl`     | string \| null    | Public URL for this deploy                       |
| `commitSha`     | string \| null    | Git commit hash                                  |
| `commitMessage` | string \| null    | Commit message (trimmed to 300 chars)            |
| `prNumber`      | int \| null       | PR number if deployed from one                   |
| `branch`        | string \| null    | Git branch                                       |
| `buildLogTail`  | array<object>     | Only on FAILED `latestDeploy`: last ~30 build-log entries |
| `buildErrorKinds` | object          | Fingerprint → count across the full build log (not just tail) |
| `buildLineCount`  | int             | Total lines pulled from the build stream         |

Contract rules:

- Fields listed above are guaranteed to be present (non-breaking additions allowed in future releases).
- `summary` always returns all four counters, even when every value is `0`.
- Env var *values* are never emitted — only `envVarNames`. Enforced by canary-value no-leak tests.
- `--pretty` only affects whitespace; field names and nesting are identical to the compact output.

## Example output

```bash
$ railway-ops overview --env production --pretty
{
  "tool": { "name": "railway-ops", "version": "0.4.0" },
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
    {
      "name": "api",
      "status": "SUCCESS",
      "activeDeploy": { "id": "dep-a", "status": "SUCCESS", "prNumber": 639, "commitMessage": "release: v0.0.4.5", "createdAt": "..." },
      "latestDeploy": { "id": "dep-a", "status": "SUCCESS", "prNumber": 639, "commitMessage": "release: v0.0.4.5", "createdAt": "..." },
      "errors": [], "errorTotal": 0, "errorKinds": {},
      "envVarNames": [ "DATABASE_URL", "OPENAI_API_KEY", "..." ]
    },
    {
      "name": "postgres",
      "status": "FAILED",
      "activeDeploy": { "id": "dep-old", "status": "SUCCESS", "prNumber": 639, "createdAt": "23h ago" },
      "latestDeploy": {
        "id": "dep-new", "status": "FAILED", "prNumber": 651, "createdAt": "8m ago",
        "commitMessage": "hotfix: stop dropping relationship_evidence FK",
        "buildLogTail": [ { "timestamp": "...", "message": "[err] Build Failed: ... \"/package.json\": not found" } ],
        "buildErrorKinds": { "Build Failed: failed to compute cache key: ... not found": 1 },
        "buildLineCount": 42
      },
      "errors": [ ... 2 ... ], "errorTotal": 2,
      "errorKinds": { "... ERROR: column \"<str>\" does not exist ...": 2 },
      "envVarNames": [ "POSTGRES_USER", "POSTGRES_PASSWORD", "..." ]
    }
  ]
}
```

## Architecture

- Shells out to `railway` via `subprocess.run` with a 30s timeout per call.
- Services queried in parallel via `concurrent.futures.ThreadPoolExecutor` (8 workers).
- Env var parsing isolated in `strip_env_values`, covered by canary-value no-leak tests.
- Log classification is regex-based with a Postgres-aware override: pg-shape lines use the embedded `LOG/WARNING/ERROR:` level; other lines fall back to `/\b(error|fatal|panic|exception|traceback|unhandled)\b/i` → errors, `/\b(warn|warning)\b/i` → warnings.
- Deploy history uses Railway's GraphQL API at `backboard.railway.com/graphql/v2` when `RAILWAY_API_TOKEN` (or `RAILWAY_TOKEN`) is set. Any failure (missing token, network, schema drift) silently falls back to the single-deploy shape from `railway service status`.
- Build logs for failed deploys come from `railway logs --deployment <id>`. A build-specific regex (`failed|cache key|not found|exit code|…`) catches the Docker/BuildKit failure modes that runtime's ERROR_RE misses.

## Tests

```bash
python -m unittest discover -s test -p "test_*.py"
```

~80 tests covering:

- `strip_env_values` (including canary-value no-leak invariants across object, list, and `KEY=VALUE` shapes).
- Runtime log classification, bucket dedupe + cap, pg embedded-level detection (ERROR/FATAL/PANIC vs LOG/STATEMENT/DETAIL), and plain-text vs JSON log handling.
- Fingerprint-based error bucketing (`errorKinds`): UUID/number/quoted-string normalisation, top-N ordering, flood-resistance against 800 identical FK violations.
- Build-log enrichment: `_bucket_build_log` and `enrich_deploy_with_build_logs` only fire on failed deploys with distinct IDs; tail + errorKinds shape tests.
- Deploy splitting: `classify_deploys` active vs latest selection, `_slim_deploy` commit metadata extraction, environment ID / name resolution (prefix + substring + ambiguity).
- End-to-end snapshot shape with a stubbed `railway` subprocess runner.
- **Schema contract tests** for `overview` JSON output: top-level keys, `summary` rollup math, `filter` narrowing (case-insensitive, empty-match), `bucket_cap` plumb-through, per-service key stability, and JSON round-trip safety.
- Tool metadata: `_with_tool_meta` dict-vs-passthrough behavior, version from plugin manifest, ordering (tool field first).
- Argparse contract: `overview` flags, env-short-form resolution, `--since-deploy` plumbing.
