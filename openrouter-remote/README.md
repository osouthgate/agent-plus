# openrouter-remote

CLI for [OpenRouter](https://openrouter.ai) — balance, usage stats, model discovery with filtering, and API key management. One file, stdlib Python 3, no dependencies.

Part of [agent-plus](../README.md) — a small collection of Claude Code plugins.

## Why

Three things that kept coming up:

1. **"Am I about to run out of credits?"** — I want a one-liner, and a cron-friendly exit code for alerting.
2. **"What's the cheapest model that supports tools and has 200k+ context?"** — the OpenRouter model list is 350+ entries; the web UI doesn't filter usefully for this.
3. **"How do I disable / rotate / limit my keys?"** — the provisioning API exists but everyone hand-rolls curl for it.

This plugin wraps all three into one CLI, using the [Management API](https://openrouter.ai/docs/guides/overview/auth/management-api-keys) for key ops.

## Install

### As a Claude Code plugin

```bash
claude --plugin-dir /path/to/agent-plus/openrouter-remote
```

### Standalone

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/openrouter-remote/bin/openrouter-remote
chmod +x openrouter-remote
./openrouter-remote balance
```

## Configure

Layered. Highest precedence first: `--env-file` → project `.env.local` / `.env` → shell env. Only `OPENROUTER_*` keys.

```bash
# .env
OPENROUTER_API_KEY=sk-or-...              # for balance + completions elsewhere
OPENROUTER_PROVISIONING_KEY=sk-or-v1-...  # for key management endpoints
```

The provisioning key is a distinct credential generated at [openrouter.ai/settings/provisioning-keys](https://openrouter.ai/settings/provisioning-keys). It **cannot make completions** — management-plane only.

## Headline commands

```bash
openrouter-remote balance                                # credits + usage
openrouter-remote balance --alert-below 5.00            # cron-friendly: exit 1 if low

openrouter-remote usage                                  # per-key + account totals, day/week/month/all-time
openrouter-remote usage --key hermes-prod

openrouter-remote models list --free --supports tools
openrouter-remote models cheap --supports vision --limit 10
openrouter-remote models show anthropic/claude-haiku-4-5
openrouter-remote models endpoints meta-llama/llama-3.3-70b-instruct --sort throughput

openrouter-remote keys list
openrouter-remote keys create --name hermes-prod --limit 20 --limit-reset monthly
openrouter-remote keys disable hermes-prod
openrouter-remote keys set-limit hermes-prod 50.00
```

See the [skill doc](skills/openrouter-remote/SKILL.md) for the full reference, filter syntax, and the `balance --alert-below` pattern for Hermes crons.

## License

MIT.
