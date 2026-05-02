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
