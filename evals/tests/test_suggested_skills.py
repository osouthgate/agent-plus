"""Stack-marker → marketplace skill suggestions (`detect_suggested_skills` in agent-plus-meta).

README / CHANGELOG: deterministic filesystem markers suggest `*-remote` plugins (no LLM).
This is separate from `skill-plus scan` (session transcript mining) — see evals/README.md coverage table.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_pkg(root: Path, obj: dict) -> None:
    (root / "package.json").write_text(json.dumps(obj, indent=2), encoding="utf-8")


def test_suggested_skills_contract_keys(agent_plus_meta_bin_mod, tmp_path: Path) -> None:
    """Each suggestion includes name, marketplace, reason, install_hint (public contract)."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: [push]\njobs: {}\n", encoding="utf-8")
    fn = agent_plus_meta_bin_mod.detect_suggested_skills
    out = fn(tmp_path)
    assert isinstance(out, list) and len(out) >= 1
    g = next(x for x in out if x.get("name") == "github-remote")
    for k in ("name", "marketplace", "reason", "install_hint"):
        assert k in g and g[k], f"missing {k}: {g!r}"
    assert g["marketplace"] == "osouthgate/agent-plus-skills"
    assert "github-remote@agent-plus-skills" in g["install_hint"]


def test_suggested_skills_github_workflows(agent_plus_meta_bin_mod, tmp_path: Path) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "x.yml").write_text("on: workflow_dispatch\n", encoding="utf-8")
    names = [x["name"] for x in agent_plus_meta_bin_mod.detect_suggested_skills(tmp_path)]
    assert "github-remote" in names


def test_suggested_skills_openrouter_dep(agent_plus_meta_bin_mod, tmp_path: Path) -> None:
    _write_pkg(tmp_path, {"name": "x", "dependencies": {"openrouter": "1.0.0"}})
    names = [x["name"] for x in agent_plus_meta_bin_mod.detect_suggested_skills(tmp_path)]
    assert "openrouter-remote" in names


def test_suggested_skills_empty_repo(agent_plus_meta_bin_mod, tmp_path: Path) -> None:
    """No markers → no suggestions (silent per product behaviour).

    Pass env={} so the assertion is deterministic regardless of host env vars
    (e.g. OPENROUTER_API_KEY exported by the developer's shell).
    """
    assert agent_plus_meta_bin_mod.detect_suggested_skills(tmp_path, env={}) == []


def test_eval_fixture_rainshift_suggested_skills(agent_plus_meta_bin_mod, repo_root: Path) -> None:
    """Rainshift fixture skill suggestions depend on whether it's real or synthetic.

    Synthetic (fallback): Next.js only — no deployment/observability markers → no suggestions.
    Real (c:/dev/rainshift): has .vercel/, supabase/, .github/workflows/ → multiple suggestions.
    """
    p = repo_root / "evals" / "fixtures" / "rainshift"
    if not p.is_dir():
        pytest.skip(f"missing {p}")
    source_marker = p / ".fixture-source"
    is_real = source_marker.is_file() and source_marker.read_text().startswith("real:")

    suggestions = agent_plus_meta_bin_mod.detect_suggested_skills(p)
    names = [s["name"] for s in suggestions]

    if is_real:
        # Real rainshift project: expect at least vercel-remote + github-remote + supabase-remote.
        assert "vercel-remote" in names, f"expected vercel-remote in {names}"
        assert "github-remote" in names, f"expected github-remote in {names}"
        assert "supabase-remote" in names, f"expected supabase-remote in {names}"
    else:
        # Synthetic fallback: Next.js only, no markers → no suggestions.
        assert suggestions == [], f"synthetic rainshift should have no suggestions, got {names}"
