# agent-plus

**Make Claude Code dramatically cheaper at running your infrastructure.**

Drop-in plugins that collapse slow, multi-step API dances into one fast call — deploys, databases, cloud, billing, logs, repo orientation, diff triage. Mined from real session transcripts, not guessed at. Stdlib Python, also runs standalone.

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install repo-analyze@agent-plus
```

That's it. No SDK, no config file, no auth dance.

---

## What it does

Every cold start in an unfamiliar repo, the same dance: ~67 grep ops, ~60 ls / directory walks, a sweep through `package.json` / `pyproject.toml` / `Cargo.toml` / `go.mod`, a README scan. Mined across real Claude Code transcripts, every time.

`repo-analyze` collapses that into one call:

```bash
$ repo-analyze --pretty | head -25
{
  "tool": {"name": "repo-analyze", "version": "0.2.1"},
  "languages": {"typescript": {"files": 142, "loc": 18203, "percent": 71.4}, ...},
  "frameworks": [
    {"name": "Next.js",     "evidence": "package.json:next",      "confidence": "high"},
    {"name": "TailwindCSS", "evidence": "package.json:tailwindcss", "confidence": "high"}
  ],
  "buildTools": [{"name": "pnpm", "evidence": "pnpm-lock.yaml"}, {"name": "Docker", ...}],
  "deps": { ... },
  "entrypoints": ["src/app/page.tsx", "manage.py", ...],
  "tree": { ... },
  "readme": {"title": "...", "headings": [...]}
}
```

One JSON blob. ~127 tool calls collapsed into 1. The agent stops re-discovering what it already discovered last session.

That's one plugin. The framework ships **five universal primitives**:

| Plugin | What it collapses | Killer command |
| :--- | :--- | :--- |
| [`agent-plus`](./agent-plus) | "What's installed, what's configured, what does this checkout know?" | `init`, `envcheck`, `refresh`, `marketplace install\|search\|prefer` |
| [`repo-analyze`](./repo-analyze) | The ~67-grep + ~60-ls cold-start dance for unfamiliar repos | `repo-analyze [--output] [--shape-depth] [--pretty]` |
| [`diff-summary`](./diff-summary) | The 5–20 Read calls to triage a PR ("test? source? config? did the public API change?") | `diff-summary [--staged \| --base BRANCH \| --range A..B] [--public-api-only] [--risk MIN]` |
| [`skill-feedback`](./skill-feedback) | "Was that skill any good?" — agent self-rates, JSONL on disk, optional bundle into a GitHub issue | `log <skill> --rating --outcome [--friction]`, `report`, `submit` |
| [`skill-plus`](./skill-plus) | "I keep typing this by hand" → mine the session log, scaffold a real skill, promote it to your marketplace | `scan`, `propose`, `scaffold <name> --from-candidate <id>`, `list`, `feedback`, `promote <name>` |

Plus a **marketplace convention** — `<user>/agent-plus-skills` — for publishing your own service-specific wrappers (GitHub, Vercel, Supabase, Railway, Linear, OpenRouter, Coolify, Hetzner, Hermes, Langfuse, etc.). Reference marketplace lives at [`osouthgate/agent-plus-skills`](https://github.com/osouthgate/agent-plus-skills) — install it, fork it, or use it as a template.

## Install

```bash
# Framework primitives (recommended)
claude plugin marketplace add osouthgate/agent-plus
claude plugin install agent-plus@agent-plus
claude plugin install repo-analyze@agent-plus
claude plugin install diff-summary@agent-plus
claude plugin install skill-feedback@agent-plus
claude plugin install skill-plus@agent-plus

# Reference service-wrapper marketplace (commit-pinned, first-run review)
agent-plus marketplace install osouthgate/agent-plus-skills

# Or scaffold your own marketplace
agent-plus marketplace init <your-handle>/agent-plus-skills

