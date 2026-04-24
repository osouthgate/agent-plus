# agent-plus

A handful of [Claude Code](https://claude.com/claude-code) plugins that cut the tool-call and token cost of driving third-party APIs from an AI agent.

Every plugin here exists because doing the same job by hand — `curl` + `jq` + raw CLI — made Claude burn round-trips, ingest giant payloads, or copy UUIDs across calls until someone got tired of watching the token meter climb and wrote a wrapper.

## The time savings, concretely

| Plugin | Without it | With it |
| :--- | :--- | :--- |
| [`hermes-remote`](./hermes-remote) | A recurring Sonnet-on-every-tick cron burned **~$10 / 12h**. | Skillify pattern — `--script` does the deterministic work, minimal Haiku prompt decides "report or `[SILENT]`". Three orders of magnitude cheaper; successful quiet runs cost pennies. |
| [`railway-ops`](./railway-ops) | Five sequential `railway` calls per service × N services for incident triage — **~40s on a 5-service project**, plus raw logs and env var *values* landing in the transcript. | `overview` — one call, parallel under the hood, **~8s**, classified errors/warnings only, env var *names* only (values stripped by a canary-tested invariant). |
| [`langfuse`](./langfuse) | Four separate API hits to piece together "what went wrong for user X" — users, sessions, traces, observations. | `monitor-user <id>` — **1 structured JSON blob** with daily totals, recent sessions, latest trace per session, error observations. |
| [`coolify-remote`](./coolify-remote) | Set env var → redeploy → poll → verify in container. Four calls + a hand-rolled `until` loop that breaks on the Windows bash shim. | `env set hermes KEY=val --verify --deploy --wait` — **one call, one exit code**. |
| [`openrouter-remote`](./openrouter-remote) | Pull the 350+ model catalogue into context, let Claude filter in-prompt. | `models list --supports tools --min-context 200000 --max-price-input 1.0` — client-side filter, **only matching rows ever reach Claude**. |
| [`hcloud-remote`](./hcloud-remote) | `curl api.hetzner.cloud \| python3 -c "..."` — mangled by Windows bash shim, multiline heredocs break. | `hcloud-remote ssh hermes-vps` — resolves name to IP, execs ssh, in-process JSON parsing. |
| [`supabase-remote`](./supabase-remote) | `supabase db query` returns a JSON envelope with an "untrusted data" preamble when it detects an agent — parsing without that knowledge produces junk. | `sql-inline` / `sql` strips the envelope, returns plain JSON. Plus `rls-audit` — one call, every table, RLS status + policy count. |

These aren't theoretical. Each row is a pain point that got codified after burning time.

## The five patterns that make this work

Every plugin reinforces at least one of these. If you're writing a new plugin, start here:

1. **Aggregate server-side, return one blob.** The CLI hits N endpoints in parallel, stitches the result, returns one structured payload. The agent sees one tool call.
2. **Resolve by name, not ID.** `coolify-remote deploy hermes` — not `coolify-remote deploy b1c6e2f0-4a3d-4d77-ae6f-...`. UUIDs never touch the agent's context.
3. **`--wait` on every async mutation.** Deploys, cron triggers, backups — if it returns an action ID, the CLI polls for you. No hand-rolled loops.
4. **`--json` on every list / show.** Structured output into `jq` is the default. Human-formatted output is for interactive use.
5. **Strip values the agent shouldn't see.** Env var values, secrets, long blobs — if the agent doesn't need it to decide the next step, it doesn't go into the transcript.

## Plugins

| Plugin | What it wraps | Headline commands |
| :--- | :--- | :--- |
| [`hermes-remote`](./hermes-remote) | [Hermes Agent](https://github.com/NousResearch/hermes-agent) deployments | `status`, `cron list/create/trigger`, `config get/set`, `chat`, `env list` |
| [`langfuse`](./langfuse) | [Langfuse](https://langfuse.com) (cloud or self-hosted) | `health`, `monitor-user`, `export-prompts`, `migrate-prompts`, `trace-ping` |
| [`coolify-remote`](./coolify-remote) | [Coolify](https://coolify.io) PaaS | `app list`, `env set --verify --deploy --wait`, `tls enable`, `deploy --wait`, `app exec` |
| [`hcloud-remote`](./hcloud-remote) | [Hetzner Cloud](https://hetzner.com/cloud) (day-to-day ops only) | `server list/show/reboot`, `snapshot create/list`, `ssh <name>` |
| [`openrouter-remote`](./openrouter-remote) | [OpenRouter](https://openrouter.ai) | `balance --alert-below`, `usage`, `models list/cheap/endpoints`, `keys create/disable/set-limit` |
| [`railway-ops`](./railway-ops) | [Railway](https://railway.app) (read-only triage) | `overview`, `errors <service>`, `envs <service>` (names only) |
| [`supabase-remote`](./supabase-remote) | [Supabase](https://supabase.com) | `projects list`, `sql`, `sql-inline`, `rls-audit`, `gen-types` |

Per-plugin READMEs have the full reference and the specific gotchas they collapse.

## Shared conventions

Same shape across every plugin so switching between them is cheap:

- **Stdlib-only Python 3.** No `pip install`, no venvs. `bin/<plugin>` is one file — copy it anywhere on `$PATH` and run standalone if you don't want the Claude Code wrapper.
- **Layered `.env` autoload**, highest precedence first: `--env-file` → project `.env.local` / `.env` (walked up from cwd) → shell env. **Project `.env` wins over shell** — drop one in the repo you're working in and it overrides whatever globals you have set. Each plugin scopes to its own prefix (`HERMES_*`, `COOLIFY_*`, `LANGFUSE_*`, etc.) so configs don't cross-pollute. Exception: `railway-ops` defers to the `railway` CLI's own auth.
- **Missing-config errors point to both locations** — the project `.env` and `~/.claude/settings.json` — so the user knows where the value should live.
- **Per-plugin `CHANGELOG.md`** for release notes and incident / pain-point logging. Short: what changed, why it matters, date.

## Install

### Recommended — marketplace install (persistent)

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install hermes-remote@agent-plus     # or any other plugin
claude plugin install coolify-remote@agent-plus
claude plugin install hcloud-remote@agent-plus
claude plugin install openrouter-remote@agent-plus
claude plugin install langfuse@agent-plus
claude plugin install railway-ops@agent-plus
claude plugin install supabase-remote@agent-plus
```

Update later:

```bash
claude plugin marketplace update agent-plus
claude plugin update hermes-remote
```

### Session-only — from a local clone

`--plugin-dir` loads a plugin for the current shell only. Good for hacking on a plugin or trying one before installing:

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/hermes-remote
```

Stack multiple plugins by repeating the flag.

### Standalone — no Claude Code at all

Every `bin/<plugin>` is a stdlib Python 3 script. Copy to `$PATH`, run it. See each plugin's README for the one-line `curl -O` install.

## Philosophy

Rule from [Garry Tan's skillify post](https://x.com/garrytan): **deterministic work belongs in scripts, not prompts.** The LLM orchestrates; the code does. Every plugin ships a `SKILL.md` that teaches Claude *when* to reach for the script, not how to reinvent it in a prompt.

That's the whole game. If a plugin's `bin/` script starts embedding LLM calls, something's wrong — the prompting belongs in the caller (Claude Code session, Hermes cron), the determinism belongs in the CLI.

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

See [Claude Code plugin docs](https://code.claude.com/docs/en/plugins) for the full spec, and [`AGENTS.md`](./AGENTS.md) for the writing conventions and doc-drift rules used in this repo.

Iterate locally without reinstalling:

```bash
claude --plugin-dir ./<plugin-name>
# edit SKILL.md / bin/<name> / README.md
# /reload-plugins to pick up changes
```

## License

MIT, see [LICENSE](./LICENSE).
