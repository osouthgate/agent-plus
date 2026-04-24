---
name: langfuse-remote
description: Manage Langfuse instances (cloud or self-hosted) from the CLI — export/import prompts for backup and cross-env migration, send smoke-test traces, check health across multiple instances, and inspect a specific user's recent activity (sessions, traces, errors) read-only from the public REST API. Use whenever the user wants to back up prompts, move prompts between Langfuse instances, verify that ingestion is working, check whether a Langfuse deployment is up, or debug what a given Langfuse user has been doing without clicking through the UI.
when_to_use: Trigger on phrases like "export the langfuse prompts", "back up langfuse prompts", "migrate prompts to the new langfuse", "move prompts from cloud to self-hosted", "ping a test trace", "is langfuse up", "check langfuse health", "list my langfuse instances", "send a trace to langfuse", "import prompts.json into langfuse", "what has user <id> been doing in langfuse", "debug this langfuse user", "monitor langfuse user", "show recent traces for user", "fetch langfuse trace <id>", "look up this observation", "any errors for user <id>".
allowed-tools: Bash(langfuse-remote:*) Bash(python3 *langfuse-remote*:*)
---

# langfuse-remote

Stdlib-only Python 3 CLI for admin ops against any Langfuse instance (cloud or self-hosted). The binary lives in this plugin's `bin/` and is auto-added to PATH when the plugin is enabled — call it as `langfuse-remote`.

> Renamed from `langfuse` → `langfuse-remote` (0.3.0) to disambiguate from the upstream Langfuse product. Matches the `*-remote` convention used across agent-plus plugins.

## When to reach for this

- User wants to **back up prompts** from a Langfuse instance before changing / tearing it down.
- User wants to **migrate prompts** between instances (cloud → self-hosted, prod → dev, etc.).
- User wants to **verify ingestion** on a Langfuse instance after a deploy or config change — send a test trace and watch it land.
- User wants to **check health** of one or many Langfuse instances (uptime probe).
- User asks which Langfuse instances are configured / which one is active.
- User hands you a **Langfuse user ID** and asks what that user has been doing / where it went wrong / whether there are errors. Use `monitor-user` — one JSON blob with metrics, sessions, latest trace + observation counts, and ERROR-level observations.
- User hands you a trace / session / observation ID and asks you to fetch it. Use `get-traces` / `get-sessions` / `get-observations`.

Do NOT use this to write new traces, evaluate prompts, or do anything that's a better fit for the Langfuse UI's dashboards. This is a control-plane / backup / migration / read-only-debug tool.

## Configure

Four ways. Pick whichever fits — they stack (shell env wins, then `--env-file`, then auto-loaded `.env`, then JSON config).

**0. Project `.env` auto-loading (portable default)**

