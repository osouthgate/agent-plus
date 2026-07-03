"""
Optional Ollama-backed smoke tests for future SKILL.md judge flows (Phase 4 prototype).

Requires a running Ollama daemon and (shell or ``evals/ollama.env``):
  OLLAMA_CHAT_MODEL=llama3.2   # or gemma2, etc.
  OLLAMA_BASE_URL=http://localhost:11434   # optional; default shown

Pytest loads ``evals/ollama.env`` if present (see ``evals/ollama.env.example``).
Unset OLLAMA_CHAT_MODEL to skip these tests (CI, laptops without Ollama).

These tests read framework SKILL.md as *documentation text* only. They do not change how
Claude Code invokes skills in production — CI never requires Ollama.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from evals.llm.ollama_chat import chat, ping

pytestmark = pytest.mark.ollama


def _skill_excerpt_for_json_probe(full: str) -> str:
    """Narrow window around `--json` so small local models are not buried in 4k tokens."""
    lines = full.splitlines()
    for i, line in enumerate(lines):
        if "--json" in line:
            start = max(0, i - 5)
            end = min(len(lines), i + 12)
            return "\n".join(lines[start:end])
    return "\n".join(lines[:120])


def _reply_contains_json_flag(reply: str) -> bool:
    """Accept minor formatting variance from small models (still must be clearly `--json`)."""
    t = reply.replace("`", "").replace("—", "-").replace("–", "-")
    compact = "".join(t.split()).lower()
    return "--json" in compact


def _ollama_config():
    model = (os.environ.get("OLLAMA_CHAT_MODEL") or "").strip()
    base = (os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434").strip()
    return base, model


@pytest.fixture(scope="module")
def ollama_ready():
    base, model = _ollama_config()
    if not model:
        pytest.skip("Set OLLAMA_CHAT_MODEL to exercise Ollama eval tests")
    if not ping(base):
        pytest.skip(f"Ollama not reachable at {base} (start daemon or fix OLLAMA_BASE_URL)")
    return base, model


def test_ollama_ping_and_echo(ollama_ready):
    base, model = ollama_ready
    text = chat(
        base,
        model,
        [{"role": "user", "content": "Reply with exactly the token: JUDGE_OK"}],
        timeout=120.0,
    )
    assert "JUDGE_OK" in text.upper(), f"unexpected assistant reply: {text[:500]!r}"


def test_ollama_extract_json_flag_from_skill(repo_root: Path, ollama_ready):
    """Judge-shaped extraction: model must read SKILL excerpt and name the JSON flag (not brittle PASS/FAIL)."""
    base, model = ollama_ready
    skill_path = repo_root / "repo-analyze" / "skills" / "repo-analyze" / "SKILL.md"
    assert skill_path.is_file(), f"missing {skill_path}"
    full = skill_path.read_text(encoding="utf-8", errors="replace")
    assert "--json" in full, "repo-analyze SKILL must mention --json for this eval to be valid"
    body = _skill_excerpt_for_json_probe(full)
    prompt = (
        "From the CLI documentation excerpt below, which single flag turns on JSON output? "
        "Answer with that flag only, like --json (two dashes then json). One line.\n\n---\n"
        f"{body}\n---"
    )
    text = chat(base, model, [{"role": "user", "content": prompt}], timeout=180.0)
    assert _reply_contains_json_flag(text), f"expected model to name --json, got: {text[:800]!r}"
