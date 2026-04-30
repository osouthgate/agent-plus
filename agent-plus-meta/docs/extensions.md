# agent-plus-meta — extensions

How to plug a custom data source into `agent-plus-meta refresh` without modifying the meta plugin. Extensions are read-only scripts declared in `<workspace>/.agent-plus/extensions.json`; they run alongside the built-in handlers and their output is aggregated into `services.<name>` using the same envelope every framework plugin emits.

This doc is for users who want to **author** an extension. End-users only need to call `agent-plus-meta extensions list|validate|add|remove` — the README covers that surface.

Without this, every workspace is locked to the 6 plugins that ship with `refresh`. With it, you drop a script into `extensions.json` and `agent-plus-meta refresh` aggregates your own data the same way it aggregates the built-ins — same envelope, same `services.<name>` slot.

## The contract

Each extension's stdout MUST be a single JSON object:

```json
{
  "status": "ok" | "unconfigured" | "partial" | "error",
  "...": "any other fields you want, passed through verbatim"
}
```

The orchestrator wraps script output as `{plugin: "<name>", source: "extension", ...your fields...}` and merges into `services.<name>`. Non-JSON output, JSON arrays, non-zero exit, and timeouts all become `{status: "error", reason: "...", stderr_tail: "<last 500 chars>"}`. Scripts run with `cwd=<repo root>` and inherit the host env (so `gh`, `vercel`, etc. work). The orchestrator never echoes env values into output.

## Worked example

```python
#!/usr/bin/env python3
# .agent-plus/scripts/refresh-releases.py
import json, subprocess
proc = subprocess.run(
    ["gh", "api", "repos/osouthgate/agent-plus/releases", "--paginate=false"],
    capture_output=True, text=True, timeout=10,
)
if proc.returncode != 0:
    print(json.dumps({"status": "error", "reason": proc.stderr[:200]}))
else:
    releases = json.loads(proc.stdout)[:5]
    print(json.dumps({
        "status": "ok",
        "latest": [{"tag": r["tag_name"], "name": r["name"]} for r in releases],
        "count": len(releases),
    }))
```

Register it once:

```bash
agent-plus-meta extensions add --name releases \
    --command python3 \
    --command-arg=.agent-plus/scripts/refresh-releases.py \
    --description "GitHub releases summary" \
    --timeout 15
```

Every `agent-plus-meta refresh` then populates `services.releases` alongside the built-ins.

## Managing extensions

```bash
agent-plus-meta extensions list                       # show registered + on-disk check
agent-plus-meta extensions validate                   # dry-run validate (no script execution)
agent-plus-meta extensions add --name X --command Y   # append (atomic; rejects collisions)
agent-plus-meta extensions remove --name X            # remove (atomic; also drops services.<name>)

agent-plus-meta refresh --no-extensions               # skip extensions
agent-plus-meta refresh --extensions-only             # run only extensions
```

`extensions list` and `agent-plus-meta list` surface `command_hash` (sha256 of argv[0]) rather than the command itself — paths often contain usernames. Disabled extensions (`"enabled": false`) load but skip at refresh time. Names colliding with built-in plugin names are rejected at add/load time. `extensions remove` returns `services_cleaned: bool` so callers can confirm stale handler output didn't linger.
