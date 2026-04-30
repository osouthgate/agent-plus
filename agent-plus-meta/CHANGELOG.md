# agent-plus-meta — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.13.0 - 2026-04-30

agent-plus-installer SKILL.md — trigger doctrine for Claude Code on *when* (and when NOT) to offer to install agent-plus on the user's behalf. Pure-markdown skill; the runtime is the existing `install.sh --unattended` one-liner shipped in v0.12.0. Plus: a no-op `--<verb>` dispatcher refactor of `install.sh` that gives v0.13.5 (`--upgrade`) and v0.15.0 (`--uninstall`) a clean plug-in surface.

### Added

- **`agent-plus-meta/skills/agent-plus-installer/SKILL.md`** — nested skill (per repo convention) with five trigger cues in `when_to_use`, each gated by a `command -v agent-plus-meta` probe AND a session-scope decline flag (`AGENT_PLUS_INSTALL_DECLINED`). Killer command is the single `curl … | sh -s -- --unattended` line; the install.sh → `agent-plus-meta init --non-interactive --auto` chain is documented under Architecture (the agent types one thing, not two). Five concrete safety rules (surface-never-auto-execute, per-invocation permission prompt, session-decline, no destructive flags without confirmation, report failures verbatim). Four explicit Do-NOT-use guards (already-installed, declined-this-session, false-positive trigger, sandbox/CI environment). `allowed-tools: Bash(curl:*) Bash(sh:*) Bash(agent-plus-meta:*)` — `Bash(install.sh:*)` deferred to the v0.13.5 upgrade skill.
- **5 new tests** in `agent-plus-meta/test/test_installer_skill.py` covering frontmatter shape, canonical h2 sections, the locked `allowed-tools` regex, and the killer-command URL/flag shape.

### Changed

