# AGENTS.md

Durable instructions for any coding agent (Claude Code, Codex, Cursor, Aider) working in this repo. If you're Claude Code, you read this at every session start — treat it as memory.

## What this repo is

`agent-plus` is a collection of Claude Code plugins. Each plugin is a single-file stdlib Python 3 CLI that wraps a third-party API (Coolify, Langfuse, Hermes Agent, OpenRouter, Hetzner Cloud, Railway, Supabase) with one goal: **cut the tool-call and token cost of driving that API from an AI agent.**

Every plugin exists because doing the same thing via `curl` + `jq` + raw CLI burned either tokens, tool calls, or human time that this repo measured and got tired of.

## The five design patterns

Any change you make — new plugin, new command, README rewrite — should reinforce one of these. If it doesn't, justify it.

1. **Aggregate server-side, return one blob.** Don't make the agent fetch `/a`, `/b`, `/c` and stitch them. Hit all three inside the CLI and return one structured payload. Example: `railway-ops overview` (services + deploy status + errors + env names in one call); `langfuse monitor-user` (daily metrics + sessions + latest traces in one call).
2. **Resolve by name, not ID.** Never require the agent to copy a UUID, hash, or 20-char project ref between calls. Look it up internally. Example: `coolify-remote deploy hermes` instead of `coolify-remote deploy b1c6e2f0-...`.
3. **`--wait` on every async mutation.** If a command returns an action ID, bundle the poll loop. The agent should never hand-roll `until curl ... | jq ...` — that breaks on the Windows bash shim and burns tool calls on poll tick.
4. **`--json` on every list/show.** Piping to `jq` is the happy path. Never format output that only humans can parse.
5. **Strip values the agent shouldn't see.** Env var values leak into transcripts if you let them. Parse them server-side, keep names, drop the dict. Example: `railway-ops overview` and the canary no-leak test.

The root README sells these patterns. Each plugin README should show at least one in action with a concrete win.

## Keeping the docs honest (READ THIS)

**When you modify `<plugin>/bin/*` or `<plugin>/skills/*/SKILL.md`, you MUST before stopping:**

1. **Update `<plugin>/README.md`** if the change adds/removes a command, flag, env var, or changes a headline behaviour. The README's "Headline commands" / "Usage" section must match what the CLI actually supports. The "What it doesn't do" section (if present) must not claim something is missing that you just shipped.
2. **Append a `<plugin>/CHANGELOG.md` entry** under `## Unreleased` with: what changed, why it matters, date. One entry per change, most recent first.
3. **Check the root `README.md`** if you added/removed a plugin or changed the top-level plugin table's one-line description.

This is the single biggest source of drift in this repo. If you skipped these steps in a previous turn, fix it now.

**Enforcement.** A `Stop` hook at `.claude/hooks/check-readme-drift.sh` runs before every session ends. It diffs the branch against `origin/main` and exits non-zero (with a message back to Claude) if any plugin's `bin/` or `SKILL.md` changed without a matching `README.md` and `CHANGELOG.md` update. If you see that message, update the docs and try again — don't bypass.

**When you change a SKILL.md**, re-read the README: the SKILL teaches Claude, the README teaches the human. They should agree on the headline commands even if the SKILL has more detail.

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
- **Layered `.env` autoload**, highest precedence first: `--env-file` → project `.env.local` / `.env` (walked up from cwd) → shell env. Project `.env` wins over shell — this is deliberate, don't flip it.
- **Scoped env prefixes.** Each plugin only picks up its own prefix (`HERMES_*`, `COOLIFY_*`, `LANGFUSE_*`, etc.) to avoid cross-pollution.
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
