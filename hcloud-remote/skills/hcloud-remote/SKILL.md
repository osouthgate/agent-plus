---
name: hcloud-remote
description: Day-to-day Hetzner Cloud ops from the CLI — list/show servers (prints IPv4/IPv6 cleanly), reboot, create and list snapshots, ssh. Resolves servers by name so you never paste IDs. Deliberately narrow — no volumes/networks/load balancers.
when_to_use: Trigger on phrases like "what's the IP of the hetzner box", "ssh into the vps", "reboot the vps", "take a snapshot before I...", "list hetzner snapshots", "hetzner status", anything that used to require curling api.hetzner.cloud + parsing JSON with a Python heredoc.
allowed-tools: Bash(hcloud-remote:*) Bash(python3 *hcloud-remote*:*)
---

# hcloud-remote

Stdlib-only Python 3 CLI for the narrow slice of Hetzner Cloud API you actually use when you have one or two VPSes and no Terraform. Lives at `${CLAUDE_SKILL_DIR}/../../bin/hcloud-remote`; the plugin auto-adds `bin/` to PATH.

## When to reach for this

- User needs an IP address or status for a Hetzner server.
- User wants to reboot or snapshot before risky work.
- User wants to SSH in but doesn't have the IP in their head.

Do NOT use for: volumes, networks, firewalls, load balancers, floating IPs, image management, server creation/destruction. If you find yourself wanting any of those, reach for Terraform / `hcloud` (the upstream CLI) instead — this wrapper intentionally doesn't cover them.

## Configure

Layered config, highest precedence first:

1. `--env-file <path>`
2. `.env.local` / `.env` (walked up from cwd)
3. Shell environment (including Claude Code settings)

Project `.env` files override the shell. Only `HCLOUD_*` and `HETZNER_*` keys are picked up.

```bash
# .env
HCLOUD_TOKEN=...
```

Or globally in `~/.claude/settings.json`:

```json
{ "env": { "HCLOUD_TOKEN": "..." } }
```

Generate a token in Hetzner Console → Security → API tokens. Read/write scope is fine; snapshot + reboot both need write.

## Commands

```bash
hcloud-remote server list                    # name, status, type, ipv4, ipv6
hcloud-remote server show <name>             # full details for one box
hcloud-remote server reboot <name> [-y]      # soft reboot, prompts unless -y

hcloud-remote snapshot create <name> --description "before schema migration"
hcloud-remote snapshot list [--server <name>]

hcloud-remote ssh <name>                     # resolves IPv4, shells out to ssh
hcloud-remote ssh <name> --user admin -- -p 2222 'uptime'
```

All list/show commands support `--json`. Every JSON payload carries a top-level `tool: {name, version}` field so you can verify which plugin version produced the output.

## Post-processing with jq

For filtering, reshaping, or extracting fields from `--json` output, pipe into [`jq`](https://jqlang.github.io/jq/). It ships on most package managers (`brew install jq`, `apt install jq`, `choco install jq`) and beats ad-hoc `grep`/`python -c` one-liners for working with structured responses. Example: `hcloud-remote server list --json | jq '.[] | {name, status, ipv4: .public_net.ipv4.ip}'`.

## Servers are resolved by name

Every command accepts either a name or a numeric id. You do not need to copy Hetzner IDs around.

```bash
hcloud-remote server show my-vps-01       # works
hcloud-remote server show 12345678            # also works
```

## The Python-heredoc bug this replaces

On Windows, piping curl output into `python3 -c "..."` with a multiline string gets mangled by the bash shim (stray `||` parsed as `goto :error`). The previous workaround was writing a temp `.py` file every time. This wrapper does the JSON parsing in-process, so:

```bash
hcloud-remote server show my-vps-01
```

just prints the IPs. No heredocs, no temp files.

## Snapshots

Snapshots in Hetzner are a type of image. `snapshot create` queues the snapshot (returns immediately with an action id); the snapshot itself finishes in the background over a few minutes depending on disk size.

```bash
hcloud-remote snapshot create my-vps-01 --description "pre-coolify-upgrade"
hcloud-remote snapshot list --server my-vps-01
```

`--server` filters client-side to snapshots whose `created_from.id` matches that server; Hetzner's `bound_to` filter only matches currently-attached snapshots, which is rarely what you want.

## ssh

```bash
hcloud-remote ssh my-vps-01
```

Resolves the server's public IPv4 and execs `ssh root@<ip>`. Pass extra ssh args after `--`:

```bash
hcloud-remote ssh my-vps-01 --user admin -- -p 2222 -i ~/.ssh/hetzner 'uptime'
```

On Unix, execs into ssh so Ctrl-C behaves. On Windows, runs ssh as a subprocess and propagates its exit code — near-identical UX.

## Troubleshooting

- **`HTTP 401`**: `HCLOUD_TOKEN` is wrong or expired. Tokens are shown once at creation — generate a new one.
- **`HTTP 423` on reboot / snapshot**: the server is locked (another action in flight). Wait and retry.
- **`ssh: connect to host … port 22: Connection refused`**: server is mid-reboot, or SSH isn't on 22. Pass `-- -p <port>` after the `--` separator.
- **Snapshot quota exceeded**: Hetzner limits snapshots per project. Prune via the Console or `hcloud` CLI — this wrapper doesn't delete images (safety).
