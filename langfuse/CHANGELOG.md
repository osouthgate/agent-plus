# langfuse — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.1.0

Initial release.

### Added
- `export-prompts` / `import-prompts` — dump and restore all prompts with all versions, labels, tags. Preserves version identity for backup and cross-environment migration.
- `migrate-prompts --from <instance> --to <instance>` — one-shot cross-instance copy.
- `trace-ping` — send a smoke-test trace, print the trace URL.
- `health [--all]` — GET `/api/public/health` on the active or all configured instances.
- `list-instances` / `show-instance` — resolve multi-instance config.
- Named instances via env prefixes (`LANGFUSE_<NAME>_BASE_URL`, …) or JSON config at `$LANGFUSE_CONFIG`.
- `.env` autoloading, walking up from cwd, loading `LANGFUSE_*` keys without overwriting shell env. (Note: precedence here is shell-wins, unlike the newer hermes/coolify/hcloud plugins which are project-wins — kept as-is to avoid breaking existing workflows.)
