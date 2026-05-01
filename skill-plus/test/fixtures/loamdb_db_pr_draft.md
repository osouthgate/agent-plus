# loamdb-db SKILL.md — paste-ready PR draft

Generated from a real `skill-plus inquire --audit` run against
`C:\dev\brockenhurst\osdb\.claude\skills\loamdb-db` on 2026-05-01.

- Envelope: 1.1
- Transcripts scanned: 240 (2026-03-31 .. 2026-05-01)
- Tier-1 invocations: 120
- SQL statements seen: 456 (parse_failures: 336 — mostly DDL/INSERT/non-SELECT noise)
- Unique tier-1 shapes: 13
- Unique tier-2 clusters: 21
- Promotions emitted: 21 (9 high, 12 medium)
- Promotion-kind distribution: 21 missing / 0 misaligned / 0 aligned

> Caveat: A.2 discovery enumerates files under `bin/`, not argparse subcommands
> inside a single-file CLI. loamdb-db is one Python file dispatching its own
> subcommands, so every cluster registers as "missing" relative to a one-entry
> existing-subcommand list. Type B/C analysis for single-binary skills is a
> follow-up (see closing notes).

---

## Type A (Missing) — High priority

### A1. `describe <table>` — schema introspection

- Tier-1 shape: `select: columns` (13 hits) + `select: tables` (4 hits) = 17 hits
- Where columns: `table_name` (5 hits), `table_name + table_schema` (3 hits), `table_schema` (2 hits)
- Sample query:
  ```sql
  SELECT column_name FROM information_schema.columns
  WHERE table_name = 'contents' ORDER BY ordinal_position
  ```
- Recommended subcommand: `loamdb-db describe <table> [--schema public]`
  - Returns columns, types, nullability, defaults
  - Optional flag `--tables` (or sibling `tables` subcommand) for schema-wide list
- SKILL.md `allowed-tools`: no change (already `Bash(.claude/skills/loamdb-db/bin/loamdb-db:*)`)
- SKILL.md doc block to add under "## Commands":
  ```bash
  # Describe a table — columns / types / nullability
  .claude/skills/loamdb-db/bin/loamdb-db --env dev describe contents
  .claude/skills/loamdb-db/bin/loamdb-db --env prod describe contents --schema public

  # List tables in a schema
  .claude/skills/loamdb-db/bin/loamdb-db --env dev tables --schema public
  ```

### A2. `connector <id>` — connector-connection lookup

- Tier-1 shape: `select: connector_connections` (10 hits)
- Where columns: `connection_id` (2 hits captured; broader pattern is `connector_id`)
- Sample query:
  ```sql
  SELECT id, organization_id, connector_id, enabled, created_at, updated_at
  FROM connector_connections WHERE connector_id = 'gmail'
  ORDER BY updated_at DESC LIMIT 5
  ```
- Recommended subcommand: `loamdb-db connector <connector_id> [--org <id>] [--enabled-only]`
  - Returns recent rows for a connector (gmail / slack / etc.) with last sweep + status
- SKILL.md doc block:
  ```bash
  # Connector coverage — recent connections for a connector type
  .claude/skills/loamdb-db/bin/loamdb-db --env dev connector gmail
  .claude/skills/loamdb-db/bin/loamdb-db --env prod connector slack --org oso --enabled-only
  ```

### A3. `chunks <content_id>` — chunk-set / embedding inspection

- Tier-1 shapes: `select: chunk_sets,content_chunks` (8 hits) +
  `select: chunk_sets,content_chunks,contents` (8 hits) = 16 hits
- Where columns: `content_id` (5 hits), `organization_id + text_vector` (2 hits)
- Sample query:
  ```sql
  SELECT cs.content_id, cc.id AS chunk_id,
    (cc.embedding IS NULL) AS emb_null,
    (cc.text_vector IS NULL) AS tsv_null,
    cc.index_status, cc.lifecycle, LEFT(cc.text, 80)
  FROM chunk_sets cs JOIN content_chunks cc ON cc.chunk_set_id = cs.id
  WHERE cs.content_id = '...'
  ```
- Recommended subcommand:
  `loamdb-db chunks <content_id> [--missing-embedding] [--with-text-preview]`
