# hermes-remote

Remote CLI for managing a [Hermes Agent](https://github.com/NousResearch/hermes-agent) deployment over its REST API. One file, stdlib Python 3, no dependencies.

Part of [agent-plus](../README.md) — a small collection of Claude Code plugins.

## Why

The upstream `hermes` CLI runs its own local gateway. There's no remote mode. Existing alternatives (`xaspx/hermes-control-interface`, `nesquena/hermes-webui`) are browser dashboards. This fills the gap for people who want a CLI — pipeable into shell scripts, driveable from another agent (Claude Code in my case).

## Install

### As a Claude Code plugin (recommended)

```bash
claude --plugin-dir /path/to/agent-plus/hermes-remote
```

Or once published to a marketplace:

```bash
/plugin install hermes-remote
```

Enabling the plugin adds `hermes-remote` to PATH and loads the skill that teaches Claude when and how to use it.

### Standalone

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/hermes-remote/bin/hermes-remote
chmod +x hermes-remote
```

## Configure

Set env vars for your Hermes deployment. Two URL modes, three password sources.

**URL — one of:**

```bash
export HERMES_URL="https://hermes.example.com"

# Or, for reverse-proxy-by-Host setups where local DNS is flaky:
export HERMES_VPS_IP="1.2.3.4"
export HERMES_HOST="hermes.example.com"
```

**Password — one of:**

```bash
export HERMES_PASSWORD="..."                         # plain value
export HERMES_PASSWORD_CMD="pass show hermes/admin"  # shell command, stdout captured
```

If Hermes is running on Coolify, the script can fetch the password from Coolify's env API:

```bash
export COOLIFY_URL="http://your-vps:8000"
export COOLIFY_API_KEY="..."
export HERMES_APP_UUID="..."
```

**Admin user** (optional, default `admin@example.com`):

```bash
export HERMES_ADMIN_USER="you@example.com"
```

## Usage

```bash
hermes-remote status
hermes-remote model
hermes-remote env list
hermes-remote cron list
hermes-remote cron show <id-or-name>
hermes-remote cron pause <id>
hermes-remote cron resume <id>
hermes-remote cron trigger <id>
hermes-remote cron remove <id> -y
hermes-remote cron create \
    --name watch-site \
    --schedule "every 1h" \
    --script ~/.hermes/scripts/watch.sh \
    --prompt "If the script output contains ERROR, report it. Otherwise [SILENT]." \
    --deliver telegram \
    --model anthropic/claude-haiku-4-5
```

Most subcommands take `--json` for piping into `jq`.

## The skillify cron pattern

If you're creating a recurring cron that does deterministic work (sync, poll, health check), put the work in `--script` and use a minimal `--prompt` with `[SILENT]`-on-success. See [skills/hermes-remote/SKILL.md](skills/hermes-remote/SKILL.md#skillify-cron-pattern-read-before-creating-a-cron) for why — it's a large cost reduction over the naive prompt-only pattern.

## How auth works

Form-logs into Hermes → grabs the `hermes_auth` cookie out of the 302 → GETs `/` and scrapes `__HERMES_SESSION_TOKEN__` from the SPA HTML → sends both (cookie + bearer token) on every subsequent call.

## DNS and Host header quirk

`urllib.request` quietly overrides the Host header based on the URL's netloc. When connecting to a raw IP and needing a specific Host (for Traefik/Caddy routing), urllib won't let you — it always sends the IP. Transport is built on `http.client` with `skip_host=True` so the Host we set is the Host that gets sent.

Irrelevant if you use `HERMES_URL`; only kicks in under the `VPS_IP + HOST` pair.

## What it doesn't do

- No chat / `/v1/chat/completions` support yet. Upstream is planning that endpoint; when it ships a `chat` subcommand is likely.
- No session / conversation history tools.
- No skill credential provisioning (OAuth, key rotation).

PRs welcome. Trying to keep it under 500 lines of stdlib Python.

## License

MIT.
