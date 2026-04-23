# hcloud-remote

Minimal CLI for Hetzner Cloud day-to-day ops. One file, stdlib Python 3, no dependencies.

Part of [agent-plus](../README.md) — a small collection of Claude Code plugins.

## Why

If you have one or two VPSes on Hetzner and no Terraform, you don't need the full `hcloud` CLI. You need: *what's the IP, ssh in, reboot, take a snapshot before I break something*. That's what this wraps.

It also fixes a real pain point on Windows: parsing Hetzner JSON via `curl | python3 -c "..."` reliably gets mangled by the bash shim. This tool parses JSON in-process.

## Scope (deliberately narrow)

- `server list / show / reboot`
- `snapshot create / list`
- `ssh <name>` — resolves IPv4, shells out

**Not included**: volumes, networks, firewalls, load balancers, floating IPs, image management, server creation/destruction. If you need any of those, reach for Terraform or the upstream `hcloud` CLI.

## Install

### As a Claude Code plugin

```bash
claude --plugin-dir /path/to/agent-plus/hcloud-remote
```

### Standalone

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/hcloud-remote/bin/hcloud-remote
chmod +x hcloud-remote
./hcloud-remote server list
```

## Configure

Layered. Highest precedence first: `--env-file` → project `.env.local` / `.env` → shell env. Only `HCLOUD_*` / `HETZNER_*` keys are picked up from .env files.

```bash
# .env
HCLOUD_TOKEN=...
```

## License

MIT.