- **`install.sh` arg parser refactored into a `--<verb>` dispatcher** (~30 lines POSIX shell). VERB defaults to `install` (today's behaviour, byte-for-byte). `--upgrade` / `--uninstall` are recognised verbs that exit 2 with a "ships in v0.13.5 / v0.15.0" stub message. Behaviourally no-op for v0.13.0 — all 9 existing `test_install_script.py` assertions pass without modification. The refactor exists to give v0.13.5 (`--upgrade`) and v0.15.0 (`--uninstall`) a clean plug-in surface; integration is explicit.
- `agent-plus-meta/.claude-plugin/plugin.json` version bumped 0.12.0 → 0.13.0.

### Tests

- 5 new (`test_installer_skill.py`). 9 existing install.sh tests still pass without edits. No new dependencies; stdlib unittest only; pathlib + UTF-8 throughout.

## 0.12.0 - 2026-04-30

Persona-aware onboarding wizard. `agent-plus-meta init` is now interactive: detects user state, picks one of three first-run branches, offers cross-repo session mining, and ends with a coherent doctor verdict. `install.sh` chains into the wizard so `curl|sh` lands the user there immediately.

### Added

- **Persona-aware `init` wizard with state detection.** Detection inspects `~/.claude/projects/` history, `<repo>/.claude/skills/`, env-vars-ready count, presence of `.agent-plus/manifest.json`, and a new `homeless` flag (cwd has no git toplevel + no project markers + at or above home). Three branches with deterministic priority `skill_author > returning > new`: **NEW** runs `repo-analyze`, **RETURNING** runs `agent-plus-meta doctor`, **SKILL-AUTHOR** runs `skill-plus list --include-global`. Re-running is idempotent — legacy `.agent-plus/` bootstrap behaviour preserved.
- **Cross-repo discovery.** Walks `~/.claude/projects/`, decodes subdir names back to repo paths (handles both Windows `C--dev-foo` and POSIX `-Users-bob-foo` encodings), filters dead paths and entries older than 30 days, surfaces top 4 by recency. Selection prompt accepts comma-separated indices, `[a]ll`, `[n]one`, or `[m]anual` for paste-in. Manual paths are validated for existence and warn (but accept) when no markers are detected. Each accepted repo is scanned via `skill-plus scan --all-projects --project <path>` with per-repo progress streamed.
- **Homeless-context handling.** When cwd has no repo context, NEW branch skips the local first-win and pivots to cross-repo discovery first. If `~/.claude/projects/` is also empty, the wizard ends gracefully at doctor.
- **Doctor finale.** Wizard's last step calls `cmd_doctor` in-process and renders pretty output inline. Wrapped in try/except — if doctor itself raises, the wizard prints a fallback hint and continues to envelope emission.
- **`--non-interactive --auto` mode.** Skips all prompts, picks the branch deterministically, scans every auto-discovered repo silently (no manual paste), emits a frozen JSON envelope on stdout, exits 0 even on recoverable errors. For agent-driven installs. Schema documented in [README.md](./README.md#init) — frozen for v0.12.0; additive changes may land in v0.13.x+ without breaking; renames or removals require a major bump.
- **8 stable error codes** in `envelope.errors[].code`: `consent_required`, `cross_repo_scan_failed`, `cross_repo_interrupted`, `stack_detect_unreadable_marker`, `doctor_unreachable`, `skill_plus_missing`, `auto_tie_break`, `install_sh_curl_failed`. Interactive runs print Tier-1 `<problem> - <cause> - <fix>` lines on stderr; `--auto` runs surface them as structured envelope entries.
- **`install.sh --unattended` and `--no-init` flags.** `--unattended` skips prompts and accepts defaults, exits 0 even on partial primitive install. `--no-init` skips the chain into the wizard. Default behaviour after a 5/5 primitive install is to chain into `agent-plus-meta init` (interactive) or `agent-plus-meta init --non-interactive --auto` (under `--unattended`). `--dry-run` short-circuits the chain regardless. Failures surface a `[install_sh_curl_failed]` prefix on stderr.
- **Observability:** each wizard run appends one JSON line to `<workspace>/.agent-plus/init.log` with `branch_chosen`, `detection`, `cross_repo_accepted`, `doctor_verdict`. Useful for "why did init pick this branch" debugging.

### Changed

- `agent-plus-meta init` envelope gains the v0.12.0 frozen-schema fields (`verdict`, `branch_chosen`, `tie_break_reason`, `detection`, `cross_repo_*`, `doctor_verdict`, `doctor_summary`, `first_win_*`, `ttl_total_ms`, `errors`). Legacy fields (`workspace`, `source`, `created`, `skipped`, `suggested_skills`) are preserved at the top level for back-compat.
- `agent-plus-meta` SKILL.md: stale `# agent-plus` H1 corrected to `# agent-plus-meta`; "Three subcommands" claim updated to "Eight+ subcommands across init, envcheck, refresh, list, extensions, marketplace, doctor".

### Companion change in skill-plus

- **`skill-plus list --include-global`** ships in skill-plus 0.2.0 (same release date). Walks `~/.claude/skills/` in addition to `<repo>/.claude/skills/` and flags name collisions across scopes. Used by the wizard's SKILL-AUTHOR branch first-win. Default `skill-plus list` envelope shape unchanged — additive flag, no back-compat break.

### Tests

- 37 new tests in `TestInitWizard` covering all three branches, tie-break, homeless detection, cross-repo discovery (Windows + POSIX path decoders), manual paste validation, Ctrl+C tolerance, doctor failure rescue, and the `--auto` envelope shape. 4 new install.sh tests for `--unattended`, `--no-init`, the auto-chain, and the `--dry-run` short-circuit. 4 new skill-plus tests for `--include-global` (default-off shape, walks-both, collision flagging, empty-global-dir). Total: 303 passing across the framework (138 agent_plus + 28 marketplace_lifecycle + 37 wizard + 87 skill-plus + 9 install + 4 skill-plus include-global already counted in 87).
- Cross-platform verified on Windows, macOS, Linux. `pathlib` everywhere, `shutil.which()` for executable checks, UTF-8 on every file I/O, ASCII-safe stderr fallback for cp1252 consoles.

## 0.11.0 - 2026-04-30

**Breaking — plugin rename.** The meta plugin is now `agent-plus-meta` (previously: `agent-plus`).

The framework, the GitHub repo, the marketplace, the `.agent-plus/` workspace dir, and the `AGENT_PLUS_*` env vars all retain their existing names. Only the plugin formerly known as `agent-plus` is renamed — to resolve the naming collision between the framework and one of its primitives (the audit found this confused new readers).

### Migration

- Reinstall: `claude plugin uninstall agent-plus@agent-plus && claude plugin install agent-plus-meta@agent-plus`.
- CLI invocations: `agent-plus init` → `agent-plus-meta init`. Same for `envcheck`, `refresh`, `list`, `extensions`, `marketplace ...`.
- The envelope `tool.name` field now emits `"agent-plus-meta"` instead of `"agent-plus"`. Downstream consumers reading `tool.name` need to update their match.
- Storage paths are unchanged: `.agent-plus/`, `~/.agent-plus/`, `AGENT_PLUS_*` env vars.

### Changed

- `plugin.json#name`: `agent-plus` → `agent-plus-meta`. Description rewritten to lead with "the meta plugin for the agent-plus framework".
- `bin/agent-plus` → `bin/agent-plus-meta`. `TOOL_NAME` constant + `argparse.prog` updated. All user-facing strings ("run `agent-plus init` first", "Upgrade agent-plus first", error prefix `agent-plus:`) now reference `agent-plus-meta`.
- `skills/agent-plus/SKILL.md` → `skills/agent-plus-meta/SKILL.md`. Frontmatter `name` + `allowed-tools` updated.
- Root `marketplace.json` entry renamed; `source` path updated.
- All tests updated. Envelope-contract suite picks up the new name automatically.

## 0.10.0 - 2026-04-30

Marketplace discovery + preference (Phase 3 of the marketplace convention). Gate 4.

### Added

- **`agent-plus marketplace search [query]`** — shells to `gh search repos --topic agent-plus-skills --json name,owner,description,stargazerCount,updatedAt,url --limit 30`, optionally with a free-text query prepended. Ranks results by `stars + recency_boost` where `recency_boost = max(0, 30 - days_since_update) * 2` (so a freshly-updated 5-star repo can outrank a stale 30-star repo). Refuses cleanly when `gh` isn't on `PATH` (`error: gh_not_installed` with an install hint). Translates non-zero `gh` exits and timeouts into envelope errors (`gh_search_failed` / `gh_search_timeout` / `gh_search_unavailable`) with the last 400 chars of stderr — never raises. Each result carries `slug` (`<owner>/<name>`), `name`, `owner`, `description`, `stars`, `updatedAt`, `url`, and the computed `score`. List-form `subprocess.run` only — the user query is never interpolated into a shell string.
- **`agent-plus marketplace prefer <user>/<repo> --skill <name>`** — records a per-skill marketplace preference in `~/.agent-plus/preferences.json` so when multiple installed marketplaces ship a skill of the same name, `<skill>` resolves unambiguously. `--list` inspects existing preferences; `--clear --skill <name>` removes one. Validates `<user>/<repo>` against `^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$` and `<skill>` against `^[a-z][a-z0-9-]{0,63}$`. Atomic write via `.tmp` → `os.replace`. `agent-plus refresh` now consults the preference when an accepted marketplace collision is detected, surfaces a `collisions: [{skill, candidates, chosen, reason}]` slot in the envelope (only when collisions occur), and falls back to deterministic first-wins (sorted iteration) when no preference exists. Non-colliding handlers behave exactly as before.

## 0.9.1 - 2026-04-29

### Fixed

- **`agent-plus extensions remove` now cleans up stale `services.json` entries.** Previously, removing an extension only updated `extensions.json` — the corresponding entry under `services.json` (populated by an earlier `agent-plus refresh`) lingered, so `agent-plus list` and SessionStart agent context kept showing a service for a plugin the user had just removed. Cleanup happens eagerly on `remove` (best-effort: a malformed or missing `services.json` is not an error). The remove envelope gained a `services_cleaned: bool` field so callers can confirm the cleanup ran. Gate 2 papercut A.

## 0.9.0 - 2026-04-28

`marketplace install / list / update / remove` + the trust model (Phase 2 of the marketplace convention).

### Added

- **`agent-plus marketplace install <user>/agent-plus-skills`** — clones the repo to a temp dir, validates `marketplace.json` against the schema (name, owner-vs-URL, `agent_plus_version` semver-range satisfaction, `surface`, every skill's path + plugin.json name/version match), optionally verifies SHA-256 plugin checksums when declared, resolves the pinned commit SHA, and moves the validated tree to `~/.agent-plus/marketplaces/<owner>-<name>/`. Records install state in `.agent-plus-meta.json` (`pinned_sha`, `installed_at`, `framework_version`, `accepted_first_run: false`). Then fires the **first-run review prompt** showing pinned SHA, plugins (name + version + path), and the union of every plugin's `obviates` list — interactive `[y/N]` on stderr, JSON envelope on stdout. Decline leaves the install in place but un-accepted; until accepted, marketplace plugins refuse to load.
- **`agent-plus marketplace list`** — emits a `marketplaces[]` envelope keyed by owner/name with pinned SHA, install date, plugin count, and `first_run_accepted` flag. Stale or malformed install dirs are surfaced under `warnings[]` rather than failing the command.
- **`agent-plus marketplace update [<user>/<name>]`** — `git fetch`, computes diff (changed files + per-skill added/removed/version-changed), prints to stderr, prompts `Accept update from <old[:8]> to <new[:8]>? [y/N]`. On accept: fast-forwards, updates `pinned_sha`, **re-arms `accepted_first_run: false`** (new code surface = new consent), then fires a re-armed first-run prompt. Without a slug, iterates every installed marketplace and prompts per-one. Refuses `--cron` explicitly with a trust-model message. Blocks (does not prompt) when the upstream `marketplace.json` raises `agent_plus_version` to a level the local framework doesn't satisfy — user upgrades agent-plus first.
- **`agent-plus marketplace remove <user>/<name>`** — interactive confirm, `shutil.rmtree` (with chmod-on-error fallback for Windows git pack files which are read-only). Idempotent: removing a non-installed marketplace returns `status: not-installed` rather than an error.

### Trust gates (all five enforced)

1. **Pin to commit SHA** — recorded at install in `.agent-plus-meta.json:pinned_sha`. Updates are explicit fast-forwards.
2. **First-run review prompt** — once per install, re-armed on update accept. Blocks plugin loading until accepted.
3. **No automatic updates** — `--cron` flag is parsed only so it can be refused. No env-var bypass, no `--non-interactive` mode.
4. **No execution at install time** — install is `git clone` + JSON parse + filesystem move. Nothing in the cloned repo runs. Verified by a test that drops `validate.py` / `post-install.sh` / `scripts/build.py` payloads in the upstream and asserts no marker file is written.
5. **Optional checksum verification** — when `marketplace.json` declares `checksums: {<plugin>: sha256:...}`, install computes a deterministic SHA-256 over each plugin directory's USTAR tar (zeroed mtime/uid/gid/uname/gname, sorted entries). Mismatch aborts the install; the partial clone is discarded with the temp dir.

### Refresh integration

- **`agent-plus refresh` now also walks `~/.agent-plus/marketplaces/`.** Plugins from un-accepted marketplaces are **skipped** rather than executed; the skipped plugin names are surfaced under a new `marketplaces_skipped_unaccepted[]` field in the envelope so the agent can warn the user. Cache discovery (`~/.claude/plugins/cache/`) is unchanged and takes precedence on plugin-name collisions.

### Configuration

- **`AGENT_PLUS_MARKETPLACES_ROOT`** env var overrides the default `~/.agent-plus/marketplaces/` location. Intended for tests; the suite uses it so it never touches a real install.

### Notes

- Stdlib only. Semver-range parser handles `>=`, `>`, `<=`, `<`, `==`, `=`, comma-AND clauses against `MAJOR.MINOR.PATCH` versions. Sufficient for the convention's `agent_plus_version: ">=0.5"` style declarations.
- All prompts read from stdin; EOF defaults to *no* (deny). Output to stderr, JSON envelope to stdout.
- Phase 1 (`marketplace init`) and Phase 2 (this slice) are now both implemented. Phase 4 (`search`, collision-resolution `prefer`) remains future work.

## 0.8.0 - 2026-04-28

`--version` output normalised to `<name> <semver>` shape across all framework plugins.

### Changed
- **`--version` shape:** now prints `agent-plus X.Y.Z` instead of bare `X.Y.Z`. Uniform with the rest of the framework + marketplace plugins, and lets a discovering reader identify the binary from the version line alone. Minor bump for the public-surface text change.

## 0.7.0 - 2026-04-28

`refresh` discovers handlers from plugin manifests instead of a hardcoded dispatch table.

### Changed

- **`agent-plus refresh` now reads `refresh_handler` blocks from each plugin's `.claude-plugin/plugin.json`** instead of dispatching to in-process Python functions hardcoded in `bin/agent-plus`. The framework walks `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/.claude-plugin/plugin.json`, collects every block of shape `{command, timeout_seconds?, identity_keys?, failure_mode?}`, and executes each via `subprocess.run(..., shell=False)`. Plugins without a `refresh_handler` block silently don't participate — no warning, no error. The framework no longer ships any plugin-specific code; the wrapper plugins (which moved to `osouthgate/agent-plus-skills`) are now the source of truth for their own refresh contract. [2026-04-28]
- **`--plugin` no longer has a hardcoded `choices=` list.** It accepts any string and reports an explicit error at run time if no handler is declared for that plugin in the current environment. The error message lists what *is* discoverable so the agent can self-correct.

### Removed

- `_refresh_github`, `_refresh_vercel`, `_refresh_supabase`, `_refresh_railway`, `_refresh_linear`, `_refresh_langfuse` (≈300 lines).
- `REFRESH_HANDLERS` registry dict.

### Notes

- **Behavior change for users with no plugins declaring `refresh_handler`:** `agent-plus refresh` returns `services: {}` rather than the old hardcoded six-plugin set. Plugins in `osouthgate/agent-plus-skills` will declare their handlers in a follow-up release; until then, `refresh` is a no-op for that marketplace. This is the correct behavior — the framework no longer claims to know how to refresh plugins it doesn't ship.
- Failure modes per the new contract: `"soft"` (default) records `status: "error"|"unconfigured"` in `services.<name>` and continues; `"hard"` aborts the whole refresh run with a non-zero exit. Timeouts default to 10s per handler.
- Discovery walks `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/.claude-plugin/plugin.json`. Multi-version caches: highest version wins (natural sort, so `0.10.0 > 0.9.0`). Session-only `--plugin-dir` plugins are out of scope for v1.
- The user-extension surface (`extensions.json`) is unchanged — these are independent paths, both feeding into `services.json`.
- Output envelope shape preserved: same `tool`, `services`, `refreshedAt`, `workspace`, `source` keys. New optional `handler_discovery_errors` array surfaces any malformed plugin.json blocks found during discovery (defensive: discovery never crashes).

## 0.6.0 - 2026-04-28

`init` now suggests matching skills from `osouthgate/agent-plus-skills` based on detected stack markers.

### Added

- **`agent-plus init` stack detection + skill suggestions.** Adds a top-level `suggested_skills` array to `init`'s JSON envelope. Hardcoded marker → suggestion table (no LLM, no fuzzy matching, no network): `vercel.json` / `.vercel/` / Next.js + Vercel deps → `vercel-remote`; `supabase/` (config.toml or dir) → `supabase-remote`; `railway.json` / `.railway/` → `railway-ops`; `.github/workflows/` → `github-remote`; `langfuse.yaml` / `LANGFUSE_PUBLIC_KEY` / langfuse in deps → `langfuse-remote`; `openrouter` in deps / `OPENROUTER_API_KEY` → `openrouter-remote`. Pure filesystem + env-var reads — env *names* only per pattern #5, values never read or echoed. Silent on no match. With `--pretty`, an extra human-readable "Suggested skills" section is rendered on stderr (stdout stays pure JSON). Solves the onboarding-discovery problem: a fresh Vercel project sees `vercel-remote` recommended without the user having to know the marketplace exists. [2026-04-28]

## 0.5.0 - 2026-04-28

`marketplace init` subcommand (Slice 1 of marketplace convention).

### Added

- **`agent-plus marketplace init <user>/<name>`** — scaffold a new marketplace repo locally per the `<user>/agent-plus-skills` convention. Validates `name === "agent-plus-skills"` (v1 reserves the name), refuses non-empty target dirs, writes `marketplace.json` (with `version: "0.1.0"`, `agent_plus_version: ">=0.5"`, `surface: "claude-code"`, empty `skills: []`), `README.md`, MIT `LICENSE`, `.gitignore`, and `CHANGELOG.md`. Runs `git init` if `git` is on PATH (records a `git_note` rather than failing if missing). Prints suggested `gh repo create` + `gh repo edit --add-topic agent-plus-skills` follow-up invocations as `next_steps` — never executes `gh` itself. Optional `--path` overrides the default `<cwd>/<name>/` target. Install / update / list / remove are Phase 2. [2026-04-28]

## 0.4.0 - 2026-04-28

Coordinated framework-plugin envelope-contract bump (Track A slice A0).

### Changed
- **Envelope field rename: `savedTo` → `payloadPath`.** Coordinated rename across the four framework plugins (`agent-plus`, `repo-analyze`, `diff-summary`, `skill-feedback`) so the `--output` envelope field reads as a payload pointer rather than a transient verb. Pre-1.0 breaking surface change, hence the minor bump per the project README's stability clause. agent-plus itself does not currently emit `savedTo`; this version bump keeps the framework plugins moving in lockstep on the shared envelope contract. [2026-04-28]

## 0.3.0 - 2026-04-27

User-defined refresh handlers (B-EXT-1) plus the `extensions` subcommand.

### Added

- **Extensions: user-defined refresh handlers via `extensions.json`.** Drop a script into `<workspace>/extensions.json`, and `agent-plus refresh` aggregates its output into `services.<name>` alongside the built-ins. Each extension's stdout must be a single JSON object with a `status` field (`ok` | `unconfigured` | `partial` | `error`); all other fields pass through verbatim. The orchestrator wraps each output as `{plugin, source: "extension", ...}` so downstream consumers can tell apart user / built-in / migrated handlers. Per-extension `timeout_seconds` (default 30), `enabled` flag, and `description`. Names matching built-in plugins (`github-remote`, etc.) are rejected at load + add time. No `shell=True` ever; argv-style command list only. [2026-04-27]
- **`agent-plus extensions list|validate|add|remove`** — manage `extensions.json` without hand-editing JSON. `add` and `remove` are atomic (write via tempfile + `os.replace`). `validate` dry-runs every extension (name format, command exists on disk, no collisions) without executing scripts. `list` and the meta `agent-plus list` surface `command_hash` (sha256 of argv[0]) rather than the command path — paths often contain usernames, no reason to leak them into agent transcripts. [2026-04-27]
- **`refresh --no-extensions` / `--extensions-only`** — skip extensions for fast/debug refresh, or run only extensions while leaving built-ins alone. Mutually exclusive. `--plugin <name>` implies built-ins-only. [2026-04-27]
- **`agent-plus list`** now includes an `extensions` array with `extensions_count`, alongside the existing `plugins` array. [2026-04-27]

### Notes

- Built-in handlers (github-remote, vercel-remote, supabase-remote, railway-ops, linear-remote, langfuse-remote) stay hardcoded in this slice — not migrated to the extension contract. The contract is intentionally rich enough to accommodate future migration: `_run_extension(ext_config, *, cwd, env)` is forward-compat and does not assume "user-supplied" anywhere in its logic.
- Pattern 5 reinforced: the canary test now also covers the extension surface. Even when the host env contains `GITHUB_TOKEN=CANARY-...`, that string never appears in refresh output unless the extension itself prints it. `stderr_tail` is bounded to 500 chars and contains only what the script emitted on its own stderr.

## 0.2.0 - 2026-04-27

Wider rollout for `refresh` (B-INIT-2) plus a new `list` discoverability subcommand (B-DISCO-1).

### Added

- `refresh` now covers four more plugins beyond the v0.1.0 github + vercel pair: **supabase-remote** (`GET /v1/projects` via Management API), **railway-ops** (shells out to `railway list --json`, mirroring how the plugin itself defers to the local CLI's auth state), **linear-remote** (POST GraphQL `viewer { id name email } teams { ... }` with the raw `Authorization: <key>` header — no Bearer prefix), **langfuse-remote** (Basic-auth `GET /api/public/health` against the default unnamed instance, base URL precedence `LANGFUSE_BASE_URL > LANGFUSE_HOST > https://cloud.langfuse.com`). All four pass the value-leakage canary — names + IDs + URLs only, never tokens. [2026-04-27]
- `agent-plus list [--names-only] [--pretty]` — discoverability subcommand that reads `.claude-plugin/marketplace.json` plus each plugin's `README.md` and emits a single envelope-wrapped JSON blob with name + description + a 400-char headline-commands preview (extracted from the first `## Headline commands` / `## Usage` / similar section, falling back to the first `##` after the title). Cross-references `env-status.json` when present to surface a `ready` flag per plugin. Pattern #1 — one call returns everything an agent needs to pick a plugin. [2026-04-27]
- Internal `_http_request_json(method, url, headers, data=)` helper generalising the existing `_http_get_json` so POST surfaces (Linear GraphQL) reuse one transport instead of duplicating urllib boilerplate. [2026-04-27]
- Forced UTF-8 stdout via `sys.stdout.reconfigure(encoding="utf-8")` so README previews containing em-dashes/arrows don't crash on Windows cp1252 consoles. [2026-04-27]

### Notes

- Plugins still treated as `unconfigured` from envcheck/refresh's POV in this slice: coolify-remote, hcloud-remote, hermes-remote, openrouter-remote, skill-feedback. They have no lightest-cost identity probe wired into `agent-plus refresh` yet — envcheck still reports them, and the per-plugin CLIs are unchanged.



## 0.1.0 - 2026-04-27

Initial release. Workspace bootstrap for the agent-plus collection — solves the cold-start cost where every fresh session re-mines `.env`, plugin dirs, and remote URLs to discover what's installed and what's configured.

### Added

- `agent-plus init [--dir PATH]` — creates `.agent-plus/manifest.json`, `services.json`, `env-status.json` with empty-but-valid JSON. Idempotent: re-runs return `skipped:[…]` for files already present. Workspace dir resolution matches `skill-feedback` exactly (`--dir` → git-toplevel → cwd → home) so both plugins share one `.agent-plus/`. [2026-04-27]
- `agent-plus envcheck [--dir PATH] [--env-file PATH]` — reports which env-var prefixes are set across every known plugin. **Names only, never values** (pattern #5). Per-plugin readiness flag (`required` set + binary on PATH where applicable). Result persisted to `.agent-plus/env-status.json`. Includes a `railway` binary-on-PATH check for `railway-ops` since it defers to the `railway` CLI's own auth. [2026-04-27]
- `agent-plus refresh [--dir PATH] [--plugin NAME]` — resolves the lightest-cost identity endpoints for `github-remote` and `vercel-remote` only (B-INIT-2 partial). For github-remote: parses `git config --get remote.origin.url` and (if a token is present) hits `GET /repos/{owner}/{repo}` for the default branch. For vercel-remote: hits `GET /v9/projects?limit=20` and caches `[{name, id}]`. Tokens never appear in stdout, stderr, or `services.json`. [2026-04-27]
- `--version` flag and `tool: {name, version}` envelope on every output (pattern #6). [2026-04-27]
- `skills/agent-plus/SKILL.md` teaching Claude to call `init` / `envcheck` / `refresh` at session start, plus an explicit "use the per-plugin CLI for actual operations" stay-in-lane clause (pattern #7). [2026-04-27]

### Pain point

Session transcripts showed the same cold-start choreography burning ~67 grep ops + ~60 ls ops per fresh session — re-discovering what `.env` already says, what `git remote -v` already shows, and which plugins are even installed. `agent-plus init` + `refresh` collapses that to two tool calls and writes the result to `.agent-plus/` so the next session reads it off disk.
