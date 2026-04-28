# repo-analyze — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.1.0 - 2026-04-28

Initial release. Replaces the cold-start orientation dance (~67 grep + ~60 ls ops, mined from real session transcripts) with one structured JSON call.

### Added
- `repo-analyze` — single-command CLI. Default behaviour: analyze cwd, emit JSON. Flags: `--path`, `--max-tree-files` (default 200), `--max-tree-depth` (default 4), `--include-readme`/`--no-readme`, `--output PATH`, `--shape-depth 1|2|3`, `--pretty`/`--json`, `--version`.
- **Languages.** Extension-based detection across ~30 common languages (Python, JS/TS, Rust, Go, Java, Kotlin, Ruby, PHP, C/C++, C#, Swift, Scala, Clojure, Elixir, Erlang, Dart, Lua, R, Shell, PowerShell, SQL, HTML, CSS, Vue, Svelte, Markdown, YAML, TOML, JSON, Terraform). LOC summed via line count; binary files (NUL byte in first 8 KB) and files > 1 MB skipped.
- **Frameworks.** Heuristic detection from manifest contents AND well-known config files. Confidence levels: `high` (manifest-confirmed), `medium` (config file present, no manifest dep), `low` (filename pattern only). Covers Next.js, React, Vue, Svelte, SvelteKit, Nuxt, Express, Fastify, Hono, TailwindCSS, Vite, Astro, Remix, FastAPI, Flask, Django, Starlette, Tornado, Pydantic, SQLAlchemy, pytest, Celery, Actix Web, Axum, Rocket, Warp, Tokio, Serde, Gin, Gorilla Mux, Echo, Fiber.
- **Build tools.** pnpm, yarn, npm, bun, cargo, go-modules, poetry, uv, pipenv, bundler, composer, Docker, docker-compose, Make, CMake, Gradle, Maven, Bazel, Turborepo, Nx — detected via lock-file or canonical config presence.
- **Deps.** Top-level deps from `package.json` (whitelist-only), `requirements.txt`, `pyproject.toml` (`[project.dependencies]` + `[tool.poetry.dependencies]` + groups), `Cargo.toml`, `go.mod` (require blocks), `Gemfile`. Capped at 50 entries per list with `truncated: true`.
- **Entrypoints.** `package.json` `main`/`bin`, `pyproject.toml` `[project.scripts]`, `Cargo.toml` `[[bin]]`, `cmd/*/main.go`, plus well-known files (`src/index.{ts,tsx,js,jsx}`, `src/main.{ts,tsx,py,rs,go}`, `app/page.tsx`, `src/app/page.tsx`, `pages/_app.tsx`, `manage.py`, `wsgi.py`, `asgi.py`, `app.py`).
- **Tree.** Capped breadth-first traversal (default depth 4, max 200 entries). Sorts directories first, alphabetical per parent. LOC included for source files. Skip-list covers `.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `.pytest_cache`, `dist`, `build`, `target`, `.next`, `.turbo`, `out`, `.cache`, `.parcel-cache`, `.nuxt`, `.svelte-kit`, `.idea`, `.vscode`, `.gradle`, `.terraform`, `coverage`, `.nyc_output`, `htmlcov`, `vendor`, `Pods`.
- **README highlights.** Title (first H1), first paragraph (capped at 800 chars), all H2 headings (capped at 20). Searches `README.md`, `README.rst`, `README.txt`, `README` in order. No code blocks, no full sections.
- **Agent-plus enrichment.** Walks up from `--path` looking for `.agent-plus/services.json`. When present, populates `agentPlusServices.services` with names + statuses only — never IDs, never project lists, never values (Pattern 5).
- **Envelope contract.** Top-level `tool.{name, version}` injected on every payload. `--output PATH` writes the full JSON to disk and returns a compact envelope (`savedTo`, `bytes`, `fileLineCount`, `payloadKeys`, `payloadShape`). `--shape-depth 1|2|3` controls recursion depth on `payloadShape` (default 3 — surfaces nested-list-of-dicts patterns directly in the envelope). Matches the `railway-ops` / `vercel-remote` / `langfuse-remote` shape exactly.
- **Pattern 5 canary.** `package.json` parsing is whitelist-only — unknown keys like `npm-publish-token`, `publishConfig`, `npmRegistry` never appear in output. Tested.
- **39 unit tests** covering envelope contract, language detection, framework detection, build-tool detection, dep parsing, entrypoint discovery, tree caps, README extraction, `--output` offload, Pattern 5 no-leakage, and agent-plus enrichment.

### Deliberately out of scope
- No symbol indexing / ctags-like resolution. Future `dep-graph` plugin handles imports and module dependencies.
- No diff analysis. Future `diff-summary` plugin handles before/after comparisons.
- No network calls. No fetching package metadata from npmjs.org or pypi.
- No state. Stateless command — every call re-analyzes from scratch.
- No env-file parsing. `agent-plus envcheck` already handles env-var status.
- Single command (no subcommands). If a `tree`/`deps`-only mode becomes useful, it can be added without breaking the default.
