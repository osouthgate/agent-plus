# agent-plus

The meta plugin. Workspace bootstrap for the agent-plus collection — one call to discover what's installed, what's configured, and what services this checkout already knows about.

Without this, every fresh Claude Code session re-mines the project: grep `.env`, `ls` plugin dirs, `git remote -v`, hand-roll a `gh repo view`, paste a Vercel project ID, repeat. Session transcripts showed 67 grep operations and 60 `ls` operations during cold-start context-gathering — most of them recovering facts the previous session also recovered. `agent-plus init` + `agent-plus refresh` collapses that into two tool calls and writes the results to `.agent-plus/` so the next session reads them off disk. `agent-plus list` now covers a broader stack — github, vercel, supabase, railway, linear, langfuse — so one refresh call resolves the whole infra surface.

Part of [agent-plus](../README.md). Stdlib-only Python 3, no dependencies, no SaaS.

## Why

- **Cold start is expensive.** A fresh session has to discover every fact about the workspace from scratch: which plugins are installed, which env vars are set, what the project IDs are, what the GitHub repo is. `agent-plus refresh` resolves the cheapest identity endpoints once and caches the result.
- **Names only, no values.** `envcheck` reports which env-var prefixes are set across every plugin — `GITHUB_TOKEN`, `VERCEL_TOKEN`, `LINEAR_API_KEY`. Values never land on disk. Same contract as `railway-ops` (canary-tested).
- **One shared `.agent-plus/`.** `skill-feedback` already established the directory; `agent-plus init` reuses the same resolution rules so both plugins agree on which workspace they're talking to.

## Headline commands

```bash
agent-plus init       [--dir PATH] [--pretty]
agent-plus envcheck   [--dir PATH] [--env-file PATH] [--pretty]
agent-plus refresh    [--dir PATH] [--env-file PATH] [--plugin <name>]
                      [--no-extensions | --extensions-only] [--pretty]
agent-plus list       [--dir PATH] [--names-only] [--pretty]
agent-plus extensions list|validate|add|remove [--dir PATH] [--pretty]
agent-plus marketplace init <user>/<name> [--path PATH] [--pretty]
agent-plus --version
```

All commands emit JSON wrapped in the standard `tool: {name, version}` envelope. `--pretty` for indented output.

### `init`

Creates `.agent-plus/` with three empty-but-valid JSON files. Idempotent — re-running leaves existing files untouched and reports `skipped:[...]`.

```bash
$ agent-plus init --pretty
{
  "tool": {"name": "agent-plus", "version": "0.1.0"},
  "workspace": "/path/to/repo/.agent-plus",
  "source": "git",
  "created": ["manifest.json", "services.json", "env-status.json"],
  "skipped": []
}
```

### `envcheck`

Walks every known plugin's required env-var prefixes. Reports which are set, which are missing, and per-plugin readiness. **Names only.** Result is also written to `.agent-plus/env-status.json`.

```bash
$ agent-plus envcheck --pretty
{
  "tool": {"name": "agent-plus", "version": "0.1.0"},
  "workspace": "/path/to/repo/.agent-plus",
  "source": "git",
  "checked": ["COOLIFY_API_KEY", "COOLIFY_URL", "GITHUB_TOKEN", ...],
  "set": ["GITHUB_TOKEN", "VERCEL_TOKEN"],
  "missing": ["LINEAR_API_KEY", "SUPABASE_ACCESS_TOKEN", ...],
  "missing_optional": ["GITHUB_REPO", ...],
  "plugins": {
    "github-remote": {"set": ["GITHUB_TOKEN"], "missing_required": [], "ready": true, ...},
    "vercel-remote": {"set": ["VERCEL_TOKEN"], "missing_required": [], "ready": true, ...},
    "linear-remote": {"set": [], "missing_required": ["LINEAR_API_KEY"], "ready": false, ...},
    ...
  },
  "checkedAt": "2026-04-27T12:00:00Z",
  "written_to": "/path/to/repo/.agent-plus/env-status.json"
}
```

