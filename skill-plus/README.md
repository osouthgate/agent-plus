# skill-plus

> Part of [**agent-plus**](../README.md) · siblings: [`agent-plus`](../agent-plus) · [`repo-analyze`](../repo-analyze) · [`diff-summary`](../diff-summary) · [`skill-feedback`](../skill-feedback)

Authoring a good Claude skill isn't hard because of the boilerplate — it's hard because picking the **killer command** is guesswork. Repo-introspection ("you have postgres, want a postgres skill?") is shape-matching; it has no idea what you actually do.

`skill-plus` is **evidence-driven**: it reads your real Claude Code session JSONL transcripts, clusters the commands you keep typing, and surfaces them as candidates. Then it scaffolds a skill that matches the contract every other agent-plus primitive follows — frontmatter, killer command, "do NOT use this for", layered config, redaction. Lifecycle: **session log → mined candidate → scaffolded skill → marketplace plugin → community discovery.** Stdlib-only Python 3, no SaaS, no SDK. Sessions never leave the machine.

## Headline commands

```bash
skill-plus scan             [--accept-consent] [--all-projects] [--pretty]
skill-plus propose          [--limit 10] [--pretty]
skill-plus install-cron     [--frequency weekly]
skill-plus scaffold <name>  [--description ...] [--when-to-use ...]
                            [--killer-command ...] [--do-not-use-for ...]
                            [--from-candidate <id>]
skill-plus list             [--pretty]
skill-plus feedback         [--pretty]
skill-plus promote <name>   [--to <user>/<repo>] [--no-dry-run] [--keep-local]
skill-plus --version
```

Every subcommand emits envelope-compliant JSON; `--pretty` for human reading.

### scan — find what you actually do

Walks `~/.claude/projects/<encoded-cwd>/*.jsonl`, extracts `Bash` tool calls, clusters by first-three-tokens, applies a denylist (`git status`, `ls`, `grep` — 80% of Bash calls and never skill candidates), and writes deduped candidates to `<git-toplevel>/.agent-plus/skill-plus/candidates.jsonl`. Allowlist bias keeps anything carrying `--service`, `--env`, `--project`, `--deployment`, or an MCP tool name through the filter.

```json
{
  "tool": {"name": "skill-plus", "version": "0.1.0"},
  "project": "/Users/me/checkout",
  "sessionsScanned": 12,
  "candidatesNew": 3,
  "candidatesUpdated": 2,
  "candidates": [
    {"id": "8ad12e3f9be1", "key": "railway logs --service",
     "count": 14, "sessions": ["a1f3", "b22a", "c91b"],
     "examples": ["railway logs --service api --since 5m", "..."],
     "firstSeen": "2026-04-15T08:42:00Z", "lastSeen": "2026-04-29T17:11:00Z"}
  ]
}
```

### propose — pick the best one

Reads the candidate log, scores by `count + 0.5 × distinct_sessions + recency_boost`, returns the top N. Each row carries a `proposedSkillName` (e.g. `railway logs --service` → `railway-logs`) and `kind: "new" | "enhance"` — flips to `enhance` when a skill of that name already exists.

### scaffold — turn it into a skill

```bash
skill-plus scaffold railway-probe \
  --description "One-shot Railway error probe across services" \
  --when-to-use "Triggers on 'is staging green', 'why is api 500ing'" \
  --killer-command "probe-errors <service> [--since 5m]" \
  --do-not-use-for "deploys; env-var management; logs over 1h windows"
```

Writes `.claude/skills/railway-probe/{SKILL.md, bin/railway-probe, bin/railway-probe.cmd, bin/railway-probe.py}`. Required slots are non-skippable — pass them on the CLI or via `--from-candidate <id>` to seed the killer command from a mined pattern. Generated `.py` is self-contained, stdlib-only, ships the same redactor `skill-plus` uses internally.

### list — audit what you have

Walks `<project>/.claude/skills/`, scores each skill against the framework contract: frontmatter (`description`, `when_to_use`, `allowed-tools`), required body sections (`## Killer command`, `## Do NOT use this for`, `## Safety rules`), POSIX + Windows launchers, stdlib-only imports. Sorted worst-first.

