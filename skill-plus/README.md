# skill-plus

> Part of [**agent-plus**](../README.md) · siblings: [`agent-plus-meta`](../agent-plus-meta) · [`repo-analyze`](../repo-analyze) · [`diff-summary`](../diff-summary) · [`skill-feedback`](../skill-feedback)

Authoring a good Claude skill isn't hard because of the boilerplate — it's hard because picking the **killer command** is guesswork. Repo-introspection ("you have postgres, want a postgres skill?") is shape-matching; it has no idea what you actually do.

`skill-plus` is **evidence-driven**: it reads your real Claude Code session JSONL transcripts, clusters the commands you keep typing, and surfaces them as candidates. Then it scaffolds a skill that matches the contract every other agent-plus primitive follows — frontmatter, killer command, "do NOT use this for", layered config, redaction. Lifecycle: **session log → mined candidate → scaffolded skill → marketplace plugin → community discovery.** Stdlib-only Python 3, no SaaS, no SDK. Sessions never leave the machine.

## Headline commands

```bash
skill-plus scan             [--accept-consent] [--all-projects] [--pretty]
skill-plus propose          [--limit 10] [--kind skill|routine|habit|blocks|all] [--pretty]
skill-plus routine          (--adopt ID | --dismiss ID) [--pretty]
skill-plus install-cron     [--frequency weekly]
skill-plus scaffold <name>  [--description ...] [--when-to-use ...]
                            [--killer-command ...] [--do-not-use-for ...]
                            [--from-candidate <id>]
skill-plus list             [--pretty]
skill-plus feedback         [--pretty]
skill-plus promote <name>   [--to <user>/<repo>] [--no-dry-run] [--keep-local]
skill-plus globalize <name> [--no-dry-run] [--keep-local] [--force]
skill-plus localize <name>  [--no-dry-run] [--keep-local] [--force]
skill-plus where <name>
skill-plus team-sync <name> [--no-dry-run] [--force]
skill-plus collisions       [--no-dry-run] [--auto] [--rename name:scope:new-name]...
skill-plus inquire <tool>   [--audit] [--plugin-path <path>] [--cli <name>]
                            [--no-cache] [--refresh] [--clear-cache]
skill-plus --version
```

Every subcommand emits envelope-compliant JSON; `--pretty` for human reading.

### scan — find what you actually do

Walks `~/.claude/projects/<encoded-cwd>/*.jsonl`, extracts `Bash` tool calls, clusters by first-three-tokens, applies a denylist (`git status`, `ls`, `grep` — 80% of Bash calls and never skill candidates), and writes deduped candidates to `<git-toplevel>/.agent-plus/skill-plus/candidates.jsonl`. Allowlist bias keeps anything carrying `--service`, `--env`, `--project`, `--deployment`, or an MCP tool name through the filter. Every persisted string is scrubbed on write (`scrub_text`): API tokens, `Authorization`/`Cookie` header values, and `VAR=secret` env-assignments (e.g. `NETLIFY_AUTH_TOKEN=…`, `AWS_SECRET_ACCESS_KEY=…`) become `[REDACTED]`, and the whole file is re-scrubbed on every rewrite so a pattern added in a later version also cleans records mined before it existed.

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

**Zero-session diagnosis (v0.6.1).** A `0` in `sessionsScanned` used to be a silent no-op. Now the envelope explains why:

```json
{
  "sessionsScanned": 0,
  "zeroReason": "project_dir_missing",
  "diagnostics": {
    "slug": "C--dev-patchboard",
    "projectDir": "/home/me/.claude/projects/C--dev-patchboard",
    "projectDirExists": false, "rawSessionFiles": 0, "filteredByCutoff": 0
  },
  "hint": "0 sessions for slug C--dev-patchboard but 2 other projects have history - possible slug mismatch; use --since-days N for backfill"
}
```

