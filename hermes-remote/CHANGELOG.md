# hermes-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## Unreleased

### Changed
- `chat` now honours `HERMES_VPS_IP + HERMES_HOST` fallback for reverse-proxy-by-Host setups — previously only `HERMES_URL` was read, leaving `chat` broken for users whose admin CLI worked via the IP+Host path. URL resolution factored into a shared `_resolve_hermes_url()` helper; `chat` also routes through the same `_request` transport so the Host header override is applied consistently. [2026-04-23]
- `chat --max-tokens` default bumped 1024 → 2048. 1024 was truncating moderately complex responses mid-sentence. [2026-04-23]

### Added
- `chat` subcommand — one-shot call to `/v1/chat/completions` (OpenAI-compatible). Bearer auth via `HERMES_CHAT_API_KEY` — distinct from the admin session cookie, so it skips the login step entirely. Supports `--model`, `--max-tokens`, `--system`, `--json`. Designed for ad-hoc CLI chat (`hermes-remote chat "status?"`) and cron-script use. [2026-04-23]
- `.env` autoloading with project-file-wins precedence: `--env-file` → project `.env.local` / `.env` → shell. Only `HERMES_*` and `COOLIFY_*` keys are picked up. Motivation: lets per-project configs override Claude Code global settings without unsetting anything. [2026-04-23]
- `--env-file` CLI flag (repeatable). [2026-04-23]
- `_suggest_env_setup()` — when `HERMES_URL` or password can't be resolved, the error tells the user exactly where to put them (project `.env` preferred, or `~/.claude/settings.json` for global). [2026-04-23]

### Changed
- Module docstring and SKILL.md updated to document the layered config model. [2026-04-23]

## 0.1.0 — 2026-04-22

Initial release.

### Added
- Remote CLI for Hermes Agent (`status`, `model`, `cron list/show/pause/resume/trigger/remove/create`, `env list`).
- `config get` / `config set` for mutating `config.yaml` keys live (overrides env-var-based config).
- Traefik-behind-reverse-proxy routing via `HERMES_VPS_IP` + `HERMES_HOST`.
- Coolify integration: auto-fetch `ADMIN_PASSWORD` from Coolify API if `HERMES_PASSWORD` not set.
- Skillify cron pattern documented — every recurring cron should use `--script` + minimal prompt + cheap model. Motivation: a Sonnet-on-every-tick cron burned ~$10/12h.
