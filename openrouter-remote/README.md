# openrouter-remote

CLI for [OpenRouter](https://openrouter.ai) — balance, usage stats, model discovery with filtering, and API key management. One file, stdlib Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

## Why

Three things that kept coming up:

1. **"Am I about to run out of credits?"** — `balance --alert-below 5.00` exits `1` on low credit, designed for the Hermes `[SILENT]`-on-success cron pattern. Successful polls stay silent; only a low balance gets delivered.
2. **"What's the cheapest model that supports tools and has 200k+ context?"** — the model list is 350+ entries. Dumping the whole catalogue into Claude's context is wasteful and slow. `models list --supports tools --min-context 200000 --max-price-input 1.0` **filters client-side and only returns matching rows**. The 350-entry blob never lands in Claude's context.
3. **"How do I disable / rotate / limit my keys?"** — the [Management API](https://openrouter.ai/docs/guides/overview/auth/management-api-keys) exists but everyone hand-rolls curl for it. Here: `keys disable hermes-prod`, `keys set-limit hermes-prod 50.00`. Keys are referenceable by name or hash-prefix, not by the full 64-char hash.

Plus: `usage` aggregates `usage_daily/weekly/monthly/all-time` across every key in one call — no per-key loops.

## Install

### Recommended — marketplace install

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install openrouter-remote@agent-plus
```

Adds `openrouter-remote` to PATH and loads the skill so Claude reaches for it automatically.

### Session-only (dev / try-before-install)

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/openrouter-remote
```

`--plugin-dir` loads for the current shell only; nothing persisted.

### Standalone — no Claude Code

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
