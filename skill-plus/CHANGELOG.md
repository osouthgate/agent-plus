# skill-plus — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## Unreleased

### Security
- **`scan`'s redactor now catches `VAR=secret` env-assignments and Netlify `nfp_` tokens.** A real `NETLIFY_AUTH_TOKEN=nfp_<...> bunx netlify-cli` command was mined from session history and persisted to `candidates.jsonl` unredacted: nothing in `_SECRET_PATTERNS` matched the shell env-assignment shape, and `nfp_` was not a known token prefix. Added a name-based env-assignment pattern (`<NAME ending in TOKEN|SECRET|PASSWORD|API_KEY|ACCESS_KEY|PRIVATE_KEY|AUTH>=<value>` -> `NAME=[REDACTED]`, keeping the name readable like the header-name rule) plus an `nfp_` token pattern. `_scrub_record` already rescrubs every record on every rewrite, so the next `scan` also cleans any already-persisted candidate. Synced the same additions into `bin/_subcommands/scaffold.py`'s generated-skill template, which had additionally drifted 7 patterns behind canonical (`gh[ousr]_`, `pk-lf-`, Slack/Discord webhooks, Stripe `(sk|rk|pk)_(live|test)_`, `sbp_`, `sntrys_`) — those are restored too (closes the TODOS.md template-scrubber-drift item). Same additions mirrored into `evals/scripts/bootstrap_fixtures.py`.

### Changed
- **Skill-picker findability.** SKILL.md frontmatter `description` now prefixed with `agent-plus | ` so typing "agent-plus" in Claude Code's skill picker surfaces this skill alongside the rest of the suite.

## 0.7.1 - 2026-07-03