- **`zeroReason`** — `"project_dir_missing"` (no `~/.claude/projects/<slug>` for this project at all), `"all_before_cutoff"` (sessions exist but every one predates `--since-days`), or `"filtered_by_caps"` (in-window sessions were all cut by `--max-sessions`); checked in that precedence order. `null` on any scan where `sessionsScanned > 0`.
- **`diagnostics`** — always present, regardless of `zeroReason` or `--all-projects`. Reports the resolved slug, its on-disk path, whether that directory exists, and raw vs. cutoff-filtered session counts for this project's own slug specifically, so "0 sessions" is diagnosable instead of a dead end.
- **`hint`** — present whenever `sessionsScanned` is `0` for this project's slug, *whichever* `zeroReason` applies, as long as `~/.claude/projects/` has *other* project directories with history — a likely slug-mismatch signal (not gated to `project_dir_missing` specifically; `all_before_cutoff` or `filtered_by_caps` alongside other active projects trips it too). Points at widening `--since-days N` (default 30) to backfill once the underlying cause is fixed: `scan`'s watermark only advances on a run that actually finds sessions, so a run that found 0 never burns the window — the old sessions are still there to mine once the mismatch is resolved.

### propose — pick the best one

Reads the candidate log, scores by `count + 0.5 × distinct_sessions + recency_boost`, returns the top N. Each row carries a `proposedSkillName` (e.g. `railway logs --service` → `railway-logs`) and `kind: "new" | "enhance"` — flips to `enhance` when a skill of that name already exists.

### propose --kind routine|habit, and routine — the cadence lens

`scan` also runs a **temporal** pass over the same session history (full history, watermark-bypassed) and writes `routine-candidates.jsonl`. A cluster that recurs on `>= 5` distinct calendar dates is classified:

