---
name: openrouter-remote
description: Check OpenRouter credit balance, aggregate per-key usage stats, search and filter the 350+ model catalogue by price / context / capability, and manage API keys via the provisioning endpoint. Designed for both interactive use and cron jobs (balance alerts, usage summaries).
when_to_use: Trigger on phrases like "what's my openrouter balance", "am i running low on credits", "show openrouter usage this month", "what's the cheapest model with vision", "find a free model with tools", "list my openrouter keys", "create a new openrouter key", "disable the hermes key", "how much have I spent today".
allowed-tools: Bash(openrouter-remote:*) Bash(python3 *openrouter-remote*:*)
---

# openrouter-remote

Stdlib-only Python 3 CLI wrapping three OpenRouter surfaces:

- **Balance** — `GET /api/v1/credits` (needs user key)
- **Models** — `GET /api/v1/models` (no auth) with rich client-side filtering
- **Keys** — `/api/v1/keys*` (needs **provisioning key**, a distinct credential)

Lives at `${CLAUDE_SKILL_DIR}/../../bin/openrouter-remote`; the plugin auto-adds `bin/` to PATH.

Every JSON payload carries a top-level `tool: {name, version}` field. Check it with `openrouter-remote --version`, or inspect live output via `openrouter-remote balance --json | jq .tool` — useful when debugging whether an agent is hitting a stale plugin install.

**Pipe JSON through `jq`** for any non-trivial slicing — e.g. `models list --json --supports tools | jq '.[] | select(.context_length >= 200000) | .id'`, or `usage --json | jq '.totals.monthly'`. The CLI's built-in filters cover the common cases; `jq` is the escape hatch for everything else without re-fetching.

## When to reach for this

- User asks about balance, remaining credits, or current spend.
- User wants to know *which model* to use for a task (cheapest with tools, biggest context, etc.).
- User wants to create / disable / delete API keys or change their limits.
- You're configuring a Hermes cron that needs to alert when credits get low — use `balance --alert-below`.

Do NOT use this for making chat completions — that's what an OpenAI-compatible SDK pointed at `https://openrouter.ai/api/v1` is for. This plugin is management-plane only.

## Configure

Layered config, highest precedence first:

1. `--env-file <path>`
2. `.env.local` / `.env` (walked up from cwd)
3. Shell environment (including Claude Code settings)

Project `.env` files override the shell. Only `OPENROUTER_*` keys are picked up.

**Two distinct credentials** — don't confuse them:

```bash
# .env
OPENROUTER_API_KEY=sk-or-...            # user key, for completions + balance
OPENROUTER_PROVISIONING_KEY=sk-or-v1-...  # management key, for /keys endpoints
```

The provisioning key is generated separately at <https://openrouter.ai/settings/provisioning-keys> and **cannot be used to make completions** — it's management-plane only. Each command documents which key it needs.

## Balance

```bash
openrouter-remote balance
# total_credits: $10.0000
# total_usage:   $9.8848
# remaining:     $0.1152

openrouter-remote balance --json                  # for jq
openrouter-remote balance --alert-below 2.00      # exits 1 if remaining < $2
```

**`--alert-below` is the cron hook.** Pair it with Hermes's `[SILENT]` pattern: the script exits 1 on low balance and the agent reports; otherwise the agent stays silent and spends no tokens.

```bash
# Hermes cron (skillify pattern)
hermes-remote cron create \
    --name openrouter-balance-watch \
    --schedule "every 6h" \
    --script "openrouter-remote balance --alert-below 5.00" \
    --prompt "If the script exited with ALERT text on stderr, report the balance. Otherwise respond with only [SILENT]." \
    --model anthropic/claude-haiku-4-5 \
    --deliver origin
```

## Usage stats

```bash
openrouter-remote usage                    # account totals + per-key breakdown
openrouter-remote usage --key hermes-prod  # narrow to one key
openrouter-remote usage --json             # for jq / dashboards
```

Shows `today / this week / this month / all-time` totals aggregated from every key's `usage_daily/weekly/monthly/usage` fields, plus a sorted per-key table. Useful for answering "where did my credit go this week?" — the keys with the biggest `monthly` numbers rise to the top.

**BYOK usage** (bring-your-own-key) is reported separately — it doesn't debit OpenRouter credit, but showing the totals helps if you're routing traffic through multiple providers and want the full picture.

Needs `OPENROUTER_PROVISIONING_KEY`. If `OPENROUTER_API_KEY` is also set, the account-level credit header is included for free.

## Models — list, filter, show

```bash
openrouter-remote models list                                    # all 350+
openrouter-remote models list --provider anthropic
openrouter-remote models list --free                             # truly free (price == 0)
openrouter-remote models list --supports tools,vision
openrouter-remote models list --max-price-input 1.0              # $/M tokens
openrouter-remote models list --min-context 200000
openrouter-remote models list --search "claude"
openrouter-remote models list --sort price-in --limit 10
openrouter-remote models cheap --supports tools                  # shortcut
openrouter-remote models show anthropic/claude-haiku-4-5
```

