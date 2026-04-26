# skill-feedback — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.1.0 - 2026-04-26

Initial release.

### Added
- `log <skill> --rating 1-5 --outcome success|partial|failure [--friction] [--note] [--tool-version] [--session-id]` — append-only JSONL writer to `<storage-root>/<skill>.jsonl`. Free-text fields are length-capped (1000 chars) and regex-scrubbed for `ghp_…`, `github_pat_…`, `gho_/ghu_/ghs_/ghr_…`, `AKIA…`, `sk-…`, `pk-lf-…`, `Bearer …`, `Authorization: …` patterns before write. `CLAUDE_SESSION_ID` env var is auto-attached when present so the author can correlate session-scoped entries.
- `show <skill> [--since 7d] [--limit 50]` — recent entries for one skill, newest first. `--since` accepts `Ns/m/h/d`.
- `report [--skill <name>] [--since 30d] [--limit 0]` — aggregate-by-skill summary: count, average rating, rating histogram, outcome histogram, top 5 friction strings. One blob; no raw entries unless requested.
- `submit <skill> [--since 30d] [--repo owner/name] [--dry-run|--no-dry-run]` — bundles entries into a markdown GitHub issue body. Dry-run by default — prints body only. `--no-dry-run` shells out to `gh issue create` if available, else writes the body to `<storage-root>/<skill>.submit.md` and prints the manual URL. Repo resolved from `--repo` flag, then the skill's `plugin.json#repository`, then errors. Never makes raw GitHub API calls.
- `path [--skill <name>]` — prints the resolved storage root and (with `--skill`) the per-skill jsonl path.
- Storage precedence: `SKILL_FEEDBACK_DIR` env > `<git-toplevel>/.agent-plus/skill-feedback/` > `<cwd>/.agent-plus/skill-feedback/` > `~/.agent-plus/skill-feedback/`. Resolves once per process.
- Skill-name whitelist (`[A-Za-z0-9._-]+`, max 128 chars) blocks path traversal and keeps jsonl filenames predictable.
- Tool-meta envelope: every JSON payload carries top-level `tool: {name, version}` read from the plugin manifest at runtime, so version drift is visible from the output alone (pattern #6).
- `--version` flag prints the plugin version and exits zero.
- `--pretty` flag indents output for human reading; default is compact JSON for `jq`.

### Safety
- Append-only: no edit / delete subcommand; the agent cannot rewrite past entries through the CLI.
- Free-text scrub runs before disk write — secrets accidentally captured in `--note` or `--friction` never persist.
- `submit` defaults to dry-run; live submission is gated on explicit `--no-dry-run` AND a resolvable repo.
- No transcript ingestion paths; the CLI never reads `~/.claude/projects/...`.

### Deliberate out-of-scope for v1
- Edit / delete entries (use a text editor on the `.jsonl`).
- SaaS upload paths.
- De-duplication or "submitted" markers on entries.
- Retroactive transcript scraping.
- Direct GitHub REST/GraphQL writes — `submit` only goes through `gh`.
