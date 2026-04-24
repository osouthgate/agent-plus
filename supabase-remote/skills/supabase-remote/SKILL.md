---
name: supabase-remote
description: Manage a Supabase project from the CLI — list projects, run SQL files (with post-run row-count verification), run inline queries, audit RLS coverage, and generate TypeScript types. Wraps the Supabase Management API + the local `supabase` CLI so agents don't have to hand-parse the untrusted-data envelope.
when_to_use: Trigger on phrases like "run this SQL on Supabase", "apply the seed file", "audit RLS", "which tables don't have RLS", "regenerate types", "list Supabase projects", "which project am I linked to", "resolve project ref".
allowed-tools: Bash(supabase-remote:*) Bash(python3 *supabase-remote*:*)
---

# supabase-remote

Stdlib-only Python 3 CLI wrapping the Supabase Management API + the local `supabase` CLI. One file, no pip installs. Lives at `${CLAUDE_SKILL_DIR}/../../bin/supabase-remote`; the plugin auto-adds `bin/` to PATH.

Generic Supabase ops only. Domain-specific helpers (member lookups, custom comms tails, app-specific onboarding queries) belong in the consuming project's own repo and should shell out to `sql-inline` here for the SQL execution.

## When to reach for this

- User wants to apply a SQL file to a Supabase project and confirm it worked.
- User wants to check RLS coverage before shipping.
- User wants to regenerate the TypeScript types file from the live schema.
- User asks "which project am I linked to" / "list my Supabase projects".
- User wants a one-shot read query against the linked project.

Do NOT use for auth user management, storage buckets, or edge function deploys — out of scope. For those, use the `supabase` CLI directly or the dashboard.

## Configure

Layered config, highest precedence first:

1. `--env-file <path>`
2. `$CWD/.env.local` / `$CWD/.env` (walked up from cwd)
3. `~/.agent-plus/.env`
4. Shell environment (including Claude Code settings)

**Project `.env` files override the shell.** Only `SUPABASE_*` keys are picked up.

```bash
# .env (project-level, preferred)
SUPABASE_ACCESS_TOKEN=sbp_...              # required for any Management API call
SUPABASE_PROJECT_REF=abcdefghijklmnopqrst  # optional default, skips --project
SUPABASE_DB_URL=postgres://...             # optional: if set, SQL commands use psql
```

Get a personal access token at <https://supabase.com/dashboard/account/tokens>. This is distinct from a project's anon/service keys.

## Commands

```bash
supabase-remote projects list [--json]
supabase-remote projects resolve <name-or-ref>

supabase-remote sql <file> [--project NAME] [--verify-rows N] [--json]
supabase-remote sql-inline "<query>" [--project NAME] [--write] [--json]

supabase-remote rls-audit [--project NAME] [--json]

supabase-remote gen-types <target.ts> [--project NAME]
```

Add `--debug` at the top level to print the underlying HTTP and shell calls (access token is scrubbed).

All list-shaped commands support `--json`.

## Projects are resolved by name

`--project` accepts either the project name (case-insensitive substring) or the 20-char ref. No need to copy refs around.

```bash
supabase-remote sql migration.sql --project myproj
supabase-remote sql migration.sql --project abcdefghijklmnopqrst
```

If `--project` is omitted, the CLI uses `SUPABASE_PROJECT_REF`, then falls back to `./supabase/.temp/project-ref` (written by `supabase link`).

## Typical flows

### 1. Apply a seed SQL file with row verification

```bash
# Apply and then assert the last-mutated table has exactly 12 rows.
supabase-remote sql migrations/seed-content.sql \
  --project myproj \
  --verify-rows 12
```

The CLI scans the SQL for the final `insert into <table>` / `update <table>`, runs the file, then re-queries `select count(*) from <table>`. Mismatch → exit 2 with a clear error. No mismatch → prints `verify: content: 12 row(s) — OK`.

Under the hood this calls `supabase db query --linked -f <path> --output json --agent no`, which avoids the "untrusted data" envelope the CLI wraps around output when it detects an agent. If `SUPABASE_DB_URL` is set, it uses `psql` directly instead.

### 2. Audit RLS across all tables

```bash
supabase-remote rls-audit --project myproj
```

```
TABLE                        RLS       POLICIES  STATUS
applications                 enabled   3         OK
brain_calendar               enabled   2         OK
new_secret_table             DISABLED  0         NO RLS, NO POLICIES — FIX
users                        enabled   3         OK
...
```

Queries `pg_tables` + `pg_policies` via `sql-inline` internally. Flags any table in `public` with RLS off, RLS on but no policies, or both. Run this before shipping any new table.

### 3. Regenerate TypeScript types into your project

```bash
supabase-remote gen-types packages/db/types.ts --project myproj
```

Wraps `supabase gen types typescript --project-id <ref>` and writes atomically (via a `.tmp` sibling + `os.replace`). Parent directory must already exist — the CLI refuses to `mkdir` implicitly to avoid dumping a types file in the wrong place.

## Inline SQL safety

`sql-inline` refuses to run a query starting with `INSERT`, `UPDATE`, `DELETE`, `DROP`, `TRUNCATE`, `ALTER`, `CREATE`, `GRANT`, or `REVOKE` unless `--write` is passed. Pattern:

```bash
# Fine — read-only.
supabase-remote sql-inline "select count(*) from events where created_at > now() - interval '1 day'"

# Requires --write.
supabase-remote sql-inline "delete from rsvps where member_id = '...'" --write
```

For anything substantial, prefer `sql <file>` so the query lands in source control.

## Gotchas

- **The "untrusted data" envelope.** `supabase db query` in agent mode wraps output in a JSON envelope with a warning preamble. We pass `--agent no` to turn that off. If a future CLI version ignores that flag, we still scan for `{ "data": [...] }` / `{ "rows": [...] }` shapes and unwrap them.
- **`--verify-rows` won't catch partial writes.** It just asserts the final row count. For stronger guarantees use real migrations (`supabase migration new ...`) and run them through CI.
- **No `psycopg2`, no `requests`.** Stdlib only, so SQL requires either the `supabase` CLI or `psql` on PATH. If neither is available the CLI errors out immediately.
- **Access token scrubbing.** Any stderr/error path with `--debug` on scrubs `$SUPABASE_ACCESS_TOKEN` (and any `sbp_...` token pattern) from the output. Still — don't paste `--debug` transcripts into untrusted places without a second look.

## Troubleshooting

- **`SUPABASE_ACCESS_TOKEN is not set`**: Create a token at <https://supabase.com/dashboard/account/tokens> and drop it in `$CWD/.env` (project) or `~/.claude/settings.json` (global).
- **`No project matching 'xyz'`**: Run `supabase-remote projects list` to see the names your token can see. Names are case-insensitive substring matches; if you have two projects with overlapping names, use the full ref or a more specific substring.
- **`supabase db query failed`**: Usually means the CLI isn't linked to any project, or your `SUPABASE_ACCESS_TOKEN` doesn't have access to the target project. Try `supabase link --project-ref <ref>` first, or set `SUPABASE_DB_URL` to bypass the CLI entirely.
