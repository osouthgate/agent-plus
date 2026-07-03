# TODOs

Deferred items that don't fit active plans but must not be forgotten.

Closed 2026-07-03 (launch-gate sweep, `feat/launch-gates-cross-platform`):
hook content-hash check (already implemented in `_install_suggest_hook` +
covered by tests — the TODO predated the implementation), Windows
`Path(git output)` audit (all five remaining raw callsites wrapped in
`_msys_to_windows()`: agent-plus-meta bin, skill-plus bin, scaffold module +
generated-skill template; repo-analyze/skill-feedback/init.py were already
fixed), marketplaces/ path verification (confirmed global at
`~/.agent-plus/marketplaces/` with `AGENT_PLUS_MARKETPLACES_ROOT` override;
covered by three test files), Windows + macOS CI runners (3-OS x 2-Python
matrix + root `test/` + `evals/tests` + mypy job), bootstrap_fixtures secret
scrub (full canonical `_SECRET_PATTERNS` pass before fixture git history is
created), `--json` dest mismatch (all three subparsers share
`dest="force_json"` with `default=argparse.SUPPRESS`; uninstall scope prompt
honors script mode; purge refuses cleanly off-TTY).

---

## Generated-skill template scrubber is missing 7 secret patterns (small, security)

**What:** `skill-plus/bin/_subcommands/scaffold.py`'s `_GENERATED_PY_TEMPLATE`
carries its own copy of `_SECRET_PATTERNS` that has drifted from the canonical
list in `skill-plus/bin/skill-plus`. Missing: `gh[ousr]_`, `pk-lf-`, Slack
webhook URLs, Discord webhook URLs, Stripe `(sk|rk|pk)_(live|test)_`, `sbp_`,
`sntrys_`. (`sk-lf-` is incidentally caught by the template's generic `sk-`
pattern; `pk-lf-` is not.)

**Why:** Skills scaffolded today ship with the weaker redactor and keep it
forever — the gap compounds in end-user repos, not ours. Found 2026-07-03
while duplicating the canonical list into `bootstrap_fixtures.py`.

**How to apply:** Sync the template's list with the canonical one; add a test
that renders the template and asserts pattern-list parity against
`bin/skill-plus` (string-compare the pattern sources so future drift fails
loudly). Longer term: centralize generation of the list at scaffold time
instead of a frozen literal.

---

## `--pretty` before a subcommand is clobbered on CPython 3.12 (small)

**What:** Same argparse mechanics as the fixed `--json` bug: subparsers parse
into a fresh namespace and copy all attrs back, so a subparser's own
`--pretty` default overwrites a parent-parsed `--pretty` when the flag is
given BEFORE the subcommand.

**Why:** `agent-plus-meta --pretty <subcommand>` silently loses the flag on
3.12+. Low blast radius (pretty-printing only) but same latent-footgun class
as the `--json` defect fixed 2026-07-03.

**How to apply:** Give every subparser `--pretty` `default=argparse.SUPPRESS`
(mirror the `--json` fix and its load-bearing comment), plus a
both-positions regression test per subcommand (extend
`TestJsonFlagPositions`'s pattern).
