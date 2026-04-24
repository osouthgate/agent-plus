# langfuse

Remote CLI for managing [Langfuse](https://langfuse.com) instances (cloud or self-hosted) from Claude Code. Stdlib-only Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

## Why

Not a replacement for the Langfuse UI or SDKs. This is the control-plane / backup / migration tool you'd otherwise hack together in one-off scripts, plus a read-only debug entrypoint designed for AI agents.

**The headline win: `monitor-user`.** Triaging "what went wrong for user X" via the API means hitting `/users`, `/sessions`, per-session `/sessions/{id}`, per-trace `/traces/{id}` — four endpoints, N+1 calls per user. `monitor-user <id>` does all of that inside the CLI in parallel and returns **one structured JSON blob**: daily-metrics totals, the last N sessions, the latest trace per session, observation counts, total latency, and any `ERROR`-level observations. One tool call, enough context to triage.

Plus the boring-but-necessary stuff: export/import prompts for backup, migrate prompts across instances (with version numbers, labels, tags, config preserved), smoke-test trace ingestion after a deploy, health-check every configured instance in one call.

## Headline commands

```bash
# Read-only debug — designed for AI agents
langfuse monitor-user <user-id> --limit 5 --pretty
langfuse get-traces <trace-id> [<trace-id> ...]
langfuse get-sessions <session-id>
langfuse list-user-traces <user-id> --from-timestamp 2026-04-01T00:00:00Z

# Instance ops
langfuse health                                      # current instance
langfuse health --all                                # every configured instance in one call
langfuse list-instances
langfuse show-instance
langfuse trace-ping --name deploy-verify             # smoke-test ingestion

# Prompt backup / migration
langfuse --instance prod export-prompts prod.json
langfuse --instance prod import-prompts prod.json
langfuse migrate-prompts --from cloud --to prod
langfuse migrate-prompts --from cloud --to prod --file snapshot.json --keep
```

All commands take `--pretty` for indented JSON; otherwise output is compact and `jq`-ready. Unknown IDs come back as `{id, error: "not_found"}` instead of hard-failing — a batch of lookups keeps going. Auth / connectivity errors still hard-fail.

Exit codes: `0` ok, `2` operational failure, `1` config error.

## Configure

Layered, highest precedence first: shell env → `--env-file <path>` → auto-loaded project `.env.local` / `.env` → JSON config file. `--no-autoload` disables `.env` discovery.

**Quickest — single instance in your shell profile:**

```bash
export LANGFUSE_BASE_URL="https://langfuse.example.com"
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
```

**Project `.env` auto-loading** (recommended when your app already has these keys):

The CLI walks up from cwd for `.env.local` / `.env` and picks up any `LANGFUSE_*` key not already in the shell. If your app's `.env` has `LANGFUSE_BASE_URL` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`, running `langfuse health` from that dir just works.

**Multiple named instances** — via env prefix, pick with `--instance <name>`:

```bash
export LANGFUSE_CLOUD_BASE_URL="https://cloud.langfuse.com"
export LANGFUSE_CLOUD_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_CLOUD_SECRET_KEY="sk-lf-..."

export LANGFUSE_PROD_BASE_URL="https://langfuse.example.com"
export LANGFUSE_PROD_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_PROD_SECRET_KEY="sk-lf-..."
```

**JSON config** at `$LANGFUSE_CONFIG` (default `~/.config/langfuse/instances.json`):

```json
{
  "default": "prod",
  "instances": {
    "prod":      {"base_url": "...", "public_key": "pk-lf-...", "secret_key": "sk-lf-..."},
    "dev-ollie": {"base_url": "...", "public_key": "pk-lf-...", "secret_key": "sk-lf-..."}
  }
}
```

Env wins on conflict. `langfuse list-instances` shows what's resolved.

## Install

```bash
claude --plugin-dir /path/to/agent-plus/langfuse
```

Or install the whole `agent-plus` repo as a marketplace and enable `langfuse` from there.

## API quirks codified here

The kind of stuff you'd otherwise have to discover by reading Langfuse's response bodies:

- **`GET /api/public/users/{id}` doesn't exist** on Langfuse 3.x — it returns a 404 HTML page. `get-users` uses `GET /api/public/metrics/daily?userId=...` and aggregates the daily rows into a `totals` object.
- **`GET /api/public/sessions` (list) returns minimal rows** (`{id, createdAt, projectId, environment}`) — no trace count. `monitor-user` enriches each row with `GET /api/public/sessions/{id}` (includes inline `traces[]`) and then `GET /api/public/traces/{latestId}` for observation details. That's the "one blob per user" trick — all the N+1 hops happen server-side.

## License

MIT.
