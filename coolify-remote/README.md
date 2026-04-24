# coolify-remote

Remote CLI for managing a [Coolify](https://coolify.io) PaaS instance over its REST API. One file, stdlib Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

## Why

Coolify's web UI is fine for humans; its REST API is fine for shell scripts — but the gap between the two is where agents get stuck. Every command in this wrapper collapses a known multi-call dance into one call, or catches a gotcha you'd otherwise discover via a 422.

**Quantified wins**

- `env set <app> KEY=val --verify --deploy --wait` → **1 call**. Without it: PATCH env → trigger redeploy → poll deploy status → re-fetch env to verify the container sees it. That's 4 calls + a hand-rolled `until ... | jq ...` loop which blows up on the Windows bash shim.
- `tls enable <app> --domain https://...` → **1 call**. Without it: PATCH the `domains` field (not `fqdn` — that's read-only, and you'll eat a 422 if you guess), flip `force_https`, trigger deploy, verify Let's Encrypt cert landed. Four calls, one footgun.
- `app exec <app> -- <cmd>` → **1 call**. Coolify has no REST exec endpoint; this wraps SSH + `docker exec` and returns stdout / stderr / exit code. Fits the `[SILENT]` cron pattern without intermediate polling.
- `deploy <app> --wait` → **1 call** that blocks to terminal state. Without it: trigger, then poll.
- App lookup by name everywhere. `coolify-remote deploy hermes --wait`, not `coolify-remote deploy b1c6e2f0-4a3d-4d77-ae6f-...`. UUIDs never touch the agent's context.

## Install

### Recommended — marketplace install

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install coolify-remote@agent-plus
```

Adds `coolify-remote` to PATH and loads the skill so Claude reaches for it automatically.

### Session-only (dev / try-before-install)

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/coolify-remote
```

`--plugin-dir` loads for the current shell only; nothing persisted.

### Standalone — no Claude Code

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/coolify-remote/bin/coolify-remote
chmod +x coolify-remote
./coolify-remote app list
```

## Configure

Layered config, highest precedence first:

1. `--env-file <path>`
2. Project `.env.local` / `.env` (walked up from cwd)
3. Shell environment (including Claude Code settings)

Project `.env` files override the shell. Only `COOLIFY_*` keys are picked up.

```bash
# .env
COOLIFY_URL=http://1.2.3.4:8000
COOLIFY_API_KEY=...
```

## Headline commands

```bash
coolify-remote app list
coolify-remote app show hermes
coolify-remote app exec hermes -- ls /data

coolify-remote env list hermes
coolify-remote env set hermes OPENAI_API_KEY=sk-... --verify --deploy --wait
coolify-remote env sync hermes .env --prefix HERMES_ --deploy --wait

coolify-remote tls enable hermes --domain https://myapp.example.com
coolify-remote deploy hermes --wait

coolify-remote server list
```

All list/show commands support `--json` for piping to `jq`. Apps are resolved by name → uuid → fqdn-substring, so `hermes` usually just works.

See `coolify-remote <cmd> --help` or the [skill doc](skills/coolify-remote/SKILL.md) for the full reference.

## License

MIT.
