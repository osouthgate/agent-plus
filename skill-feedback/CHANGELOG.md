# skill-feedback — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## Unreleased

### Fixed
- **Repo-resolution regex now handles dots in repo names and `.git` suffix.** Previously `_resolve_repo_from_plugin` parsed `https://github.com/foo/some.lib` as `foo/some` and `https://github.com/foo/bar.git` as `foo/bar.git` (no strip). New regex extracts `foo/some.lib` and `foo/bar` correctly. Without this, `submit` could file an issue against the wrong repo. [2026-04-27]
- **Skill-name whitelist now rejects `..` and leading dots.** The old `[A-Za-z0-9._-]+` regex accepted `..` (it's just two dots). Tightened to require an alphanumeric first character and reject any `..` substring, so `..`, `..foo`, `foo..bar` all fail. Caught by a new test, not exploited in any release. [2026-04-27]
- **README "Wiring it into a skill" section** used a triple-backtick `markdown` fence that closed prematurely on the inner bash example, leaking prose out of the code block when rendered on GitHub. Switched to a quad-backtick outer fence. [2026-04-27]

### Added
- **Test suite** at `test/test_skill_feedback.py` (stdlib unittest, 21 tests):
  - Scrub canary: every secret pattern (`ghp_…`, `github_pat_…`, `gho_…`, `ghs_…`, `AKIA…`, `sk-ant-…`, `sk-lf-…`, `pk-lf-…`, `sk-…`, `xoxb-/xoxp-…`, `Bearer …`, `Authorization: …`) injected via `--note` and `--friction` is verified absent from the on-disk JSONL, `show`, `report`, and `submit` output paths.
  - Skill-name validation: `..`, `../etc/passwd`, `foo/bar`, null bytes, leading dots, and over-length names all reject with non-zero exit.
  - Storage precedence: `SKILL_FEEDBACK_DIR` > git-toplevel > cwd-marker > home; `path` reports which rule fired.
  - Schema-1 round-trip: `log` then `show` returns the same entry.
  - Repo-URL regex: 9 cases including `.lib` repo names, `.git` suffix, SSH form, query strings.
  - `submit --dry-run` never invokes `gh`; missing-repo path emits `note` field.
- **Anthropic and Slack secret patterns** added to the scrub set: `sk-ant-…`, `xoxb-/xoxa-/xoxp-/xoxr-/xoxs-…`. The previous set was the github-remote subset; this plugin's job is specifically "store text the agent passes in", so it gets a strictly larger scrub set.
- **`path` command now reports which precedence rule fired** via a `source: env|git|cwd|home` field, so users aren't surprised when feedback lands in an unrelated git repo's `.agent-plus/`.

### Changed
- **`cmd_report` aggregations use `collections.Counter`** instead of dict comprehensions calling `.count()` per bucket. Single-pass instead of O(5n).
- **Removed redundant outcome validation** in `cmd_log` — argparse's `choices=` already enforces it.
- **Removed dead `gh_remote = shutil.which("github-remote")` + empty branch** in `cmd_submit`. Plugin will not silently rely on github-remote until that plugin actually exposes issue-create. Updated the no-`gh` fallback message to say only `gh` is checked.
- **Removed redundant `except SystemExit: raise`** in `main`. `SystemExit` inherits `BaseException`, so the broad `except Exception` never swallowed it.

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
