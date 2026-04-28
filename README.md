# agent-plus

A framework for building deterministic CLI tools that [Claude Code](https://claude.com/claude-code) shells out to. Wraps the slow, multi-step API dances agents otherwise burn round-trips on into single-call structured output. Ships an envelope contract, a handful of universal primitives, and a marketplace convention that lets users publish their own plugin collections under a discoverable name.

> **Status: pre-1.0.** This repo is the framework. A reference marketplace built using it lives at [`osouthgate/agent-plus-skills`](https://github.com/osouthgate/agent-plus-skills) — install it directly, fork it, or use it as a template. You can publish your own marketplace at `<your-handle>/agent-plus-skills` and your team or the public can install your skills the same way.

## What this is

agent-plus is the framework. It defines:

- The **envelope contract** every plugin emits (`tool: {name, version}`, large-payload offload via `payloadPath`, NAMES-only secret discipline).
- The **seven patterns** (below) that make an LLM-friendly CLI tool actually save tokens.
- A handful of **universal primitives** that apply to any project regardless of stack: workspace bootstrap, repo orientation, diff triage, skill self-assessment.
- The **marketplace convention** — `<user>/agent-plus-skills` — for publishing service-specific wrappers (GitHub, Vercel, Supabase, Railway, etc.) that build on the framework.

It does *not* ship wrappers for specific services. Those live in marketplaces.

## What this is for

**Claude Code** (CLI, IDE extensions, desktop). The framework's native target — Bash execution, filesystem access, plugin loader, env-file resolution all work as designed.

## What this is NOT for

**claude.ai (web Skills)** and **Claude Cowork**. Those surfaces run prompt-template skills, not deterministic CLI tools — they don't have Bash execution, can't shell out to a `bin/<name>` Python launcher, and can't tail logs or trigger deploys against external services.

For prompt-template skill generation, see [`claude-reflect`](https://github.com/cnocon/claude-reflect)'s `/reflect-skills` command — it complements agent-plus rather than competes with it. agent-plus = action skills (deterministic CLIs that act on services). claude-reflect = thinking skills (slash commands that shape how Claude reasons).

The marketplace convention's `marketplace.json` schema reserves a `surface` field (default: `claude-code`) so a parallel prompt-template ecosystem could grow under the same naming convention later. For now, treat agent-plus as Claude Code-native.

## Universal primitives (ships in this repo)

The four plugins that apply to any Claude Code project:

| Plugin | Purpose | Headline |
| :--- | :--- | :--- |
| [`agent-plus`](./agent-plus) | The meta plugin — workspace bootstrap, env-var readiness, identity cache, marketplace scaffolding. | `init`, `envcheck`, `refresh`, `list`, `extensions`, `marketplace init <user>/<name>` (shipped); `marketplace install/update/remove` (planned, Phase 2) |
| [`repo-analyze`](./repo-analyze) | Cold-start orientation in any unfamiliar repo. Replaces the ~67 grep + ~60 ls dance with one structured payload. | `repo-analyze [--max-tree-files] [--max-tree-depth] [--output] [--shape-depth] [--pretty]` |
| [`diff-summary`](./diff-summary) | Per-file role + risk classification of a git diff. Replaces 5–20 Read calls with one structured triage. | `diff-summary [--staged \| --base BRANCH \| --range A..B] [--include-patches] [--public-api-only] [--risk MIN] [--output] [--pretty]` |
| [`skill-feedback`](./skill-feedback) | Local-first agent self-assessment for any Claude Code skill. Append-only JSONL, optional bundle-into-GitHub-issue submit. | `log <skill> --rating --outcome [--friction]`, `show`, `report`, `submit` |

A fifth — `skill-plus`, for session-mining-driven skill discovery + scaffold + feedback aggregation — is in design. See [`plans/todo/2026-04-28-skill-plus-plugin.md`](./plans/todo/2026-04-28-skill-plus-plugin.md).

## Service wrappers (live in `osouthgate/agent-plus-skills`)

The 10 service-specific wrappers (`github-remote`, `vercel-remote`, `supabase-remote`, `railway-ops`, `linear-remote`, `openrouter-remote`, `langfuse-remote`, `hermes-remote`, `coolify-remote`, `hcloud-remote`) previously shipped here. They now live in the reference skills marketplace — `osouthgate/agent-plus-skills` — installed via Claude Code's native plugin marketplace command (see Install).

The full pre-extraction snapshot is preserved at `plans-agent-plus-archive/` for reference during migration.

## The marketplace convention

`<user>/agent-plus-skills` is the convention. Anyone can publish their own collection at `<their-github-username>/agent-plus-skills`; agent-plus's tooling discovers, installs, and updates them by that naming pattern.

The pattern is borrowed from Homebrew taps (`<user>/homebrew-<tap>`) and GitHub Actions Marketplace — naming-convention-as-discovery, no central registry to run.

```bash
# Install the framework's universal primitives (uses Claude Code's native marketplace)
claude plugin marketplace add osouthgate/agent-plus
claude plugin install agent-plus@agent-plus
claude plugin install repo-analyze@agent-plus
claude plugin install diff-summary@agent-plus
claude plugin install skill-feedback@agent-plus

# Install the reference skills marketplace (also via Claude Code's native command)
claude plugin marketplace add osouthgate/agent-plus-skills
claude plugin install github-remote@agent-plus-skills
claude plugin install vercel-remote@agent-plus-skills
# ... etc

# Scaffold your own skills marketplace
agent-plus marketplace init <your-github-user>/agent-plus-skills
```

> A native `agent-plus marketplace install / update / remove` flow (with commit pinning, opt-in update, first-run review prompt) is **planned for Phase 2** — for now, use `claude plugin marketplace add` for installs.

Each marketplace declares a `marketplace.json` at its root with: skills it ships, minimum agent-plus version, optional commit pinning for verify-on-install, and a `surface` field (`claude-code` for now). Spec lives in [`plans/todo/2026-04-28-marketplace-convention.md`](./plans/todo/2026-04-28-marketplace-convention.md) (forthcoming).

**Trust model (Phase 2):** install-time pinning to commit SHA, opt-in update flow, explicit first-run review prompt. The native `agent-plus marketplace install` command will not ship without these gates — supply-chain security is the difference between a thriving ecosystem and a single bad-actor incident. Until then, installs go through Claude Code's native `claude plugin marketplace add`.

## The seven patterns

Every plugin built on this framework should reinforce at least one. Don't write a plugin without first asking which of these it's solving for.

1. **Aggregate server-side, return one blob.** N endpoints in parallel under the hood, one structured payload back to the agent. The agent sees one tool call.
2. **Resolve by name, not ID.** `coolify-remote deploy hermes` — not `coolify-remote deploy b1c6e2f0-4a3d-…`. UUIDs never touch the agent's context.
3. **`--wait` on every async mutation.** Deploys, cron triggers, backups — if it returns an action ID, the CLI polls. No hand-rolled `until` loops in the agent's session.
4. **`--json` on every list / show.** Structured output into `jq` is the default. Human-formatted output is for interactive use.
5. **Strip values the agent shouldn't see.** Env-var values, secrets, large blobs — if the agent doesn't need it to decide the next step, it doesn't enter the transcript.
6. **Self-diagnosing output.** Every JSON payload carries a top-level `tool: {name, version}` field read from the plugin manifest at runtime. Stale plugin caches and PATH pinning are visible from the output alone — no extra subprocess call. Every plugin also exposes `--version`.
7. **Stay in your lane.** Each plugin's SKILL.md explicitly lists the cases where the agent should drop to the raw CLI / API instead of looping on a rejection. Wrappers cover the 80% that's cheap to make fast; writes, admin ops, and anything narrowly out-of-scope belong to the upstream tool.

## Envelope contract

Every plugin's `--json` output uses the same outer shape so an agent can parse, version-check, and offload large payloads identically across plugins.

**Top-level keys.** Every JSON payload is an object with at minimum:

- `tool.name` — plugin name, read from `<plugin>/.claude-plugin/plugin.json` at runtime.
- `tool.version` — plugin version, same source. Falls back to `"unknown"` if the manifest is unreadable; never raises.
- The plugin's actual payload alongside (e.g. `pr`, `services`, `deployments`).

**Large-payload offload.** When a command supports `--output PATH`, the full payload is written to disk. Stdout returns a compact envelope:

- `payloadPath` — absolute path of the written file.
- `payloadShape` — recursive shape descriptor (keys + types + sizes, no values), so the agent can decide whether to read the file. Recursion depth controlled by `--shape-depth N` (default 3, valid 1–3).

> **Note:** the `payloadPath` field was renamed from the earlier `savedTo` during the framework extraction (slice A0, 2026-04-28 — coordinated minor bump across `agent-plus`, `repo-analyze`, `diff-summary`, `skill-feedback`). Wrapper plugins on `osouthgate/agent-plus-skills` migrate independently.

**Universal flags.**

- `--json` — structured output; default mode.
- `--version` — print `tool.version` and exit 0.
- `--pretty` — indented JSON.
- `--output PATH` — offload large payloads; emit compact envelope on stdout.
- `--shape-depth 1|2|3` — recursion depth for `payloadShape`.

**What is not in the envelope.** No env-var values, no secrets, no tokens. Names and IDs only (Pattern 5).

**Stability.** Plugin authors should treat these field names as a public contract. Backward-incompatible renames bump the major plugin version. New optional fields are fine.

## Shared conventions across framework plugins

- **Stdlib-only Python 3.** No `pip install`, no venvs. `bin/<plugin>` is one file — copy it anywhere on `$PATH` and run standalone.
- **Layered `.env` autoload**, highest precedence first: `--env-file` → project `.env.local` / `.env` (walked up from cwd) → `~/.agent-plus/.env` → shell env. **Project `.env` wins over shell.**
- **Per-plugin `CHANGELOG.md`** for release notes. Short: what changed, why it matters, date.

## Install

### Recommended — marketplace install (via Claude Code's native command)

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install agent-plus@agent-plus       # meta plugin (recommended first)
claude plugin install repo-analyze@agent-plus
claude plugin install diff-summary@agent-plus
claude plugin install skill-feedback@agent-plus
```

Update later:

```bash
claude plugin marketplace update agent-plus
claude plugin update repo-analyze
```

> An `agent-plus marketplace install/update/remove` flow with commit pinning is planned for Phase 2. Today, only `agent-plus marketplace init <user>/<name>` (scaffolder) ships.

### Session-only — from a local clone

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/repo-analyze
```

Stack multiple plugins by repeating the flag.

### Standalone — no Claude Code at all

Every `bin/<plugin>` is a stdlib Python 3 script. Copy to `$PATH`, run it. See each plugin's README.

## Philosophy

Rule from [Garry Tan's skillify post](https://x.com/garrytan): **deterministic work belongs in scripts, not prompts.** The LLM orchestrates; the code does. Every plugin ships a `SKILL.md` that teaches Claude *when* to reach for the script, not how to reinvent it in a prompt.

That's the whole game. If a plugin's `bin/` script starts embedding LLM calls, something's wrong — the prompting belongs in the caller (Claude Code session, Hermes cron), the determinism belongs in the CLI.

## Roadmap

Active design and migration plans:

- [`2026-04-28-skill-plus-plugin.md`](./plans/todo/2026-04-28-skill-plus-plugin.md) — discovery + scaffolding + feedback aggregator. The framework's fifth universal primitive.
- [`2026-04-28-existing-plugin-audit.md`](./plans/todo/2026-04-28-existing-plugin-audit.md) — quality + tier audit of the pre-extraction plugin set.
- [`2026-04-28-framework-extraction.md`](./plans/todo/2026-04-28-framework-extraction.md) *(forthcoming)* — migration of the 10 service wrappers to `osouthgate/agent-plus-skills`.
- [`2026-04-28-marketplace-convention.md`](./plans/todo/2026-04-28-marketplace-convention.md) *(forthcoming)* — `<user>/agent-plus-skills` convention, `marketplace.json` schema, install/update/trust protocol.

## Contributing / development

Plugins follow the standard Claude Code plugin shape:

```
<plugin>/
├── .claude-plugin/plugin.json
├── bin/<plugin>                  # stdlib Python 3, ~500 lines max
├── skills/<plugin>/SKILL.md
├── README.md
├── CHANGELOG.md
└── LICENSE (or inherits root)
```

See [Claude Code plugin docs](https://code.claude.com/docs/en/plugins) for the full spec and [`AGENTS.md`](./AGENTS.md) for the writing conventions and doc-drift rules used in this repo.

Iterate locally without reinstalling:

```bash
claude --plugin-dir ./<plugin-name>
# edit SKILL.md / bin/<name> / README.md
# /reload-plugins to pick up changes
```

## License

MIT, see [LICENSE](./LICENSE).