### Fixed
- **`_git_toplevel()` now normalises MSYS-style paths on Windows** (`/c/dev/foo` -> `C:/dev/foo`) in both `bin/skill-plus` and the generated-skill template in `bin/_subcommands/scaffold.py`. `git rev-parse --show-toplevel` under Git Bash/MSYS emits POSIX-style paths, and `Path("/c/dev/foo")` on Windows silently resolves to `C:\c\dev\foo` — so project state (`candidates.jsonl`, `last-scan.txt`) and scaffold targets could land under a phantom `C:\c\...` tree (the exact incident class TODOS.md's "Windows path audit" documents: state written to `C:\c\dev\Tinker-Tailor\.agent-plus`). Uses the same `_msys_to_windows()` helper already shipped in skill-feedback, agent-plus-meta's init hook, and repo-analyze; duplicated locally because bin scripts have no `.py` extension and cannot import each other, and generated skills are self-contained by design (same rationale as the template's `_SECRET_PATTERNS` copy).
- **`feedback`'s stream 1 (explicit ratings) read the dead project-local store.** `bin/_subcommands/feedback.py` still resolved `<project>/.agent-plus/skill-feedback/`, but the skill-feedback plugin has written to the user-global store (`~/.agent-plus/skill-feedback/`) since framework v0.19.4 — so the headline cross-plugin join was reading a directory that no longer receives writes and silently reported no explicit ratings. Stream 1 now resolves exactly the way the skill-feedback CLI does: `SKILL_FEEDBACK_DIR` env override (expanduser + resolve), else `~/.agent-plus/skill-feedback/`. Deliberately does NOT read the project-local dir in addition — a user who hand-merged legacy data into the global store would be double-counted. The `stream1Source` envelope field now reports the resolved global path. Module docstring and README's "feedback — close the loop" section updated to match.

### Tests
- New MSYS-normalisation coverage: helper-level cases in `test_foundation.py` (`/c/dev/foo` -> `C:/dev/foo` on win32, `/home/user/x` and plain `C:/dev/foo` / `C:\dev\foo` unchanged, strict no-op on POSIX) plus a `_git_toplevel()` wiring test (mocked `git rev-parse` stdout `/c/dev/foo` resolves to `Path("C:/dev/foo")`), and `test_scaffold.py` proves the generated `bin/<name>.py` carries the helper and routes its own `_git_toplevel()` through it.
- Stream-1 fixtures (`test_stream1_aggregation`, `test_skill_filter`, `test_since_days_zero_returns_no_entries`) repointed from the project-local dir to the fake home's user-global store; `test_malformed_jsonl_line_tolerated` repointed via `SKILL_FEEDBACK_DIR` so the env-override tier gets coverage too. New regression test `test_stream1_reads_user_global_not_project_local`: a rating in `~/.agent-plus/skill-feedback/` IS aggregated while one in `<project>/.agent-plus/skill-feedback/` is NOT, and `stream1Source` points at the global store.

## 0.7.0 - 2026-07-03

### Added
- **Cadence lens (ported from staging).** `scan` runs a temporal pass over full session history (watermark-bypassed) and writes `routine-candidates.jsonl`. Clusters recurring on >= 5 distinct dates classify as `routine` (tight weekday/3h-bucket signature; gets a deterministic, paste-ready `scheduleString` for Claude Code routines) or `habit` (diffuse timing; gets a skill suggestion instead). New envelope fields `routineCandidates` / `routineCandidatesTop`.
- **`routine` subcommand.** `skill-plus routine --adopt <id> | --dismiss <id>` — boomerang state in `routines-adopted.jsonl`; adopted candidates are suppressed from future scans, dismissed ones de-ranked and annotated.
- **`propose --kind skill|routine|habit|blocks|all`.** Kind-dispatched proposals over the frequency, cadence, and friction lenses.
- **Friction lens (ported from staging).** `scan` mines tool-permission blocks/denials into `blocks.jsonl`; `propose --kind blocks` ranks block clusters.

### Fixed
- **Rescrub-on-write extended to the new logs.** `routine-candidates.jsonl` full rewrites pass every carried-forward record through `scrub_text` (same threat model as the 0.6.1 candidates.jsonl fix), and the append-only `routines-adopted.jsonl` scrubs `clusterKey`/`scheduleString` at append time. Regression tests included.
- Ported test fixtures pin literal correctly-encoded project slugs and real current-time clocks (the private originals used the pre-0.6.1 buggy slug encoder and hardcoded authoring-date timestamps that would silently age out of the scan window).

## 0.6.2 - 2026-07-02

### Fixed
- **Zero-scan slug-mismatch hint no longer fires on empty sibling project dirs.** `scan`'s `hint` field was triggered by the mere existence of other directories under `~/.claude/projects/`, even when those directories held no session `.jsonl` files. The check now only counts sibling project dirs that actually contain session `.jsonl` files, so an empty dir no longer trips a false "likely slug mismatch" hint.

## 0.6.1 - 2026-07-02

v0.19.7 framework hotfix. Two field-reported bugs: session-mining silently found nothing on Windows, and a redaction gap let a live bearer token reach disk.

### Security
- **Redactor was leaking bearer token values -- including Laravel Sanctum `<id>|<hash>` tokens -- into `candidates.jsonl`.** The `Bearer` pattern's charset excluded `|`, so Sanctum-shaped tokens never matched it at all; separately, the `Authorization:` pattern consumed only the scheme word, leaving whatever followed (the actual token) untouched in the scrubbed output. Both `bin/skill-plus` and `bin/_subcommands/scaffold.py` (the scrubber baked into every scaffolded skill's generated `.py`) carried the same gap and are fixed in sync. Hardened: a new sensitive-header-name pattern (`Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key`, `X-Auth-Token`, `Private-Token`, `CF-Access-Client-Secret`, `CF-Access-Client-Id`, `X-Amz-Security-Token`) now consumes the whole `Name: value` span while keeping the header name itself readable; a dedicated Sanctum-shaped `\d+\|[A-Za-z0-9]{20,}` pattern catches the token standalone (e.g. in a URL query string); the `Bearer` pattern widened from a narrow 20+ char charset to any 8+ char non-space/quote token. **Every record already on disk in `candidates.jsonl` is rescrubbed on every `scan` rewrite** -- not just the current run's new/updated examples -- so a token that leaked before this fix doesn't survive indefinitely just because a later scan never touched that record again.

### Fixed
- **Windows/POSIX project-slug encoding in `encoded_cwd_for`.** The previous implementation dashed only `[\\/:]`, collapsed runs of dashes, stripped leading/trailing dashes, then re-prepended `C--` -- for `C:\dev\patchboard` that produced `C--C-dev-patchboard`, a directory that doesn't exist, instead of the real `C--dev-patchboard`. `scan` and `feedback`'s session-mining stream silently reported 0 sessions on every real Windows project, with no error surfaced anywhere. Field-reported. Fixed: every non-alphanumeric character of the resolved cwd is now dashed one-for-one (no collapsing, no stripping, no re-prepending), matching the on-disk format Claude Code actually uses.
- **`scan`'s watermark no longer advances on a 0-session run.** Previously `last-scan.txt` advanced to "now" even when `sessionsScanned == 0` (e.g. because of the slug bug above, or every session predating the cutoff) -- so once the underlying cause was fixed, the *next* scan's cutoff still started from the broken run's timestamp and silently skipped everything before it forever. The watermark now only advances when at least one session was actually parsed this run.

### Added
- **`zeroReason`, `diagnostics`, and (conditional) `hint` fields in the `scan` envelope.** Turns a 0-session run from a silent no-op into something diagnosable: `zeroReason` explains *why* (`project_dir_missing` / `all_before_cutoff` / `filtered_by_caps`, in that precedence order), `diagnostics` always reports the resolved slug + project dir + raw/filtered session counts regardless of `--all-projects`, and `hint` (present only when 0 sessions were found for this project's own slug but other project dirs with history exist under `~/.claude/projects/`) flags a likely slug mismatch and points at `--since-days N` for backfill once the cause is fixed. See README for the full field reference.

## 0.5.0 - 2026-05-02

### Added
- **`nextSteps[]` in output envelope.** Per-command follow-up hints: `scan` → `propose` (with candidate count); `propose` → `scaffold`; `scaffold` → `skill-feedback log` + `promote`; `promote` → `skill-feedback report`. Only injected when `ok` is not explicitly `False` so consent-required and error responses are unaffected.
- **`when_to_use` trigger phrases in SKILL.md.** Added friction phrases: "I've done this three times", "I keep running the same command", "make this repeatable", "I do this every PR". Concrete phrases improve Claude's skill-dispatch reliability over vague behavioral descriptions.

## 0.4.0 - 2026-05-01

### Added
- **`inquire <tool>`** — universal tool inquiry. Probes a tool across the seven framework patterns (Q1 errors_surface, Q2 lookup_keys, Q3 wait_async, Q4 json_output, Q5 stays_in_lane, Q6 strips_secrets, Q7 tool_envelope) using stacked source classes (`cli`, `plugin`, `web`, `openapi`, `repo`). Emits a JSON envelope with per-Q answer, confidence rating (`high` ≥2 sources agree; `medium` 1 authoritative source; `low` web-only; `none` unknown), evidence, and a `recommended_skill` scaffold. Web probe uses DuckDuckGo HTML (D1: stdlib `urllib.request` + `html.parser`; no API key, no third-party deps; 5s timeout, fail-soft to `unknown`).
- **`inquire <plugin> --audit --plugin-path <path>`** — auditor mode. Same probe pipeline, run against an existing agent-plus marketplace plugin. Diffs current state vs achievable, surfaces gaps, places Q1/Q3 on a 4-rung maturity ladder ("Plugin is at Level 1/3 on Q1, recommended Level 2 → 3 because annotations API exists"), and emits a `pr_body_draft` field — paste-ready into `gh pr create` without manual editing. Supports `--cli <name>` to override the binary, `--spec <url>` for OpenAPI (Phase A: skeleton), `--repo <path>` for repo-signal probes (Phase A: skeleton).
- **Probe cache** at `~/.agent-plus/inquire-cache/<tool>.json` (D2: 7-day TTL, one file per tool). Bypass flags: `--no-cache`, `--refresh`, `--clear-cache`. Cache key includes audit mode so generate vs audit results don't masquerade as each other.
- **Cross-platform discipline:** pathlib everywhere, `subprocess.run([list], timeout=...)` form (10s for CLI probes, 5s for web), `MSYS_NO_PATHCONV=1` set on every probe subprocess so Git Bash doesn't rewrite leading-slash args (matches the v0.15.6 F2 fix).
- **45 new tests** in `test_inquire.py` covering each probe in isolation, source stacking + confidence rules, cache hit/miss/expiry/clear, audit envelope shape, `pr_body_draft` content, `na` outcome for non-applicable Qs, maturity-ladder placement, DuckDuckGo HTML parser, and CLI integration. Total: 172 passing.

### Notes
- Phase A scope. Phase B (run audit across the 10 wrappers in `agent-plus-skills`) is a separate slice.
- The inquiry's web probe is one of multiple sources — its job is corroboration, not primary truth. Source-stacking floor is `low` confidence (web alone) so a fresh inquiry with no creds + no CLI still returns actionable answers, never "5 unknowns out of 7."
- `inquire` mention added to `skill-plus --help` (auto-surfaced via argparse subparser registration).

## 0.3.0 - 2026-04-30

### Added
- **`globalize <name>`** — moves `<repo>/.claude/skills/<name>/` to `~/.claude/skills/<name>/`. Default is dry-run; `--no-dry-run` performs. `--keep-local` copies instead of moves. `--force` overwrites the destination. Cross-volume safe via `shutil.move`. Verdicts: `would_move | moved | would_copy | copied | error_source_missing | error_destination_exists | error_no_git_repo | error_invalid_name`.
- **`localize <name>`** — symmetric mirror of `globalize`. Source `~/.claude/skills/<name>/`, destination `<repo>/.claude/skills/<name>/`. Same flags, same verdicts.
- **`where <name>`** — read-only three-tier resolver. Walks `<repo>/.claude/skills/`, `~/.claude/skills/`, and `~/.claude/plugins/cache/**/skills/<name>/` (using each plugin's `.claude-plugin/plugin.json` for plugin name + version when present). Reports every location plus a `resolution_hint` (Claude Code's documented loader preference: `project > global > plugin`). Flags `collision: true` when the skill is defined in more than one tier.
- **`team-sync <name>`** — one-step alias for "share my personal skill with the team via the repo." Equivalent to `localize <name>` plus an emitted `commit_hint` field with a suggested commit message. Does not invoke git — caller decides whether to commit.
- **`collisions`** — detects collisions between project + global scopes and offers renames in four UX modes: interactive prompt (default tty), non-tty bail (emits `verdict: needs_user_input` + `suggested_renames[]` for every collision), explicit `--rename name:scope:new-name` (repeatable), and deterministic `--auto` (project wins, global side gets `-global` suffix). Validates that planned new names are legal (`^[a-zA-Z0-9_-]+$`) and don't collide with anything else. Default is dry-run; `--no-dry-run` performs.
- **40 new tests** across `test_globalize.py` (8), `test_localize.py` (8), `test_where.py` (7), `test_team_sync.py` (6), `test_collisions.py` (11). Total: 127 passing.

### Notes
- Per-subcommand JSON envelopes (not a shared discriminator union) — distinct shapes already established by v0.2.0's `list --include-global` precedent. Each new subcommand emits the standard `tool: {name, version}` wrapper plus `verdict` and `dry_run` keys; errors use `verdict: "error_<reason>"` plus a human-readable `error` field.
- Cross-platform: `pathlib` everywhere, `shutil.move` / `shutil.copytree` for cross-volume safety on Windows, utf-8 file I/O.
- Plugin-cache walk in `where` resolves the marketplace-tier visibility gap that `list --include-global` couldn't surface in v0.2.0.

## 0.2.0 - 2026-04-30

### Added
- **`list --include-global`** — walks `~/.claude/skills/` in addition to `<repo>/.claude/skills/`. Each row carries a `scope: "project" | "global"` tag. Name collisions across scopes are flagged with `collision: true` on every colliding row, and a top-level `collisions[]` lists the names. Powers the v0.12.0 `agent-plus-meta init` wizard's SKILL-AUTHOR branch first-win. Default invocation (`skill-plus list` without the flag) preserves the pre-v0.2.0 envelope shape byte-for-byte — additive change, zero back-compat break. Collision resolution (rename helper, `where`/`globalize`/`localize` subcommands) lands in v0.14.0 per the agent-plus skill-scope-topology plan. 4 new tests, 87 total passing.

## 0.1.0 - 2026-04-30

Initial release. The fifth universal primitive of the agent-plus framework — alongside `agent-plus`, `repo-analyze`, `diff-summary`, `skill-feedback`. Replaces the "I keep typing this same command" → "I should turn this into a skill" gap with one structured mining loop.

### Added
- **`scan`** — single-pass session-log miner. Walks `~/.claude/projects/<encoded-cwd>/*.jsonl`, defensively recurses through tool-use envelopes to find every `Bash` invocation, clusters by first-three-tokens, applies a denylist (`git`, `ls`, `grep`, `cat`, etc — routine noise) plus an allowlist override (anything carrying `--service`, `--env`, `--project`, `--deployment`, or an `mcp__*` tool name keeps through). Threshold defaults: `min_count=3`, `min_sessions=2`. Persists to `<git-toplevel>/.agent-plus/skill-plus/candidates.jsonl` with sha1-keyed dedupe and atomic `.tmp` rewrite. Last-scan watermark in `last-scan.txt` for incremental delta scans.
- **`propose`** — read+rank surface over `candidates.jsonl`. Score = `count + 0.5 * distinct_sessions + recency_boost (max(0, 7 - days_since_lastSeen))`. Augments each row with `proposedSkillName` (slug derived from first non-flag token), `daysSinceLastSeen`, `existing` (does `.claude/skills/<name>/` already exist), and `kind: "new" | "enhance"`.
- **`install-cron`** — cross-platform self-installer for scheduled `scan --accept-consent`. POSIX uses `crontab` with marker-line idempotency; Windows uses `schtasks` with sanitized task name + exit-code-based reinstall detection (locale-independent — no English-only stderr matching). `--print-only` emits the planned entry without writing; `--uninstall` is idempotent. Consent for cron is captured at install time — cron writes only inside `~/.agent-plus/skill-plus/` and the project's `.agent-plus/skill-plus/` tree.
- **`scaffold <name>`** — writes `.claude/skills/<name>/{SKILL.md, bin/<name>, bin/<name>.cmd, bin/<name>.py}` matching the agent-plus framework contract. Non-skippable required slots: `description` (≥10 chars), `when_to_use` (≥10 chars), `## Killer command` (≥5 chars), at least one `## Do NOT use this for` bullet. Slots can be CLI flags or `--from-candidate <id>` to seed the killer command from a mined pattern. Generated `.py` is self-contained, stdlib-only, ships envelope helpers + redactor + layered env resolver (`--env-file` → `<repo>/.env.local` → `<repo>/.env` → `~/.agent-plus/.env` → shell). Refuses to overwrite an existing skill unless `--force`.
- **`list`** — read-only audit of `.claude/skills/*/` against the contract. Hand-rolled stdlib frontmatter parser (no pyyaml). Lenient on key spelling (`when_to_use` / `when-to-use` / `whenToUse` all accepted). Per-skill checks: frontmatter completeness, body sections present (`## Killer command`, `## Do NOT use this for` / `## When NOT to use`, `## Safety rules`), POSIX + Windows launchers present, stdlib-only imports (advisory). Sorted worst-first so the worst-scoring skills surface first.
- **`feedback`** — cross-source aggregator joining (1) `<git-toplevel>/.agent-plus/skill-feedback/<skill>.jsonl` ratings and (2) implicit session-mining failure signals: plugin invocation followed by manual fallback within 10 tool calls, plugin re-invoked within 5 calls, raw command pattern that should have been a plugin invocation but wasn't (discoverability gap). Threshold of 5 invocations to avoid noise. Read-only — never writes either log. Stream 2 gated by consent.
- **`promote <name>`** — moves a project-local skill to a `<user>/agent-plus-skills` marketplace clone. Validates against the contract (frontmatter, `## Killer command`, non-empty `obviates` from frontmatter or body section). Reads + writes the **live marketplace shape** — `{name, owner, version, agent_plus_version, surface, skills: [{name, version, path, obviates}]}` — preserving canonical key order and any unknown top-level keys. Dry-run by default; `--no-dry-run` copies the directory tree, mutates `marketplace.json#skills`, removes source unless `--keep-local`. Refuses if destination already lists a skill of that name.
- **Envelope contract.** Every subcommand emits `tool: {name, version}` top-level. `--output PATH` writes the full payload to disk and returns a compact summary (`payloadPath`, `bytes`, `payloadKeys`, `payloadShape`). `--shape-depth 1|2|3` controls payload-shape recursion (default 3). Matches the `repo-analyze` / `diff-summary` / `skill-feedback` shape exactly.
- **Privacy gates.** Scan refuses without `--accept-consent` (or prior consent grant) unless interactive consent has been recorded; per-project consent persisted to `~/.agent-plus/skill-plus/consent.json`. Cross-project mining (`--all-projects`) opt-in. Secret redaction patterns cover GitHub PATs, AWS, Anthropic, Langfuse, Stripe, OpenAI-style, OpenRouter, Supabase, Sentry, Google, Slack tokens + webhooks, Discord bot tokens + webhooks, JWTs, Bearer, Authorization, connection strings, and `--token`/`--password`/`--secret` argv pairs — applied **before** any candidate is written to disk.
- **83 unit tests** across foundation (envelope shape, storage resolution, secret scrubber, encoded-cwd format), scan (8), propose (8), install-cron (17), scaffold (13), list (8), feedback (7), promote (13).

### Deliberately out of scope (deferred)
- v1 argument normalization (`railway logs --service api --since 5m` collides with `railway logs --since 10m --service api` under v0).
- v2 sequence detection (n-grams of ordered tool calls for wrapper-skill candidates).
- MCP-tool clustering (separate from Bash for now).
- Telemetry sharing — local-only is a feature.
- Interactive consent prompt for first-run scan (currently flag-only via `--accept-consent`; interactive mode lands when `propose` gets its full TUI).

[2026-04-30]