# Discover other people's marketplaces
agent-plus marketplace search [query]
```

Standalone (no Claude Code): every `bin/<plugin>` is one stdlib Python 3 file. Copy to `$PATH`, run.

## Before / after

| Without agent-plus | With agent-plus |
|---|---|
| `~67 grep + ~60 ls` per cold start | `repo-analyze` — 1 call |
| `git diff` + 5–20 Reads to triage a PR | `diff-summary --staged` — 1 call with role + risk per file |
| Manual `gh pr view --json` + `gh run list` + `gh pr checks` triage | `github-remote pr <name>` — one structured overview |
| "Did that skill work? Should I keep using it?" — never tracked | `skill-feedback log` after each use; aggregated reports + GitHub-issue submit |
| "I keep typing this by hand" — stays manual forever | `skill-plus scan` mines the session log, `scaffold` writes the skill |
| UUID-shaped IDs leaking into the agent's context | Name-resolved IDs everywhere; UUIDs never enter the transcript |
| Env-var values, tokens, secrets in command output | NAMES-only — values stripped on read paths, scrub-on-write on log paths |

## How it works — the seven patterns

Every plugin reinforces at least one. Don't write a plugin without first asking which of these it's solving for.

1. **Aggregate server-side, return one blob.** N endpoints in parallel under the hood, one structured payload back. The agent sees one tool call.
2. **Resolve by name, not ID.** `coolify-remote deploy hermes` — not `coolify-remote deploy b1c6e2f0-4a3d-…`. UUIDs never touch the agent's context.
3. **`--wait` on every async mutation.** Deploys, cron triggers, backups — if it returns an action ID, the CLI polls. No hand-rolled `until` loops in prompts.
4. **`--json` on every list / show.** Structured output is the default; human-formatted output is for interactive use only.
5. **Strip values the agent shouldn't see.** Env-var values, secrets, large blobs — if the agent doesn't need them to decide, they don't enter the transcript.
6. **Self-diagnosing output.** Every payload carries `tool: {name, version}` from the manifest at runtime. Stale plugin caches and PATH pinning are visible from the output alone — no extra subprocess call.
7. **Stay in your lane.** Each plugin's SKILL.md lists the cases where the agent should drop to the raw CLI / API instead of looping on a rejection.

## The envelope contract

Every `--json` output uses the same outer shape so an agent can parse, version-check, and offload large payloads identically across plugins.

- `tool.name` / `tool.version` — read from `<plugin>/.claude-plugin/plugin.json` at runtime.
- `--output PATH` writes the full payload to disk; stdout returns a compact summary (`payloadPath`, `bytes`, `payloadKeys`, `payloadShape`).
- `--shape-depth 1|2|3` — recursion depth for `payloadShape` (default 3). Lets the agent decide whether to read the file.
- Universal flags: `--json`, `--pretty`, `--version`, `--output`, `--shape-depth`.
- **No env-var values, no secrets, no tokens.** Names and IDs only.

## The marketplace convention

`<user>/agent-plus-skills` is the convention. Anyone can publish their own collection at their GitHub handle. agent-plus's tooling discovers, installs, and updates them by that naming pattern — borrowed from Homebrew taps and the GitHub Actions marketplace. **No central registry to run.**

```bash
agent-plus marketplace search          # gh search repos topic:agent-plus-skills
agent-plus marketplace install <user>/agent-plus-skills    # commit-pinned + first-run review
agent-plus marketplace list
agent-plus marketplace update [<user>/<repo>]
agent-plus marketplace prefer <user>/<repo> --skill <name>  # collision resolution
agent-plus marketplace remove <user>/<repo>
```

**Trust model — five gates enforced.** Install pins the commit SHA. Nothing in the cloned repo runs at install time. A first-run review is shown once per install (and re-armed on every accepted update). Updates are opt-in only — `--cron` is parsed only so it can be refused. When a marketplace declares `checksums`, install verifies them. Plugins from un-accepted marketplaces are skipped.

Spec: [`plans/todo/2026-04-28-marketplace-convention.md`](./plans/todo/2026-04-28-marketplace-convention.md).

## Philosophy

**Deterministic work belongs in scripts, not prompts.** The LLM orchestrates; the code does. Every plugin ships a `SKILL.md` that teaches Claude *when* to reach for the script, not how to reinvent it in a prompt.

That's the whole game. If a plugin's bin script starts embedding LLM calls, something's wrong — the prompting belongs in the caller (Claude Code session, Hermes cron), the determinism belongs in the CLI.

## Project status

Pre-1.0. The four core primitives (`agent-plus`, `repo-analyze`, `diff-summary`, `skill-feedback`) and the marketplace lifecycle have been dogfooded for months. `skill-plus` is the latest addition (0.1.0). The framework is for **Claude Code** — claude.ai web Skills and Cowork are out of scope (no Bash, no filesystem, no plugin loader). For prompt-template skill generation, see [`claude-reflect`](https://github.com/cnocon/claude-reflect)'s `/reflect-skills` — it complements agent-plus rather than competes with it.

Service wrappers — `github-remote`, `vercel-remote`, `supabase-remote`, `railway-ops`, `linear-remote`, `openrouter-remote`, `langfuse-remote`, `hermes-remote`, `coolify-remote`, `hcloud-remote` — live in [`osouthgate/agent-plus-skills`](https://github.com/osouthgate/agent-plus-skills).

## Conventions across framework plugins

- **Stdlib-only Python 3.** No `pip install`, no venvs. `bin/<plugin>` is one file — copy it anywhere on `$PATH` and run standalone.
- **Layered `.env` autoload**, highest precedence first: `--env-file` → project `.env.local` / `.env` (walked up from cwd) → `~/.agent-plus/.env` → shell env. **Project `.env` wins over shell.**
- **Per-plugin `CHANGELOG.md`** for release notes. Short: what changed, why it matters, date.

## Contributing

Plugins follow the standard Claude Code plugin shape:

```
<plugin>/
├── .claude-plugin/plugin.json
├── bin/<plugin>                  # stdlib Python 3 (some grow large; meta plugin is ~3k LoC)
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