### `refresh`

Resolves project / repo identity for the lightest-cost endpoints in **github-remote, vercel-remote, supabase-remote, railway-ops, linear-remote, langfuse-remote**. Caches the result into `.agent-plus/services.json`. Each handler hits one read-only endpoint and keeps NAMES + IDs + URLs only — tokens never appear in stdout, stderr, or services.json (canary-tested). Plugins outside this set still record `unconfigured` from envcheck's POV.

```bash
$ agent-plus refresh --pretty
{
  "tool": {"name": "agent-plus", "version": "0.1.0"},
  "services": {
    "github-remote": {
      "plugin": "github-remote",
      "owner": "osouthgate",
      "repo": "agent-plus",
      "status": "ok",
      "default_branch": "main",
      "repo_id": 123456789
    },
    "vercel-remote": {
      "plugin": "vercel-remote",
      "status": "ok",
      "projects": [{"name": "my-app", "id": "prj_..."}],
      "count": 1
    }
  },
  "refreshedAt": "2026-04-27T12:00:00Z",
  "written_to": "/path/to/repo/.agent-plus/services.json"
}
```

### `list`

Discoverability — one call returns every plugin in `.claude-plugin/marketplace.json` plus a 400-char headline-commands preview pulled from each plugin's README. Cross-references `env-status.json` (when present) to surface a `ready` flag per plugin so the agent can pick the right tool without opening every README itself.

```bash
$ agent-plus list --pretty
{
  "tool": {"name": "agent-plus", "version": "0.2.0"},
  "plugins": [
    {
      "name": "github-remote",
      "description": "Read-first GitHub REST API wrapper — ...",
      "headline_commands": "## Headline commands\n\n```bash\ngithub-remote pr ...",
      "source": "./github-remote",
      "ready": true
    },
    ...
  ],
  "count": 12
}

$ agent-plus list --names-only
{"tool": {...}, "plugins": ["agent-plus", "hermes-remote", ...], "count": 12}
```

## Workspace dir resolution

Identical to `skill-feedback` so both plugins share one `.agent-plus/`:

1. `--dir PATH` (CLI flag)
2. `<git-toplevel>/.agent-plus/` (cwd is in a git repo)
3. `<cwd>/.agent-plus/` (cwd contains an existing `.agent-plus/`)
4. `~/.agent-plus/` (last-resort fallback for read paths; `init` defaults to cwd in non-git projects)

The `source` field on every payload tells you which rule fired.

## Config

Layered `.env` autoload, highest precedence first:

1. `--env-file <path>`
2. project `.env.local` / `.env` (walked up from cwd)
3. shell env

Each plugin's required env-vars are checked by their canonical prefix (`HERMES_*`, `COOLIFY_*`, `LANGFUSE_*`, …) — the spec is hardcoded in `bin/agent-plus#PLUGIN_ENV_SPEC` and tracked there to keep drift visible.

## What it doesn't do

Deliberately out of scope:

- **No `refresh` built-in for coolify-remote, hcloud-remote, hermes-remote, openrouter-remote, skill-feedback.** Six of the eleven plugins are wired. The remaining five are `unconfigured` from envcheck's POV — the per-plugin CLIs are unchanged. (Plug your own gap with an extension; see below.)
- **No SessionStart hook.** Bootstrap is on the agent / user; `agent-plus init` is idempotent so it's cheap to run on every session start.
- **No edit / generic file ops.** `.agent-plus/*.json` is plain JSON. Use `jq` or an editor.
- **No secret values anywhere.** Names only; values stay in your shell / `.env`. Verified by a canary test.

## Extensions

Without this, every workspace is locked to whatever 6 plugins ship with `refresh`. With it, you drop a script into `extensions.json` and `agent-plus refresh` aggregates your own data the same way it aggregates the built-ins — same envelope, same `services.<name>` slot, same `--pretty` JSON.

### The contract

Each extension's stdout MUST be a single JSON object. Status is advisory.

