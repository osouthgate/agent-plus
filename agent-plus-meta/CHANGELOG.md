# agent-plus-meta — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.16.0 - 2026-05-01

Companion bump for the `skill-plus@0.4.0` inquire slice — agent-plus-meta itself unchanged in this release. The framework version bump tracks the inquire pattern landing as a new public surface, per the v0.15.6 keystone-plugin discipline (agent-plus-meta's version IS the umbrella version; other plugins like skill-feedback and the wrappers version independently). The doc-drift gate `check_meta_version_matches_root` from v0.15.6 enforces this invariant.

### Companion changes (skill-plus v0.4.0)

- New `skill-plus inquire <tool>` subcommand: universal skill recommender + auditor. Probes tools across 4 source classes (CLI, plugin, web search, OpenAPI) with cross-source confidence rating. Web probe uses DuckDuckGo HTML via stdlib `urllib.request` + `html.parser` — no API key, no `pip install`, runs from CI.
- New `--audit <plugin> --plugin-path <path>` mode: runs the inquiry against an existing agent-plus marketplace plugin, diffs current state vs achievable maturity, returns a paste-ready `pr_body_draft` envelope field.
- Maturity ladder placement (Q1 errors_surface + Q3 wait_async): each tool gets placed on a 4-rung ladder rather than binary "gap or no gap." Audits read as "Plugin is at Level 2/3, here's Level 3" instead of doom signal.
- Per-tool `MAX_ACHIEVABLE_OVERRIDES` + `_platform_limit_note`: vercel-remote correctly placed at Q1 ceiling=2 because Vercel API doesn't expose source-location records. Audit returns `ok` at the ceiling instead of recommending impossible upgrades.
- 61 new tests in skill-plus (`test_inquire.py`).
- Cache at `~/.agent-plus/inquire-cache/<tool>.json`, 7-day TTL, bypass via `--no-cache`/`--refresh`/`--clear-cache`.

### Companion changes (github-remote v0.5.0, in agent-plus-skills)

- Worked example for the inquire pattern: new `github-remote ci errors <ref>` subcommand using GitHub's check-run annotations API. Hybrid REST+GraphQL (GraphQL for batch when annotation count <40, REST for safety when high; `output.annotations_count` as cheap pre-filter). Outputs structured records (path/line/level/title/message), not log scrapes. Accepts branch/PR#/commit-sha/run-id as `<ref>`. 19 new tests.

### Doc-drift

- README badges synced: `version-0.15.6` → `version-0.16.0`; `tests-419` → `tests-526`. Doc-drift CI gate (including the v0.15.6 keystone-plugin check) green.

## 0.15.6 - 2026-05-01

DX-audit follow-up: closes the two friction points the v0.15.5 live `/devex-review` surfaced. F1 is the version-surface fix (every fresh user hit it within seconds); F2 is the bad-`--dir` error (Git-Bash MSYS path-mangling + raw `WinError 5` instead of three-tier explanation).

### Fixed

- **F1 — `agent-plus-meta --version` now matches the umbrella framework version.** Pre-fix, plugin.json said `0.15.4` while root `VERSION` (read by `upgrade-check`) said `0.15.5`, so a fresh installer would run `--version` and immediately get an "upgrade available!" prompt for a release they had just pulled. Plugin.json bumped to `0.15.6` to match VERSION. New doc-drift gate (`check_meta_version_matches_root`) asserts they stay in sync going forward — agent-plus-meta is the framework's keystone plugin, so its version IS the umbrella version. Other plugins (skill-feedback, hermes-remote, etc.) keep independent versions; the gate only locks meta.
- **F2 — `init --dir <bad-path>` now returns a structured three-tier envelope instead of leaking `WinError 5`.** The old failure mode: `{"error": "[WinError 5] Access is denied: 'C:\\Program Files\\Git\\this'"}` — both unhelpful and misleading (the `C:\Program Files\Git` prefix wasn't even in the user's command; Git Bash MSYS rewrote `/this` silently). New envelope:
  ```json
  {
    "error": "could not create workspace directory",
    "problem": "directory <PATH> is not writable",
    "cause": "Git Bash MSYS may have rewritten the POSIX-style path you passed (\"/foo\") into a Windows path under the Git install prefix. The path the OS actually saw was: <RESOLVED>",
    "fix": "use --dir with a path under your home directory, e.g.: agent-plus-meta init --dir ~/test/foo"
  }
  ```
  MSYS detection fires only when the original arg started with `/` AND resolved under a Git/MSYS prefix. Non-MSYS bad paths (typo, perm denial under writable parent) get a cleaner two-tier message without the false MSYS hint.

## 0.15.5 - 2026-05-01

Companion release for `skill-feedback@0.4.0` — provenance-aware advisor + submit. agent-plus-meta itself is unchanged in this release; the framework version bump tracks the skill-feedback plugin upgrade so users see a single coordinated tag.

### Companion changes (skill-feedback v0.4.0)
- New `skill-feedback feedback <name>` subcommand: provenance-aware advisor that shells out to `skill-plus where` and tells the user the right action per tier (edit project/global SKILL.md vs file an issue against a marketplace plugin vs resolve a collision first).
- `skill-feedback submit` is now provenance-aware (additive — explicit `--repo` flag still works as before). Refuses to file issues for project/global skills with an actionable edit hint; auto-resolves the marketplace repo for plugin-tier skills.
- 6 new tests in skill-feedback (44 → 50). Total framework: 413 → 419.

### Doc-drift
- README badges synced: `version-0.15.4` → `version-0.15.5`; `tests-413` → `tests-419`. The doc-drift CI gate enforces this.

## 0.15.4 - 2026-05-01

Closes the lifecycle-ring "install → healthy" semantic gap that v0.15.2 + v0.15.3 left open. Plus the doc-drift CI gate caught a real one — README badges fell behind through the v0.15.2/v0.15.3 churn.

### Fixed

- **Verdict logic now distinguishes "not yet configured" from "partially configured."** New `envcheck.user_configured_count` field counts only plugins that became ready BECAUSE the user set their required env vars (excludes "trivially ready" plugins like `github-remote`, `railway-ops`, `skill-feedback` that have `required: []` and are always ready). New verdict path:
  - `user_configured_count == 0 && missing_count > 0` → fresh install, **healthy** (the lifecycle-ring win)
  - `user_configured_count > 0 && missing_count > 0` → partial config, **degraded** (real signal worth surfacing)
  - `missing_count == 0` → fully configured, **healthy**
  - error severity OR stale_entries → **degraded** (unchanged)
  - self_broken OR !ws_exists → **broken** (unchanged)
- **`AGENT_PLUS_NO_ENV_FILES=1` env var** to suppress the `_find_env_files` walk-up from cwd. Test-friendly: prevents the maintainer's `~/.env` from leaking into hermetic test envcheck assertions. Documented as test-friendly + available to users who want explicit shell-env-only control.
- **README badges synced.** Doc-drift CI gate caught that `version-0.15.1-green` and `tests-405%20passing` fell behind the actual VERSION (0.15.4) and test count (413). Both updated. The gate worked exactly as designed — failed CI on the v0.15.3 push, surfaced the drift, fix landed in this release. The unfair-advantage move from the outside-opinion review is paying off already.

### Tests

- 2 new tests in `TestDoctorSelfMultiSourceAndVerdict`:
  - `test_verdict_healthy_on_fresh_install_no_user_config` — confirms fresh install with no env vars set → verdict=healthy
  - `test_verdict_degraded_on_partial_user_config` — confirms `LINEAR_API_KEY` set + others unset → verdict=degraded
- Test infrastructure: `_setup_install_and_workspace` (init workspace so doctor doesn't return broken) + `_doctor_with_envfile` (bypasses the shared `_run` helper which merges `os.environ` BACK on top of the cleaned env, defeating the strip — now uses `subprocess.run(env=)` directly for true env replacement).
- agent-plus-meta unittest: 269 → 271. Total framework: 411 → 413 (271 + 15 install.sh + 127 skill-plus).

### Cross-platform

- `AGENT_PLUS_NO_ENV_FILES` checked via `os.environ.get` — works identically on Windows + macOS + Linux. No subprocess shell interpretation.

## 0.15.3 - 2026-05-01

Follow-up to v0.15.2: dogfood gate against v0.15.2 surfaced that the multi-source primitives detection worked correctly (`primitives_source` populated, all values `"install_dir"` as expected) but doctor STILL reported `verdict: degraded` because the **self-check** still used `shutil.which("agent-plus-meta")` only — same blind spot as the primitives check before v0.15.2. On a fresh tarball install where `$INSTALL_DIR` isn't on `$PATH` yet, `self.on_path: false` → warn-severity issue → cosmetic noise (warns don't trigger degraded but appear as red flags in the report). v0.15.3 closes the loop.

### Fixed

- **doctor self-check is now multi-source.** Same 3-path resolution as the primitives check from v0.15.2: `shutil.which → $AGENT_PLUS_INSTALL_DIR → $AGENT_PLUS_PREFIX/<bin>/.claude-plugin/plugin.json`. Records the source in a new `self.on_path_source` envelope field (`"path" | "install_dir" | "prefix" | "missing"`) plus a derived `self.reachable` boolean.
- **Self-check warning is now scope-aware.** Three states:
  - `reachable=false` → severity `warn` (genuine problem — bin can't be invoked by name from any known location)
  - `reachable=true && on_path is None` → severity `info` with hint to add `$INSTALL_DIR` to `$PATH` (reachable, just not in shell convenience yet)
  - `on_path is not None` → no issue
- **Envelope additions.** New fields on `self`: `on_path_source`, `reachable`. Existing `on_path` and `on_path_location` semantics preserved (PATH-specific). Additive — non-breaking.

### Tests

- 2 new tests in `TestDoctorSelfMultiSourceAndVerdict`:
  - `test_self_detected_via_install_dir_no_warn` — confirms wrapper-only install yields `info` not `warn`
  - `test_self_truly_unreachable_emits_warn` — confirms truly-missing self bin still warns
- agent-plus-meta unittest: 267 → 269. Total framework: 409 → 411 (269 + 15 + 127).

### Not changed (verdict semantics preserved)

The verdict logic still treats `envcheck.missing_count > 0` as `degraded`. This is correct: env-var configuration gaps legitimately mean some plugins won't work, and surfacing that as `degraded` is honest and actionable. The "fresh install → healthy" lifecycle-ring claim is satisfied by the v0.15.2 + v0.15.3 multi-source detection fixes (primitives + self), NOT by relaxing envcheck verdict semantics. A draft change to count only "user-configured" plugins (excluding trivially-ready ones with no required env vars) was prototyped + reverted: it was over-reach and the existing semantics are correct.

## 0.15.2 - 2026-05-01

P2 follow-up from the v0.15.1 hotfix's dogfood gate: `agent-plus-meta doctor` correctly reported `degraded` when bins were installed via the new v0.15.1 tarball layout into a `$AGENT_PLUS_INSTALL_DIR` that wasn't on the user's `$PATH` (fresh install before the user adds `~/.local/bin` to PATH; tempdir-overridden test/CI runs; or any non-default install location). The check used only `shutil.which(prim)` which only sees PATH. Net effect: a perfectly correct install reported `primitives: 0/5 installed` because doctor was looking through the wrong window.

### Fixed

- **doctor primitives check is now multi-source.** Three detection paths, returned as `primitives_source[<prim>]: "path" | "install_dir" | "prefix" | "missing"`:
  1. `shutil.which(prim)` — system `$PATH` (covers post-install when user has added `$INSTALL_DIR` to PATH; also covers Claude-plugin-installed primitives).
  2. `$AGENT_PLUS_INSTALL_DIR/<prim>` exists — the wrapper shim, even when `$INSTALL_DIR` isn't on PATH yet (fresh install, tempdir, CI).
  3. `$AGENT_PLUS_PREFIX/<prim>/.claude-plugin/plugin.json` is a file — the v0.15.1 tarball-install plugin tree, when neither PATH nor wrapper detection succeeded.
- **`primitives_source` field added to doctor envelope.** Additive — non-breaking. Visible in `doctor --json` so tooling can surface the resolution path. The `primitives` field's enum is unchanged (`installed | missing`).

### Tests

- 4 new tests in `TestDoctorPrimitivesMultiSource`: `install_dir` detection, `prefix` detection, missing-everywhere, install_dir-precedence-over-prefix. Existing 7 doctor tests all still pass.
- agent-plus-meta unittest: 263 → 267. Total framework: 405 → 409 (267 + 15 + 127).

### Cross-platform

- `pathlib` everywhere; `os.environ.get` for the env vars (no shell interpretation); existing `shutil.which` semantics preserved as path #1.

## 0.15.1 - 2026-04-30

Hotfix: install.sh on public main was fundamentally broken — the per-file `curl` loop downloaded only entrypoint scripts but missed each plugin's `_subcommands/` package directories and `plugin.json`, so every `from _subcommands import ...` ImportError'd at first invocation. Fresh installs reported `--version: unknown` and every wizard / upgrade / uninstall died on import. Plus 5 doc-drift fixes from the outside-opinion review and a doc-drift CI gate so this class of bug can't recur.

### Fixed

- **install.sh — tarball packaging (P0).** Switched from N single-file `curl` calls to one tarball download (`https://github.com/osouthgate/agent-plus/archive/refs/tags/<tag>.tar.gz`, falling back to `main` branch when no tag is set), extract, and copy each plugin's full tree to `$PREFIX/<plugin>/`. Tiny POSIX wrapper shims land in `$INSTALL_DIR/<plugin>` and `exec python3 $PREFIX/<plugin>/bin/<plugin> "$@"`. Result: `_subcommands/` and `plugin.json` ship correctly, every entrypoint imports cleanly, `--version` reports the running build instead of `unknown`. New `AGENT_PLUS_PREFIX` env (default `~/.local/share/agent-plus`); new `AGENT_PLUS_VERSION` to pin a specific tag; new `--source-dir=PATH` test-only flag for hermetic round-trip tests.
- **install.sh test suite expanded.** 12 → 15 tests. Added `test_dryrun_mentions_tarball_url`, `test_dryrun_mentions_prefix_and_install_dir`, and `test_install_sh_round_trip_via_source_dir` (extracts the live staging tree via `--source-dir`, asserts wrappers + trees + `_subcommands/` + `plugin.json` all land correctly). Total framework test count: 399 → 405 (260 + 12 + 127 → 263 + 15 + 127).

### Added

- **`uninstall --auto`** — alias for `--non-interactive`. Restores parity with `init` and `upgrade` (lifecycle ring claim). Does NOT bypass `--purge` confirmation (T6 one-way door is preserved).
- **`uninstall` removes `$PREFIX/<plugin>/` trees too.** New `kind: primitive_tree` in the manifest schema. Default scope now removes both the wrapper at `$INSTALL_DIR/<plugin>` AND the tree at `$PREFIX/<plugin>/` as one logical "primitive install" unit. Additive schema change — existing consumers parsing the envelope continue to work; new consumers can filter on the new `kind`.
- **`uninstall --prefix PATH`** — explicit override mirroring `--install-dir`. Defaults to `AGENT_PLUS_PREFIX` env, then `~/.local/share/agent-plus`.
- **Doc-drift CI gate.** `.github/scripts/doc-drift-check.py` + `.github/workflows/doc-drift.yml`. Asserts: `VERSION` file matches latest annotated git tag; root README badges match `VERSION`; each plugin's `plugin.json#version` is valid semver; the most recent CHANGELOG entry is dated within the last 7 days. Fails CI with line-precise errors. Catches the class of drift this hotfix is patching.
- **Doc-drift content fixes (5 items from outside-opinion review).** Root README badges (`version-0.11.1` → `0.15.1`, `tests-377 passing` → `tests-419 passing`); `agent-plus-meta/README.md` "future v0.13.0 agent-plus-installer skill" → past tense (it shipped); per-plugin versioning explainer added to root README; envelope examples in `agent-plus-meta/README.md` get an explanatory note that `tool.version` reflects the running build (the schema literal `0.12.0` is illustrative); homeless-pivot wizard branch documented in the install section.

### Changed

- `install.sh` requires `tar` in addition to `curl` (POSIX-portable; both are present on every platform we target). Help text refreshed; `print_footer` now mentions both `$PREFIX` and `$INSTALL_DIR`.
- `agent-plus-meta/.claude-plugin/plugin.json` version bumped 0.15.0 → 0.15.1.

### Notes

- Tarball install layout: `$PREFIX/<plugin>/{bin,*.claude-plugin,...}` + `$INSTALL_DIR/<plugin>` (wrapper). `python3 .../bin/<plugin>` works identically on Windows Git Bash, macOS, and Linux — the bin's `_HERE = Path(__file__).resolve().parent` resolves correctly because the bin lives next to its real `_subcommands/`.
- Dogfood verification: a fresh end-to-end round-trip (extract live tree → install via `--source-dir` → wrapper exec'd → `--version` reports `0.15.0`/current — confirming the import path resolves) passed before this entry was written.

## 0.15.0 - 2026-04-30

agent-plus uninstall slice. The framework finally owns its off-ramp end-to-end. Safe-by-default removal (5 bins; nothing else) with opt-in escalation flags, a self-contained `install.sh --uninstall` shell fallback for broken installs, and a frozen JSON envelope schema as public contract. Skips v0.14.0 (a skill-plus-only slice that didn't bump agent-plus-meta).

### Added

- **`agent-plus-meta uninstall`** — canonical uninstall action at `_subcommands/uninstall.py`. Default scope removes only the 5 framework primitive bins from `$INSTALL_DIR`; workspace, marketplaces, plugins, sessions, and skills are KEPT and listed with hints. Flag matrix: `--workspace` (also removes `<repo>/.agent-plus/` AND `~/.agent-plus/`), `--marketplaces` (unregisters `~/.agent-plus/marketplaces/` state — plugins INSTALLED from marketplaces are NOT touched, Claude Code owns those), `--all` (bins + workspace + marketplaces; "all of ours" semantics — does NOT include `--purge`, T2), `--purge` (`--all` PLUS any other agent-plus state we own; ALWAYS prompts for the literal word `PURGE` even under `--non-interactive`, T6 one-way door), `--dry-run` (manifest preview only), `--non-interactive` (skip prompt; auto-accept the safe default OR explicitly-flagged scope), `--json` (suppress human preview), `--install-dir PATH` (explicit override, defaults to `AGENT_PLUS_INSTALL_DIR` env or `~/.local/bin`).
- **JSON envelope schema locked as public contract** — see [docs/uninstall-envelope.md](./docs/uninstall-envelope.md). The `kind` enum reserves slots for v0.16+ additive use (`settings_hook`, `daemon_pid`, `migration_state`) so future hooks/daemons/migrations can ship without breaking the contract.
- **`install.sh --uninstall`** — replaces v0.13.0's `exit 2` stub with: (1) delegate (`exec`) to `agent-plus-meta uninstall` when reachable on PATH or in `$INSTALL_DIR`, all flags pass through; (2) self-contained POSIX shell fallback when the bin is broken/missing — removes ONLY the 5 primitive bins. Refuses `--workspace`/`--marketplaces`/`--all`/`--purge` in fallback mode with exit 3 and a "re-install first" hint (T1).
- **Idempotency** — every removal target reports one of `removed | missing | skipped | kept | error`. Re-running after a clean uninstall: every target reports `missing`, exit 0.
- **Self-delete handling on Windows (E1)** — on Linux/macOS the running bin can unlink itself (POSIX inode semantics). On Windows, `os.remove()` may fail with `PermissionError`; we emit `status: error` with a manual-removal note for self, and succeed for the other 4 bins.
- **`uninstall_run` telemetry event** — appends one JSON line to `~/.agent-plus/analytics/uninstall.jsonl` if the analytics directory exists. Schema: `{ts, event, mode, summary}`. Names only — no paths in telemetry.
- **Plugin-cache LIST-only behaviour** — walks `~/.claude/plugins/cache/` for plugins tagged `@agent-plus`, surfaces them in `claude_plugin_hints[]` with the exact `claude plugin uninstall <name>@agent-plus` strings to copy-paste. We don't touch them — Claude Code owns them (P3 jurisdictional).
- **17 new tests** — 14 in `agent-plus-meta/test/test_uninstall.py` (default dry-run / default removal / each scope flag / PURGE confirmation / non-interactive PURGE still prompts / user skills + sessions kept under PURGE / idempotent rerun / partial state / Claude plugin hints / envelope schema / Windows self-delete / install-dir override) + 3 in `test/test_install_script.py` (install.sh delegates when bin present / fallback when missing / fallback refuses workspace flag).

### Changed

- `install.sh`'s verb-dispatcher now correctly forwards `"$@"` to `dispatch_upgrade` and `dispatch_uninstall`. Previously the args were dropped at the case-statement boundary; the upgrade slice never noticed because its tests didn't exercise multi-arg invocations.
- `agent-plus-meta/.claude-plugin/plugin.json` version bumped 0.13.5 → 0.15.0 (skipping 0.14.0 — that slot was a skill-plus-only slice and didn't bump agent-plus-meta).
- `agent-plus-meta/README.md` gains an "Uninstalling" section documenting the flag matrix and pointing at the envelope schema.
- Root `README.md` gets a one-line pointer to the uninstall section.

### Deferred

- **Reverse dual-track skill (`agent-plus-uninstaller` SKILL.md)** — would teach Claude Code WHEN to offer to uninstall (mirror of v0.13.0 `agent-plus-installer`). Deferred to v0.16+ (T3) — validate the JSON envelope as a public contract before binding the skill to it.
- **`--include-claude-plugins` opt-in inside `--purge`** — would shell out to `claude plugin uninstall <name>@agent-plus` for each tagged plugin. Designed but not implemented; add when users request.

## 0.13.5 - 2026-04-30

agent-plus upgrade slice. Closes the framework's biggest current craft gap: every dogfooder pasted `curl … install.sh | sh` weeks ago and is on a stale install with no probe, no prompt, no migration path, no rollback. v0.13.5 ships all four — the cached probe, the upgrade action, the migration runner, and a per-bin `.bak` rollback — adapted from gstack's proven shape to agent-plus's multi-plugin reality.

### Added

- **`agent-plus-meta upgrade-check`** — cached probe at `_subcommands/upgrade_check.py`. Reads the single-root `VERSION` file at `raw.githubusercontent.com/osouthgate/agent-plus/main/VERSION` (T1 — tag-bound, NOT derived from any plugin.json). Cache at `~/.agent-plus/upgrade/cache.json` with TTL 60min for `up_to_date` / 720min for `upgrade_available`. Snooze ladder at `~/.agent-plus/upgrade/snooze.json` (24h → 48h → 7d → never), reset on new latest version. Fail-silent on every network failure mode (P4) — verdict degrades to `unknown`, the workflow proceeds, the next probe in 60min retries. Flags: `--check` (default), `--force`, `--snooze {24h,48h,7d,never}`, `--clear-snooze`, `--json`, `--non-interactive`, `--no-telemetry` (no-op stub for forward-compat), `--timeout SEC` (default 3, max 10).
- **`agent-plus-meta upgrade`** — upgrade action at `_subcommands/upgrade.py`. Detects install type (`global` for `~/.local/bin` or `$AGENT_PLUS_INSTALL_DIR`; `git_local` for clones) — vendored branch deferred per /review C4. Five-bin `.bak` snapshot at `~/.agent-plus/.bak/<UTC-timestamp>/<bin>.bak` BEFORE replace. Atomic `tmp → rename` writes. Migration runner reads `agent-plus-meta/migrations/v*.py` modules (empty on day one — see Migration runner below). Post-test gate calls `cmd_doctor()` in-process; verdict=broken → automatic rollback from `.bak`. 4-option `AskUserQuestion` matching gstack: A) Yes upgrade, B) Always (sets `silent_upgrade: true`), C) Snooze (advances ladder), D) Never ask again (sets `update_check: false`). `--non-interactive --auto` picks A by default, B if config.silent_upgrade=true AND bump is patch (T5 — minor/major always prompts even under `--auto`). Flags: `--rollback` (standalone restore from most recent `.bak` set), `--dry-run`, `--user-choice`, `--no-telemetry` (no-op stub).
- **Migration runner contract.** `agent-plus-meta/migrations/` ships empty on day one with `__init__.py` and `README.md`. Each module exposes `def migrate(workspace: Path) -> dict` returning `{status: "ok"|"skipped"|"failed", message, changes}`. Idempotent. History at `~/.agent-plus/migrations.json` keyed by id (e.g. `v0_13_5`). The runner ships, the directory exists, the contract is documented — first breaking change has somewhere to land.
- **`install.sh --upgrade`** — wired into v0.13.0's verb dispatcher. Delegates to `agent-plus-meta upgrade` on PATH or in `$AGENT_PLUS_INSTALL_DIR`; falls back with a helpful pointer when the bin is broken (re-run install.sh).
- **Single-root `VERSION` file at repo root** — single line `0.13.5\n`. Tag-bound (bumps on each `git tag v0.X.Y`, NOT derived from plugin.json). The CI gate added in this slice asserts `cat VERSION` matches the latest annotated git tag at release time so the probe never lies.
- **Frozen JSON envelope schemas** — both `upgrade-check` and `upgrade` lock public contracts at v0.13.5. Documented in [agent-plus-meta/README.md](./README.md#upgrade-check). Additive enum widening is non-breaking; field renames or removals require a major bump. Per /review C5 the `telemetry` field is omitted (the `--no-telemetry` flag exists as a no-op for forward-compat — when a real telemetry endpoint ships in ~v0.16, the field is added additively).
- **4 stable error codes** in `envelope.errors[].code`: `upgrade_check_network_failed`, `upgrade_partial_failure`, `upgrade_migration_failed`, `upgrade_rollback_required`. Per /review C2, user-declined runs are NOT errors — verdict `noop` plus `user_choice` carries the signal.
- **VERSION-vs-tag CI gate** — `.github/workflows/ci.yml` gains a `version-tag-gate` job that runs on tag pushes and asserts `cat VERSION` matches the latest annotated git tag. Without this gate, a forgotten VERSION bump means the upgrade probe lies.
- **~30 new tests** across `test_upgrade_check.py`, `test_upgrade.py`, `test_migrations.py`. Stdlib unittest, mocked `urllib.request.urlopen` and `subprocess.run` so the suite stays offline. TTHW invariants enforced: cache hit < 1s, network probe < 5s, dry-run < 3s, rollback < 2s.

### Changed

- `install.sh` `--upgrade` branch replaced its v0.13.0 stub with the live delegation. All 9 existing `test_install_script.py` assertions still pass.
- `agent-plus-meta/.claude-plugin/plugin.json` version bumped 0.13.0 → 0.13.5.

### Distinguished from `--auto` CLI flag (per /review C3)

The config key `silent_upgrade: bool` (default false) means "skip the upgrade prompt entirely on patch bumps." The CLI flag `--auto` means "non-interactive, pick the recommended option." Two distinct concepts cleanly separated. README documents both with explicit cross-references.

### Cuts honored

- `--sentinel` mode for upgrade-check is CUT (defer to v0.16 — option (a) skill-preamble integration not shipping today).
- `silent_upgrade_policy` config knob is CUT — hardcoded patch-only behaviour in v0.13.5.
- `upgrade_user_declined` error code is CUT — verdict + user_choice carry the signal.
- `telemetry` envelope field is CUT — flag is a no-op forward-compat stub.
- Vendored install detector branch is CUT — install-type returns `global` or `git_local` only.
- GitHub API fallback for VERSION is CUT — single-root `VERSION` file is the source of truth.

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
