# STAGING.md — private staging context (NOT released to public)

This file lives only in the **private** `osouthgate/plans-agent-plus` clone. It is intentionally absent from the public `osouthgate/agent-plus` repo. If you are reading this in the public repo, something went wrong with a cherry-pick — delete it and amend the commit.

## What this repo is

`plans-agent-plus` is the **private staging clone** of [`osouthgate/agent-plus`](https://github.com/osouthgate/agent-plus) — the framework-only repo. As of the 2026-04-28 framework extraction, agent-plus ships only the four universal primitives (`agent-plus` meta, `repo-analyze`, `diff-summary`, `skill-feedback`) plus `skill-plus` (in design). The 10 service wrappers have moved to a separate marketplace at `osouthgate/agent-plus-skills` (live, v0.2.0) and no longer stage through this repo.

All new framework work — primitive plugins, envelope-contract changes, doc rewrites, the marketplace install command — is built and tested here first. Once a slice is solid (tests green, drift hook clean, dogfooded for at least one session), it gets promoted to the public repo.

The public repo is the user-facing artifact. The private repo is the build surface.

## Branch model

- `main` — the integration branch in this private repo. Slices fast-forward onto it after they land.
- `slice-N-<topic>` — short-lived branches per discrete unit of work. One slice = one logical feature + matching docs/tests/changelog. Land via `git merge --ff-only` to private `main`, then `git push origin main`.
- The drift hook (`.claude/hooks/check-readme-drift.sh`) and the SessionStart context hook (`.claude/hooks/agent-plus-context.py`) are repo-resident and run automatically.

## Release / promotion ritual

When promoting a slice from private `main` to public `agent-plus/main`:

1. **Identify the commits to promote.** Usually a single commit per slice (the slice landed FF onto private main as one feature commit).
2. **Cherry-pick into a public branch:**
   ```bash
   cd C:/dev/agent-plus  # or a fresh clone
   git fetch
   git checkout main
   git checkout -b release/<slice-topic>
   # cherry-pick the SHA(s) from the private repo. The private repo is configured
   # locally as `upstream` on the public clone so it's just:
   git cherry-pick <sha>
   ```
3. **Verify there's no staging residue:** `STAGING.md` must NOT be in the cherry-picked diff. If it is, abort and remove from the diff.
4. **Run tests in the public clone**, push the branch, open a PR (or push to main directly if going solo).
5. **Run the AGENTS.md item #4 topic-add reminders** (see queue below) for any new plugin shipped in the slice.

Use `git format-patch <range>` + `git am` if a slice spans multiple commits and the cherry-pick range is awkward.

## Framework extraction (in progress)

The 2026-04-28 split moves agent-plus to framework-only. Pre-extraction snapshot is preserved at `C:/dev/plans-agent-plus-archive/` for reference during migration.

The 10 wrappers — `github-remote`, `linear-remote`, `vercel-remote`, `supabase-remote`, `railway-ops`, `openrouter-remote`, `langfuse-remote`, `hermes-remote`, `coolify-remote`, `hcloud-remote` — are migrating to `osouthgate/agent-plus-skills` per [`plans/todo/2026-04-28-framework-extraction.md`](./plans/todo/2026-04-28-framework-extraction.md). Once that repo exists they ship from there independently.

**Wrapper staging discipline is being dropped.** Pre-1.0, the wrappers are stable enough that the cherry-pick-from-private overhead isn't justified — and once they leave this repo, there's no shared `main` to stage against anyway. They'll iterate directly on `osouthgate/agent-plus-skills`.

**Framework-plugin staging discipline continues unchanged.** The four universal primitives plus `skill-plus` still go through the private→public cherry-pick ritual described above.

## Pending public-release queue

Things to do the next time we promote slices to public. Update this list as slices land.

### Topic adds (per AGENTS.md item #4)

These are GitHub topic adds that can only happen on the public repo (auth-gated):

- [ ] `gh repo edit osouthgate/agent-plus --add-topic agent-plus`
- [ ] `gh repo edit osouthgate/agent-plus --add-topic repo-analyze`
- [ ] `gh repo edit osouthgate/agent-plus --add-topic diff-summary` *(after slice 5 lands)*
- [ ] `gh repo edit osouthgate/agent-plus --add-topic skill-plus` *(after slice A lands)*

Wrapper-related topic adds (`github-remote`, `vercel-remote`, etc.) are no longer queued here — those wrappers are migrating to `osouthgate/agent-plus-skills` and their topic adds happen on that repo instead. When `osouthgate/agent-plus-skills` is created, run `gh repo edit osouthgate/agent-plus-skills --add-topic agent-plus-skills` for marketplace discoverability.

### Slices ready to promote

| Slice | Private SHA | Plugin / change | Public-side notes |
|---|---|---|---|

(Empty — promotion queue cleared 2026-04-28 evening. All previously-queued slices either landed on public via the v0.9.0 promotion (slices 1–5, 8, MP1, MP2 — all bundled into public commits up through `db7ff68`) or are private-only (slices 6 + 7). The next promotable slice will be the gate-2 papercut bundle in `plans/todo/2026-04-28-unified-plan.md`.)

### Public release log

| Tag | Public SHA | Released | Contents |
|---|---|---|---|
| v0.1.0 | (early) | pre-2026-04 | Initial framework split |
| v0.2.0 | `c923160` | 2026-04-28 | Status README + framework-only restructure |
| v0.9.0 | `db7ff68` | 2026-04-28 | Marketplace install/update/list/remove + trust model (Phase 2) |
| v0.9.1 | `abe11db` | 2026-04-30 | Papercut bundle: services.json cleanup, repo-analyze deque BFS, diff-summary public-API regex fix, slice-8 coverage |
| v0.10.0 | `6f24782` | 2026-04-30 | skill-plus 0.1.0 (the fifth universal primitive — scan/propose/install-cron/scaffold/list/feedback/promote, 83 tests). agent-plus marketplace `search` + collision-resolution `prefer` (Phase 4). `core.autocrlf=false` pinned by `agent-plus init`. Wrappers deprecated from this repo's marketplace.json. Root + agent-plus README rewrite. PR #9. Topic `skill-plus` added to public repo. |
| v0.10.1 | `5426207` | 2026-04-30 | README polish pass driven by re-audit (excitement 3→7): version drift normalised across 6 worked-example JSON blocks; sibling-nav strip on all 5 plugin READMEs; voice-consistency pass (skill-feedback 167→151, skill-plus 139→132, agent-plus 302→280); AGENTS.md `~500 lines` claim fixed. Cross-repo: agent-plus-skills wrappers aligned to rc=0 + `status: "unconfigured"` for refresh-handler contract parity. Extension-API canary design spec written (private). PR #10. |
| v0.11.0 | `dd347f6` | 2026-04-30 | **BREAKING.** Rename meta plugin: `agent-plus` → `agent-plus-meta`. Resolves the audit-flagged naming collision between the framework and its meta primitive. Migration: `claude plugin uninstall agent-plus@agent-plus && claude plugin install agent-plus-meta@agent-plus`; CLI `agent-plus init/...` → `agent-plus-meta init/...`. Framework name, repos, `.agent-plus/` workspace dir, `AGENT_PLUS_*` env vars all unchanged. Root README tail cut: contributor sections moved to new `CONTRIBUTING.md`. CI workflow fixed to iterate the renamed plugin set + add skill-plus (was missing) + install pytest. PR #11. |
| v0.11.1 | `5204631` | 2026-04-30 | Audit-3 polish: fix dead sibling-nav links (`../agent-plus` → `../agent-plus-meta`) introduced by the v0.11.0 rename across 4 plugin READMEs; remove rename-blockquote lede leak from `agent-plus-meta/README.md`; add "all five at once" install one-liner; add a "90-second tour" terminal-cast section above Install showing the framework's actual flow. PR #12. |
| v0.11.2 | `3e3c088` | 2026-04-30 | "What gets us better scores" polish: 6 shields.io badges row + TOC line + ASCII framework-vs-marketplace diagram + tightened pitch line on root README. agent-plus-meta README split 285→216 lines (Extensions + Resolution moved to `docs/`). New `agent-plus-meta doctor` subcommand (read-only diagnostic, verdict ladder broken/degraded/healthy). New `install.sh` POSIX one-shot installer at repo root (`curl -fsSL .../install.sh \| sh` for all 5 primitives, `AGENT_PLUS_INSTALL_DIR` override, `--dry-run`). +10 tests (5 doctor + 5 install). Audit trajectory: pitch 8→9, onboarding 7→9, voice 7→9, info-arch 7→9, trust 8→9. PR #13. |
| v0.11.3 | `d46f487` | 2026-04-30 | Animated tour GIF closes the audit's last open dimension (excitement 7→9). Synthetic asciinema v2 cast at `assets/tour.cast` (plain JSON, deterministic regenerator at `assets/generate_tour_cast.py`); rendered to `assets/tour.gif` (530KB, 940×649px) via `agg` (Rust binary on PATH or `ghcr.io/asciinema/agg` Docker image fallback). Build script `assets/build_tour_gif.sh` handles Git Bash path-mount quirk. Embedded under the pitch on the root README. CONTRIBUTING.md gains a "The tour GIF" section. PR #14. |

**Upcoming slices** (not yet shipped):

| Slice | Topic | Notes |
|---|---|---|

(Empty — promotion queue cleared 2026-04-30 with v0.10.0.)

Note: slice 6 (`d9ce987`) is `STAGING.md` + staging banner — **PRIVATE ONLY, never cherry-pick**. Slice 7 (`1758144`) is `plans/todo/2026-04-28-backlog-status.md` — also private (the public repo doesn't track `plans/todo/`).

### Doc follow-ups noted but not yet shipped

(Empty — `core.autocrlf=false` shipped in v0.10.0.)

## What's intentionally NOT in this file

- Strategic / business / personal context — that lives in [`plans/todo/2026-04-24-strategic-direction.md`](./plans/todo/2026-04-24-strategic-direction.md) and its appendices.
- Cross-session memory — that lives in `~/.claude/projects/C--dev-agent-plus/memory/`.
- Per-plugin specifics — those live in each plugin's own `README.md` / `CHANGELOG.md`.

## Why this file exists at all

The agent-plus AGENTS.md and READMEs are user-facing and must be byte-identical between the private staging clone and the public repo. Anything staging-specific has to live in a separate file that gets stripped (or simply never cherry-picked) at promotion time. `STAGING.md` is that file.

The SessionStart hook (`.claude/hooks/agent-plus-context.py`) detects when this repo's remote points at `plans-agent-plus` and surfaces a one-line "STAGING MODE" notice automatically, so coding agents working in this repo get the context without needing to read this file every session. This file is the durable reference; the hook is the runtime nudge.
