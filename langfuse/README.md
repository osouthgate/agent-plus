# langfuse

Remote CLI for managing Langfuse instances (cloud or self-hosted) from Claude Code.

Not a replacement for the Langfuse UI or SDKs. This is a control-plane / backup / migration tool for ops you'd otherwise hack together in one-off scripts:

- **Export / import prompts** — dump all prompts + all versions to JSON, restore anywhere.
- **Migrate prompts** between instances (cloud → self-hosted, prod → dev) with version numbers, labels, tags, and config preserved.
- **Trace ping** — send a smoke-test trace after a deploy to verify ingestion end-to-end.
- **Health checks** across multiple named instances.
- **Read-only debug** — given a Langfuse user ID, inspect that user's recent sessions, traces, and error observations without clicking through the UI. Designed for AI agents (`monitor-user` returns a single structured JSON blob).

Stdlib-only Python 3. No `pip install` required.

## Install

```bash
claude --plugin-dir /path/to/agent-plus/langfuse
```

Or install the whole `agent-plus` repo as a marketplace and enable `langfuse` from there.

## Configure

Options stack (shell env wins, then `--env-file`, then auto-loaded `.env`, then JSON config). Pick what fits.

**Project `.env` auto-loading** — the default. The CLI walks up from cwd for `.env.local` / `.env` and picks up any `LANGFUSE_*` keys not already in the shell. So if your app already has `LANGFUSE_BASE_URL` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` in its `.env`, running `langfuse health` from that dir just works. `--env-file <path>` adds extra files (repeatable); `--no-autoload` disables discovery.

**Single active instance** — in your shell profile, quickest for ad-hoc use:

```bash
export LANGFUSE_BASE_URL="https://langfuse.example.com"
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
```

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

## Commands

```bash
langfuse list-instances
langfuse show-instance
langfuse --instance <name> show-instance

langfuse health
langfuse health --all

langfuse trace-ping
langfuse --instance dev-ollie trace-ping --name deploy-verify

langfuse --instance prod export-prompts prod.json
langfuse --instance prod import-prompts prod.json

langfuse migrate-prompts --from cloud --to prod
langfuse migrate-prompts --from cloud --to prod --file snapshot.json --keep
```

## Debug commands (read-only)

All debug commands print JSON to stdout (add `--pretty` for indented output).
Unknown IDs don't hard-fail — they come back as `{id, error: "not_found"}` so a
batch of lookups keeps going. Auth / connectivity errors still hard-fail.

```bash
# Single-ID lookups (accept one or many IDs)
langfuse get-traces <trace-id> [<trace-id> ...]
langfuse get-observations <obs-id> [<obs-id> ...]
langfuse get-sessions <session-id> [<session-id> ...]
langfuse get-users <user-id> [<user-id> ...]

# Recent activity for a user
langfuse list-user-traces <user-id> --limit 10
langfuse list-user-traces <user-id> --from-timestamp 2026-04-01T00:00:00Z --to-timestamp 2026-04-30T23:59:59Z
langfuse list-user-sessions <user-id> --limit 10

# One-shot structured summary for an AI agent
langfuse monitor-user <user-id> --limit 5 --pretty
```

`monitor-user` is the entrypoint for AI-agent debugging: give it a Langfuse user
ID and it returns a single JSON blob with the user's daily-metrics totals, the
last N sessions, and for each session the latest trace plus its observation
count, total observation latency, and any `ERROR`-level observations — enough
context to triage "what went wrong for this user" without hitting the Langfuse UI.

### API quirks

- There is **no** `GET /api/public/users/{id}` endpoint on Langfuse 3.x — the path
  returns a 404 HTML page. `get-users` uses `GET /api/public/metrics/daily?userId=…`
  instead and aggregates the daily rows into a `totals` object.
- `GET /api/public/sessions` (list) returns minimal rows (`{id, createdAt,
  projectId, environment}`) with no trace count. `monitor-user` enriches each
  row by calling `GET /api/public/sessions/{id}` (which includes inline
  `traces[]`) and then `GET /api/public/traces/{latestId}` for observation
  details.

Exit codes: `0` ok, `2` operational failure, `1` config error.

## License

MIT.
