# AGENTS.md

Durable instructions for any coding agent (Claude Code, Codex, Cursor, Aider) working in this repo. If you're Claude Code, treat this as memory.

**For context on what this repo is and why it exists, see [README.md](./README.md).** This file is operational rules only.

## Plugins in this repo

| Plugin | Killer command |
| :--- | :--- |
| `agent-plus-meta` | `init`, `envcheck`, `refresh`, `marketplace install\|list\|update\|remove\|search\|prefer` |
| `repo-analyze` | `repo-analyze [--output] [--shape-depth] [--pretty]` |
| `diff-summary` | `diff-summary [--staged \| --base BRANCH \| --range A..B] [--public-api-only] [--risk MIN]` |
| `skill-feedback` | `log <skill> --rating --outcome [--friction]`, `report`, `submit` |
| `skill-plus` | `scan`, `propose`, `scaffold <name> --from-candidate <id>`, `inquire <tool> [--audit]`, `list`, `feedback`, `promote <name>` |

Service wrappers (`github-remote`, `vercel-remote`, `supabase-remote`, `railway-ops`, `linear-remote`, `openrouter-remote`, `langfuse-remote`, `hermes-remote`, `coolify-remote`, `hcloud-remote`) moved to [`osouthgate/agent-plus-skills`](https://github.com/osouthgate/agent-plus-skills).

## The seven design patterns

Any change must reinforce one of these. Full rationale: [README.md](./README.md).

1. Aggregate server-side, return one blob.
2. Resolve by name, not ID.
3. `--wait` on every async mutation.
4. `--json` on every list / show.
5. Strip values the agent shouldn't see (names only, no values).
6. Self-diagnosing output — every payload carries `tool: {name, version}`.
7. Stay in your lane — each plugin's SKILL.md says when to drop to raw CLI.

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
├── bin/<plugin>                  # stdlib Python 3, single file
├── skills/<plugin>/SKILL.md      # how Claude uses it
├── README.md                     # how a human understands it
├── CHANGELOG.md                  # pain points and wins, most-recent-first
└── LICENSE (or inherits root)
```

- **Stdlib only.** No pip installs. No venvs. If you reach for `requests`, stop and use `urllib.request`.
- **Layered `.env` autoload**, highest precedence first: `--env-file` → project `.env.local` / `.env` (walked up from cwd) → `~/.agent-plus/.env` → shell env. Project `.env` wins over shell — this is deliberate, don't flip it.
- **Scoped env prefixes** (where applicable). Only pick up the plugin's own prefix to avoid cross-pollution.
- **Missing-config errors point to both `.env` and `~/.claude/settings.json`** so the user knows where to put the value.

## Writing style for READMEs

Every plugin README should:

1. **Open with the counterfactual**, not the feature list. "Without this, Claude would do N round-trips and burn M tokens." The opening paragraph answers *what breaks without this*.
2. **Quote a number if you have one.** "~$10/12h → ~$0.03/12h", "40s sequential → 8s parallel", "4 API calls → 1". Numbers sell.
3. **Name the gotcha you're collapsing.** E.g. coolify-remote's "env vars visible in UI but not in container (needed a redeploy)". These are the hard-won bits that justify the plugin's existence.
4. **Headline commands before config.** The reader needs to see the shape of the tool before they care about `.env` precedence.
5. **No emoji decorations. No feature-table fluff.** Terse voice, one idea per line.

## Philosophy

Deterministic work belongs in scripts, not prompts. The LLM orchestrates; the code does. If you catch yourself adding LLM-call-driven logic to a plugin's `bin/` script, stop — that's a smell. The CLI is deterministic; the prompting belongs in the calling agent or a Hermes cron.
