# Plans

Tracked ideas and deferred work. Format: title, effort, description, open questions.

---

## Slash command in init footer (quick)

**Effort:** small — one-liner change in `init.py`  
**Status:** open

The init footer currently shows bare CLI commands (`repo-analyze --pretty`). It should show the slash command alongside, since that's the right path when already inside a Claude Code session:

```
  /repo-analyze:repo-analyze
    -> or from a terminal: repo-analyze --pretty
```

Open question: confirm the exact slash command format Claude Code uses for marketplace plugins (`/repo-analyze:repo-analyze` vs `/agent-plus:repo-analyze` — depends on resolution of the namespace plan below).

---

## Cold-repo hook: suggest repo-analyze on first message (medium)

**Effort:** medium — new hook file + cache check logic  
**Status:** open

A `UserPromptSubmit` hook that runs before every user message. Checks whether `.agent-plus/repo-analyze-cache.json` exists in the current workspace. If not, injects a one-line suggestion into Claude's context:

```
[agent-plus] This repo hasn't been scanned yet. Run /repo-analyze:repo-analyze for full context before starting.
```

Silent if cache exists. Zero interruption for already-warmed repos.

Implementation notes:
- Hook lives at `.claude/hooks/suggest-repo-analyze.sh` (installed by `agent-plus-meta init`)
- Cache file written by `repo-analyze` on first run — check if that marker already exists or needs adding
- Should be opt-out, not opt-in; `agent-plus-meta init` installs the hook by default

Open questions:
- Does the hook fire on `/` commands or only on natural language messages? If it fires on slash commands, add a guard so it doesn't interrupt plugin invocations.
- Should the suggestion be injected as a system prompt addition or printed to stderr? Test both — stderr may not be visible in all Claude Code surfaces.

---

## Audit: global vs project-local file storage (medium)

**Effort:** medium — audit + likely migrations in multiple plugins  
**Status:** open — needs hands-on verification

The framework writes files into two locations:
- **Global:** `~/.agent-plus/` — user-level, shared across all repos
- **Project:** `<repo-root>/.agent-plus/` — repo-specific, committed or gitignored per project

The split is probably wrong in at least one place. Known cases to verify:

**`skill-plus/candidates.jsonl` — likely wrong**  
Currently landing at `<project>/.agent-plus/skill-plus/candidates.jsonl`.  
Candidates are mined from global session logs (`~/.claude/projects/`) across all your repos — they reflect YOUR patterns, not this repo's patterns. They should almost certainly be global: `~/.agent-plus/skill-plus/candidates.jsonl`. Storing them per-project means candidates disappear when you switch repos and accumulate stale copies everywhere.

**`repo-analyze` cache — likely correct**  
The analysis output is about a specific codebase. `<project>/.agent-plus/repo-analyze-cache.json` (or equivalent) is the right place — it should be project-local so running `repo-analyze` in `Tinker-Tailor` doesn't pollute the cache for `rainshift`.  
Confirm: does `repo-analyze` actually write a cache file on run? If so, does the cold-repo hook (see above) read it correctly?

**`skill-feedback` ratings — likely global**  
Ratings are about skill quality, not about a specific repo. `~/.agent-plus/skill-feedback/` or a similar global path makes more sense than per-project.

**`env-status.json`, `services.json`, `manifest.json`**  
These are workspace-bootstrapped by `agent-plus-meta init` and intentionally project-local — they record which services are configured for this checkout. Probably correct.

**`.agent-plus/marketplaces/`**  
Marketplace installs feel global (you don't reinstall the marketplace per repo). Verify where these land and whether the install path respects `--dir`.

Action: walk each plugin's write paths, list them against the global/project rule, move anything that's in the wrong bucket, update `_resolve_for_read` / `_resolve_workspace` logic and tests.

---

## Plugin namespace: `/agent-plus:*` umbrella architecture (big)

**Effort:** large — plugin architecture change, affects install UX  
**Status:** design decision needed

Currently each primitive installs as its own plugin (`repo-analyze`, `diff-summary`, etc.), so slash commands appear as `/repo-analyze:repo-analyze` — the redundant name reads badly and the namespace is flat.

Goal: `/agent-plus:repo-analyze`, `/agent-plus:diff-summary`, etc.

Two approaches:

**Option A — Umbrella plugin**  
One plugin named `agent-plus` that registers all commands. Single install: `claude plugin install agent-plus@agent-plus`. Slash commands become `/agent-plus:repo-analyze`.  
- Pro: clean namespace, one install command, obvious brand
- Con: can't install primitives individually; one broken command affects the whole plugin; harder to version independently

**Option B — Rename each plugin**  
Rename binaries and plugin.json names to `agent-plus-repo-analyze`, `agent-plus-diff-summary`, etc. Slash commands become `/agent-plus-repo-analyze:agent-plus-repo-analyze` — still redundant, just with a different prefix. Not an improvement.

**Option A is the right call if we go forward.** Key questions before committing:
- Does Claude Code support one plugin registering multiple slash commands, each mapping to a different binary? Check plugin spec.
- Migration path for existing installs: `claude plugin uninstall repo-analyze@agent-plus` + `claude plugin install agent-plus@agent-plus`? Or can `agent-plus-meta upgrade` handle it?
- Does individual plugin versioning (repo-analyze@0.2.1 vs framework@0.19.x) survive the umbrella model, or do all primitives share one version?
