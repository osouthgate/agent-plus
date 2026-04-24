# hcloud-remote

Minimal CLI for Hetzner Cloud day-to-day ops. One file, stdlib Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

## Why

If you have one or two VPSes on Hetzner and no Terraform, you don't need the full `hcloud` CLI. You need: *what's the IP, ssh in, reboot, take a snapshot before I break something*. That's what this wraps.

**Why not just curl?** Two reasons:

- **Windows bash shim mangles `curl | python3 -c "..."`** with multiline heredocs. This tool parses JSON in-process — no shell plumbing.
- **Server resolution by name, not by numeric ID.** `hcloud-remote server show my-vps-01` instead of `hcloud-remote server show 12345678`. You stop copy-pasting 8-digit IDs across commands, and the agent's context stays clean. `ssh <name>` goes one step further — resolves the IPv4 and execs `ssh` in one call, so you don't `server show` then copy the IP.

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
