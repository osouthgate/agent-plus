# skill-feedback

Local-first self-assessment for Claude Code skills. The agent rates its own use of a skill; the rating lands as one JSONL line on disk, next to the project. Skill authors aggregate locally with `report`, or bundle entries into a GitHub issue body for the skill's source repo with `submit`.

Part of [agent-plus](../README.md). Stdlib-only Python 3, no dependencies, no SaaS, no SDK.

## Why

Skill authors are flying blind. You write a SKILL.md, ship it, and have no idea whether agents reach for it correctly, what they fall back to, or which flag they wished was there. Hosted alternatives solve this by posting telemetry to a third-party service — fine for some teams, a non-starter for others.

`skill-feedback` flips the model: **the agent self-assesses every time it uses a skill, and the entry stays on the user's machine until they explicitly choose to share it.**

**Measured wins**

- **One CLI call to log.** No SDK, no config file. Pasted snippet at the bottom of any SKILL.md teaches the agent the contract: rating, outcome, optional one-line friction.
- **JSONL on disk.** `.agent-plus/skill-feedback/<skill>.jsonl`. Trivially `jq`-able, trivially `grep`-able, trivially deletable. No DB, no migration, no auth dance.
- **`report` aggregates server-side.** Average rating, outcome histogram, top friction strings — pattern #1, one blob, not 200 raw lines.
- **`submit` is dry-run by default.** It prints a markdown issue body the user can review. Only `--no-dry-run` actually files an issue, and only via `gh` (or falls through to writing a `.md` file for manual paste). No raw GitHub API surface.
- **Privacy by construction.** Free-text fields are capped at 1000 chars and regex-scrubbed for GitHub PATs (`ghp_…`, `github_pat_…`, `gho_/ghu_/ghs_/ghr_…`), AWS access keys (`AKIA…`), Anthropic (`sk-ant-…`), Langfuse (`pk-lf-…` / `sk-lf-…`), Stripe (`sk_live_/sk_test_/rk_/pk_…`), generic OpenAI-style `sk-…`, Supabase (`sbp_…`), Sentry (`sntrys_…`), Google API keys (`AIza…`), Slack (`xoxb-/xoxa-/xoxp-/xoxr-/xoxs-…`), Discord bot tokens, JWTs (`eyJ…`), `Bearer …`, and `Authorization: …` patterns before write. No transcript ingestion. Skill name is whitelisted to `[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?` (must start AND end with alphanumeric, no `..`).
- **Defence-in-depth at submit time.** Regex covers token shapes, not PII or contextual leaks. The Claude agent invoking `submit` is instructed (in SKILL.md) to scan the dry-run body for real names, customer/employer identifiers, internal hostnames, and similar leaks the regex can't catch — and to abort `--no-dry-run` if anything looks off. No extra API call: the existing Claude Code session does the review. The dry-run JSON exposes `agent_review_required: true` + `agent_review_checklist` so the responsibility is unmissable.

## Install

### Recommended — marketplace install

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install skill-feedback@agent-plus
```

Adds `skill-feedback` to PATH and loads the skill so Claude reaches for it automatically after using other skills.

### Session-only (dev / try-before-install)

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/skill-feedback
```

### Standalone — no Claude Code

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/skill-feedback/bin/skill-feedback
chmod +x skill-feedback
./skill-feedback log my-skill --rating 4 --outcome success --friction "missing --json"
./skill-feedback report --pretty
```

## Prerequisites

- **Python 3.9+** (stdlib only).
- **(Optional)** `gh` on PATH if you want `submit --no-dry-run` to actually file the GitHub issue. Without it, `submit --no-dry-run` writes the body to `<storage-root>/<skill>.submit.md` for manual paste and prints the URL.

No env vars are required. Optional knobs:

- `SKILL_FEEDBACK_DIR=/abs/path` — override the storage root.
- `CLAUDE_SESSION_ID` — picked up automatically if Claude Code sets it; gets attached to each entry so the author can correlate logs from one session.

## Usage

```bash
# 1. Log one self-assessment after using a skill.
skill-feedback log hermes-remote --rating 5 --outcome success
skill-feedback log hermes-remote --rating 3 --outcome partial \
  --friction "no streaming chat support; fell back to curl + SSE"

# 2. Show recent entries.
skill-feedback show hermes-remote --since 7d --limit 20

# 3. Aggregate. Use --pretty for indented output, drop it for jq.
skill-feedback report --since 30d --pretty
skill-feedback report --skill hermes-remote --since 30d \
  | jq '.skills[].top_friction'

# 4. Bundle entries into a GitHub issue body. Dry-run by default.
skill-feedback submit hermes-remote --since 30d                # preview body
skill-feedback submit hermes-remote --since 30d --no-dry-run   # actually file

# 5. Inspect storage paths.
skill-feedback path
skill-feedback path --skill hermes-remote
```

## Storage

JSONL, one entry per line, append-only:

```jsonc
{"ts":"2026-04-26T21:30:00Z","skill":"hermes-remote","rating":5,
 "outcome":"success","friction":"none","session_id":"...","tool_version":"0.4.1","schema":1}
