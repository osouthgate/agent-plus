"""Validation tests for the agent-plus-upgrade SKILL.md.

Stdlib unittest only — no pytest, no third-party YAML. Verifies:

  1. Frontmatter shape — name/description/when_to_use/allowed-tools keys
     exist and are non-empty.
  2. Body sections — the five canonical h2 headers are present.
  3. allowed-tools matches the locked contract exactly.
  4. The probe and upgrade commands appear verbatim, with the four
     `--user-choice` values the CLI's own `_prompt_choice()` defines.
  5. The gating fields (verdict / config.update_check / snooze.active)
     that decide whether to surface an offer are all documented.

Run with:
    python3 -m unittest agent-plus-meta/test/test_upgrade_skill.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_PATH = REPO_ROOT / "agent-plus-meta" / "skills" / "agent-plus-upgrade" / "SKILL.md"


def _read_skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_block, body). Stdlib-only mini parser."""
    if not text.startswith("---\n"):
        raise AssertionError("SKILL.md does not start with '---' frontmatter fence")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise AssertionError("SKILL.md frontmatter is not closed by '---' fence")
    return text[4:end], text[end + 5:]


def _parse_frontmatter_keys(block: str) -> dict[str, str]:
    """Extract top-level `key: value` pairs. Multiline `|` blocks are folded
    into a single string keyed by the first line. Good enough for the four
    keys this skill ships with — no nested mappings, no flow lists."""
    out: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    for line in block.splitlines():
        if not line:
            if current_key is not None:
                current_lines.append("")
            continue
        if not line.startswith((" ", "\t")) and ":" in line:
            if current_key is not None:
                out[current_key] = "\n".join(current_lines).strip()
            key, _, val = line.partition(":")
            current_key = key.strip()
            val = val.strip()
            if val == "|":
                current_lines = []
            else:
                current_lines = [val]
        else:
            current_lines.append(line.strip())
    if current_key is not None:
        out[current_key] = "\n".join(current_lines).strip()
    return out


class TestUpgradeSkill(unittest.TestCase):
    def test_skill_file_exists(self) -> None:
        self.assertTrue(SKILL_PATH.is_file(), f"missing {SKILL_PATH}")

    def test_frontmatter_required_keys_present(self) -> None:
        fm, _body = _split_frontmatter(_read_skill())
        keys = _parse_frontmatter_keys(fm)
        for required in ("name", "description", "when_to_use", "allowed-tools"):
            self.assertIn(required, keys, f"missing key {required!r} in frontmatter")
            self.assertTrue(
                keys[required].strip(),
                f"frontmatter key {required!r} is empty",
            )
        self.assertEqual(keys["name"], "agent-plus-upgrade")

    def test_body_has_canonical_h2_sections(self) -> None:
        _fm, body = _split_frontmatter(_read_skill())
        h2s = re.findall(r"(?m)^## (.+)$", body)
        for required in (
            "The probe",
            "Offering the upgrade",
            "Do NOT use this for",
            "Safety rules",
            "Architecture",
        ):
            self.assertIn(
                required, h2s,
                f"missing canonical h2 section {required!r}; found: {h2s!r}",
            )

    def test_allowed_tools_matches_locked_contract(self) -> None:
        fm, _body = _split_frontmatter(_read_skill())
        keys = _parse_frontmatter_keys(fm)
        self.assertEqual(keys["allowed-tools"], "Bash(agent-plus-meta:*)")

    def test_probe_command_present(self) -> None:
        _fm, body = _split_frontmatter(_read_skill())
        self.assertIn("agent-plus-meta upgrade-check", body)

    def test_upgrade_command_covers_all_four_choices(self) -> None:
        # Pin against the literal --user-choice <...> invocation itself, not
        # bare substrings anywhere in the doc: "always"/"never"/etc. also
        # occur repeatedly in ordinary prose (e.g. "never raises, never
        # blocks"), so a regression that drops or mistypes a choice on the
        # actual command line would still pass a bare assertIn check.
        _fm, body = _split_frontmatter(_read_skill())
        match = re.search(r"agent-plus-meta upgrade --user-choice <([a-z|]+)>", body)
        self.assertIsNotNone(
            match, "missing the literal 'agent-plus-meta upgrade --user-choice <...>' invocation"
        )
        self.assertEqual(set(match.group(1).split("|")), {"yes", "always", "snooze", "never"})

    def test_gating_fields_documented(self) -> None:
        _fm, body = _split_frontmatter(_read_skill())
        for field in ("verdict", "config.update_check", "snooze.active"):
            self.assertIn(
                field, body,
                f"missing gating field {field!r} — offer-suppression logic must "
                "reference it explicitly",
            )

    def test_silent_upgrade_branch_is_internally_consistent(self) -> None:
        """Regression guard for a real contradiction caught during review:
        Safety rule 4 used to guard an 'Always' silent-patch path that no
        other section actually implemented (every branch drove an explicit
        --user-choice, bypassing the CLI's own silent_upgrade/--auto logic
        entirely). The skip-the-prompt branch must be documented in the
        Offering section, not just asserted in the safety rules."""
        _fm, body = _split_frontmatter(_read_skill())
        self.assertIn("config.silent_upgrade", body)
        self.assertIn("--non-interactive --auto", body)
        offering_idx = body.index("## Offering the upgrade")
        do_not_idx = body.index("## Do NOT use this for")
        offering_section = body[offering_idx:do_not_idx]
        self.assertIn(
            "config.silent_upgrade", offering_section,
            "the silent_upgrade branch must be decided in 'Offering the "
            "upgrade', not only mentioned in the safety rules",
        )

    def test_when_to_use_has_explicit_and_ambient_paths(self) -> None:
        """Regression guard: ambient-only routing is fuzzy. An explicit user
        request ("is agent-plus up to date?") must always re-probe regardless
        of the once-per-session cap — don't let a future edit collapse the
        two paths back into ambient-only triggering."""
        fm, _body = _split_frontmatter(_read_skill())
        keys = _parse_frontmatter_keys(fm)
        when_to_use = keys["when_to_use"]
        self.assertIn("Explicit", when_to_use)
        self.assertIn("Ambient", when_to_use)
        self.assertIn(
            "is agent-plus up to date", when_to_use,
            "missing a concrete explicit-request trigger phrase",
        )
        self.assertIn(
            "exempt from this cap", when_to_use,
            "explicit path must be documented as bypassing AGENT_PLUS_UPGRADE_CHECKED",
        )

    def test_session_scope_flag_named_consistently(self) -> None:
        fm, body = _split_frontmatter(_read_skill())
        self.assertIn("AGENT_PLUS_UPGRADE_CHECKED", fm)
        self.assertIn("AGENT_PLUS_UPGRADE_CHECKED", body)


if __name__ == "__main__":
    unittest.main()
