# supabase-remote

Remote CLI for day-to-day [Supabase](https://supabase.com) project ops. One file, stdlib Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

> This plugin stays generic. Domain-specific helpers (member lookups, custom comms tails, app-specific onboarding queries) belong in the consuming project's own repo and should shell out to `sql-inline` here for the SQL execution.

## Why

The `supabase` CLI is fine, but it optimises for humans at a REPL. Agents hit the same rough edges every time — this wrapper collapses each into one call.

**The agent-envelope gotcha.** `supabase db query` detects when it's being run by an agent and wraps results in a JSON envelope with an **"untrusted data" preamble**. If Claude doesn't know about that preamble, it parses the response as a raw result and produces garbage. `supabase-remote sql` / `sql-inline` strips the envelope server-side so downstream pipelines see plain JSON.

**Other wins**

- `rls-audit` → **1 call**, every table in the public schema, RLS enabled/disabled + policy count per table. Without it: loop over `information_schema.tables`, join to `pg_policies`, per-table. Turns "is anything dangerously exposed?" from an investigation into a boolean. Supports both `--format table` (human) and `--format json` (machine) output.
- `sql seed.sql --verify-rows 12` → assert row count in the same call that applies the file. Catches partial applies without a follow-up `select count(*)`.
- `gen-types packages/db/types.ts [--schema public --schema auth]` — wraps the multi-flag `supabase gen types typescript --project-id ... --schema ...` dance into one command. Schema names are validated to block CLI-flag injection.
- `projects current [--format text|json]` — show the ref `supabase-remote` would use right now and *where that value came from* (`--project` arg, `SUPABASE_PROJECT_REF`, or `supabase link`). Useful before a destructive apply.
- Project refs (20-char opaque strings) never appear in commands once you've set `SUPABASE_PROJECT_REF` as a default.

## Install

### Recommended — marketplace install

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install supabase-remote@agent-plus
```

Adds `supabase-remote` to PATH and loads the skill so Claude reaches for it automatically.

### Session-only (dev / try-before-install)

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/supabase-remote
```

`--plugin-dir` loads for the current shell only; nothing persisted.

### Standalone — no Claude Code

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/supabase-remote/bin/supabase-remote
chmod +x supabase-remote
./supabase-remote projects list
```

Needs Python 3.9+ and either the `supabase` CLI or `psql` on `PATH` for any SQL command.

## Configure

Layered config, highest precedence first:

1. `--env-file <path>`
2. `$CWD/.env.local` / `$CWD/.env` (walked up from cwd)
3. `~/.agent-plus/.env`
4. Shell environment (including Claude Code settings)

Project `.env` files override the shell. Only `SUPABASE_*` keys are picked up.

```bash
# .env
SUPABASE_ACCESS_TOKEN=sbp_...              # required — https://supabase.com/dashboard/account/tokens
SUPABASE_PROJECT_REF=abcdefghijklmnopqrst  # optional default
SUPABASE_DB_URL=postgres://...             # optional — if set, SQL uses psql
```

## Headline commands

```bash
supabase-remote projects list                           # all projects visible to the token
supabase-remote projects current                        # which ref gets used, and why
supabase-remote sql seed.sql --verify-rows 12           # apply a file, assert row count
supabase-remote sql-inline "select count(*) from users"
supabase-remote rls-audit --format json                 # every table, RLS status + policy count
supabase-remote gen-types packages/db/types.ts \
  --schema public --schema auth                         # wraps supabase gen types
```

**JSON-first output.** List-shaped commands (`projects list`, `rls-audit`, `projects current`) support structured output for agents:

- `projects list --json` → array of `{id, name, region, organization_id, ...}`.
- `rls-audit --format json` → stable array of `{table, rls, policies}` objects. (The older `--json` flag still works as a deprecated alias.)
- `projects current --format json` → `{resolved, ref, name, source, raw_input}` where `source ∈ {argument, env, linked}`. On no-default, `{resolved: false, error: ...}` instead of a hard exit.

See `supabase-remote <cmd> --help` or the [skill doc](skills/supabase-remote/SKILL.md) for the full reference.

## Scope (v1)

In scope: project listing, SQL file/inline execution, RLS audit, TypeScript gen-types.

Out of scope for v1: auth user management, storage buckets, edge functions, migration authoring. Use the `supabase` CLI directly for those.

Project-specific helpers (e.g. domain-specific member lookups) belong in the consuming project's own repo, shelling out to `sql-inline` here.

## License

MIT.
