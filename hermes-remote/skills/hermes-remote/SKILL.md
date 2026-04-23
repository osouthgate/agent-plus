---
name: hermes-remote
description: Manage a remote Hermes Agent deployment from the CLI — list/create/pause/trigger cron jobs, check status, inspect env vars, swap models. Use this skill whenever you need to control a Hermes instance that lives on another machine (VPS, Coolify app, etc.), since the upstream `hermes` CLI only talks to a local gateway.
when_to_use: Trigger on phrases like "list the hermes crons", "pause the hermes cron", "check the hermes model", "what is hermes running", "swap the hermes model to X", "trigger the gbrain sync cron", "what's on the remote hermes", "show hermes env", "restart hermes", anything about scheduled tasks on a remote Hermes.
allowed-tools: Bash(hermes-remote:*) Bash(python3 *hermes-remote*:*)
---

# hermes-remote

Remote CLI for talking to a [Hermes Agent](https://github.com/NousResearch/hermes-agent) deployment over its REST API. Stdlib-only Python 3. No pip installs.

The binary lives at `${CLAUDE_SKILL_DIR}/../../bin/hermes-remote` inside this plugin, and `bin/` is auto-added to PATH when the plugin is enabled — so call it as `hermes-remote`.

## When to reach for this

- The user asks about the state of a remote Hermes instance (status, model, crons, env).
- The user wants to pause, resume, trigger, or delete a Hermes cron job.
- The user wants to create a new cron — **always use the skillify pattern below**, not a prompt-only job.
- The user wants to inspect which env vars Hermes can see vs has missing.

Do NOT use this for chat-style conversations with the Hermes agent. This is for admin / control-plane ops.

## Configure

Everything is env-driven. Set one URL mode and one password source.

**URL — one of:**

```bash
export HERMES_URL="https://hermes.example.com"
# OR for reverse-proxy-by-Host setups with flaky DNS:
export HERMES_VPS_IP="1.2.3.4"
export HERMES_HOST="hermes.example.com"
```

The second form hits the IP on port 80 directly and passes the Host header, letting Traefik/Caddy/Nginx route. Skips DNS.

**Password — one of:**

```bash
export HERMES_PASSWORD="..."                      # plain value
export HERMES_PASSWORD_CMD="pass show hermes"     # shell command, stdout is the password
```

If the Hermes instance is managed by Coolify, the script can fetch the password automatically:

```bash
export COOLIFY_URL="http://your-vps:8000"
export COOLIFY_API_KEY="..."
export HERMES_APP_UUID="..."
```

**Admin user** (optional, default `admin@example.com`):

```bash
export HERMES_ADMIN_USER="you@example.com"
```

## Commands

```bash
hermes-remote status                    # gateway state + connected platforms
hermes-remote model                     # current LLM + context window
hermes-remote env list [--all]          # env vars Hermes knows about
hermes-remote config get [key]          # read full config, or a dotted-path key
hermes-remote config set <key> <value>  # mutate config.yaml via /api/config
hermes-remote cron list                 # all cron jobs, one line each
hermes-remote cron show <id-or-name>    # full JSON for one job
hermes-remote cron pause <id>
hermes-remote cron resume <id>
hermes-remote cron trigger <id>         # fire on next scheduler tick
hermes-remote cron remove <id> -y
hermes-remote cron create \
    --name <name> --schedule "every 15m" \
    --script /path/to/work.sh \
    --prompt "If script output contains ERROR, report it. Otherwise [SILENT]." \
    --deliver origin --model anthropic/claude-haiku-4-5
```

Most subcommands take `--json` for piping to `jq`.

## Changing the LLM model (READ THIS)

**Hermes's `config.yaml` overrides env vars for keys it knows about.** The `LLM_MODEL` env var only sets the default at first boot. Once config.yaml has a model, `LLM_MODEL` is ignored — so setting it in Coolify / Docker env and redeploying won't change anything.

To actually change the model, write it via the config API:

```bash
hermes-remote config set model anthropic/claude-haiku-4-5
```

No redeploy needed — it's live. Verify:

```bash
hermes-remote model   # reads /api/model/info; should match immediately
```

Same applies to any other config.yaml key (agent.max_turns, compression.threshold, telegram.channel_prompts, etc.) — `config set` is the mutation path.

## Skillify cron pattern (READ BEFORE CREATING A CRON)

A Hermes cron job has two input fields: `prompt` and `script`.

- `prompt` runs **inside a full agent LLM session** on every tick. Default model, full context, tool loop. Expensive.
- `script` runs **before** the agent starts — pure shell/python, zero LLM tokens. Its stdout becomes context for the agent.

If your cron does deterministic work (sync, embed, poll, sanity check), the work belongs in `--script`. The agent should only be invoked to decide "report or stay quiet" based on the script's output.

**Template for any recurring deterministic cron:**

```bash
hermes-remote cron create \
    --name <job-name> \
    --schedule "every 15m" \
    --script /path/on/hermes/host/to/worker.sh \
    --prompt "If the script output contains ERROR, report the relevant lines. Otherwise respond with only [SILENT]." \
    --model anthropic/claude-haiku-4-5 \
    --deliver origin
```

When the agent's response starts with `[SILENT]`, Hermes suppresses delivery — so successful quiet runs cost essentially nothing.

**Why this matters (real incident):** A gbrain sync cron on our Hermes deployment was created with `--prompt` only, using the default Sonnet 4.6 model, running every 15 minutes. Each run invoked the full reasoning loop to execute two shell commands masquerading as reasoning work — burning OpenRouter credit fast. Fixing it to `--script` + minimal Haiku prompt was a roughly three-orders-of-magnitude cost reduction.

**Rules of thumb for cron models:**
- Cheap recurring jobs → `anthropic/claude-haiku-4-5` (fast, cheap, fine for "is this output an error?")
- Genuinely latent work (entity extraction, summarisation, non-trivial judgment) → stay on Sonnet but cap `max_tokens` in Hermes config.

## Quirks

- `is_build_time` is not a valid field on Hermes env POSTs (will 422). Stick to `{"key":..., "value":..., "is_preview": false, "is_literal": true}`.
- urllib.request overrides Host based on URL netloc; this script uses `http.client` with `skip_host=True` to honour explicit Host headers.
- Hermes's `/api/env` only lists vars that match its config schema. A var can be in the container's shell env but not in this list — they're different surfaces.

## Troubleshooting

- **`HTTP 401` from /login**: password wrong, or you're hitting the `:3000` port directly and it has a separate basic-auth gate in front. Route through the main reverse-proxy URL.
- **`Couldn't extract __HERMES_SESSION_TOKEN__`**: login worked but the SPA HTML didn't include the token. Probably a version mismatch; upstream Hermes < 0.10 may not expose it in the same place.
- **`Unauthorized` on specific `/api/*` endpoints with cookie + token set**: that endpoint isn't wired up in your Hermes version, or you're on a preview that renamed it. Try listing with `--json` on a known-good endpoint first to confirm auth is working.
