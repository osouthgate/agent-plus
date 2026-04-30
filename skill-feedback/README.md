# skill-feedback

> Part of [**agent-plus**](../README.md) · siblings: [`agent-plus`](../agent-plus) · [`repo-analyze`](../repo-analyze) · [`diff-summary`](../diff-summary) · [`skill-plus`](../skill-plus)

Skill authors are flying blind. You ship a SKILL.md and have no idea whether agents reach for it correctly, what they fall back to, or which flag they wished was there. Hosted alternatives solve it by posting telemetry to a third-party service — fine for some teams, a non-starter for others.

`skill-feedback` flips it: **the agent self-assesses every time it uses a skill, the entry lands on disk as one JSONL line, and nothing leaves the machine until the user explicitly runs `submit --no-dry-run`.** Stdlib-only Python 3, no SDK, no DB, no auth dance.

## Headline command

```bash
skill-feedback log <skill> --rating 1-5 --outcome success|partial|failure \
                  [--friction "<short label>"]

skill-feedback show <skill> [--since 7d] [--limit 20]
skill-feedback report [--skill <name>] [--since 30d] [--pretty]
skill-feedback submit <skill> [--since 30d] [--no-dry-run] [--repo owner/name]
skill-feedback path  [--skill <name>]
skill-feedback --version
```

`log` is the primitive — a SKILL.md footer (below) teaches the agent the contract. `report` aggregates locally. `submit` is dry-run by default; `--no-dry-run` opens a GitHub issue via `gh`, falling through to a `.md` file for manual paste if `gh` isn't on PATH.

## Worked example

```bash
$ skill-feedback log hermes-remote --rating 3 --outcome partial \
    --friction "no streaming chat support; fell back to curl + SSE"
{
  "tool": {"name": "skill-feedback", "version": "0.3.0"},
  "ok": true,
  "skill": "hermes-remote",
  "appended": "/repo/.agent-plus/skill-feedback/hermes-remote.jsonl",
  "entry": {
    "ts": "2026-04-26T21:30:00Z",
    "skill": "hermes-remote",
    "rating": 3,
    "outcome": "partial",
    "friction": "no streaming chat support; fell back to curl + SSE",
    "session_id": "abc123",
    "tool_version": "0.3.0",
    "schema": 1
  }
}

$ skill-feedback report --skill hermes-remote --since 30d --pretty
{
  "tool": {"name": "skill-feedback", "version": "0.3.0"},
  "window": "30d",
  "skills": [{
    "skill": "hermes-remote",
    "count": 14,
    "avg_rating": 3.6,
    "outcomes": {"success": 9, "partial": 4, "failure": 1},
    "top_friction": [
      {"label": "no streaming chat support", "count": 3},
      {"label": "missing --json flag", "count": 2}
    ]
  }]
}
```

## What it covers

- **`log` is one CLI call.** No SDK, no config file. The footer at the bottom of any SKILL.md teaches the agent the contract.
- **JSONL on disk.** `.agent-plus/skill-feedback/<skill>.jsonl`. Trivially `jq`-able, `grep`-able, deletable.
- **`report` aggregates locally.** Average rating, outcome histogram, top friction strings — one blob, not 200 raw lines.
- **`submit` is dry-run by default.** Prints a markdown issue body for review. `--no-dry-run` files via `gh` (using your existing GitHub auth), or writes `<storage-root>/<skill>.submit.md` for manual paste if `gh` is missing.
- **Repo resolution for `submit`** (highest first): `--repo owner/name` → `repository`/`homepage` in the skill's `plugin.json` (dev checkout under `<agent-plus>/<skill>/.claude-plugin/` or installed under `~/.claude/plugins/<skill>/.claude-plugin/`) → error and ask for `--repo`.

## Storage resolution

Highest precedence first:

1. `SKILL_FEEDBACK_DIR` env var (absolute path)
2. `<git-toplevel>/.agent-plus/skill-feedback/` (cwd in a git repo)
3. `<cwd>/.agent-plus/skill-feedback/` (project-local `.agent-plus/` exists)
4. `~/.agent-plus/skill-feedback/` (last-resort)

`skill-feedback path` prints the resolved root + the rule that fired (`source: env|git|cwd|home`). The `.agent-plus/` directory is the standard agent-plus convention — gitignore it for personal feedback, commit it to share with the team.

`CLAUDE_SESSION_ID` is auto-attached to each entry when Claude Code sets it, so the author can correlate logs from one session.

## Privacy + safety contract

- **Local-first.** No network on `log`, `show`, `report`, or `path`. Only `submit --no-dry-run` reaches the network, and only via `gh`.
- **No transcript ingestion.** The CLI never reads `~/.claude/projects/...` or any session log. Only what the agent passes on the command line is stored.
- **Length-cap + secret-scrub on free-text.** Capped at 1000 chars and regex-stripped before write for: GitHub PATs (`ghp_…`, `github_pat_…`, `gho_/ghu_/ghs_/ghr_…`), AWS access keys (`AKIA…`), Anthropic (`sk-ant-…`), Langfuse (`pk-lf-…` / `sk-lf-…`), Stripe (`sk_live_/sk_test_/rk_/pk_…`), generic OpenAI-style `sk-…`, Supabase (`sbp_…`), Sentry (`sntrys_…`), Google API keys (`AIza…`), Slack (`xoxb-/xoxa-/xoxp-/xoxr-/xoxs-…`), Discord bot tokens, JWTs (`eyJ…`), `Bearer …`, and `Authorization: …`. Not exhaustive — exotic provider-specific formats may slip through; the SKILL.md tells the agent not to put secret-shaped strings in `--friction` in the first place.
- **Agent review at submit.** Regex covers token shapes, not PII or contextual leaks. The dry-run JSON exposes `agent_review_required: true` + `agent_review_checklist`; the existing Claude Code session is instructed (in SKILL.md) to scan the body for real names, customer/employer identifiers, and internal hostnames before `--no-dry-run`.
- **Skill name whitelist.** `[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?` only — blocks path traversal and keeps JSONL filenames predictable.

## What it doesn't do

- **No retroactive transcript scraping.** Agent has to log explicitly.
- **No edit / delete commands.** The `.jsonl` is plain text; edit it.
- **No SaaS upload.** `submit` only opens a GitHub issue, only with `--no-dry-run`.
- **No de-duplication or "submitted" marker.** User owns log rotation (`mv …jsonl …jsonl.bak`).
- **No richer analytics.** Pipe `report` or raw `.jsonl` into `jq` / DuckDB / a notebook.

## Wiring it into a skill

Drop this footer into any SKILL.md you ship; the agent will log on its own:

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

## Optional Stop hook

The repo ships a draft `.claude/hooks/check-skill-feedback.sh` that nudges Claude to log feedback when a skill ran but no entry was appended. **NOT registered by default** — wire it into `.claude/settings.json` yourself if you want it. Off unless `.agent-plus/skill-feedback/.enabled` exists.

## Install

### Marketplace

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install skill-feedback@agent-plus
```

### Session-only

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/skill-feedback
```

### Standalone

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/skill-feedback/bin/skill-feedback
chmod +x skill-feedback
./skill-feedback log my-skill --rating 4 --outcome success --friction "missing --json"
```

Python 3.9+ stdlib only. Optional: `gh` on PATH for `submit --no-dry-run` to file directly.

## Tests

```bash
python3 -m pytest skill-feedback/test/ -v
```
