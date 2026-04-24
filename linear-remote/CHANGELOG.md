# linear-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.1.0 - 2026-04-24

Initial release.

### Added
- `issues get <id-or-query>` — one-call issue context with optional `--include-comments` and `--include-relations` flags. Accepts UUID, human key (`LOA-229`), or free-text (searched).
- `issues list` with `--project`, `--state`, `--assignee`, `--team-filter`, `--label` filters. Cursor pagination (`--limit` default 25, max 100, `--cursor <endCursor>` for next page). Response includes `pageInfo` for agent-driven pagination.
- `issues search <query>` — quick title search.
- `issues create --title <t> --team <name>` with optional `--project`, `--body` (accepts `@path` for file input), `--labels`, `--priority 0-4`, `--assignee`.
- `issues create --from-markdown <path>` — turns a design doc into a Linear issue. YAML frontmatter keys: `team`, `project`, `labels`, `assignee`, `priority`, `title`. First `# H1` becomes title; rest becomes body. Frontmatter keys are optional; `--team` flag overrides frontmatter.
- `issues update <id>` — partial update for title / state / assignee / labels / priority / project.
- `issues move <id> <state-name>` with `--wait` / `--timeout` for webhook-driven transitions (default 60s, 3s poll). Partial JSON on timeout, non-zero exit.
- `issues assign <id> <assignee>` — assignee by email / name / UUID.
- `comments add <issue-id> <body>` (body accepts `@path` for file input) and `comments list <issue-id>`.
- `projects list` and `projects overview <name>` — state-bucketed issues (backlog / todo / inProgress / inReview / done / canceled), milestones, recent activity, completion %. Per-bucket cap default 25, max 100.
- `teams list`, `states <team-name>`, `labels [--team-filter ...]`, `cycles <team-name>` — enum enumeration for name-resolution preview.
- **ID format flexibility.** Every issue argument accepts human key (`LOA-229`) or UUID. Human keys resolved via `issue(id:)` which accepts either form. Central `_resolve_issue_id` helper for free-text fallback.
- **Name resolution plumbed centrally** in `resolve_team` / `resolve_state` / `resolve_labels` / `resolve_user` / `resolve_project`. Per-process caches keyed on `(api_key, team_id)` so `update + move` doesn't re-query enums. Ambiguity fails with sorted candidates.
- **`_normalise_markdown()`** strips YAML frontmatter and HTML comments (Linear renders `<!-- -->` as visible text), preserves `TEAM-N` autolinks and task lists. Applied to every description and comment body on both read and write paths.
- **Auth header is `Authorization: <api-key>` — NO `Bearer` prefix.** Linear's personal API key quirk handled centrally in `_gql()`.
- **`_scrub()` response filter** strips `apiKey`, `token`, `secret`, `webhookSecret`, `password`, `accessToken`, `refreshToken` from any API response before emission. Walks nested dicts/lists.
- **Layered `.env` autoload.** `--env-file` > project `.env.local` / `.env` walked up from cwd > shell env. Only `LINEAR_*` prefixed vars are picked up.
- **Error message contract.** Missing key / 401 / 403 / GraphQL field-level / 429 each emit problem + cause + fix + link. GraphQL errors surface `path` and `message` verbatim.
- **`--wait` contract** scoped to webhook-driven state transitions (PR → auto-move). Instant mutations return immediately. Per-command worked example in `--help`.

### Safety
- Read-only by default. Writes (`issues create`, `issues update`, `issues move`, `issues assign`, `comments add`) are explicit subcommands.
- Deliberate out-of-scope: cycle / milestone CRUD, initiative CRUD, team / workspace management, custom field mutations, webhook configuration, cross-issue relation mutations (`relate`, deferred to v2), OAuth.
