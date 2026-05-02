---
name: skill-plus
description: Mine the Claude Code session log to find commands the user keeps typing by hand, then scaffold them into proper Claude skills under `.claude/skills/<name>/`. Read-only audit of existing skills against the framework contract via `list`. Cross-source feedback aggregator joining `skill-feedback` ratings with implicit session-mining failure signals via `feedback`. Promote project-local skills to a `<user>/agent-plus-skills` marketplace via `promote`. Stdlib Python, local-only, no network.
when_to_use: Trigger when the user says "I keep doing this", "I've done this three times", "I keep running the same command", "make this repeatable", "I do this every PR", "make this a skill", "what skills should I have", "audit my skills", "scaffold a skill for X", "promote this skill to my marketplace", "show me what I do repeatedly", "feedback on the framework", "is this skill any good", "session mining", "what's in my session log". Also trigger AFTER a stretch of repetitive Bash work where the user expresses friction — surface `skill-plus propose` to show the candidates that have already been mined.
allowed-tools: Bash(skill-plus:*) Bash(python3 *skill-plus*:*)
---

# skill-plus

The fifth universal primitive of agent-plus. Turns repeated Bash patterns from real Claude Code sessions into project-scoped Claude skills, audits the skills you already have, and feeds back two streams of evidence (explicit ratings + implicit failure signals) into one quality loop.

The binary lives at `${CLAUDE_SKILL_DIR}/../../bin/skill-plus`; the plugin auto-adds `bin/` to PATH, so call it as `skill-plus`.

## When to reach for this

**Mining loop:**

```bash
# First time in a project — grant consent (one-shot per project)
skill-plus scan --accept-consent

# Show ranked candidates from the candidate log
skill-plus propose --pretty

# Scaffold the top candidate into a real skill
skill-plus scaffold railway-probe \
  --from-candidate 8ad12e3f9be1 \
  --description "One-shot Railway error probe across services" \
  --when-to-use "Triggers on 'is staging green' / 'why is api 500ing' / 'check railway errors'" \
  --do-not-use-for "deploys; env-var management; logs over 1h windows"

# Make scanning continuous (weekly)
skill-plus install-cron --frequency weekly
```

**Audit + feedback loop:**

```bash
# What's in this project, scored against the contract
skill-plus list --pretty

# Where are agents falling back from existing plugins? Where's the discoverability gap?
skill-plus feedback --pretty

# Single skill deep-dive
skill-plus feedback --skill repo-analyze --pretty
```

**Marketplace lifecycle:**

```bash
# Default is dry-run — see the plan
skill-plus promote railway-probe --to osouthgate/agent-plus-skills

# Ship it — copies into the marketplace clone, mutates marketplace.json#skills,
# removes the project-local copy unless --keep-local
skill-plus promote railway-probe --to osouthgate/agent-plus-skills --no-dry-run
```

## Killer command

`skill-plus scaffold <name> --from-candidate <id>` — turn a mined session pattern into a complete `.claude/skills/<name>/` skeleton (SKILL.md + POSIX + Windows launchers + stdlib Python entry with envelope helpers, redactor, layered env resolver) with the killer command pre-filled from the evidence. One call replaces the blank-page boilerplate that is the friction every skill author hits.

## Do NOT use this for

- **Logging skill ratings** — that's `skill-feedback`'s job. `skill-plus feedback` is a read-side aggregator over `skill-feedback`'s output, not a competing logger.
- **Diff summarization** — `diff-summary`.
- **Repo orientation** — `repo-analyze`.
- **Generating skills from a stack ("I have postgres, give me a postgres skill")** — that's shape-matching, not evidence. Mine the session log instead.
- **Anything that requires reading session content beyond `Bash` tool calls** — by design, only Bash invocations are clustered. Other tool types are out of scope for v0.

## Safety rules

- **No transcript ever leaves the machine.** All processing local; no network calls.
- **Consent is mandatory.** First scan in a project requires `--accept-consent` or prior interactive grant; cron consent is captured at install time. Recorded in `~/.agent-plus/skill-plus/consent.json`. Without consent, `scan` returns rc=2 and exits clean — never reads the log.
- **Secret redaction runs before write.** Every command is scrubbed for known token patterns (GitHub, AWS, Anthropic, Langfuse, Stripe, OpenAI-style, OpenRouter, Supabase, Sentry, Google, Slack, Discord, JWTs, Bearer, Authorization, connection strings, `--token=`/`--password=`/`--secret=` argv) **before** it lands in `candidates.jsonl`. Display, on-disk persistence, and `--from-candidate` seeding all read from the already-redacted store.
- **Read-only by default.** `propose`, `list`, `feedback` never write. `scaffold` writes only inside `.claude/skills/<name>/` and refuses to overwrite without `--force`. `promote --no-dry-run` is the only command that touches another repo, and it's gated behind explicit opt-out of the dry-run default.
- **Cross-project mining is opt-in.** `--all-projects` requires the flag; default scope is the current project only.

## Architecture

`bin/skill-plus` is a stdlib Python 3.9+ launcher — no pip installs, no requirements. Subcommand handlers live in `bin/_subcommands/<name>.py` and are loaded via `importlib.SourceFileLoader` so each parallel-developed slice owns its own file (no merge conflicts on the dispatcher). Shared helpers (`project_state_root`, `scrub_text`, `_git_toplevel`, `session_files_for_project`, `has_consent_for`, etc.) are injected into each subcommand module's namespace at load time.

Storage precedence mirrors `skill-feedback`: `SKILL_PLUS_DIR` env override → `<git-toplevel>/.agent-plus/skill-plus/` → `<cwd>/.agent-plus/skill-plus/` → `~/.agent-plus/skill-plus/`. Per-user config + consent always live in `~/.agent-plus/skill-plus/`.

Envelope contract: `tool: {name, version}` top-level on every payload. `--output PATH` offloads the full JSON to disk and returns a compact summary (`payloadPath`, `bytes`, `payloadKeys`, `payloadShape`). `--shape-depth 1|2|3` controls how deep the shape recurses. Matches the `repo-analyze` / `diff-summary` / `skill-feedback` shape exactly.