```

Storage root is resolved in this order (highest first):

1. `SKILL_FEEDBACK_DIR` env var (absolute path)
2. `<git-toplevel>/.agent-plus/skill-feedback/` (if cwd is inside a git repo)
3. `<cwd>/.agent-plus/skill-feedback/` (if a project-local `.agent-plus/` exists)
4. `~/.agent-plus/skill-feedback/` (last-resort fallback)

`skill-feedback path` prints both the resolved root and the rule that fired (`source: env|git|cwd|home`) so you can see where feedback is landing without guessing — important when you might be running from inside an unrelated git repo.

The `.agent-plus/` folder is the standard agent-plus convention for local state and session bookkeeping. Add it to your repo's `.gitignore` if you want feedback to stay personal, or commit it to share with the team.

## Submit flow

`submit` bundles entries into a markdown issue body. The body includes:

- A one-line summary (window, count, average rating, outcome histogram).
- Top friction strings, ranked by frequency.
- Up to ten recent notes (with timestamp, rating, outcome).

**Default is `--dry-run`** — the body is printed; nothing is sent. With `--no-dry-run`:

1. If `gh` is on PATH, `gh issue create --repo <owner/name> --title ... --body ...`.
2. Else write the body to `<storage-root>/<skill>.submit.md` and print the manual URL.

The repo is resolved in this order:

1. `--repo owner/name` flag.
2. `repository` (or `homepage`) field in the skill's `plugin.json`. Looked up under `<agent-plus>/<skill>/.claude-plugin/plugin.json` (development checkout) or `~/.claude/plugins/<skill>/.claude-plugin/plugin.json` (installed plugin).
3. Else error out and ask for `--repo`.

`submit` does NOT mark entries as "sent". If you want to rotate the log after filing an issue, run `mv .agent-plus/skill-feedback/<skill>.jsonl .agent-plus/skill-feedback/<skill>.jsonl.$(date +%s).bak` yourself.

## Wiring it into a skill

Drop this footer into any SKILL.md you ship and the agent will log on its own (the skill name should match what's in your SKILL.md frontmatter):

````markdown
## Logging feedback (automatic)

After using this skill, log one self-assessment so the skill author has signal:

```bash
skill-feedback log <skill-name> --rating 1-5 --outcome success|partial|failure \
                  [--friction "<short label>"]
```

Be honest — over-positive ratings waste the author's time. Free-text fields are
length-capped and regex-scrubbed for secret patterns before write; nothing
leaves the machine unless the user runs `skill-feedback submit --no-dry-run`.
````

## Privacy + safety contract

- **Local-first.** No network call on `log`, `show`, `report`, or `path`. Only `submit --no-dry-run` reaches the network, and only via `gh` (which uses your existing GitHub auth).
- **Length-cap + secret-scrub.** Free-text inputs are capped at 1000 chars and regex-stripped of GitHub PATs (`ghp_…`, `github_pat_…`, `gho_/ghu_/ghs_/ghr_…`), AWS access keys (`AKIA…`), Anthropic (`sk-ant-…`), Langfuse (`pk-lf-…` / `sk-lf-…`), Stripe (`sk_live_/sk_test_/rk_/pk_…`), generic OpenAI-style `sk-…`, Supabase (`sbp_…`), Sentry (`sntrys_…`), Google API keys (`AIza…`), Slack (`xoxb-/xoxa-/xoxp-/xoxr-/xoxs-…`), Discord bot tokens, JWTs (`eyJ…`), `Bearer …`, and `Authorization: …` before write. Not exhaustive — exotic provider-specific formats may slip through; the SKILL.md tells the agent not to put secret-shaped strings in `--note` in the first place.
- **No transcript ingestion.** The CLI never reads `~/.claude/projects/...` or any session log. Only what the agent passes on the command line is stored.
- **Skill name whitelist.** `[A-Za-z0-9._-]+` only — blocks path traversal and keeps the JSONL filename predictable.

## What it doesn't do

Deliberately out of scope for v1:

- No retroactive transcript scraping. The agent has to log explicitly.
- No edit / delete commands. Edit the `.jsonl` directly if needed (it's plain text).
- No SaaS upload. `submit` only opens a GitHub issue (and only with `--no-dry-run`).
- No de-duplication or "submitted" marker on entries — the user owns log rotation.
- No richer analytics. Pipe `report` or raw `.jsonl` into `jq` / DuckDB / a notebook for that.

## Optional Stop hook

The repo ships a draft `.claude/hooks/check-skill-feedback.sh` that nudges Claude to log feedback when a skill ran but no entry was appended. It is **NOT registered by default** — wire it into `.claude/settings.json` yourself if you want it. See [`.claude/hooks/check-skill-feedback.sh`](../.claude/hooks/check-skill-feedback.sh) for the gating contract (off unless `.agent-plus/skill-feedback/.enabled` exists).

## License

MIT. See repo root.
