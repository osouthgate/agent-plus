# skill-feedback — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## Unreleased

## 0.5.0 - 2026-05-02

### Added
- **`nextSteps[]` in output envelope.** Per-command follow-up hints injected into every response: `log` → `report`; `report` → `submit`; `show` → `log`. Lets Claude chain the feedback lifecycle automatically.
- **Stop hook template in SKILL.md.** Documents how to configure a `.claude/settings.json` Stop hook so Claude is reminded to log skill feedback at the end of every session without relying on memory. Includes a smarter variant that reads installed skills by name. Controlled by `SKILL_FEEDBACK_QUIET=1` env var.
- **`when_to_use` trigger phrases.** Added concrete confirmation phrases to the SKILL.md frontmatter: "that worked well", "that skill was useful", "this skill is broken", "rate that last skill", "log feedback for X". Concrete phrases improve Claude's skill-dispatch reliability over vague behavioral descriptions.

## 0.4.0 - 2026-05-01

Provenance-aware advisor + submit. Slots into the v0.15.5 framework release.

### Added
- **`skill-feedback feedback <name>` subcommand** — provenance-aware advisor that calls `skill-plus where <name>` to detect tier and emits a tier-appropriate `recommended_action`:
  - **project / global tier** → `kind: "edit"` with the absolute SKILL.md path. The skill is user-authored; there's no upstream marketplace to file an issue against. The recommendation is to edit the SKILL.md directly using the recent feedback log as evidence.
  - **plugin tier (marketplace)** → `kind: "submit"` with a copy-pasteable `skill-feedback submit <name>` command. The marketplace `repository` is auto-resolved from the cache plugin's `plugin.json`.
  - **ambiguous (collision across tiers)** → `kind: "resolve_collision"` with `skill-plus collisions` as the next step.
  - **unknown** → graceful no-action with install hint when skill-plus isn't on PATH.
- **`_detect_skill_provenance(name)` helper** — single-source provenance detection. Subprocesses out to `skill-plus where`, parses its JSON envelope, and returns `{tier, locations, primary_path, marketplace_repo, edit_hint, collision, skill_plus_available, error}`. Used by `feedback`, `submit`, and `report`.
- **`report` envelope now carries `provenance`** when scoped to a single skill (`--skill <name>`), so consumers don't need a second call to know whether to recommend edit vs submit. Best-effort — falls through silently when skill-plus is absent.

### Changed
- **`submit` is now provenance-aware (additive — `--repo` contract unchanged).** When `--repo` is omitted:
  - `tier=plugin` → uses the auto-detected marketplace `repository` (replaces the legacy `_resolve_repo_from_plugin` probe for this tier).
  - `tier=project` or `tier=global` → REFUSES with `result.error` plus `result.edit_hint` pointing at the SKILL.md. Old behaviour was a confusing "no plugin.json found" path.
  - `tier=ambiguous` → REFUSES with the locations list and a hint to use `skill-plus collisions`.
  - `tier=unknown` (skill-plus not installed, or skill not found anywhere) → falls back to the v0.3.0 `_resolve_repo_from_plugin` probe, preserving pre-0.4.0 behaviour for users who don't install skill-plus.
