# coolify-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## Unreleased

### Added
- `app exec <app> -- <cmd>` — run a shell command inside the app's running Docker container. SSH-based (to `$COOLIFY_SSH_HOST` or the host parsed from `COOLIFY_URL`) + `docker exec`, because Coolify has no REST exec endpoint — every obvious path (`/applications/{uuid}/execute`, `/exec`, `/command`, `/run`, `/terminal`, `/shell`) returns 404. The web UI's terminal is WebSocket-based, not worth wrapping. Stdout/stderr/exit-code all propagate — suitable for cron/skillify patterns. Supports `-t` for TTY, `-v` to print the ssh command. Unblocks the common "I need to run a one-off check inside the container" workflow without clicking through the Coolify UI. [2026-04-23]
- SSH env vars: `COOLIFY_SSH_HOST`, `COOLIFY_SSH_USER` (default `root`), `COOLIFY_SSH_KEY`, `COOLIFY_SSH_PORT` (default 22). [2026-04-23]

### Changed
- `app exec` picks the newest matching container deterministically (sort by `CreatedAt` desc) and warns on stderr when >1 containers match the app UUID — previously took whichever `docker ps` returned first, which could silently hit the wrong side of a blue/green rollout. Exit code 3 reserved for the multi-match warning case; exit 0 still means the remote command ran. [2026-04-23]
- `app exec` defaults to `ssh -o StrictHostKeyChecking=accept-new` so first-contact from cron doesn't hang on the fingerprint prompt. Mismatched host keys still refuse (rotation is caught). Opt out with `--ssh-strict` to use ssh's own default. [2026-04-23]

### Encoded gotchas
- **Container lookup is by name prefix, not label.** Initial implementation filtered by `label=coolify.applicationId=<uuid>` — that label doesn't exist on Coolify's application containers (only on proxy-layer services). Coolify names running containers `<app-uuid>-<timestamp>`, so `docker ps --filter name=<uuid>` matches reliably across versions.
- **No REST exec endpoint exists.** Documented in the SKILL with the 8 paths we probed, so future agents don't waste time looking.
- **Windows / Git Bash MSYS path rewriting**: absolute Linux paths in arguments get mangled (`/etc/foo` → `C:/Program Files/Git/etc/foo`) before Python sees them. SKILL documents both workarounds (`sh -c '...'` wrap or `MSYS_NO_PATHCONV=1`).

## 0.1.0 — 2026-04-23

Initial release.

### Added
- Remote CLI for Coolify PaaS over its REST API.
- `app list` / `app show <name>` — resolves apps by name, UUID, or FQDN substring.
- `env list / set / sync` — upserts (POST with PATCH fallback on 422), `--verify` reads back to catch silent no-ops, `--deploy --wait` chains a redeploy and blocks to completion.
- `domain set <app> <url>` — uses the correct `domains` field (not `fqdn`, which is read-only and 422s on PATCH).
- `tls enable <app> --domain <url>` — bundled four-step flow: PATCH domain + `is_force_https_enabled`, trigger deploy, wait for Let's Encrypt cert, HEAD the HTTPS URL as smoke test.
- `deploy <app> --wait` — polls `/api/v1/deployments/<id>` every 3s to a terminal state (`finished` / `failed` / `cancelled-by-*`). Replaces hand-rolled `until curl … | python3 -c …` loops that broke on the Windows bash shim.
- `server list` — Coolify-managed hosts.
- Layered `.env` autoloading with project-file-wins precedence (same pattern as `hermes-remote`).

### Encoded gotchas (in SKILL.md)
- **Env var propagation**: Coolify stores env on write but does not inject into running containers — redeploy required. `--verify` only checks API-level storage, not the container. Caught this after `OPENAI_API_KEY` was visible in UI but empty in container.
- `fqdn` is read-only, `domains` is writable.
- Deploy trigger is `GET /api/v1/deploy?uuid=…&force=true` (not POST).
- POST `/envs` returns 422 if key exists → wrapper auto-retries with PATCH.
