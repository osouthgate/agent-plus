# Strategic-Direction Decision Record

| Field | Value |
| :--- | :--- |
| Date | 2026-04-24 |
| Status | Decisions captured; full deliberation in private companion repo. |
| Full doc | osouthgate/plans-agent-plus (private) — `2026-04-24-strategic-direction.md` |

A strategic review proposed evolving agent-plus from a token-efficiency tool layer into a "deterministic agent runtime" (workflow engine, persistent state, execution loop, tool governance, observability of agent decisions). The proposal was triaged through the repo's documented design patterns (`AGENTS.md`) and rejected on principle.

## Decisions

- **Direction.** Stay a deterministic *tool layer* — *"don't build the brain, build the reflexes."* The brain is Claude Code (or any equivalent harness); agent-plus stays the stdlib underneath.
- **Runtime layer recs (workflow engine, state layer, execution loop, tool governance, replay/debug).** Rejected. Five of seven on documented principles — they duplicate what Claude Code already does and break the determinism that is the value prop.
- **Observability of the agent's decisions.** Rejected. *Tool* telemetry (opt-in, payload bytes + duration + exit code) accepted instead, via the existing `langfuse-remote` plugin.
- **Validation gates.** Rejected as step gates. Accepted as schema-aware extensions to the existing `--output` payload envelope.
- **Universal-primitive plugins.** Five candidates queued — `repo-analyze`, `dep-graph`, `diff-summary`, `log-parse`, `schema-extract` — gated on the maintainer confirming broad usefulness as a *side-effect* of building primitives they already want.
- **Discoverability layer.** Accepted — `agent-plus list` and `agent-plus search` over the marketplace + plugin READMEs.
- **`.agent-plus/` workspace + SessionStart hook.** Accepted — `manifest.json`, `services.json` (resolved IDs/names per plugin, no secrets), `env-status.json`. Removes day-zero rediscovery.
- **Envelope contract.** The de facto `tool: {name, version}` + `--output` + `payloadShape` + `--version` surface promoted to a documented public contract in the root README.

## Top three next steps

1. Document the envelope contract in root README.
2. Ship `.agent-plus/` workspace + SessionStart hook for day-one project context.
3. Ship `repo-analyze` as the highest-leverage universal primitive.

## Out of scope (explicit non-goals)

Workflow engine. State layer. Execution loop. Tool-governance plane. Replay/debug. Multi-agent orchestration. Managed-SaaS packaging. Plugin permissions plane.
