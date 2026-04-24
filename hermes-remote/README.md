# hermes-remote

Remote CLI for a [Hermes Agent](https://github.com/NousResearch/hermes-agent) deployment — cron jobs, live config, chat passthrough, env inspection. One file, stdlib Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

## Why

The upstream `hermes` CLI runs its own local gateway; there's no remote mode. Existing alternatives (`xaspx/hermes-control-interface`, `nesquena/hermes-webui`) are browser dashboards. This is a CLI — pipeable into shell scripts, driveable from another agent.

**The big win: the skillify cron pattern.** A recurring Sonnet-on-every-tick cron burned **~$10 / 12h** before this plugin forced the discipline of `--script` (deterministic work out of the prompt) + minimal Haiku prompt + `[SILENT]` on success. Three orders of magnitude cheaper. See [the pattern](#the-skillify-cron-pattern) below.

## Install

### Recommended — marketplace install

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install hermes-remote@agent-plus
```

Adds `hermes-remote` to PATH and loads the skill so Claude reaches for it automatically.

### Session-only (dev / try-before-install)

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/hermes-remote
```

`--plugin-dir` loads for the current shell only; nothing persisted.

### Standalone — no Claude Code

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/hermes-remote/bin/hermes-remote
chmod +x hermes-remote
./hermes-remote status
```

## Configure

Layered config, highest precedence first: `--env-file <path>` → project `.env.local` / `.env` (walked up from cwd) → shell env. Project `.env` wins over shell. Only `HERMES_*` and `COOLIFY_*` keys are picked up.

**URL — one of:**

```bash
HERMES_URL="https://hermes.example.com"

# Or, for reverse-proxy-by-Host setups where local DNS is flaky:
HERMES_VPS_IP="1.2.3.4"
HERMES_HOST="hermes.example.com"
```

**Password — one of:**

```bash
HERMES_PASSWORD="..."                         # plain value
HERMES_PASSWORD_CMD="pass show hermes/admin"  # shell command, stdout captured

# Or, if Hermes runs on Coolify, auto-fetch from Coolify's env API:
COOLIFY_URL="http://your-vps:8000"
COOLIFY_API_KEY="..."
HERMES_APP_UUID="..."
```

**For the `chat` subcommand only** — bearer auth against `/v1/chat/completions`, distinct from the admin session cookie:

```bash
HERMES_CHAT_API_KEY="..."   # the API_SERVER_KEY configured in Hermes env
```

**Admin user** (optional, default `admin@example.com`): `HERMES_ADMIN_USER="you@example.com"`.

If any required value is missing, the CLI tells you exactly where to set it (project `.env` preferred, or `~/.claude/settings.json` for global).

## Headline commands

```bash
hermes-remote status
hermes-remote model
hermes-remote env list

# Live config — overrides env-var-based config without a redeploy
hermes-remote config get                        # full config.yaml as JSON
hermes-remote config get agent.max_turns        # single dotted key
hermes-remote config set model anthropic/claude-haiku-4-5

# Cron management
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

# Chat — OpenAI-compatible /v1/chat/completions passthrough, one-shot
hermes-remote chat "summarise the last hermes run"
hermes-remote chat "status?" --model anthropic/claude-haiku-4-5 --max-tokens 2048
hermes-remote chat "json diff x y" --system "You output only valid JSON." --json
```

Most list/show commands take `--json` for piping into `jq`.

## The skillify cron pattern

If you're creating a recurring cron that does deterministic work (sync, poll, health check), put the work in `--script` and use a minimal `--prompt` with `[SILENT]`-on-success. See [skills/hermes-remote/SKILL.md](skills/hermes-remote/SKILL.md#skillify-cron-pattern-read-before-creating-a-cron) for why — it's a large cost reduction over the naive prompt-only pattern.

## How auth works

Form-logs into Hermes → grabs the `hermes_auth` cookie out of the 302 → GETs `/` and scrapes `__HERMES_SESSION_TOKEN__` from the SPA HTML → sends both (cookie + bearer token) on every subsequent call.

## DNS and Host header quirk

`urllib.request` quietly overrides the Host header based on the URL's netloc. When connecting to a raw IP and needing a specific Host (for Traefik/Caddy routing), urllib won't let you — it always sends the IP. Transport is built on `http.client` with `skip_host=True` so the Host we set is the Host that gets sent.

Irrelevant if you use `HERMES_URL`; only kicks in under the `VPS_IP + HOST` pair.

## What it doesn't do

- No session / conversation history tools — `chat` is one-shot, no threading.
- No skill credential provisioning (OAuth, key rotation).
- No `cron edit` — change-in-place isn't exposed; remove + create is the idiom.

PRs welcome. Trying to keep it under 500 lines of stdlib Python.

## License

MIT.
