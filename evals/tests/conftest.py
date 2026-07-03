from __future__ import annotations

import importlib.util
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from evals.load_eval_env import load_ollama_env


@pytest.fixture(scope="session", autouse=True)
def _apply_eval_ollama_env(repo_root: Path) -> None:
    load_ollama_env(repo_root)


@pytest.fixture(scope="session", autouse=True)
def ensure_minimal_git_fixtures(repo_root: Path) -> None:
    fix = repo_root / "evals" / "fixtures"
    names = ("osdb", "rainshift", "tinker-tailor")
    if all((fix / n / ".git").is_dir() for n in names):
        return
    script = repo_root / "evals" / "scripts" / "bootstrap_fixtures.py"
    subprocess.run([sys.executable, str(script)], cwd=str(repo_root), check=True)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    p = Path(__file__).resolve().parent.parent.parent
    assert (p / "VERSION").is_file(), f"expected VERSION at {p}"
    return p


@pytest.fixture(scope="session")
def agent_plus_meta_bin_mod(repo_root: Path):
    """Load `agent-plus-meta` bin once (mirrors `agent-plus-meta/test/test_agent_plus._load_module`)."""
    path = repo_root / "agent-plus-meta" / "bin" / "agent-plus-meta"
    assert path.is_file(), path
    loader = SourceFileLoader("_eval_agent_plus_meta", str(path))
    spec = importlib.util.spec_from_loader("_eval_agent_plus_meta", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def plugin_bin(repo_root: Path):
    def _bin(name: str) -> Path:
        path = repo_root / name / "bin" / name
        assert path.is_file(), f"missing {path}"
        return path

    return _bin
