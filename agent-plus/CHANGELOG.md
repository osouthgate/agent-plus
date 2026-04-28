# agent-plus — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## Unreleased

## 0.3.0 - 2026-04-27

User-defined refresh handlers (B-EXT-1) plus the `extensions` subcommand.

### Added

- **Extensions: user-defined refresh handlers via `extensions.json`.** Drop a script into `<workspace>/extensions.json`, and `agent-plus refresh` aggregates its output into `services.<name>` alongside the built-ins. Each extension's stdout must be a single JSON object with a `status` field (`ok` | `unconfigured` | `partial` | `error`); all other fields pass through verbatim. The orchestrator wraps each output as `{plugin, source: "extension", ...}` so downstream consumers can tell apart user / built-in / migrated handlers. Per-extension `timeout_seconds` (default 30), `enabled` flag, and `description`. Names matching built-in plugins (`github-remote`, etc.) are rejected at load + add time. No `shell=True` ever; argv-style command list only. [2026-04-27]
- **`agent-plus extensions list|validate|add|remove`** — manage `extensions.json` without hand-editing JSON. `add` and `remove` are atomic (write via tempfile + `os.replace`). `validate` dry-runs every extension (name format, command exists on disk, no collisions) without executing scripts. `list` and the meta `agent-plus list` surface `command_hash` (sha256 of argv[0]) rather than the command path — paths often contain usernames, no reason to leak them into agent transcripts. [2026-04-27]
- **`refresh --no-extensions` / `--extensions-only`** — skip extensions for fast/debug refresh, or run only extensions while leaving built-ins alone. Mutually exclusive. `--plugin <name>` implies built-ins-only. [2026-04-27]
- **`agent-plus list`** now includes an `extensions` array with `extensions_count`, alongside the existing `plugins` array. [2026-04-27]

### Notes

- Built-in handlers (github-remote, vercel-remote, supabase-remote, railway-ops, linear-remote, langfuse-remote) stay hardcoded in this slice — not migrated to the extension contract. The contract is intentionally rich enough to accommodate future migration: `_run_extension(ext_config, *, cwd, env)` is forward-compat and does not assume "user-supplied" anywhere in its logic.
- Pattern 5 reinforced: the canary test now also covers the extension surface. Even when the host env contains `GITHUB_TOKEN=CANARY-...`, that string never appears in refresh output unless the extension itself prints it. `stderr_tail` is bounded to 500 chars and contains only what the script emitted on its own stderr.

## 0.2.0 - 2026-04-27

Wider rollout for `refresh` (B-INIT-2) plus a new `list` discoverability subcommand (B-DISCO-1).

### Added

- `refresh` now covers four more plugins beyond the v0.1.0 github + vercel pair: **supabase-remote** (`GET /v1/projects` via Management API), **railway-ops** (shells out to `railway list --json`, mirroring how the plugin itself defers to the local CLI's auth state), **linear-remote** (POST GraphQL `viewer { id name email } teams { ... }` with the raw `Authorization: <key>` header — no Bearer prefix), **langfuse-remote** (Basic-auth `GET /api/public/health` against the default unnamed instance, base URL precedence `LANGFUSE_BASE_URL > LANGFUSE_HOST > https://cloud.langfuse.com`). All four pass the value-leakage canary — names + IDs + URLs only, never tokens. [2026-04-27]
- `agent-plus list [--names-only] [--pretty]` — discoverability subcommand that reads `.claude-plugin/marketplace.json` plus each plugin's `README.md` and emits a single envelope-wrapped JSON blob with name + description + a 400-char headline-commands preview (extracted from the first `## Headline commands` / `## Usage` / similar section, falling back to the first `##` after the title). Cross-references `env-status.json` when present to surface a `ready` flag per plugin. Pattern #1 — one call returns everything an agent needs to pick a plugin. [2026-04-27]
- Internal `_http_request_json(method, url, headers, data=)` helper generalising the existing `_http_get_json` so POST surfaces (Linear GraphQL) reuse one transport instead of duplicating urllib boilerplate. [2026-04-27]
- Forced UTF-8 stdout via `sys.stdout.reconfigure(encoding="utf-8")` so README previews containing em-dashes/arrows don't crash on Windows cp1252 consoles. [2026-04-27]

### Notes

- Plugins still treated as `unconfigured` from envcheck/refresh's POV in this slice: coolify-remote, hcloud-remote, hermes-remote, openrouter-remote, skill-feedback. They have no lightest-cost identity probe wired into `agent-plus refresh` yet — envcheck still reports them, and the per-plugin CLIs are unchanged.



## 0.1.0 - 2026-04-27

Initial release. Workspace bootstrap for the agent-plus collection — solves the cold-start cost where every fresh session re-mines `.env`, plugin dirs, and remote URLs to discover what's installed and what's configured.

### Added

- `agent-plus init [--dir PATH]` — creates `.agent-plus/manifest.json`, `services.json`, `env-status.json` with empty-but-valid JSON. Idempotent: re-runs return `skipped:[…]` for files already present. Workspace dir resolution matches `skill-feedback` exactly (`--dir` → git-toplevel → cwd → home) so both plugins share one `.agent-plus/`. [2026-04-27]
- `agent-plus envcheck [--dir PATH] [--env-file PATH]` — reports which env-var prefixes are set across every known plugin. **Names only, never values** (pattern #5). Per-plugin readiness flag (`required` set + binary on PATH where applicable). Result persisted to `.agent-plus/env-status.json`. Includes a `railway` binary-on-PATH check for `railway-ops` since it defers to the `railway` CLI's own auth. [2026-04-27]
- `agent-plus refresh [--dir PATH] [--plugin NAME]` — resolves the lightest-cost identity endpoints for `github-remote` and `vercel-remote` only (B-INIT-2 partial). For github-remote: parses `git config --get remote.origin.url` and (if a token is present) hits `GET /repos/{owner}/{repo}` for the default branch. For vercel-remote: hits `GET /v9/projects?limit=20` and caches `[{name, id}]`. Tokens never appear in stdout, stderr, or `services.json`. [2026-04-27]
- `--version` flag and `tool: {name, version}` envelope on every output (pattern #6). [2026-04-27]
- `skills/agent-plus/SKILL.md` teaching Claude to call `init` / `envcheck` / `refresh` at session start, plus an explicit "use the per-plugin CLI for actual operations" stay-in-lane clause (pattern #7). [2026-04-27]

### Pain point

Session transcripts showed the same cold-start choreography burning ~67 grep ops + ~60 ls ops per fresh session — re-discovering what `.env` already says, what `git remote -v` already shows, and which plugins are even installed. `agent-plus init` + `refresh` collapses that to two tool calls and writes the result to `.agent-plus/` so the next session reads it off disk.
