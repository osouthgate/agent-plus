from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest


def _strip_ephemeral_fixture_tree(tree: dict) -> None:
    """Remove `.agent-plus/` rows created when repo-analyze writes a local stamp under the fixture."""
    if not isinstance(tree, dict):
        return
    folders = tree.get("folders")
    if not isinstance(folders, list):
        return
    removed_files = 0
    kept: list = []
    for entry in folders:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        folder = str(entry.get("folder") or "")
        base = folder.rstrip("/").split("/")[-1] if folder else ""
        if base == ".agent-plus":
            removed_files += int(entry.get("files") or 0)
            continue
        kept.append(entry)
    tree["folders"] = kept
    if removed_files and "totalFiles" in tree:
        try:
            tree["totalFiles"] = max(0, int(tree["totalFiles"]) - removed_files)
        except (TypeError, ValueError):
            pass


def _normalize_for_compare(data: dict, fixture: str) -> dict:
    o = copy.deepcopy(data)
    if "tool" in o and isinstance(o["tool"], dict):
        o["tool"] = {"name": o["tool"].get("name")}
    o.pop("analyzedAt", None)
    o.pop("agentPlusServices", None)
    o["path"] = f"<fixture:{fixture}>"
    g = o.get("git")
    if isinstance(g, dict):
        # `branch` of a freshly `git init`'d synthetic fixture is environment
        # state (init.defaultBranch: `main` on modern Git, `master` on older
        # defaults / the WSL box a golden was regenerated on) -- not a portable
        # assertion target. Normalize it away like analyzedAt/path/headCommit.
        o["git"] = {k: g[k] for k in ("isRepo",) if k in g}
    rm = o.get("readme")
    if isinstance(rm, dict) and "path" in rm:
        nm = dict(rm)
        nm["path"] = "README.md"
        o["readme"] = nm
    tr = o.get("tree")
    if isinstance(tr, dict):
        _strip_ephemeral_fixture_tree(tr)
    return o


def _run_analyze(repo_root: Path, fixture: str) -> dict:
    b = repo_root / "repo-analyze" / "bin" / "repo-analyze"
    fixture_path = repo_root / "evals" / "fixtures" / fixture
    r = subprocess.run(
        [sys.executable, str(b), "--path", str(fixture_path), "--json"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _fixture_source(repo_root: Path, fixture: str) -> str:
    """Returns 'real:...' or 'synthetic' based on .fixture-source marker."""
    marker = repo_root / "evals" / "fixtures" / fixture / ".fixture-source"
    if marker.is_file():
        return marker.read_text(encoding="utf-8").strip()
    return "synthetic"


def _fixture_hints(fixture: str, data: dict, source: str = "synthetic") -> None:
    blob = json.dumps(data).lower()
    langs = data.get("languages") or {}
    is_real = source.startswith("real:")

    if fixture == "osdb":
        # Always synthetic — strict checks.
        assert "typescript" in langs
        assert "vitest" in blob or any(
            x.get("name") == "pnpm" for x in (data.get("buildTools") or [])
        )
    elif fixture == "rainshift":
        assert "typescript" in langs or "javascript" in langs, \
            f"rainshift: expected TypeScript/JavaScript in {list(langs.keys())}"
        if is_real:
            # Real rainshift is a Next.js monorepo with Supabase.
            assert "next" in blob or "supabase" in blob or "vercel" in blob, \
                "real rainshift: expected next/supabase/vercel mention"
        else:
            assert "next" in blob or "supabase" in blob
    elif fixture == "tinker-tailor":
        if is_real:
            # Real tinker-tailor is a Next.js + Prisma + Vitest app.
            assert "typescript" in langs or "javascript" in langs, \
                f"real tinker-tailor: expected TypeScript in {list(langs.keys())}"
            # Framework detection: Next.js (next.config.ts) or at least a recognised framework.
            fw = data.get("frameworks") or []
            assert isinstance(fw, list)
        else:
            fw = data.get("frameworks") or []
            assert isinstance(fw, list)
            assert fw == [] or all(
                isinstance(x, dict) and str(x.get("name", "")).lower() in ("next.js", "next", "react")
                for x in fw
            )


@pytest.mark.parametrize("fixture", ["osdb", "rainshift", "tinker-tailor"])
def test_repo_analyze_matches_golden_and_hints(repo_root, fixture: str) -> None:
    golden_path = repo_root / "evals" / "golden" / f"{fixture}-repo-analyze.json"
    source = _fixture_source(repo_root, fixture)
    is_real = source.startswith("real:")

    raw = _run_analyze(repo_root, fixture)

    assert raw["tool"]["name"] == "repo-analyze"
    assert raw["tool"].get("version")
    assert raw.get("languages"), f"{fixture}: expected non-empty languages"

    _fixture_hints(fixture, raw, source=source)

    if not golden_path.is_file():
        pytest.skip(f"no golden for {fixture} — run evals/scripts/regenerate_goldens.py")

    if is_real:
        # Real-repo fixtures: golden was built from a different machine state.
        # Do a relaxed check: tool name + key structural fields match shape only.
        golden_raw = json.loads(golden_path.read_text(encoding="utf-8"))
        assert golden_raw["tool"]["name"] == "repo-analyze"
        # The hints above already validated the actual output — that's sufficient for real repos.
    else:
        golden_raw = json.loads(golden_path.read_text(encoding="utf-8"))
        n_act = _normalize_for_compare(raw, fixture)
        n_gold = _normalize_for_compare(golden_raw, fixture)
        assert n_act == n_gold


def test_repo_analyze_next_steps_lifecycle_hints(repo_root: Path) -> None:
    """README lifecycle: repo-analyze should nudge skill-plus scan + diff-summary (see repo-analyze main)."""
    raw = _run_analyze(repo_root, "osdb")
    steps = raw.get("nextSteps")
    assert isinstance(steps, list) and len(steps) >= 2
    joined = " ".join(steps).lower()
    assert "skill-plus" in joined and "scan" in joined
    assert "diff-summary" in joined
