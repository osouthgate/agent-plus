---
name: repo-analyze
description: One-call cold-start orientation for an unfamiliar repo. Returns a structured map — file tree (capped), language mix, framework + build-tool detection, top-level deps, entrypoints, README highlights — so you don't burn 60+ Read/Glob/Grep calls re-mining the same facts every fresh session. Stateless, no network, stdlib Python only.
when_to_use: Trigger on phrases like "what is this project", "tell me about this repo", "give me an overview", "what's the tech stack", "where do I start in this repo", "scan the codebase", "what does this codebase use", "list the entrypoints". This is the first call in any new repo. Use ONCE per session, then drop to Read/Grep for the actual file contents.
allowed-tools: Bash(repo-analyze:*) Bash(python3 *repo-analyze*:*)
---

# repo-analyze

The map, not the contents. One call replaces the cold-start dance — file-tree exploration, finding entrypoints, reading every manifest, scanning README. Session mining showed ~67 grep + ~60 ls ops on a typical fresh-repo session, almost all of it answerable from one structured payload.

Lives at `${CLAUDE_SKILL_DIR}/../../bin/repo-analyze`; the plugin auto-adds `bin/` to PATH, so just run `repo-analyze ...`.

## When to reach for this

- User asks **"what is this project / what's the tech stack / give me an overview"** → run `repo-analyze --pretty`. One call → languages, frameworks, deps, entrypoints, README highlights.
- User asks **"where do I start"** → run `repo-analyze --pretty` and read the `entrypoints` + `readme` sections.
- User asks **"is this a Next.js / FastAPI / Rust project"** → run `repo-analyze` and inspect `frameworks[]`. Each entry has a `confidence` field (`high` = manifest-confirmed, `medium` = config file present but not in manifest).
- Cold-starting in any unfamiliar checkout → run this BEFORE the first Read/Glob/Grep.

## Headline command

```bash
repo-analyze [--path PATH]
             [--max-tree-files INT]    # default 200
             [--max-tree-depth INT]    # default 4
             [--no-readme]             # skip README highlights
             [--output PATH]           # offload to disk
             [--shape-depth 1|2|3]     # envelope detail (default 3)
             [--pretty | --json]
             [--version]
```

Default behaviour: analyze cwd, return one JSON blob.

## Output shape (truncated)

```json
{
  "tool": {"name": "repo-analyze", "version": "0.1.0"},
  "path": "/abs/path",
  "git": {"isRepo": true, "branch": "main", "remote": "...", "headCommit": "abc1234"},
  "languages": {"python": {"files": 42, "loc": 12345, "percent": 38.2}, ...},
  "frameworks": [{"name": "Next.js", "evidence": "package.json:next", "confidence": "high"}],
  "buildTools": [{"name": "pnpm", "evidence": "pnpm-lock.yaml"}],
  "deps": {"node": {...}, "python": {...}, "rust": null, "go": null, "ruby": null},
  "entrypoints": [{"path": "src/app/page.tsx", "kind": "next-page"}],
  "tree": {"totalFiles": 1234, "shown": 200, "truncated": true, "entries": [...]},
  "readme": {"title": "...", "firstParagraph": "...", "headings": [...]},
  "agentPlusServices": {"services": {"github-remote": {"status": "ok"}, ...}}
}
```

When `.agent-plus/services.json` exists in the analyzed path or an ancestor, `agentPlusServices` is populated with names + statuses (no IDs, no values). Otherwise it's `null`.

## Offloading large responses

For monorepos or anything over ~50KB of output, use `--output PATH`:

```bash
repo-analyze --output /tmp/analyze.json --shape-depth 3
```

The full payload lands on disk. Stdout returns a compact envelope: `payloadPath`, `bytes`, `payloadKeys`, `payloadShape`. `payloadShape` is recursive (depth 3 by default) so you can see `tree.entries.length` and `languages.python.loc` without opening the file.

## Stay in your lane

- **Reading file contents** → use the Read tool. `repo-analyze` is the map; Read is the territory.
- **Running tests / build commands** → that's the harness, not this CLI.
- **Symbol indexing / "where is `myFunction` defined"** → not this tool. (Future `dep-graph` plugin handles imports.)
- **Diff-related analysis ("what changed in this PR")** → not this tool. (Future `diff-summary` plugin handles diffs.)
- **Looking up package metadata from npmjs.org / pypi** → no network calls; this is local-file analysis only.

## Scope

- Pure local-file analysis. No network. No state.
- One call per session is normally enough. If the tree is truncated and you actually need the rest, raise `--max-tree-files` or use Glob for a targeted slice — don't loop on `repo-analyze` itself.
- Framework + build-tool detection is heuristic — `confidence: "high"` means a manifest confirmed it, `"medium"` means a config file was found without the manifest dep. Treat `low` as a hint.
- Detected languages cover ~30 common extensions. Missing language → file ignored from `languages`/LOC, still listed in `tree`.
