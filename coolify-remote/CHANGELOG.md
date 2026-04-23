# coolify-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

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
