"""Validation tests for the agent-plus-installer SKILL.md.

Stdlib unittest only — no pytest, no third-party YAML. Verifies:

  1. Frontmatter shape — name/description/when_to_use/allowed-tools keys
     exist and are non-empty.
  2. Body sections — the four canonical h2 headers are present.
  3. allowed-tools matches the locked v0.13.0 contract exactly.
  4. Killer command is a single curl ... | sh -s -- --unattended invocation
     pointing at the canonical install.sh URL on github.com/osouthgate/agent-plus.

Run with:
    python3 -m unittest agent-plus-meta/test/test_installer_skill.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_PATH = REPO_ROOT / "agent-plus-meta" / "skills" / "agent-plus-installer" / "SKILL.md"


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
        # New top-level key only if the line has no leading whitespace AND
        # contains a colon before any space.
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


class TestInstallerSkill(unittest.TestCase):
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
        self.assertEqual(keys["name"], "agent-plus-installer")

    def test_body_has_canonical_h2_sections(self) -> None:
        _fm, body = _split_frontmatter(_read_skill())
        h2s = re.findall(r"(?m)^## (.+)$", body)
        for required in (
            "Killer command",
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
        # Locked in v0.13.0 plan §Q4 — install.sh re-run lives in a separate
        # upgrade skill (v0.13.5), so it is intentionally absent here.
        pattern = r"^Bash\(curl:\*\)\s+Bash\(sh:\*\)\s+Bash\(agent-plus-meta:\*\)$"
        self.assertRegex(keys["allowed-tools"], pattern)

    def test_killer_command_shape(self) -> None:
        _fm, body = _split_frontmatter(_read_skill())
        # Single curl ... | sh -s -- --unattended invocation pointing at the
        # canonical install.sh on github.com/osouthgate/agent-plus.
        pattern = re.compile(
            r"curl\s+-fsSL\s+"
            r"https://raw\.githubusercontent\.com/osouthgate/agent-plus/main/install\.sh"
            r"\s*\|\s*sh\s+-s\s+--\s+--unattended"
        )
        self.assertRegex(body, pattern)


if __name__ == "__main__":
    unittest.main()
