---
name: coolify-remote
description: Manage a remote Coolify PaaS instance from the CLI — list apps, set env vars with post-write verification, flip domains, enable TLS, trigger deploys and wait for them to finish. Use whenever you need to mutate a Coolify app without clicking through the UI or hand-rolling curl + polling loops.
when_to_use: Trigger on phrases like "deploy to coolify", "set the env var on coolify", "change the domain", "enable https on coolify", "redeploy the app", "is the deploy done", "coolify status", "sync .env to coolify", "flip the hermes app to a real domain", anything mutating a Coolify-managed application.
allowed-tools: Bash(coolify-remote:*) Bash(python3 *coolify-remote*:*)
---

# coolify-remote

Stdlib-only Python 3 CLI wrapping [Coolify](https://coolify.io)'s REST API. One file, no pip installs. Lives at `${CLAUDE_SKILL_DIR}/../../bin/coolify-remote`; the plugin auto-adds `bin/` to PATH.

## When to reach for this

- User wants to change something on a Coolify app (env, domain, TLS, deploy).
- User asks about deploy status or wants to redeploy and confirm it worked.
- User says "the env var isn't showing up in the container" — see **Env propagation gotcha** below.
- User wants to sync a local `.env` to a Coolify app.

Do NOT use for server provisioning — Coolify's server objects are already-bootstrapped hosts, not Hetzner VMs. For VM lifecycle use the (separate) hcloud CLI.

## Configure

Layered config, highest precedence first:

1. `--env-file <path>`
2. `.env.local` / `.env` (walked up from cwd)
3. Shell environment (including Claude Code settings)

**Project `.env` files override the shell** — drop a `.env` in the repo you're working in and it wins. Only `COOLIFY_*` keys are picked up.

```bash
# .env (project-level, preferred)
COOLIFY_URL=http://1.2.3.4:8000
COOLIFY_API_KEY=...

# Optional — only needed for `app exec`:
COOLIFY_SSH_HOST=1.2.3.4         # default: host parsed from COOLIFY_URL
COOLIFY_SSH_USER=root            # default: root
COOLIFY_SSH_KEY=~/.ssh/agent-plus # default: ssh's own defaults
COOLIFY_SSH_PORT=22              # default: 22
```

Or globally in `~/.claude/settings.json`:

```json
{ "env": { "COOLIFY_URL": "...", "COOLIFY_API_KEY": "..." } }
```

If either is missing, the CLI prints a suggestion pointing to both locations.

## Commands

```bash
coolify-remote app list                       # all apps with status + fqdn
coolify-remote app show <name-or-uuid>        # full JSON

coolify-remote env list <app>                 # redacted values
coolify-remote env list <app> --show          # full values
coolify-remote env set <app> KEY=val [KEY=val...] [--verify] [--deploy] [--wait]
coolify-remote env sync <app> .env [--prefix HERMES_] [--deploy] [--wait]

coolify-remote domain set <app> https://app.example.com --force-https --deploy --wait
coolify-remote tls enable <app> --domain https://app.example.com

coolify-remote deploy <app> [--wait]          # trigger; optionally block to completion

coolify-remote app exec <app> -- <cmd>        # run cmd inside the app's container
coolify-remote app exec <app> -t -- <cmd>     # allocate a TTY (for interactive tools)

coolify-remote server list                    # Coolify-managed hosts
```

All list/show commands support `--json` for piping to `jq`.

## Apps are resolved by name

Every command takes `<app>` which is matched against `name`, then `uuid`, then `fqdn` substring. You don't need to copy UUIDs around.

```bash
coolify-remote deploy hermes --wait                      # works
coolify-remote deploy b1c6e2f0-4a3d-4d77-ae6f-123456789ab --wait   # UUID also works
```

## Running commands inside a container (`app exec`)

Coolify has **no REST exec endpoint** — every obvious path (`/applications/{uuid}/execute`, `/exec`, `/command`, `/run`, `/terminal`, `/shell` on both application and server routes) returns HTTP 404. The web UI's terminal is a WebSocket feature gated by `is_terminal_enabled`. Rather than wrap that, this command SSHes to the Coolify host and runs `docker exec` on the matching container.

```bash
coolify-remote app exec hermes -- whoami                          # prints `root`
coolify-remote app exec hermes -- ls /data | head
coolify-remote app exec hermes -- sh -c 'cat /app/.env | wc -l'
coolify-remote app exec hermes -t -- sh                           # interactive shell
coolify-remote app exec hermes -v -- whoami                       # print the ssh command too
```

**How it works:** resolves the app by name → UUID, SSHes to `$COOLIFY_SSH_HOST` (or the hostname parsed from `COOLIFY_URL`), runs `docker ps --filter name=<uuid>` to find the live container (Coolify names them `<uuid>-<timestamp>`), then `docker exec -i` into it. Stdout, stderr, and exit codes all propagate to your local shell — so cron/skillify scripts can branch on `$?`.

**What you need on the host:** working key-based SSH as root (or whichever user can talk to the Docker socket). The CLI won't prompt for a password — if your key isn't already set up, `COOLIFY_SSH_KEY=~/.ssh/mykey` points it at one.

**Cron-friendly pattern** (inherited from Hermes's skillify model):

```bash
# Run a real health check inside the container; only escalate on failure.
coolify-remote app exec hermes -- sh -c 'curl -sf http://localhost:3000/health' \
  || echo "hermes unhealthy" >&2
# exit code 0 = healthy, non-zero = agent reports it
```

**Container not running?** The command exits with code 2 and a clear `no running container found for app <uuid>` message — distinguishable from a real command that happened to produce no output.

**Coolify label quirk:** an earlier implementation tried `--filter label=coolify.applicationId=<uuid>` — that label doesn't exist on Coolify's application containers (only on the proxy layer). Container-name-prefix matching works reliably across Coolify versions.

**Windows / Git Bash gotcha:** MSYS rewrites absolute Linux paths in arguments before they reach Python. `coolify-remote app exec hermes -- cat /etc/os-release` becomes `cat C:/Program Files/Git/etc/os-release` *locally*, then fails in the container. Two fixes, both reliable:

```bash
# Wrap in sh -c so the path is inside a shell string MSYS doesn't touch:
coolify-remote app exec hermes -- sh -c 'cat /etc/os-release'

# Or disable path conversion for this call:
MSYS_NO_PATHCONV=1 coolify-remote app exec hermes -- cat /etc/os-release
```

Affects every Windows CLI that shells out with Unix-style paths — not specific to this wrapper.

## Env propagation gotcha (READ THIS)

**Coolify stores env vars immediately but does not inject them into a running container.** The container must be redeployed to see new values. This has burned us before: `OPENAI_API_KEY` was visible in Coolify's UI / `env list` but empty inside the container, because no redeploy had happened.

The right pattern:

```bash
coolify-remote env set hermes OPENAI_API_KEY=sk-... --verify --deploy --wait
```

- `--verify` reads the envs back via API and confirms the value Coolify stored matches what you sent. Catches silent no-ops on write.
- `--deploy` triggers a redeploy so the container actually picks up the change.
- `--wait` blocks until the deployment finishes (status `finished` / `failed`).

**`--verify` only checks Coolify's API**, not the running container. To verify in-container, you either need to redeploy (`--deploy`) or exec into the container via the host. If an env refuses to propagate even after a successful redeploy, fall back to writing it via the app's own config system (e.g. Hermes `config set`, gbrain `config set`) — some apps layer their own config over env.

## Domain + TLS workflow

Coolify's PATCH field for the domain is **`domains`** (not `fqdn` — `fqdn` is read-only on the application object). Setting `fqdn` returns HTTP 422. The wrapper uses the correct field; you don't need to remember.

Typical flow for flipping an app from the default sslip.io URL to a real domain with HTTPS:

```bash
# DNS A record already points at the Coolify host.
coolify-remote tls enable hermes --domain https://hermes.example.com
```

`tls enable` does four things: PATCH domain + `is_force_https_enabled=true`, trigger deploy, wait for Let's Encrypt to issue, HEAD the HTTPS URL as a smoke test. Exits non-zero on any step failing.

If you only want to change the domain without touching TLS:

```bash
coolify-remote domain set hermes https://new.example.com --deploy --wait
```

## Deploy with --wait

`--wait` polls `/api/v1/deployments/<deployment_uuid>` every 3s until it hits a terminal state (`finished`, `failed`, `cancelled-by-*`). Prints state transitions as they happen. Exits 0 only on `finished`.

This replaces the hand-rolled `until [ "$(curl … | python3 -c …)" = "finished" ]` loops that used to fail on the Windows bash shim.

## Syncing a local .env to Coolify

```bash
coolify-remote env sync hermes .env --prefix HERMES_ --deploy --wait
```

Useful when you've been editing locally and want to push a subset of keys up. `--prefix` filters so you don't accidentally upload unrelated vars. Values are upserted (POST, falling back to PATCH on 422 "already exists").

## Quirks

- **`fqdn` is read-only, `domains` is writable.** The wrapper only accepts `domains` via `domain set`. Don't hand-roll PATCHes with `fqdn`.
- **`is_force_https_enabled` needs a redeploy** to take effect — Traefik labels are recomputed at deploy time.
- **Deploy trigger is `GET /api/v1/deploy?uuid=…&force=true`**, not POST. Yes, really.
- **POST to `/api/v1/applications/<uuid>/envs` returns 422 if the key exists**; the wrapper retries with PATCH automatically.
- **`--verify` after `env set` checks Coolify's API, not the container.** Only a redeploy puts new values into the container's process env.

## Troubleshooting

- **`HTTP 401`**: `COOLIFY_API_KEY` is wrong or expired. Generate a new one in Coolify → Keys & Tokens.
- **`Validation failed` on PATCH**: you're sending the wrong field name. Use `domain set` / `env set` rather than raw PATCH. If the wrapper itself 422s, check `coolify-remote app show <app>` against [Coolify's API docs](https://coolify.io/docs/api-reference) — the schema shifts between versions.
- **Deploy hangs on `queued` forever**: another deploy is in flight, or the worker is stuck. Check `coolify-remote app show <app>` and the Coolify web UI.
- **TLS smoke test fails after `tls enable`**: DNS hasn't propagated, or Let's Encrypt rate-limited you (5 duplicate certs / week). Check `dig +short <host>` and Coolify's deployment logs.