- SKILL.md doc block:
  ```bash
  # Chunk-level inspection for a content row — embeddings + tsv coverage
  .claude/skills/loamdb-db/bin/loamdb-db --env dev chunks <content_id>
  .claude/skills/loamdb-db/bin/loamdb-db --env prod chunks <content_id> --missing-embedding
  ```

---

## Type A (Missing) — Medium priority

### A4. `pg-extensions` — Postgres extension probe

- Tier-1 shape: `select: pg_extension` (4 hits)
- Where columns: `extname` (4 hits)
- Sample query:
  ```sql
  SELECT extname, extversion FROM pg_extension WHERE extname='vector';
  ```
- Recommended: `loamdb-db extensions [<name>]` — returns extname/extversion;
  default lists the canonical set (pgvector, pg_trgm, uuid-ossp, etc.)
- Light enough that it could fold into `health` instead of being its own
  subcommand. Suggest: extend `health --pretty` to print extension list.

### A5. `migrations` — drizzle migration history

- Tier-1 shape: `select: __drizzle_migrations` (3 hits)
- Sample query:
  ```sql
  SELECT hash, created_at FROM drizzle.__drizzle_migrations
  ORDER BY created_at DESC LIMIT 5
  ```
- Recommended: `loamdb-db migrations [--limit N]` — N most recent rows from
  `drizzle.__drizzle_migrations`. Useful for "did the migration land in prod?"

### A6. `pg-tables-like <pattern>` — table-name pattern search

- Tier-1 shape: `select: pg_tables` (6 hits, 3 tier2 clusters)
- Where columns: `schemaname + tablename` (2 hits), `tablename` (2 hits),
  `schemaname` (2 hits)
- Sample query:
  ```sql
  SELECT tablename FROM pg_tables WHERE schemaname='public'
    AND (tablename LIKE 'connector%' OR tablename LIKE 'sync%')
  ```
- Recommended: extend `tables` (from A1) with `--like <pattern>` rather than
  a separate subcommand. Keeps surface small.

---

## Type B (Misaligned) — none surfaced this run

A.2's discovery treats `bin/loamdb-db` as one subcommand because the file is
a single argparse dispatcher. Until per-subcommand introspection lands, no
existing canned (`health`, `org`, `ingestion`, `compare`, `archived`, `select`)
can register as Type B. Manually, the strong candidates from this transcript
set would have been:

- **`org` extension** — many `select: contents WHERE organization_id` shapes
  (40 tier-1 hits) cluster under different where/select combinations the
  current `org <id>` summary doesn't break out (lifecycle filter, source_uri
  selection). Worth adding `org <id> --include contents-by-lifecycle` or
  similar.
- **`ingestion` extension** — `select: sync_runs` (6 hits) appears as its own
  cluster with status/started_at projections — mirrors what `ingestion <id>`
  already does, suggesting a `--all-orgs` mode or richer columns would
  consolidate manual queries.

These are noted for the human reviewer; the audit run did not classify them.

---

## Type C (Aligned) — none surfaced

Same root cause as Type B.

---

## Surprises / data-quality notes

- `parse_failures: 336` out of 456 SQL statements seen — investigate whether
  `extract_sql` is over-matching (DDL, COPY, multi-statement strings, comments)
  or whether the corpus genuinely has that much non-SELECT noise. Either way,
  signal-to-noise on tier-1 clustering is healthy: 120 successful clustered
  invocations across 13 shapes.
- `select: contents` is the dominant shape (40 hits, 6 promotions). The
  algorithm split it into 6 tier-2 clusters by where/select column tuples;
  consider raising tier-2 minimum count or merging clusters that share the
  same where-cols set.
- `select: entities` (3 hits) registered as a tier-1 shape but produced 0
  tier-2 clusters (no where-columns extracted) — entity-type filter
  prediction is partially confirmed (shape detected, columns not).

---

## Follow-up for skill-plus itself (out of scope for this PR)

- Per-subcommand introspection inside single-file argparse CLIs (would
  unlock Type B/C for loamdb-db and similar dispatcher-style skills).
- Investigate `extract_sql` parse-failure rate (336/456 = 73% on the
  loamdb-db transcript set).
