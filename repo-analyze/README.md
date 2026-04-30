# repo-analyze

Cold-starting in an unfamiliar repo, every agent session re-mines the same facts. File-tree exploration. Finding entrypoints. Reading `package.json` / `pyproject.toml` / `Cargo.toml` / `go.mod`. Scanning the README. Detecting whether this is a Next.js app or a Django service.

Session mining across real Claude Code transcripts showed **~67 grep + ~60 directory-discovery ops** per cold-start, almost all of it recovering facts a previous session already recovered, none of it cached anywhere the next session can see.

`repo-analyze` collapses that into one call. One structured JSON blob: file tree (capped), language mix, framework + build-tool detection, top-level deps, entrypoints, README highlights. Stdlib Python only. No network. Stateless.

## Headline command

```bash
repo-analyze [--path PATH]
             [--max-tree-files 200]    # tree cap
             [--max-tree-depth 4]      # depth cap
             [--no-readme]             # skip README highlights
             [--output PATH]           # offload to disk for large responses
             [--shape-depth 1|2|3]     # envelope detail (default 3)
             [--pretty | --json]
             [--version]
```

Default behaviour: analyze cwd, emit JSON to stdout.

## Worked example

```bash
$ repo-analyze --pretty | head -40
{
  "tool": {"name": "repo-analyze", "version": "0.1.0"},
  "path": "/Users/me/checkout",
  "analyzedAt": "2026-04-28T12:34:56Z",
  "git": {"isRepo": true, "branch": "main", "remote": "https://github.com/me/repo", "headCommit": "abc1234"},
  "languages": {
    "typescript": {"files": 142, "loc": 18203, "percent": 71.4},
    "python":     {"files":  18, "loc":  3421, "percent": 13.4},
    "css":        {"files":  12, "loc":  1822, "percent":  7.1}
  },
  "frameworks": [
    {"name": "Next.js",      "evidence": "package.json:next",   "confidence": "high"},
    {"name": "React",        "evidence": "package.json:react",  "confidence": "high"},
    {"name": "TailwindCSS",  "evidence": "package.json:tailwindcss", "confidence": "high"}
  ],
  "buildTools": [
    {"name": "pnpm",   "evidence": "pnpm-lock.yaml"},
    {"name": "Docker", "evidence": "Dockerfile"}
  ],
  "deps": {
    "node":   {"runtime": ["next", "react", "..."], "dev": ["typescript", "..."], "truncated": false},
    "python": {"runtime": ["fastapi", "..."], "dev": [], "truncated": false},
    ...
```

## What it covers

- **Languages.** ~30 common extensions (Python, JS/TS, Rust, Go, Java, Kotlin, Ruby, PHP, C/C++, C#, Swift, Scala, Clojure, Elixir, Erlang, Dart, Lua, R, Shell, PowerShell, SQL, HTML, CSS/SCSS, Vue, Svelte, Markdown, YAML, TOML, JSON, Terraform). Files counted, LOC summed, percent computed.
- **Frameworks.** Manifest-confirmed (`high`): Next.js, React, Vue, Svelte, SvelteKit, Nuxt, Express, Fastify, Hono, TailwindCSS, Vite, Astro, Remix; FastAPI, Flask, Django, Starlette, Tornado, Pydantic, SQLAlchemy, pytest, Celery; Actix Web, Axum, Rocket, Warp, Tokio, Serde; Gin, Gorilla Mux, Echo, Fiber. Config-file-only (`medium`): TailwindCSS via `tailwind.config.*` without the dep. Each entry carries `evidence` so you can verify.
- **Build tools.** pnpm, yarn, npm, bun, cargo, go-modules, poetry, uv, pipenv, bundler, composer, Docker, docker-compose, Make, CMake, Gradle, Maven, Bazel, Turborepo, Nx — each detected via lock-file or canonical config.
- **Deps.** Top-level only, capped at 50 per list (with `truncated: true`). Sources: `package.json`, `requirements.txt`, `pyproject.toml` (`[project.dependencies]` + `[tool.poetry.dependencies]` + groups), `Cargo.toml`, `go.mod` (require blocks), `Gemfile`. Whitelist-only on `package.json` — unknown fields like `npm-publish-token` never make it into the output.
- **Entrypoints.** `package.json`'s `main` / `bin`, `pyproject.toml`'s `[project.scripts]`, `Cargo.toml`'s `[[bin]]`, `cmd/*/main.go`, plus well-known files (`src/index.{ts,tsx,js,jsx}`, `src/main.{ts,tsx,py,rs,go}`, `app/page.tsx`, `pages/_app.tsx`, `manage.py`, `wsgi.py`, `asgi.py`, `app.py`).
- **Tree.** Breadth-first, depth-capped (default 4), file-capped (default 200). Skips the boring suspects (`.git`, `node_modules`, `__pycache__`, `.venv`, `dist`, `build`, `target`, `.next`, `.turbo`, `out`, `.cache`, `vendor`, etc.). LOC included for source files, omitted for binary or > 1 MB.
- **README.** First H1 → `title`. Text under H1 until next blank line / heading → `firstParagraph` (capped at 800 chars). All H2 headings → `headings` (capped at 20). No code blocks, no full sections — agents that want them call Read.
- **Agent-plus enrichment.** If `.agent-plus/services.json` exists in the analyzed path or any ancestor, `agentPlusServices` is populated with **service names + statuses only** (Pattern 5 — no IDs, no project lists). Otherwise `null`.

## What it doesn't do

- **No file content reading.** This is the map, not the territory. Use Read for actual content.
- **No symbol resolution / ctags / "where is `myFunction` defined".** Different tool.
- **No diff analysis.** Different tool.
- **No network calls.** No fetching package metadata from npmjs.org or pypi. Local-file only.
- **No state.** No cache, no `--refresh`. Stateless command — every call re-analyzes.
- **No env-file parsing.** Out of scope; agent-plus already handles envcheck.

## Performance

Default caps target <2s on a typical 500-file repo. Skip-list is non-negotiable — if you're seeing slow runs, the cap is wrong, not the skip-list. Internal BFS uses `collections.deque` so traversal cost stays linear in node count regardless of cap depth.

## Offloading large responses

For monorepos or any output over ~50KB:

```bash
repo-analyze --output /tmp/analyze.json --shape-depth 3
```

Full payload to disk. Stdout returns the standard envelope: `payloadPath`, `bytes`, `payloadKeys`, `payloadShape`. `payloadShape` is the recursive type+size descriptor — at depth 3 you see e.g. `languages.python.loc` directly without opening the file.

## Install

### Marketplace

```bash
claude plugin install repo-analyze@agent-plus
```

### Standalone

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/repo-analyze/bin/repo-analyze
chmod +x repo-analyze
./repo-analyze --pretty
```

Stdlib Python 3.11+ (uses `tomllib` from stdlib). No pip installs.

## Tests

```bash
python3 -m pytest repo-analyze/test/ -v
```
