# agent-plus-meta — resolution rules

Where the meta plugin looks for the workspace, env vars, and per-plugin config. Most users never need to read this — the defaults work. Read this when:
- You're running multiple repos that should share `~/.agent-plus/`
- You hit "workspace not initialised" and want to know which precedence rule fired
- You're authoring an extension and need to understand the env-pass-through layering

## Workspace dir

Highest first; identical to `skill-feedback` so both plugins share one `.agent-plus/`:

1. `--dir PATH` (CLI flag)
2. `<git-toplevel>/.agent-plus/` (cwd is in a git repo)
3. `<cwd>/.agent-plus/` (cwd contains an existing `.agent-plus/`)
4. `~/.agent-plus/` (last-resort fallback for read paths; `init` defaults to cwd in non-git projects)

The `source` field on every payload tells you which rule fired.

## Env (.env autoload)

Highest precedence first:

1. `--env-file <path>`
2. project `.env.local` / `.env` (walked up from cwd)
3. shell env

Each plugin's required env-vars are checked by canonical prefix (`HERMES_*`, `COOLIFY_*`, `LANGFUSE_*`, …) — spec hardcoded in `bin/agent-plus-meta#PLUGIN_ENV_SPEC` to keep drift visible.
