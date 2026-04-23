# supabase-remote

Remote CLI for day-to-day [Supabase](https://supabase.com) project ops. One file, stdlib Python 3, no dependencies. Wraps the Supabase Management API for project listing and the local `supabase` CLI for SQL execution — stripping the "untrusted data" envelope the CLI emits in agent mode so pipelines get plain JSON.

Part of [agent-plus](../README.md) — a small collection of Claude Code plugins.

> Rainshift-specific Rayna ops (members lookup, comms tail, stuck-onboarding) live in the rainshift repo under `ops/rayna` and shell out to `sql-inline` here for the heavy lifting. This plugin stays generic.

## Why

The `supabase` CLI is fine, but it optimises for humans at a REPL. Agents and scripts keep hitting the same rough edges:

- `supabase db query` returns a JSON envelope with an untrusted-data preamble when it detects an agent. Parsing it without that knowledge produces junk.
- Project refs are 20-char opaque strings. Commands take refs, not names.
- There's no one-liner for "which of my tables is missing RLS".
- Regenerating types is a multi-flag dance every time.

This wrapper collapses each of those into a single command.

## Install

### As a Claude Code plugin (recommended)

```bash
claude --plugin-dir /path/to/agent-plus/supabase-remote
```

Enabling the plugin adds `supabase-remote` to PATH and loads the skill so Claude reaches for it automatically.

### Standalone

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/supabase-remote/bin/supabase-remote
chmod +x supabase-remote
./supabase-remote projects list
```

Needs Python 3.9+ and either the `supabase` CLI or `psql` on `PATH` for any SQL command.

## Configure

Layered config, highest precedence first:

1. `--env-file <path>`
2. `$CWD/rayna-setup/.env.local` (rainshift convenience)
3. `$CWD/.env.local` / `$CWD/.env` (walked up from cwd)
4. `~/.agent-plus/.env`
5. Shell environment (including Claude Code settings)

Project `.env` files override the shell. Only `SUPABASE_*` keys are picked up.

```bash
# .env
SUPABASE_ACCESS_TOKEN=sbp_...              # required — https://supabase.com/dashboard/account/tokens
SUPABASE_PROJECT_REF=abcdefghijklmnopqrst  # optional default
SUPABASE_DB_URL=postgres://...             # optional — if set, SQL uses psql
```

## Headline commands

```bash
supabase-remote projects list                         # all projects visible to the token
supabase-remote sql seed.sql --verify-rows 12         # apply a file, assert row count
supabase-remote sql-inline "select count(*) from users"
supabase-remote rls-audit                             # every table, RLS status + policy count
supabase-remote gen-types packages/db/types.ts        # wraps supabase gen types
```

See `supabase-remote <cmd> --help` or the [skill doc](skills/supabase-remote/SKILL.md) for the full reference.

## Scope (v1)

In scope: project listing, SQL file/inline execution, RLS audit, TypeScript gen-types.

Out of scope for v1: auth user management, storage buckets, edge functions, migration authoring. Use the `supabase` CLI directly for those.

Project-specific helpers (e.g. domain-specific member lookups) belong in that project's own repo, shelling out to `sql-inline` here. Rainshift's lives under `rainshift/ops/rayna`.

## License

MIT.