The CLI walks up from cwd looking for `.env.local` / `.env`. Any `LANGFUSE_*` keys it finds are loaded unless already set in the shell. This makes the skill "just work" when you run it from inside a project that already has Langfuse keys wired up for its own SDK (e.g. a `.env` with `LANGFUSE_BASE_URL` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`).

```bash
cd /path/to/my-app        # has .env with LANGFUSE_BASE_URL=... etc
langfuse-remote health           # picks up keys from ./.env automatically
langfuse-remote --env-file ./other.env trace-ping   # extra file, repeatable
langfuse-remote --no-autoload list-instances        # disable discovery if needed
```

Set `LANGFUSE_DEBUG=1` to see which `.env` files got loaded.

**1. Single active instance (simplest shell setup)**

```bash
export LANGFUSE_BASE_URL="https://langfuse.example.com"
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
```

**2. Multiple named instances via env prefix** — pick with `--instance <name>`, or `--from/--to` for migrate:

```bash
export LANGFUSE_CLOUD_BASE_URL="https://cloud.langfuse.com"
export LANGFUSE_CLOUD_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_CLOUD_SECRET_KEY="sk-lf-..."

export LANGFUSE_PROD_BASE_URL="https://langfuse-web-production-6100.up.railway.app"
export LANGFUSE_PROD_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_PROD_SECRET_KEY="sk-lf-..."

langfuse-remote --instance prod health
langfuse-remote migrate-prompts --from cloud --to prod
```

**3. JSON config file** at `$LANGFUSE_CONFIG` (default `~/.config/langfuse/instances.json`):

```json
{
  "default": "prod",
  "instances": {
    "prod":      {"base_url": "https://langfuse.example.com", "public_key": "pk-lf-...", "secret_key": "sk-lf-..."},
    "dev-ollie": {"base_url": "https://dev-ollie.example.com", "public_key": "pk-lf-...", "secret_key": "sk-lf-..."}
  }
}
```

Env and file configs merge; env wins on conflict. `list-instances` shows what the CLI sees.

## Commands

```bash
# Discover what's configured
langfuse-remote list-instances
langfuse-remote show-instance                      # default instance
langfuse-remote --instance cloud show-instance

# Health
langfuse-remote health                             # active/default instance
langfuse-remote health --all                       # every configured instance, one per line

# Trace ping (ingestion smoke test)
langfuse-remote trace-ping                         # prints the trace URL
langfuse-remote --instance dev-ollie trace-ping --name deploy-verify

# Prompt backup
langfuse-remote --instance prod export-prompts prod-prompts.json
langfuse-remote --instance prod import-prompts prod-prompts.json   # also works for restore

# Cross-instance migration (export + import in one shot)
langfuse-remote migrate-prompts --from cloud --to prod             # auto temp file, deleted after
langfuse-remote migrate-prompts --from cloud --to dev-ollie --file snapshot.json --keep

# Read-only debug — JSON to stdout (add --pretty to indent)
langfuse-remote get-traces <trace-id> [<trace-id> ...]
langfuse-remote get-observations <obs-id>
langfuse-remote get-sessions <session-id>
langfuse-remote get-users <user-id>                                # uses metrics/daily aggregate
langfuse-remote list-user-traces <user-id> --limit 10
langfuse-remote list-user-sessions <user-id> --limit 10

# Compound summary for agent debugging (metrics + sessions + latest trace + errors)
langfuse-remote monitor-user <user-id> --limit 5 --pretty
```

Debug-command conventions: unknown IDs come back as `{id, error: "not_found"}`
so an agent can batch lookups without poisoning the whole call. Auth /
connectivity failures still hard-fail with a snippet of the response body.

Every structured JSON payload carries a top-level `tool: {name, version}` so
you can detect version drift from output alone. `langfuse-remote --version` prints
the plugin version directly.

**Pipe to `jq`** for any filtering / projection — e.g.
`langfuse-remote monitor-user <uid> | jq '.sessions[].errors'` to pull just the
ERROR-level observations across recent sessions, or
`langfuse-remote get-traces <id> | jq '.traces[0].observations | length'` for a
quick observation count. Compact output is `jq`-ready by default.

API quirks worth knowing: `/api/public/users/{id}` doesn't exist on Langfuse 3.x
(404 HTML) — `get-users` falls back to `/api/public/metrics/daily?userId=…`.
The `/api/public/sessions` list returns minimal rows; `monitor-user` enriches
each with a per-session GET (which includes inline `traces[]`) and a per-trace
GET for observation-level detail.

Exit codes: `0` ok, `2` operational failure (uploads failed / instance down), `1` config error.

## How to use it with Claude

When the user asks to do one of these ops, **don't write an ad-hoc Python script** — run the `langfuse` CLI. If you're in a project directory, the CLI will auto-pick up `LANGFUSE_*` keys from `./.env` / `./.env.local` (or any ancestor), so in most cases you can just run it.

If nothing's configured (`langfuse-remote list-instances` shows nothing), ask the user which instance to act on and how they want to supply credentials: use the project's `.env`, pass `--env-file path/to/file`, or add a persistent entry in `~/.config/langfuse/instances.json`. Then call the CLI.

Good shapes for responses:

- "Backing up prompts from the cloud instance." → `langfuse-remote --instance cloud export-prompts cloud-backup-$(date +%Y%m%d).json`
- "Migrating prompts from cloud to the new hosted Langfuse." → `langfuse-remote migrate-prompts --from cloud --to prod --keep`
- "Is the Langfuse I just deployed accepting traces?" → `langfuse-remote --instance <name> trace-ping`, then fetch the URL it prints.
- "What has user `abc123` been up to in Langfuse?" / "Debug this Langfuse user" → `langfuse-remote monitor-user abc123 --pretty`, parse the returned JSON (user totals + sessions + latestTrace + errors) to answer without opening the UI.
- "Fetch trace `t_abc`" / "look at observation `o_xyz`" → `langfuse-remote get-traces t_abc --pretty` / `langfuse-remote get-observations o_xyz --pretty`.

## Notes on prompt migration

- Versions are preserved 1:1 as long as the target instance is empty for that prompt name. If the target already has versions, new versions get appended (the server assigns the next free version number).
- Labels are carried over except for `latest` (server manages that).
- Tags, config, and commit messages are carried over; `commitMessage` falls back to "imported (orig vN)" when missing.
- Prompt bodies (chat vs text) are auto-detected from the dump's `type` field.
- On failure, the command exits with code 2 but continues uploading the rest — inspect stderr for `FAIL` lines.

## Notes on trace-ping

Uses the `/api/public/ingestion` batch endpoint. Sends a single trace + one child generation. If the instance is behind a proxy that strips `Authorization` headers, this will fail with 401 — check the proxy config. If the API accepts but the trace never appears in the UI, check the worker logs (ingestion is async) — common causes are Redis down or S3/MinIO credentials out of sync.