Filters compose — `--free --supports tools --min-context 100000` narrows to free, tool-capable, 100k+ context models. Prices are displayed in $/million tokens.

**Capability shorthand** accepted by `--supports`:

| Shorthand | Checked against |
|---|---|
| `tools` | `supported_parameters` contains `tools` or `tool_choice` |
| `vision` / `image` | `architecture.input_modalities` contains `image` |
| `reasoning` / `thinking` | `supported_parameters` contains `reasoning` or `include_reasoning` |
| `json` / `structured` | `supported_parameters` contains `response_format` |

**Meta-routers** like `openrouter/auto`, `openrouter/pareto-code` report price `-1` as a sentinel meaning "varies by underlying model". Displayed as `varies` and correctly excluded from `--free`.

## Models — per-provider endpoints (throughput, quant, routing data)

The bulk `/models` list only shows the `top_provider` — but most popular models are served by **multiple providers** with different prices, quantizations (fp16 / bf16 / fp8 / int8), context lengths, and live throughput/latency/uptime stats. This is where you actually answer "which provider should I route to?".

```bash
openrouter-remote models endpoints meta-llama/llama-3.3-70b-instruct
openrouter-remote models endpoints anthropic/claude-sonnet-4.5 --sort latency
openrouter-remote models endpoints mistralai/mixtral-8x7b-instruct --sort price-in --limit 3
openrouter-remote models endpoints google/gemini-2.5-flash --all --json  # include degraded
```

Columns: provider (with routing tag), quantization, context length, `throughput_last_30m` in tokens/sec, `latency_last_30m` (first-token), `uptime_last_30m`, and per-provider input/output pricing.

Sort by `throughput`, `latency`, `price-in`, `price-out`, `uptime`, `context`, or `name`. Throughput and latency fields populate sporadically — OpenRouter aggregates them from live traffic, so quiet models sometimes show `-`. Pricing / quant / uptime are always present.

**Why you care:** for a 70B Llama you might see DeepInfra at $0.10/M (fp8, 131k ctx, 99% uptime) and Groq at $0.59/M (unknown quant, but potentially much faster). Same model id, wildly different tradeoffs. You can't see this from the web UI quickly, and the bulk `/models` hides it.

## BYOK integrations — web-UI-only

OpenRouter supports "bring your own key" (plug your own Anthropic / Groq / OpenAI keys into OpenRouter, and OR routes through them — costs hit your provider account, not your OR credit balance).

**There is no public REST API for managing BYOK keys.** Verified against the docs on 2026-04-23:

- <https://openrouter.ai/docs/guides/overview/auth/byok>
- <https://openrouter.ai/docs/features/provisioning-api-keys> (scoped to `/api/v1/keys` user-key CRUD only)

Manage BYOK providers at <https://openrouter.ai/settings/integrations>.

The CLI keeps an `openrouter-remote integrations` subcommand that just prints the URL and exits non-zero — so old invocations in scripts or skills get a clear pointer instead of a silent 404 chasing a non-existent endpoint.

## Key management

All `keys` subcommands require `OPENROUTER_PROVISIONING_KEY`.

```bash
openrouter-remote keys list [--all]                       # paginate with --all
openrouter-remote keys show <hash-prefix-or-name>
openrouter-remote keys create --name hermes-prod --limit 20 --limit-reset monthly
openrouter-remote keys disable <key>                      # PATCH disabled=true
openrouter-remote keys enable <key>
openrouter-remote keys set-limit <key> 50.00 [--reset monthly]
openrouter-remote keys rename <key> <new-name>
openrouter-remote keys delete <key> -y
```

Keys can be referenced by full hash, hash prefix (if unambiguous), or name/label. You don't need to copy full hashes around.

**`create` shows the new `sk-or-...` string ONCE on stderr.** Save it immediately — OpenRouter won't show it again. After that, you only have the `hash` to identify the key by.

**`disable` vs `delete`**: disable is reversible, delete is not. Prefer disable for temporary revocation (e.g. a key that leaked briefly).

## Quirks

- **Two credential types.** `OPENROUTER_API_KEY` = user key for completions + balance. `OPENROUTER_PROVISIONING_KEY` = management-only. The error messages steer you to the right one.
- **POST to `/keys/` needs the trailing slash** — some clients 307 without it.
- **`include_byok_in_limit` and `limit_reset`** only kick in after a PATCH; on first create they may be silently defaulted.
- **The `key` string is returned only on creation** — never again. The `hash` is what you use to identify it afterwards.
- **Free models can silently return errors** when overloaded; if a `:free` model starts 500ing, swap in the paid variant.

## Troubleshooting

- **`HTTP 401` on `/credits`**: `OPENROUTER_API_KEY` wrong. Check [openrouter.ai/keys](https://openrouter.ai/keys).
- **`HTTP 401` on `/keys*`**: you're using the user key instead of the provisioning key. Generate one at [openrouter.ai/settings/provisioning-keys](https://openrouter.ai/settings/provisioning-keys).
- **`HTTP 403 disabled`** on completions elsewhere: one of your keys was auto-disabled (usually rate-limit abuse). `openrouter-remote keys list` will show `[x]` next to it.
