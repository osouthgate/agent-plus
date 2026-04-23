# agent-plus

A collection of [Claude Code](https://claude.com/claude-code) plugins I use day-to-day. Each subdirectory is a self-contained plugin you can install with `--plugin-dir` today, or via a plugin marketplace once this repo is listed.

## Plugins

| Plugin | What it does |
| :--- | :--- |
| [`hermes-remote`](./hermes-remote) | CLI for managing a remote [Hermes Agent](https://github.com/NousResearch/hermes-agent) deployment — cron jobs, env, status, model. |
| [`langfuse`](./langfuse) | CLI for managing [Langfuse](https://langfuse.com) instances (cloud or self-hosted) — export/import prompts for backup and cross-env migration, smoke-test trace ingestion, health checks across multiple named instances. |

More coming as I skillify workflows I keep repeating.

## Install one plugin

```bash
claude --plugin-dir /path/to/agent-plus/<plugin-name>
```

Stack multiple by repeating the flag. When a plugin in this repo is ready to use, its own README has install and config details.

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
