# supabase-remote

Remote CLI for day-to-day [Supabase](https://supabase.com) project ops. One file, stdlib Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

> Rainshift-specific Rayna ops (members lookup, comms tail, stuck-onboarding) live in the rainshift repo under `ops/rayna` and shell out to `sql-inline` here for the heavy lifting. This plugin stays generic.

## Why

The `supabase` CLI is fine, but it optimises for humans at a REPL. Agents hit the same rough edges every time — this wrapper collapses each into one call.

**The agent-envelope gotcha.** `supabase db query` detects when it's being run by an agent and wraps results in a JSON envelope with an **"untrusted data" preamble**. If Claude doesn't know about that preamble, it parses the response as a raw result and produces garbage. `supabase-remote sql` / `sql-inline` strips the envelope server-side so downstream pipelines see plain JSON.

**Other wins**

- `rls-audit` → **1 call**, every table in the public schema, RLS enabled/disabled + policy count per table. Without it: loop over `information_schema.tables`, join to `pg_policies`, per-table. Turns "is anything dangerously exposed?" from an investigation into a boolean.
- `sql seed.sql --verify-rows 12` → assert row count in the same call that applies the file. Catches partial applies without a follow-up `select count(*)`.
- `gen-types packages/db/types.ts` — wraps the multi-flag `supabase gen types typescript --project-id ... --schema ...` dance into one command.
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
