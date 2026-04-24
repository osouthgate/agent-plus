# vercel-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.1.0 - 2026-04-24

Initial release.

### Added
- `projects list` / `projects resolve <name-or-id>` — resolve by name, no ID copy-pasting.
- `overview --project <name>` — single-call project snapshot: project info + latest deployments (default 10, capped at 50) with commit metadata projected from `meta.githubCommitSha` / `meta.githubCommitMessage`, domain verification summary, env var NAMES only, top-level warnings.
- `deployments list` with `--state` / `--limit` filters; `deployments show <ref>` accepting either ID or URL.
- `deployments trigger --hook-url <url>` using Vercel Deploy Hooks (not the `/v13/deployments` file-upload path, which is the wrong shape for agent use). Supports `--wait` with a 15-min default timeout and poll-every-5s contract; partial-JSON-on-timeout exit.
- `logs <deployment-or-url>` with `--since 1h|30m|24h`, `--errors-only`, `--limit`. Uses `/v2/deployments/{id}/events`.
- `domains list` / `domains verify <domain> --wait` (5-min default timeout).
- `env list` returning NAMES only (values never touch output), `env set` / `env remove` with `--env production|preview|development` target filtering.
- Team scoping: every API call appends `?teamId=$VERCEL_TEAM_ID` centrally via `_api()`. No per-command wiring.
- `_scrub()` response filter strips `password`, `token`, `githubToken`, `gitlabToken`, `secret`, `encryptedValue`, `value`, and related keys from any API response before emission. Walks nested dicts/lists.
- Layered `.env` autoload: `--env-file` > project `.env.local` / `.env` walked up from cwd > shell env. Only `VERCEL_*` prefixed vars are picked up.
- Error message contract: missing token / 401 / 403 / 429 / 404-with-team each emit problem + cause + fix + link.
- `--wait` polling contract: per-command default timeouts (15m deploy, 5m domain, 30s env), 5s poll interval (2s for env), non-zero exit with last-known state on timeout. Never hangs indefinitely.
- Worked example in every command's `--help` string.

### Safety
- Read-only by default. Writes (`env set`, `env remove`, `deployments trigger`) are explicit subcommands.
- Deliberate out-of-scope: team/user CRUD, billing, Edge Config authoring, framework-specific build logic.
