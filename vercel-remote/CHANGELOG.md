# vercel-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## Unreleased

### Added
- `--shape-depth <1|2|3>` flag — controls how deep `payloadShape` recurses in the `--output` envelope. Default is `3` (two layers of nesting — surfaces `deployments[0].meta` without a second Read). Drop to `1` for a minimal envelope. Only affects `--output`. [2026-04-24]

### Changed
- Default `payloadShape` depth is now **3** (was effectively 1). Agents using `--output` get nested-structure visibility by default — e.g. `deployments: {type: list, length: 50, sample: {type: dict, keys: 12, shape: {...}}}` instead of just `deployments: {type: list, length: 50}`. SKILL.md updated with a dedicated "Offloading large responses" section explaining when to reach for `--output`. [2026-04-24]

### Fixed
- Top-level `--output` and `--pretty` are now immune to subparser re-declaration (`default=argparse.SUPPRESS`). Previously a value passed before the subcommand could be silently overwritten by the subparser's default. Not user-visible in normal use but matters for `railway-ops`-style plugins that add globals per-subparser. [2026-04-24]
- `--output` no longer silently drops list-shaped payloads (e.g. `projects list`). The raw list is written to disk unchanged; the envelope reports `payloadType: "list"` + `payloadLength` instead of `payloadKeys`/`payloadShape`, plus head/tail item previews. [2026-04-24]

### Added
- `--output <path>` global flag — writes the full JSON payload to disk and prints a compact envelope (`savedTo`, `bytes`, `payloadKeys`, `payloadShape`, head/tail previews for log-shaped payloads). Use for large responses (`logs`, long `overview`) that are wasteful to route through the model's context window. [2026-04-24]
- `payloadShape` field on the `--output` envelope — shallow type + size map for each top-level key (e.g. `{"deployments": {"type": "list", "length": 50}}`) so the agent can decide which key to drill into without scanning the file. [2026-04-24]

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