### feedback — close the loop

Reads `.agent-plus/skill-feedback/<skill>.jsonl` (explicit ratings) AND the session log (implicit signals: plugin invocation followed by manual fallback, plugin re-invoked with different flags within 5 calls, raw command pattern that an installed plugin would obviate but the user keeps typing). Joins both streams per-skill, ranks by combined-concern score. Read-only — never mutates either log.

### promote — ship it to the marketplace

```bash
skill-plus promote railway-probe --to osouthgate/agent-plus-skills --no-dry-run
```

Validates the skill against the contract, copies the directory into your local marketplace clone, adds `{name, version, path, obviates}` to `marketplace.json`'s `skills` array, removes the project-local copy unless `--keep-local`. Dry-run by default.

### install-cron — make it continuous

```bash
skill-plus install-cron --frequency weekly
```

POSIX: idempotent crontab edit, marker-line keyed. Windows: `schtasks` with sanitized task name. Cron consent captured at install time; cron itself runs `scan --accept-consent` and never writes outside `~/.agent-plus/skill-plus/`. Errors land in `<state>/scan.log`.

## Privacy

- **No transcript ever leaves the machine.** All processing local; no network calls.
- **Consent gate.** First scan in a project requires `--accept-consent` (or interactive grant); cron consent captured at install time. Recorded in `~/.agent-plus/skill-plus/consent.json`.
- **Secret redaction before write.** Every scrubbed command runs through patterns covering GitHub PATs (`ghp_…`, `github_pat_…`, `gho_/ghu_/ghs_/ghr_…`), AWS access keys (`AKIA…`), Anthropic (`sk-ant-…`), Langfuse (`pk-lf-…` / `sk-lf-…`), Stripe (`sk_live_/sk_test_/rk_/pk_…`), generic OpenAI-style `sk-…`, OpenRouter (`sk-or-…`), Supabase (`sbp_…`), Sentry (`sntrys_…`), Google API keys (`AIza…`), Slack (`xoxb-/xoxa-/xoxp-/xoxr-/xoxs-…`), Slack/Discord webhook URLs, Discord bot tokens, JWTs (`eyJ…`), `Bearer …`, `Authorization: …`, connection strings (`postgres://user:pw@host/…`), and `--token=…` / `--password=…` / `--secret=…` argv pairs **before** they're written to `candidates.jsonl`.
- **Scope defaults narrow.** Current project, last 30 days, last 50 sessions. `--all-projects` is opt-in with a stronger prompt.
- **Read-only by default.** `propose`, `list`, `feedback` never write. `scan` writes `candidates.jsonl` and `last-scan.txt`. `scaffold` writes inside `.claude/skills/<name>/` only — explicit and pre-announced.

## State layout

```
~/.agent-plus/skill-plus/                  # per-user, machine-local
  config.json                              # defaultMarketplace, prefs
  consent.json                             # per-project consent grants

<git-toplevel>/.agent-plus/skill-plus/     # per-repo, gitignored
  candidates.jsonl                         # mined patterns, deduped by id
  last-scan.txt                            # watermark for incremental scans
  scan.log                                 # cron error log
```

Storage precedence (highest first): `SKILL_PLUS_DIR` env override → git toplevel → cwd `.agent-plus/` if present → home fallback.

## Install

### Marketplace

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install skill-plus@agent-plus
```

### Standalone

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/skill-plus/bin/skill-plus
chmod +x skill-plus
./skill-plus scan --pretty
```

Python 3.9+ stdlib only. No pip installs.

## Tests

```bash
python3 -m pytest skill-plus/test/ -v
```

83 tests covering envelope contract, foundation helpers, scan (clustering / denylist / dedupe / redaction / malformed JSONL / consent gate / cap), propose (ranking / limit / name derivation / enhance-flip), scaffold (slot validation / from-candidate seeding / generated bin runs), list (frontmatter parser / contract checks / non-stdlib import detection), install-cron (POSIX + Windows idempotency / reinstall detection / consent), feedback (stream-1 aggregation / stream-2 fallback rate / discoverability gap), and promote (live marketplace shape / contract validation / dry-run default).
