# langfuse-remote

Remote CLI for managing [Langfuse](https://langfuse.com) instances (cloud or self-hosted) from Claude Code. Stdlib-only Python 3, no dependencies.

> Renamed from `langfuse` → `langfuse-remote` (0.3.0) to disambiguate from the upstream Langfuse product. Matches the `*-remote` convention used across agent-plus plugins (coolify-remote, github-remote, hcloud-remote, hermes-remote, linear-remote, openrouter-remote, supabase-remote, vercel-remote).

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

## Why

Not a replacement for the Langfuse UI or SDKs. This is the control-plane / backup / migration tool you'd otherwise hack together in one-off scripts, plus a read-only debug entrypoint designed for AI agents.

**The headline win: `monitor-user`.** Triaging "what went wrong for user X" via the API means hitting `/users`, `/sessions`, per-session `/sessions/{id}`, per-trace `/traces/{id}` — four endpoints, N+1 calls per user. `monitor-user <id>` does all of that inside the CLI in parallel and returns **one structured JSON blob**: daily-metrics totals, the last N sessions, the latest trace per session, observation counts, total latency, and any `ERROR`-level observations. One tool call, enough context to triage.

Plus the boring-but-necessary stuff: export/import prompts for backup, migrate prompts across instances (with version numbers, labels, tags, config preserved), smoke-test trace ingestion after a deploy, health-check every configured instance in one call.

## Headline commands

```bash
# Read-only debug — designed for AI agents
langfuse-remote monitor-user <user-id> --limit 5 --pretty
langfuse-remote get-traces <trace-id> [<trace-id> ...]
langfuse-remote get-sessions <session-id>
langfuse-remote list-user-traces <user-id> --from-timestamp 2026-04-01T00:00:00Z

# Instance ops
langfuse-remote health                                      # current instance
langfuse-remote health --all                                # every configured instance in one call
langfuse-remote list-instances
langfuse-remote show-instance
langfuse-remote trace-ping --name deploy-verify             # smoke-test ingestion

# Prompt backup / migration
langfuse-remote --instance prod export-prompts prod.json
langfuse-remote --instance prod import-prompts prod.json
langfuse-remote migrate-prompts --from cloud --to prod
langfuse-remote migrate-prompts --from cloud --to prod --file snapshot.json --keep
```

All commands take `--pretty` for indented JSON; otherwise output is compact and `jq`-ready. Unknown IDs come back as `{id, error: "not_found"}` instead of hard-failing — a batch of lookups keeps going. Auth / connectivity errors still hard-fail.

`--output <path>` (top-level flag, place it before the subcommand) writes the full JSON payload to disk and prints a compact envelope (`savedTo`, `bytes`, `payloadKeys`, `payloadShape` with per-key type + size) instead. Use for large dumps (`get-traces` for many IDs, `monitor-user` for a chatty user, full session fetches) that are wasteful to route through the model's context window.

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

The CLI walks up from cwd for `.env.local` / `.env` and picks up any `LANGFUSE_*` key not already in the shell. If your app's `.env` has `LANGFUSE_BASE_URL` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`, running `langfuse-remote health` from that dir just works.

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

Env wins on conflict. `langfuse-remote list-instances` shows what's resolved.

## Install

### Recommended — marketplace install

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install langfuse-remote@agent-plus
```

Adds `langfuse-remote` to PATH and loads the skill so Claude reaches for it automatically.

### Session-only (dev / try-before-install)

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/langfuse-remote
```

`--plugin-dir` loads for the current shell only; nothing persisted.

### Standalone — no Claude Code

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/langfuse-remote/bin/langfuse-remote
chmod +x langfuse-remote
./langfuse-remote health
```

## API quirks codified here

The kind of stuff you'd otherwise have to discover by reading Langfuse's response bodies:

- **`GET /api/public/users/{id}` doesn't exist** on Langfuse 3.x — it returns a 404 HTML page. `get-users` uses `GET /api/public/metrics/daily?userId=...` and aggregates the daily rows into a `totals` object.
- **`GET /api/public/sessions` (list) returns minimal rows** (`{id, createdAt, projectId, environment}`) — no trace count. `monitor-user` enriches each row with `GET /api/public/sessions/{id}` (includes inline `traces[]`) and then `GET /api/public/traces/{latestId}` for observation details. That's the "one blob per user" trick — all the N+1 hops happen server-side.

## License

MIT.
