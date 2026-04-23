# hcloud-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.1.0 — 2026-04-23

Initial release.

### Added
- Minimal CLI for Hetzner Cloud API.
- `server list / show / reboot` — resolves by name or numeric id.
- `snapshot create / list` — filter list by `--server <name>` (client-side, since Hetzner's `bound_to` filter only matches currently-attached snapshots).
- `ssh <name>` — resolves public IPv4 and execs `ssh` (subprocess on Windows, execvp on Unix). Extra ssh args pass through after `--`.
- Layered `.env` autoloading with project-file-wins precedence. Picks up `HCLOUD_*` and `HETZNER_*` keys.

### Why this exists
- On Windows, `curl | python3 -c "..."` to parse Hetzner JSON reliably gets mangled by the bash shim (stray `||` parsed as `goto :error`). This wrapper parses in-process so `hcloud-remote server show <name>` just works.
- Scope deliberately narrow: no volumes, networks, LBs, firewalls, floating IPs, image management, or server create/destroy. If any of those become recurring needs, reach for Terraform — don't expand this.
