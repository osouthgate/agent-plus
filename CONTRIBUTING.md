# Contributing to agent-plus

Architecture, conventions, and design rules for the framework. The user-facing pitch lives in [`README.md`](./README.md); doc-drift rules + writing conventions in [`AGENTS.md`](./AGENTS.md). This file is for contributors and plugin authors.

## The seven patterns

Every plugin built on this framework should reinforce at least one. Don't write a plugin without first asking which of these it's solving for.

1. **Aggregate server-side, return one blob.** N endpoints in parallel under the hood, one structured payload back. The agent sees one tool call.
2. **Resolve by name, not ID.** `coolify-remote deploy hermes` — not `coolify-remote deploy b1c6e2f0-4a3d-…`. UUIDs never touch the agent's context.
3. **`--wait` on every async mutation.** Deploys, cron triggers, backups — if it returns an action ID, the CLI polls. No hand-rolled `until` loops in prompts.
4. **`--json` on every list / show.** Structured output is the default; human-formatted output is for interactive use only.
5. **Strip values the agent shouldn't see.** Env-var values, secrets, large blobs — if the agent doesn't need them to decide, they don't enter the transcript.
6. **Self-diagnosing output.** Every payload carries `tool: {name, version}` from the manifest at runtime. Stale plugin caches and PATH pinning are visible from the output alone — no extra subprocess call.
7. **Stay in your lane.** Each plugin's SKILL.md lists the cases where the agent should drop to the raw CLI / API instead of looping on a rejection.

## The envelope contract

Every `--json` output uses the same outer shape so an agent can parse, version-check, and offload large payloads identically across plugins.

- `tool.name` / `tool.version` — read from `<plugin>/.claude-plugin/plugin.json` at runtime.
- `--output PATH` writes the full payload to disk; stdout returns a compact summary (`payloadPath`, `bytes`, `payloadKeys`, `payloadShape`).
- `--shape-depth 1|2|3` — recursion depth for `payloadShape` (default 3). Lets the agent decide whether to read the file.
- Universal flags: `--json`, `--pretty`, `--version`, `--output`, `--shape-depth`.
- **No env-var values, no secrets, no tokens.** Names and IDs only.

The contract is enforced by `agent-plus-meta/test/contract/test_envelope_contract.py`, which runs every installed plugin's `--version`, `--help`, and a safe read probe, asserting the envelope shape, version match, and absence of `savedTo` (renamed to `payloadPath` in the framework-extraction slice A0). New plugins must register a probe in `SAFE_PROBES` to be picked up.

## Philosophy

**Deterministic work belongs in scripts, not prompts.** The LLM orchestrates; the code does. Every plugin ships a `SKILL.md` that teaches Claude *when* to reach for the script, not how to reinvent it in a prompt.

That's the whole game. If a plugin's bin script starts embedding LLM calls, something's wrong — the prompting belongs in the caller (Claude Code session, Hermes cron), the determinism belongs in the CLI.

## Conventions across framework plugins

- **Stdlib-only Python 3.** No `pip install`, no venvs. `bin/<plugin>` is one file — copy it anywhere on `$PATH` and run standalone.
- **Layered `.env` autoload**, highest precedence first: `--env-file` → project `.env.local` / `.env` (walked up from cwd) → `~/.agent-plus/.env` → shell env. **Project `.env` wins over shell.**
- **Per-plugin `CHANGELOG.md`** for release notes. Short: what changed, why it matters, date.
- **Per-plugin `bin/<name>`** is one file — single-file authoring keeps the standalone-install path trivial. The meta plugin runs ~3k LoC; service wrappers usually 500–1500.

## Plugin shape

Plugins follow the standard Claude Code plugin shape:

```
<plugin>/
├── .claude-plugin/plugin.json
├── bin/<plugin>                  # stdlib Python 3, single file
├── skills/<plugin>/SKILL.md      # how Claude uses it
├── README.md                     # how a human understands it
├── CHANGELOG.md                  # pain points and wins, most-recent-first
└── LICENSE (or inherits root)
```