```json
{
  "status": "ok" | "unconfigured" | "partial" | "error",
  "...": "any other fields you want, passed through verbatim"
}
```

The orchestrator wraps the script's output as `{plugin: "<name>", source: "extension", ...your fields...}` and merges it into `services.<name>`. Non-JSON output, JSON arrays, non-zero exit, and timeouts all become `{status: "error", reason: "...", stderr_tail: "<last 500 chars>"}`. Extension scripts run with `cwd=<repo root>` and inherit the host env (so `gh`, `vercel`, etc. work). The orchestrator never echoes env values into the output.

### Worked example

```python
#!/usr/bin/env python3
# .agent-plus/scripts/refresh-releases.py
import json, subprocess
proc = subprocess.run(
    ["gh", "api", "repos/osouthgate/agent-plus/releases", "--paginate=false"],
    capture_output=True, text=True, timeout=10,
)
if proc.returncode != 0:
    print(json.dumps({"status": "error", "reason": proc.stderr[:200]}))
else:
    releases = json.loads(proc.stdout)[:5]
    print(json.dumps({
        "status": "ok",
        "latest": [{"tag": r["tag_name"], "name": r["name"]} for r in releases],
        "count": len(releases),
    }))
```

Register it once:

```bash
agent-plus extensions add --name releases \
    --command python3 \
    --command-arg=.agent-plus/scripts/refresh-releases.py \
    --description "GitHub releases summary" \
    --timeout 15
```

Then every `agent-plus refresh` populates `services.releases` alongside the built-ins.

### Managing extensions

```bash
agent-plus extensions list                       # show registered extensions + on-disk check
agent-plus extensions validate                   # dry-run validate (no script execution)
agent-plus extensions add --name X --command Y   # append (atomic; rejects collisions)
agent-plus extensions remove --name X            # remove by name (atomic)

agent-plus refresh --no-extensions               # skip extensions (debug / fast)
agent-plus refresh --extensions-only             # run only extensions, skip built-ins
```

`extensions list` and `agent-plus list` surface `command_hash` (sha256 of argv[0]) rather than the command itself — paths often contain usernames, and there's no reason to leak them into agent transcripts.

Disabled extensions (`"enabled": false` in `extensions.json`) load but skip at refresh time. Names that collide with built-in plugin names are rejected at add/load time — no silent shadowing.

## `marketplace init`

Scaffold a new `<user>/agent-plus-skills` marketplace repo following the marketplace convention. Phase 1 of the marketplace surface — install, update, list, and remove are Phase 2.

```bash
$ agent-plus marketplace init osouthgate/agent-plus-skills --pretty
{
  "tool": {"name": "agent-plus", "version": "0.5.0"},
  "marketplace": {
    "path": "/path/to/agent-plus-skills",
    "owner": "osouthgate",
    "name": "agent-plus-skills",
    "files_written": ["marketplace.json", "README.md", "LICENSE", ".gitignore", "CHANGELOG.md"],
    "git_initialized": true,
    "next_steps": [
      "gh repo create osouthgate/agent-plus-skills --public --source . --remote origin",
      "gh repo edit osouthgate/agent-plus-skills --add-topic agent-plus-skills",
      "gh repo edit osouthgate/agent-plus-skills --add-topic claude-code"
    ]
  }
}
```

The `name` portion of the slug **must** be `agent-plus-skills` for v1 — the convention is fixed so `gh search repos topic:agent-plus-skills` is unambiguous. Default target dir is `<cwd>/<name>/`; override with `--path`. Refuses to scaffold into a non-empty directory. `agent-plus marketplace init` never runs `gh` itself — it prints suggested invocations only, keeping the scaffold pure and avoiding a `gh` auth dependency.

## Install

### Marketplace install

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install agent-plus@agent-plus
```

### Session-only

```bash
claude --plugin-dir ./agent-plus
```

### Standalone

`bin/agent-plus` is one stdlib Python 3 file. Copy to `$PATH`, run.

## License

MIT, inherits the root [LICENSE](../LICENSE).
