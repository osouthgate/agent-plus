# agent-plus

A collection of [Claude Code](https://claude.com/claude-code) plugins I use day-to-day. Each subdirectory is a self-contained plugin you can install with `--plugin-dir` today, or via a plugin marketplace once this repo is listed.

## Plugins

| Plugin | What it does |
| :--- | :--- |
| [`hermes-remote`](./hermes-remote) | CLI for managing a remote [Hermes Agent](https://github.com/NousResearch/hermes-agent) deployment — cron jobs, env, status, model, `config get/set`. |
| [`langfuse`](./langfuse) | CLI for managing [Langfuse](https://langfuse.com) instances (cloud or self-hosted) — export/import prompts for backup and cross-env migration, smoke-test trace ingestion, health checks across multiple named instances. |
| [`coolify-remote`](./coolify-remote) | CLI for managing a [Coolify](https://coolify.io) PaaS instance — app lookup by name, env vars with post-write verify, domain set with correct field names, `deploy --wait` polling, bundled TLS enable. |
| [`hcloud-remote`](./hcloud-remote) | Minimal CLI for day-to-day [Hetzner Cloud](https://hetzner.com/cloud) ops — `server list/show/reboot`, `snapshot create/list`, `ssh <name>`. Deliberately narrow (no volumes/networks/LBs). |
| [`openrouter-remote`](./openrouter-remote) | CLI for [OpenRouter](https://openrouter.ai) — balance check (with `--alert-below` for crons), usage stats aggregated per key, model catalogue search/filter by price/context/capability, and API key management via the provisioning endpoint. |
| [`railway-ops`](./railway-ops) | Read-first wrapper around the [Railway](https://railway.app) CLI — single-call env overviews (services, deploy status, recent errors/warnings, env var **names**-only) for fast incident triage. Never leaks env var values. |

More coming as I skillify workflows I keep repeating.

## Shared conventions

Every plugin in this repo follows the same patterns so switching between them is cheap:

- **Stdlib-only Python 3 CLI** in `bin/<plugin>`. No pip installs, no venvs. Run it standalone if you don't want the plugin wrapper.
- **Layered `.env` autoloading**, precedence highest-first: `--env-file` → project `.env.local` / `.env` (walked up from cwd) → shell environment (including `~/.claude/settings.json` env). **Project `.env` files win over the shell** — drop one in the repo you're working in and it overrides whatever globals you have set, no unset required. Each plugin scopes autoload to its own prefixes (`HERMES_*`, `LANGFUSE_*`, `COOLIFY_*`, `HCLOUD_*`, `OPENROUTER_*`). **Exception**: `railway-ops` defers to the `railway` CLI's own auth (`railway login` / `~/.railway/config.json`) — no `.env` autoload.
- **Helpful missing-config errors**: when a required env var is absent, the CLI prints both the preferred project-level location and the global Claude Code settings location.
- **Resolve-by-name everywhere**: apps, servers, and instances are identified by human names in commands; the CLI looks up UUIDs/IDs internally. You never copy an opaque identifier across commands.
- **`--json` on every list/show command** for piping to `jq`.
- **`--wait` on mutating commands** that return async action IDs (deploys, etc.) so you can chain reliably instead of hand-rolling polling loops.

## Per-plugin changelogs

Each plugin keeps its own `CHANGELOG.md` for release notes and incident / pain-point logging. Keep it short: what changed, why it matters, date.

## Install

### Recommended — marketplace install (persistent, no clone needed)

Add this repo as a marketplace once, then install any plugin by name:

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install hermes-remote@agent-plus     # or any other plugin
claude plugin install coolify-remote@agent-plus
claude plugin install hcloud-remote@agent-plus
claude plugin install openrouter-remote@agent-plus
claude plugin install langfuse@agent-plus
claude plugin install railway-ops@agent-plus
```

Updates later:

```bash
claude plugin marketplace update agent-plus
claude plugin update hermes-remote
```

### Alt — session-only, from a local clone (for dev / testing)

`--plugin-dir` loads a plugin for the current session only — nothing persisted. Useful when hacking on a plugin or trying one before installing:

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/hermes-remote     # this shell only
```

Stack multiple plugins by repeating the flag. Each plugin's own README has its config details.

### Standalone (no Claude Code at all)

Every plugin's `bin/<plugin>` file is a stdlib-only Python 3 script. Copy it anywhere on `$PATH` and run it — no pip install, no venv, no Claude Code required. See each plugin's README for the one-line `curl -O` install.

## Philosophy

Plugins here follow a rule I stole from [Garry Tan's skillify post](https://x.com/garrytan): **deterministic work belongs in scripts, not prompts**. The LLM orchestrates; the code does. Each plugin has a SKILL.md that teaches Claude when to reach for the bundled scripts, not how to reinvent them.

## Development

Plugins live as directories with the standard Claude Code plugin shape:

```
<plugin-name>/
├── .claude-plugin/plugin.json
├── bin/                    # executables auto-added to PATH when plugin enabled
├── skills/<skill-name>/SKILL.md
├── README.md
└── LICENSE (or inherits root)
```

See [Claude Code plugin docs](https://code.claude.com/docs/en/plugins) for the full spec.

To iterate on a plugin locally without reinstalling:

```bash
claude --plugin-dir ./<plugin-name>
# Edit SKILL.md / scripts
# /reload-plugins to pick up changes
```

## License

MIT, see [LICENSE](./LICENSE).
