# coolify-remote

Remote CLI for managing a [Coolify](https://coolify.io) PaaS instance over its REST API. One file, stdlib Python 3, no dependencies.

Part of [agent-plus](../README.md) — a small collection of Claude Code plugins.

## Why

Coolify's web UI is fine for humans; its REST API is fine for shell scripts — but the gap between the two is where agents get stuck. Common fumbles this wrapper collapses:

- PATCHing the wrong field name (`fqdn` is read-only; the writable field is `domains`) and getting 422'd.
- Threading a UUID through six calls instead of looking an app up by name.
- Hand-rolling `until curl … | python3 -c …` loops to poll deployments — which blow up on the Windows bash shim.
- Setting an env var, seeing it in the UI, but the running container still can't see it (you needed a redeploy).

This tool turns each of those into a one-liner.

## Install

### As a Claude Code plugin (recommended)

```bash
claude --plugin-dir /path/to/agent-plus/coolify-remote
```

Enabling the plugin adds `coolify-remote` to PATH and loads the skill that teaches Claude when and how to use it.

### Standalone

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
coolify-remote env set hermes OPENAI_API_KEY=sk-... --verify --deploy --wait
coolify-remote tls enable hermes --domain https://myapp.example.com
coolify-remote deploy hermes --wait
```

See `coolify-remote <cmd> --help` or the [skill doc](skills/coolify-remote/SKILL.md) for the full reference.

## License

MIT.
