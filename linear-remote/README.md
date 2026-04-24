# linear-remote

Read-first wrapper around the [Linear GraphQL API](https://developers.linear.app/docs/graphql/working-with-the-graphql-api) for fast issue context, project triage, and "turn this design doc into a Linear issue" workflows. Stdlib-only Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

## Why

The alternative for an agent is the Linear MCP — which is capable but requires an OAuth browser round-trip to authenticate. The transcript evidence for this plugin is a session where the agent received an 8KB design doc with "add this to Linear as an issue", reached for `plugin:linear:authenticate`, hit the browser auth wall, gave up, and wrote the content to a local `.issues/` markdown file instead. The issue never got into Linear. A second transcript shows the same agent making 7 `get_issue` + 6 `save_issue` + 2 `list_projects` + 1 `list_issue_labels` calls to drive a single issue through a state transition — one issue, 16 tool calls.

`linear-remote` wraps a static personal API key (no browser step) and collapses the MCP round-trip dance. `issues create --from-markdown design-doc.md --team LOA` replaces "open browser, auth, copy team UUID, copy project UUID, copy label UUIDs, stitch mutation". `issues get LOA-229 --include-comments --include-relations` replaces the multi-call context fetch.

**Measured wins**

- **One-call issue context.** `issues get --include-comments --include-relations` returns title, description, state, assignee, labels, project, parent, comments, and blocking/blocked-by relations in a single GraphQL request. No chained `get_issue` + `list_comments` + `get_team` + `list_issue_labels`.
- **`--from-markdown` is first-class.** The "8KB design doc → Linear issue" path is a single command. YAML frontmatter controls `team`, `project`, `labels`, `assignee`, `priority`. First H1 becomes the title. HTML comments are stripped (Linear renders `<!-- -->` as visible text — the gotcha this plugin quietly handles).
- **Accepts either ID format.** `LOA-229` (the form agents see in URLs, git branches, PR titles) and `abc123de-...` UUIDs both work everywhere. Free-text falls through to search with ambiguity surfacing candidates.
- **Zero API-key leakage.** Every response walks through `_scrub()` before emission, masking `apiKey`, `token`, `secret`, `webhookSecret`, `password`. A canary-value test asserts a known secret substring cannot appear in any output path.
- **`--wait` scoped to webhook transitions.** `issues move --wait` is for the GitHub-PR → Linear-state pipeline, not the instant mutation itself. 3s poll, 60s default timeout, partial JSON on timeout.

## Install

### Recommended — marketplace install

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install linear-remote@agent-plus
```

Adds `linear-remote` to PATH and loads the skill so Claude reaches for it automatically.

### Session-only (dev / try-before-install)

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/linear-remote
```

### Standalone — no Claude Code

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/linear-remote/bin/linear-remote
chmod +x linear-remote
./linear-remote teams list --pretty
```

## Prerequisites

- **`LINEAR_API_KEY`** — personal API key. Get one at https://linear.app/settings/api.
- **Python 3.9+** (stdlib only).

Config precedence (highest first):
1. `--api-key` / `--team` CLI flags
2. `--env-file <path>`
3. `.env.local` / `.env` walked up from cwd (closest wins)
4. Shell env

Only `LINEAR_*` prefixed vars are read.

## Usage

```bash
# One-call issue context
linear-remote issues get LOA-229 --include-comments --include-relations --pretty

# List in-progress work on a project, assigned to me
linear-remote issues list --project 'Agent Plus' --state 'In Progress' --assignee alice@example.com

# Turn a design doc into a Linear issue
linear-remote issues create --from-markdown ./rfcs/cache-invalidation.md --team LOA

# Create an ad-hoc issue
linear-remote issues create --title 'Redis timeout on prod' --team LOA --priority 2 --labels bug,prod

# Move an issue and wait for webhook-driven transition
linear-remote issues move LOA-229 'In Review' --wait --timeout 120

# Add a comment from a file
linear-remote comments add LOA-229 @./review-notes.md

# Project snapshot (state-bucketed + milestones + recent activity)
linear-remote projects overview 'Agent Plus' --pretty

# Enumerate states for a team (for --state resolution)
linear-remote states LOA
```

## Auth header quirk

Linear's personal API keys are sent as `Authorization: <key>` — **no `Bearer` prefix**. Other GraphQL APIs use `Bearer`; Linear does not. The plugin handles this centrally in `_gql()`. If you hit a 401, the first thing to check is whether you accidentally prefixed the key with `Bearer`.

## Pagination

List commands return `{nodes: [...], pageInfo: {hasNextPage, endCursor}}`. Pass `--cursor <endCursor>` for the next page. `--limit` default 25, max 100.

## What it doesn't do

- **Cycle / milestone CRUD** — read-only in `projects overview` and `cycles`.
- **Initiative CRUD**, team / workspace management, custom field mutations, webhook configuration.
- **Cross-issue relation mutations (`relate`)** — deferred to v2. Relations surface read-only in `issues get`.
- **OAuth** — personal API key only. Avoids the browser-auth wall.

Use the Linear UI, the MCP, or raw GraphQL for those. This plugin is read-first Linear work plus the common write surface.

## Gotchas the plugin collapses

- **Auth header has no `Bearer`.** Every other API wrapper in this repo uses `Bearer <token>`; Linear uses the bare key. Easy to miss.
- **HTML comments render as visible text in Linear.** `<!-- todo -->` in your markdown shows up as "<!-- todo -->" in the rendered issue. The plugin strips them in `_normalise_markdown()` before sending.
- **Issue IDs come in two flavours.** The human key `LOA-229` is what URLs and git branches expose; the UUID is what the API returns. Every command accepts either — you never copy UUIDs between invocations.
- **Mutations are instant; webhooks are not.** `issues move` returns the mutation ACK immediately. Use `--wait` only when Linear's GitHub integration is about to move the issue a second time (PR merge → auto-Done).
- **Name-resolution caches within one invocation.** `update + move` in a single CLI run doesn't re-query the state / label / user lists — `lru_cache` binds per-process.

## License

MIT. See repo root.