- **Behaviour change**: a `submit` invocation against a project-tier or global-tier skill without `--repo` is now a hard refusal (with an actionable hint), where v0.3.0 would either silently succeed against an arbitrary `plugin.json#repository` it found, or land at "no repo resolved." This is a non-breaking change for the documented happy paths (marketplace skill + `--repo override + plugin.json with repository field).

### Tests
- 6 new tests in `TestProvenanceAwareFeedback` and `TestSubmitProvenanceAware`:
  - `test_feedback_subcommand_project_tier` — mocked project provenance → `recommended_action.kind == "edit"`.
  - `test_feedback_subcommand_plugin_tier` — mocked plugin provenance + plugin.json on disk → marketplace repo resolved, `kind == "submit"`.
  - `test_feedback_subcommand_ambiguous_tier` — mocked collision → `kind == "resolve_collision"`.
  - `test_feedback_subcommand_unknown_tier_skill_plus_unavailable` — `shutil.which("skill-plus") → None` → `kind == "no_action"` with install hint.
  - `test_submit_refuses_on_project_skill_with_edit_hint` — project provenance + no `--repo` → refusal with `edit_hint`, no gh call.
  - `test_submit_uses_provenance_marketplace_repo_when_no_explicit_repo` — plugin provenance + no `--repo` → repo auto-resolved.
- Test suite: 44 → 50.

### Cross-platform
- Helper uses `subprocess.run([list], ...)` (no `shell=True`), `pathlib`, and utf-8 throughout. Works identically on Windows + macOS + Linux. Tests use `Path.parent` comparisons (not string `startswith`) to handle path separator normalisation on Windows.

## 0.3.0 - 2026-04-28

`--version` output normalised to `<name> <semver>` shape.

### Changed
- **`--version` shape:** now prints `skill-feedback X.Y.Z` instead of bare `X.Y.Z`. Aligns with the rest of the framework + marketplace plugins.

## 0.2.0 - 2026-04-28

Coordinated framework-plugin envelope-contract bump (Track A slice A0).

### Changed
- **Envelope field rename: `savedTo` → `payloadPath`.** Coordinated rename across the four framework plugins (`agent-plus`, `repo-analyze`, `diff-summary`, `skill-feedback`) so the `--output` envelope field reads as a payload pointer rather than a transient verb. Pre-1.0 breaking surface change, hence the minor bump per the project README's stability clause. skill-feedback itself does not currently emit `savedTo`; this version bump keeps the framework plugins moving in lockstep on the shared envelope contract. [2026-04-28]

### Added (round 3 — defence-in-depth at submit)
- **Agent privacy-review responsibility at `submit` time.** Regex scrub catches token shapes; it cannot catch PII, customer names, internal hostnames, or contextual leaks. SKILL.md now has a "Privacy review before submit" section telling the Claude agent already executing the skill to scan the dry-run body against an explicit checklist (real names, customer/employer identifiers, internal URLs, ticket IDs, error messages quoting internal data, etc.) and to ABORT `--no-dry-run` if anything is sensitive. No extra API call, no SDK dep — the existing Claude Code session does the review using its own context. [2026-04-27]
- **`submit --dry-run` JSON now exposes `agent_review_required: true`** plus `agent_review_instructions` and a 6-item `agent_review_checklist` covering the categories regex can't pattern-match. The fields appear on dry-run only — once `--no-dry-run` runs, the review should already have happened. The agent reads the result as JSON, so the responsibility lands directly in the consuming surface; this is documentation, not enforcement (no flag to require, no flag to bypass). [2026-04-27]
- **README "Privacy by construction" gained a sibling bullet** ("Defence-in-depth at submit time") describing the second layer so users evaluating the plugin see the full privacy story, not just the regex layer. [2026-04-27]
- **Tests** for the new behaviour: `agent_review_required` is True on dry-run with entries, True on dry-run with zero entries (preview should still teach the habit), and absent on `--no-dry-run` paths. Suite: 41 → 44.

### Fixed (round 2 — external review)
- **`_filter_since` no longer keeps malformed-timestamp entries.** Predicate was `if ts is None or ts >= cutoff`, which kept unparseable rows in EVERY since-window — silently skewing aggregates and `submit` bodies whenever a hand-edited line slipped in. Now drops them. [2026-04-27]
- **`_parse_iso` coerces naive datetimes to UTC instead of returning naive.** Previously, an entry with `"ts":"2026-04-26T21:30:00"` (no `Z`/offset) parsed as a naive datetime; `_filter_since` then crashed with `TypeError: can't compare offset-naive and offset-aware datetimes`, and `report`/`show` were unusable until the user hand-edited the file. Naive inputs now get `tzinfo=UTC` attached. Also guards `None`/empty input. [2026-04-27]
- **`cmd_submit --no-dry-run` refuses to file an empty issue.** With 0 entries and a resolvable repo, the previous code would shell out to `gh issue create` with a "No skill-feedback entries…" body. Now bails with a `result.error` explaining how to widen `--since` or use `show`. [2026-04-27]
- **`_resolve_repo_from_plugin` no longer falls back to `homepage`.** Skill authors sometimes set `homepage` to a personal site; the fallback could silently route `submit` issues to the wrong repo. Only `repository` is honoured now. [2026-04-27]
- **Skill-name regex now requires alphanumeric AT BOTH ENDS.** Previously `foo.` was accepted, producing `foo..jsonl` on disk — looks like a path-traversal artifact. New regex `^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$` plus the existing `..`-substring rejection. Single-char alphanumeric names still allowed. [2026-04-27]
- **Mangled comment block fixed in `.claude/hooks/check-skill-feedback.sh:23-30`** — adjacent bullets had been spliced into one another. [2026-04-27]
- **README's "Privacy by construction" bullet** still listed the v0.1.0 scrub set (no `sk-ant-`, no Slack); now matches the SKILL.md and CHANGELOG. AGENTS.md flags this exact drift class as the repo's #1 bug source. [2026-04-27]
- **POSIX append-atomicity comment in `cmd_log`** softened from "concurrent agents won't interleave" to "best-effort, not guaranteed on NFS." A 1000-char `--note` plus 1000-char `--friction` produces a JSON line in the ~2.2KB range with `ensure_ascii=False`; that fits in a single `write()` on Linux (`PIPE_BUF=4096`) and APFS in practice, but exceeds POSIX's 512-byte minimum guarantee and can interleave on networked filesystems. [2026-04-27]

### Fixed (round 1 — self-review)
- **Repo-resolution regex now handles dots in repo names and `.git` suffix.** Previously `_resolve_repo_from_plugin` parsed `https://github.com/foo/some.lib` as `foo/some` and `https://github.com/foo/bar.git` as `foo/bar.git` (no strip). New regex extracts `foo/some.lib` and `foo/bar` correctly. Without this, `submit` could file an issue against the wrong repo. [2026-04-27]
- **Skill-name whitelist now rejects `..` and leading dots.** The old `[A-Za-z0-9._-]+` regex accepted `..` (it's just two dots). Tightened to require an alphanumeric first character and reject any `..` substring, so `..`, `..foo`, `foo..bar` all fail. Caught by a new test, not exploited in any release. [2026-04-27]
- **README "Wiring it into a skill" section** used a triple-backtick `markdown` fence that closed prematurely on the inner bash example, leaking prose out of the code block when rendered on GitHub. Switched to a quad-backtick outer fence. [2026-04-27]

### Added
- **Test suite** at `test/test_skill_feedback.py` grew to 41 stdlib-unittest cases (round 1: 21, round 2: +20). New round-2 coverage:
  - `TestParseIso` — aware/naive/None/empty/garbage inputs all return tz-aware datetimes or None; never raise.
  - `TestFilterSinceCorrectness` — malformed-ts entries are dropped by `--since`, naive ts doesn't crash `show`, no-since returns everything.
  - `TestSubmitEmptyGuard` — `--no-dry-run` with 0 entries does NOT shell out to `gh`; dry-run on 0 entries still prints body without populating `error`.
  - `TestSkillNameTrailing` — `foo.`, `foo-`, `foo_` reject; `a`, `5`, `foo.bar`, `a.b.c` accept.
  - `TestRepoResolutionNoHomepageFallback` — `repository` resolves; `homepage`-only is ignored.
  - `TestLimitZeroSemantics` — `--limit 0` returns all entries (not zero); default (50) returns all 7.
  - Scrub canary expanded to 20 patterns: + Stripe (live/test/restricted/pub), Supabase, Sentry, Google, Discord, JWT.
- **Scrub set extended**: Stripe (`sk_live_/sk_test_/rk_/pk_…`), Supabase (`sbp_…`), Sentry (`sntrys_…`), Google API keys (`AIza…[35]`), Discord bot tokens (3-part dotted base64), JWTs (`eyJ…`). Previous v0.1.0 set was missing all of these — the README/CHANGELOG over-promised "strictly larger scrub set" without earning it. Now earned. Pattern set is documented in `bin/skill-feedback` and the privacy contracts in README.md and SKILL.md.
- **`path` command now reports which precedence rule fired** via a `source: env|git|cwd|home` field, so users aren't surprised when feedback lands in an unrelated git repo's `.agent-plus/`.
- **`--limit 0` documented as "unbounded"** in `--help` for `show` and `report`. `report --limit` help text also notes the slice happens after multi-skill flattening.

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