- **`routine`** — tight `(weekday-class, 3h-bucket)` signature (`>= 50%` of hits in one 3h bucket AND `>= 70%` in one weekday-class). Gets a deterministic, paste-ready `scheduleString` for Claude Code [routines](https://code.claude.com/docs/en/routines), e.g. `On weekday mornings around 09:00 UTC, run the workflow that does \`vercel deploy --prod\` (refine this intent before saving)`. The `(refine ...)` marker is honest: agent-plus runs offline and cannot infer task intent, so the command is a proxy you sharpen.
- **`habit`** — recurs on many dates but the time is diffuse (no schedulable trigger). Gets a `suggestion` to consider a skill instead, not a `scheduleString`.

```bash
skill-plus propose --kind routine     # schedulable cadences only
skill-plus propose --kind habit       # diffuse repeats -> skill candidates
skill-plus propose --kind all         # both
skill-plus routine --adopt <id>       # you set this up -> suppress from future scans
skill-plus routine --dismiss <id>     # not useful -> de-rank, annotate "dismissed Nx"
```

**Known limitation (documented, not hidden):** Claude Code routines require a Pro/Max/Team/Enterprise plan with Claude Code on the web, and the cron minimum interval is 1 hour. The detector runs fully offline regardless — `propose`/`routine` work without any of that; only the eventual `/schedule` paste needs it. On organic history with no clock-shaped cadence the detector correctly emits **zero** routine candidates (it is precision-first); `habit` is what surfaces for diffuse-but-frequent work.

### propose --kind blocks — the friction lens

`scan` additionally mines tool-permission blocks and denials from the same session history into `blocks.jsonl`. `propose --kind blocks` ranks the block clusters so you can remove the friction at the source — a settings allowlist entry, a skill, or a workflow change — instead of re-hitting the same denial every session. Records pass through the same `scrub_text` redaction as every other persisted log.

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

Reads `~/.agent-plus/skill-feedback/<skill>.jsonl` (explicit ratings — user-global, resolved the same way the skill-feedback CLI does: `SKILL_FEEDBACK_DIR` env overrides) AND the session log (implicit signals: plugin invocation followed by manual fallback, plugin re-invoked with different flags within 5 calls, raw command pattern that an installed plugin would obviate but the user keeps typing). Joins both streams per-skill, ranks by combined-concern score. Read-only — never mutates either log.

### promote — ship it to the marketplace

```bash
skill-plus promote railway-probe --to osouthgate/agent-plus-skills --no-dry-run
```

Validates the skill against the contract, copies the directory into your local marketplace clone, adds `{name, version, path, obviates}` to `marketplace.json`'s `skills` array, removes the project-local copy unless `--keep-local`. Dry-run by default.

## Scope topology (v0.3.0)

Skills can live in three places: **project** (`<repo>/.claude/skills/<name>/`), **global** (`~/.claude/skills/<name>/`), and **plugin-installed** (`~/.claude/plugins/cache/**/skills/<name>/`). Five subcommands move skills between scopes and resolve name collisions. All five default to **dry-run**; pass `--no-dry-run` to actually write.

### where — three-tier scope resolver

```bash
skill-plus where my-skill --pretty
```

Walks all three tiers and reports every location, plus a `resolution_hint` reflecting Claude Code's documented loader preference (`project > global > plugin`). Read-only; never writes. Use this to answer "why am I not seeing my skill" or "is this skill colliding with something."

### globalize — move project skill to your user scope

```bash
skill-plus globalize my-skill --no-dry-run
```

Moves `<repo>/.claude/skills/my-skill/` to `~/.claude/skills/my-skill/`. With `--keep-local`, copies instead of moves so the project copy stays. With `--force`, overwrites an existing global skill of the same name. Cross-volume safe via `shutil.move`.

### localize — pull a global skill into the repo

```bash
skill-plus localize my-skill --no-dry-run
```

Symmetric mirror of `globalize`. Source `~/.claude/skills/<name>/`, destination `<repo>/.claude/skills/<name>/`. Same flags.

### team-sync — share a personal skill with the team

```bash
skill-plus team-sync my-skill --no-dry-run
```

One-step alias for "share my personal skill with my team via the repo." Equivalent to `localize <name>` plus an emitted `commit_hint` field suggesting:

```
chore(skills): share my-skill via repo (was global)

Was at ~/.claude/skills/my-skill/, now at .claude/skills/my-skill/
so teammates pick it up automatically.
```

Does **not** invoke git — caller decides whether to commit.

### collisions — detect and resolve name overlaps

```bash
# Interactive (default tty): prompts you per collision
skill-plus collisions --no-dry-run

# Non-interactive (CI / pipe): bails with suggested renames
skill-plus collisions

# Deterministic: project wins, global gets `-global` suffix
skill-plus collisions --auto --no-dry-run

# Scripted: explicit per-collision instruction (repeatable)
skill-plus collisions --rename foo:global:foo-old --no-dry-run
```

UX modes (T1 + T3 in `2026-04-30-scope-topology.md`):
- **Interactive (default tty + no flags):** prompts `[p=project, g=global, s=skip]` then asks for the new name.
- **Non-interactive (no tty, no flags):** emits `verdict: "needs_user_input"` plus a `suggested_renames[]` block listing two candidates per collision (`-project`, `-global` suffixes). No FS writes.
- **Explicit (`--rename name:scope:new-name`, repeatable):** validates the new name is legal (`^[a-zA-Z0-9_-]+$`) and doesn't collide; refuses otherwise.
- **Auto (`--auto`):** project always wins; global side gets `-global` suffix. Deterministic, scriptable.

### inquire — probe a tool, audit a plugin (v0.4.0)

```bash
# Generator mode: probe a tool, get a recommended skill scaffold.
skill-plus inquire github --pretty

# Auditor mode: probe an existing plugin, get a paste-ready PR body.
skill-plus inquire github-remote --audit \
  --plugin-path ~/dev/agent-plus-skills/github-remote --pretty
```

Runs the universal inquiry (Q1-Q7 — error surface, lookup keys, async `--wait`, `--json`, stays in lane, strips secrets, tool envelope) across every available source class (`cli`, `plugin`, `web`, `openapi`, `repo`). At least 2 sources required for non-`unknown` confidence. Web probe uses DuckDuckGo HTML — stdlib `urllib.request` + `html.parser`, no API key, no `pip install`. Q1/Q3 results carry maturity-ladder placement (current rung + recommended next rung) so the audit reads as "Plugin is at Level 1/3, here's Level 2" instead of binary "gap." Audit envelope's `pr_body_draft` field pastes straight into `gh pr create`.

Cache lives at `~/.agent-plus/inquire-cache/<tool>.json`, 7-day TTL. Bypass with `--no-cache`, `--refresh`, or `--clear-cache`.

**Known limitations** (R7 — documented honest signal):

- **Maturity ladder Level 4 ("platform-aware hybrid")** is not detectable from static analysis alone. The auto-detector caps at Level 3. railway-ops, for instance, sits at Level 4 in practice (it picks Railway's CLI for logs because Railway's GraphQL log queries are unreliable, but uses GraphQL for deploy metadata) — a deliberate choice the regex probes can't recognize. Plugins with this kind of platform-quirk awareness will be reported as Level 3 with no further upgrade suggestion. That's honest signal: we don't surface what we can't verify.
- **Q6 (`strips_secrets`) confidence is structurally capped at `medium`.** The behavioral CLI probe is deliberately skipped to avoid touching real auth state. The plugin-source probe (greps for `scrub`/`redact`/etc.) is the only authoritative source; `medium` is the highest honest rating without behavioral corroboration.
- **`max_achievable_level` overrides are tool-specific and finite.** Currently only `vercel-remote: Q1=2` is registered (Vercel's API doesn't expose source-location records). Other platform ceilings will be added as we discover them. Plugin authors who hit a ceiling that isn't documented yet should file an issue with evidence.
- **Web probe quality scales with vendor doc quality.** Mainstream tools (github, vercel, supabase) get rich corroboration. Niche tools or hand-rolled internal CLIs may turn up nothing on web search and degrade to `low_confidence` from CLI alone. Honest signal — inquiry quality varies by tool popularity.

### install-cron — make it continuous

```bash
skill-plus install-cron --frequency weekly
```

POSIX: idempotent crontab edit, marker-line keyed. Windows: `schtasks` with sanitized task name. Cron consent captured at install time; cron itself runs `scan --accept-consent` and never writes outside `~/.agent-plus/skill-plus/`. Errors land in `<state>/scan.log`.

## nextSteps[] chaining

Every output envelope includes a `nextSteps` array. Per-command hints: `scan` → `propose` (with candidate count); `propose` → `scaffold`; `scaffold` → `skill-feedback log` + `promote`; `promote` → `skill-feedback report`. Only injected when `ok` is not explicitly `False` — consent-required and error responses are unaffected.

## Privacy

- **No transcript ever leaves the machine.** All processing local; no network calls.
- **Consent gate.** First scan in a project requires `--accept-consent` (or interactive grant); cron consent captured at install time. Recorded in `~/.agent-plus/skill-plus/consent.json`.
- **Secret redaction before write.** Every scrubbed command runs through patterns covering GitHub PATs (`ghp_…`, `github_pat_…`, `gho_/ghu_/ghs_/ghr_…`), AWS access keys (`AKIA…`), Anthropic (`sk-ant-…`), Langfuse (`pk-lf-…` / `sk-lf-…`), Stripe (`sk_live_/sk_test_/rk_/pk_…`), generic OpenAI-style `sk-…`, OpenRouter (`sk-or-…`), Supabase (`sbp_…`), Sentry (`sntrys_…`), Google API keys (`AIza…`), Slack (`xoxb-/xoxa-/xoxp-/xoxr-/xoxs-…`), Slack/Discord webhook URLs, Discord bot tokens, JWTs (`eyJ…`), `Bearer …`, `Authorization: …`, connection strings (`postgres://user:pw@host/…`), and `--token=…` / `--password=…` / `--secret=…` argv pairs **before** they're written to `candidates.jsonl`.
- **Scope defaults narrow.** Current project, last 30 days (`--since-days N` to widen — e.g. to backfill after fixing a slug mismatch, per the `hint` field above), last 50 sessions (`--max-sessions`). `--all-projects` is opt-in with a stronger prompt.
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

On Windows, the git-toplevel path is normalised from MSYS/Git-Bash form (`/c/dev/foo` -> `C:/dev/foo`) before use — `git rev-parse --show-toplevel` under Git Bash emits POSIX-style paths, which would otherwise resolve project state to a phantom `C:\c\...` tree. Scaffolded skills carry the same normalisation in their generated `bin/<name>.py`.

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
