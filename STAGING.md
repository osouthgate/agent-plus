# STAGING

Pre-release notes for the next version cut. Each entry collapses into a
CHANGELOG line at ship time.

## v0.17.0 (UNRELEASED — staged)

skill-plus inquire — capability + usage auditor

- Transcripts as a first-class source class (auto-discovery from
  `~/.claude/projects/`, `~/.gstack/projects/`, `~/.codex/sessions/`,
  `~/.cursor/chats/`; user-extensible via
  `~/.agent-plus/inquire-sources.json` + `~/.agent-plus/inquire-adapters/`)
- Two-tier clustering: Tier 1 = (verb, table-set), Tier 2 =
  (select-cols, where-cols). Generic SQL-grammar normalisation only —
  no per-tool regex
- Type A/B/C promotion classification: Missing / Misaligned / Aligned
  against the plugin or skill's existing subcommands
- Friction-ranked priority: capability gap + heavy usage = high; light
  usage = low; aligned clusters = n/a
- Skill-as-target support: SKILL.md frontmatter parsed alongside
  `plugin.json`; subcommand bin auto-resolved from `Bash(<name>:*)`
  allowed-tools entry
- ENVELOPE_VERSION 1.1 (additive only — v1.0 consumers unaffected).
  New fields: `usage_signal`, `usage_clusters`, `promotions`, optional
  per-Q `usage_evidence`/`promotion_kind`/`priority`
- New verdict state: `well_used` (no capability gaps + canned commands
  actually being used per transcript signal)
- New CLI flag: `--no-transcripts` (also env
  `AGENT_PLUS_INQUIRE_NO_TRANSCRIPTS=1`)
- 67 new tests across the inquire surface (clustering, transcripts,
  envelope shape, priority calc, skill-kind detection)
- Real-data dogfood: 13 Tier 1 shapes discovered against the user's
  own transcripts (27,026 tuples across 238 jsonl files), including
  all 6 SQL shapes documented in the delta plan plus 7 surprises
  (anti-confirmation discipline holds — algorithm finds usage we
  didn't predict)

Demos:

- Primary (transcripts branch): cluster reference fixture at
  `skill-plus/test/fixtures/loamdb_db_clusters_reference.json`
  (12 KB, structured cluster output only — no raw command strings).
  Paste-ready PR notes at `loamdb_db_pr_draft.md` (3 high-priority
  new subcommands: `describe`, `connector`, `chunks`; 3 medium
  follow-ups). Full audit envelopes are not committed to the repo
  because the raw transcript tuples they contain can carry secrets
  (API keys, DSNs, bearer tokens). Delta plan predictions held:
  describe / connector / chunks all hit; Type B/C absent because
  A.2 discovery doesn't introspect argparse subcommands inside
  single-file CLIs (noted as follow-up)
