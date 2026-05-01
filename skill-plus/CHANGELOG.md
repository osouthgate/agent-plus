# skill-plus — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.5.0 - 2026-05-01

### Added
- **Transcripts as a first-class source class** alongside `cli`, `plugin`, `web`, `openapi`, `repo`. Auto-discovered from `~/.claude/projects/`, `~/.gstack/projects/`, `~/.codex/sessions/`, `~/.cursor/chats/`. Extend via `~/.agent-plus/inquire-sources.json` + user-supplied adapters at `~/.agent-plus/inquire-adapters/<name>.py`. Symlinks in the adapter dir are skipped.
- **Two-tier clustering.** Tier 1 = `(verb, sorted(tables))` shape fingerprint (threshold: count >= 3). Tier 2 = `(select_cols, where_cols)` column fingerprint within parent (threshold: count >= 2). Generic SQL-grammar normalisation only -- no per-tool regex.
- **Type A/B/C promotion classifier.** A = Missing (no canned command, heavy raw use). B = Misaligned (canned exists but shape differs). C = Aligned (canned covers it correctly). Determined by diffing clusters against the target plugin or skill's existing subcommands.
- **Friction-ranked priority.** Capability gap + heavy usage = `high`; light usage = `low`. New verdict state `well_used` for targets with no gaps where canned commands are actually being used.
- **Skill-as-target support.** `--audit` now resolves against SKILL.md frontmatter (in addition to `plugin.json`). Subcommand bin auto-resolved from `Bash(<name>:*)` allowed-tools entry. Both plugin and skill kinds are supported.
- **`--no-transcripts` flag** (also `AGENT_PLUS_INQUIRE_NO_TRANSCRIPTS=1` env var) to skip transcript sourcing entirely.
- **ENVELOPE_VERSION 1.1** (additive only -- v1.0 consumers unaffected). New fields: `usage_signal`, `usage_clusters`, `promotions`, optional per-Q `usage_evidence` / `promotion_kind` / `priority`.
- **Security:** raw command-string tuples are never persisted to envelope or cache -- clustering is in-memory only.
- **67 new tests** across clustering, transcript adapter, envelope shape, priority calc, skill-kind detection. Total: 263 passing, 1 skipped.

### Notes
- Real-data dogfood: 13 Tier 1 shapes discovered across 27,026 tuples from 238 jsonl files. Cluster reference fixture at `skill-plus/test/fixtures/loamdb_db_clusters_reference.json` (structured shapes only -- no raw command strings committed).
- Type B/C absent in current dogfood because A.2 discovery doesn't yet introspect argparse subcommands inside single-file CLIs. Noted as a follow-up.

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