See [Claude Code plugin docs](https://code.claude.com/docs/en/plugins) for the full plugin spec.

## Iterating locally

```bash
claude --plugin-dir ./<plugin-name>
# edit SKILL.md / bin/<name> / README.md
# /reload-plugins to pick up changes
```

Stack multiple plugins by repeating `--plugin-dir`.

## The inquiry pattern (skill-plus inquire)

`inquire` is the canonical way to answer "does this plugin cover what users actually do?" The pipeline stacks multiple source classes (cli, plugin, web, openapi, repo, transcripts) and classifies gaps as Type A (missing), B (misaligned), or C (aligned) against the target's existing subcommands. Transcript mining is the key addition to v0.5.0 of skill-plus: raw command tuples are clustered in-memory using a two-tier fingerprint scheme and never written to disk or the envelope. The `well_used` verdict means no action is needed -- the canned command matches real usage. When writing a new `--audit` mode for a plugin, the Type A/B/C labels and priority field are the contract; presentation details are free to evolve.

## Tests

```bash
python3 -m pytest <plugin>/test/ -v
```

Each plugin owns its own test suite. Cross-plugin contract tests live under `agent-plus-meta/test/contract/` and run automatically against the installed plugin set (resolved from `~/.claude/plugins/cache/agent-plus/` by default; override with `AGENT_PLUS_PLUGINS_DIR=<path>` to test the dev tree).

## The tour GIF

The animated demo at the top of the root README is rendered from a synthetic asciinema cast file checked in at `assets/tour.cast`. The cast is the source of truth — `assets/tour.gif` is a build artifact.

Regenerate the cast (after adding a step to the tour or changing wording):

```bash
python3 assets/generate_tour_cast.py     # writes assets/tour.cast
```

Rebuild the GIF (needs `agg` on PATH or `docker` available):

```bash
bash assets/build_tour_gif.sh
```

The script uses [`agg`](https://github.com/asciinema/agg) directly when on PATH, falls back to the official `ghcr.io/asciinema/agg` Docker image otherwise. Install agg via `cargo install --git https://github.com/asciinema/agg` if you want the local-binary path.

The cast is plain JSON (asciinema v2 format) — anyone with agg can render it without our build script.

## Pre-commit hook (recommended)

agent-plus's tests can pass locally but fail in CI when the maintainer's `~/.env` (real API keys) leaks into `load_env`, or when shell env vars trigger different code paths than CI's clean env. Two recent incidents (v0.12.0 `test_decode_windows_drive_form`, v0.15.5 `test_doctor_degraded_when_envvar_missing`) shipped passing local tests + red CI.

The repo ships a versioned pre-commit hook at `.githooks/pre-commit` that re-runs the test suite under `env -i` (clean env, like CI) before every commit. **Activate it once per clone:**

```bash
sh scripts/install-precommit.sh
```

That sets `git config core.hooksPath .githooks`. From then on, `git commit` runs the hook unless bypassed via `--no-verify` or `SKIP_PRECOMMIT_TESTS=1`. Skips automatically for pure documentation commits (no `.py` / `.sh` / test/ changes staged).

To deactivate: `git config --unset core.hooksPath`.

## Doc-drift discipline

When you modify `bin/<name>` or `skills/<name>/SKILL.md`, you must also update `README.md` and append a `CHANGELOG.md` entry **before** the commit. The repo's `.claude/hooks/check-readme-drift.sh` enforces this on every Stop event. The `.github/workflows/doc-drift.yml` CI gate also asserts README badges match `VERSION` + actual test count + plugin.json semver — if they drift, the gate fails the PR.

When you ADD a whole new plugin, also:
- Add it to the root `README.md` plugin table.
- Add it to `.claude-plugin/marketplace.json`.
- Run `gh repo edit osouthgate/agent-plus --add-topic <name>` after first promotion to public.

See [`AGENTS.md`](./AGENTS.md) for the full writing conventions and drift rules.

## Releasing

The private staging clone (`osouthgate/plans-agent-plus`) is the build surface; the public repo (`osouthgate/agent-plus`) is the user-facing artifact. Slices land on private `main` first, then promote to public via cherry-pick. See [`STAGING.md`](./STAGING.md) (private only) for the full release ritual.

Public release log lives at [`STAGING.md` § Public release log](./STAGING.md) and as GitHub releases under tags `v0.X.Y`.
