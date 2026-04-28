# AGENTS.md

Durable instructions for any coding agent (Claude Code, Codex, Cursor, Aider) working in this repo. If you're Claude Code, you read this at every session start — treat it as memory.

## What this repo is

`agent-plus` is a **framework** for Claude Code plugins. As of the 2026-04-28 framework extraction, it ships only the four universal primitives:

- `agent-plus` — the meta plugin (workspace bootstrap, env-var readiness, identity cache, marketplace lifecycle)
- `repo-analyze` — cold-start orientation in any unfamiliar repo
- `diff-summary` — per-file role + risk classification of a git diff
- `skill-feedback` — local-first agent self-assessment for any Claude Code skill

A fifth (`skill-plus`, session-mining-driven skill discovery + scaffolding + feedback aggregation) is in design.

Service wrappers (`github-remote`, `vercel-remote`, `supabase-remote`, `railway-ops`, `linear-remote`, `openrouter-remote`, `langfuse-remote`, `hermes-remote`, `coolify-remote`, `hcloud-remote`) previously shipped here. They moved to a separate marketplace at `osouthgate/agent-plus-skills` and now iterate independently.

Every plugin in either repo exists because doing the same thing via `curl` + `jq` + raw CLI burned either tokens, tool calls, or human time that the framework measured and got tired of.

## The seven design patterns

Any change you make — new plugin, new command, README rewrite — should reinforce one of these. If it doesn't, justify it. The full list is canonical in the [root README](./README.md#the-seven-patterns) — paraphrased here:

1. **Aggregate server-side, return one blob.** N endpoints in parallel under the hood, one structured payload back to the agent.
2. **Resolve by name, not ID.** UUIDs never touch the agent's context.
3. **`--wait` on every async mutation.** No hand-rolled `until` loops in the agent's session.
4. **`--json` on every list / show.** Structured output into `jq` is the default.
5. **Strip values the agent shouldn't see.** Env-var values, secrets, large blobs — names and IDs only.
6. **Self-diagnosing output.** Every JSON payload carries a top-level `tool: {name, version}` field read from the plugin manifest at runtime.
7. **Stay in your lane.** Each plugin's SKILL.md explicitly lists when the agent should drop to the raw CLI / API instead of looping on a rejection.

The root README sells these patterns; each plugin README should show at least one in action with a concrete win.

## Keeping the docs honest (READ THIS)

**When you modify `<plugin>/bin/*` or `<plugin>/skills/*/SKILL.md`, you MUST before stopping:**

1. **Update `<plugin>/README.md`** if the change adds/removes a command, flag, env var, or changes a headline behaviour. The README's "Headline commands" / "Usage" section must match what the CLI actually supports. The "What it doesn't do" section (if present) must not claim something is missing that you just shipped.
2. **Append a `<plugin>/CHANGELOG.md` entry** under `## Unreleased` with: what changed, why it matters, date. One entry per change, most recent first.
3. **Check the root `README.md`** if you added/removed a plugin or changed the top-level plugin table's one-line description.

This is the single biggest source of drift in this repo. If you skipped these steps in a previous turn, fix it now.

**Enforcement.** A `Stop` hook at `.claude/hooks/check-readme-drift.sh` runs before every session ends. It diffs the branch against `origin/main` and exits non-zero (with a message back to Claude) if any plugin's `bin/` or `SKILL.md` changed without a matching `README.md` and `CHANGELOG.md` update. If you see that message, update the docs and try again — don't bypass.

**When you change a SKILL.md**, re-read the README: the SKILL teaches Claude, the README teaches the human. They should agree on the headline commands even if the SKILL has more detail.

## Adding a new plugin (checklist)

This applies to **framework plugins** added to this repo (rare — the framework is intentionally small). Service-wrapper plugins go in `osouthgate/agent-plus-skills` and follow that repo's `marketplace.json` schema instead.

When scaffolding a whole new framework plugin, do these in the same PR — the `Stop` hook enforces the first two, but none catches #4 automatically:

1. **Copy the shape of an existing plugin.** Write `bin/<name>` (stdlib Python 3 only), `skills/<name>/SKILL.md`, `README.md`, `CHANGELOG.md`, `.claude-plugin/plugin.json`.
2. **Add the entry to `.claude-plugin/marketplace.json`** so `claude plugin install <name>@agent-plus` works.
3. **Add a row to the root `README.md`** — Plugins table at minimum.
4. **Remind the user to run** `gh repo edit osouthgate/agent-plus --add-topic <name>` so the plugin shows up in GitHub's topic search. You cannot do this yourself without auth; flag it explicitly in your completion summary.
5. **Validate** with `claude plugin validate <plugin>` and `claude plugin validate .claude-plugin/marketplace.json` before committing.

The `Stop` hook catches missing root README / marketplace entries on a new-plugin commit. It cannot catch missing GitHub topics — that's on you.

## Per-plugin conventions

Every plugin directory has the same shape — preserve it:

```
<plugin>/
├── .claude-plugin/plugin.json
├── bin/<plugin>                  # stdlib Python 3, ~500 lines max
├── skills/<plugin>/SKILL.md      # how Claude uses it
├── README.md                     # how a human understands it
├── CHANGELOG.md                  # pain points and wins, most-recent-first
└── LICENSE (or inherits root)
```

- **Stdlib only.** No pip installs. No venvs. If you reach for `requests`, stop and use `urllib.request`.
- **Layered `.env` autoload**, highest precedence first: `--env-file` → project `.env.local` / `.env` (walked up from cwd) → `~/.agent-plus/.env` → shell env. Project `.env` wins over shell — this is deliberate, don't flip it.
- **Scoped env prefixes** (where applicable). The framework primitives don't take service-specific env vars, but if you add one that does, only pick up its own prefix to avoid cross-pollution.
- **Missing-config errors point to both `.env` and `~/.claude/settings.json`** so the user knows where to put the value.

## Writing style for READMEs

Every plugin README should:

1. **Open with the counterfactual**, not the feature list. "Without this, Claude would do N round-trips and burn M tokens." The opening paragraph answers *what breaks without this*.
2. **Quote a number if you have one.** "~$10/12h → ~$0.03/12h", "40s sequential → 8s parallel", "4 API calls → 1". Numbers sell.
3. **Name the gotcha you're collapsing.** E.g. coolify-remote's "env vars visible in UI but not in container (needed a redeploy)". These are the hard-won bits that justify the plugin's existence.
4. **Headline commands before config.** The reader needs to see the shape of the tool before they care about `.env` precedence.
5. **No emoji decorations. No feature-table fluff.** Terse voice, one idea per line.

## Philosophy (don't delete)

Plugins here follow the rule from [Garry Tan's skillify post](https://x.com/garrytan): **deterministic work belongs in scripts, not prompts.** The LLM orchestrates; the code does. Every plugin has a `SKILL.md` that teaches Claude *when* to reach for the script, not how to reinvent it in a prompt.

If you catch yourself adding LLM-call-driven logic to a plugin's `bin/` script, stop. That's a smell. The CLI is deterministic; the prompting belongs in the calling agent or a Hermes cron.
