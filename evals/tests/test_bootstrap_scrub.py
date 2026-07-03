"""bootstrap_fixtures.py must scrub secret-shaped strings during the copy.

2026-07-03 security launch gate: an adversarial review found a real Langfuse
sk-lf-/pk-lf- key pair materialized into evals/fixtures/rainshift/ by the
verbatim real-repo copy, re-leaking on every re-bootstrap. These tests seed a
FAKE Langfuse-shaped pair into a temp source tree and prove the scrub rewrites
text files, leaves binary files byte-identical, preserves LF line endings, and
runs BEFORE the fixture's git history is created (so scrubbed content is what
gets committed). No network; no dependence on real local repos.

The fake tokens are built from concatenated parts so this file never contains
a contiguous token-shaped literal (mirrors skill-feedback's canary-test
convention -- keeps push-protection / secret-scan hooks quiet while the
runtime value exercises the real regexes).
"""

from __future__ import annotations

import importlib.util
import subprocess
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

# Concatenated at runtime -> identical to a single literal on the wire.
FAKE_SK = "sk-lf-" + "0" * 8 + "-" + "a" * 16
FAKE_PK = "pk-lf-" + "1" * 8 + "-" + "b" * 16
# The exact whole-token replacement scrub_text emits for these patterns.
REDACTED = "[REDACTED]"

# Not valid utf-8 (0x80-0xFF standalone bytes) -> must be skipped as binary.
BINARY_BLOB = b"\x89PNG\r\n\x1a\n" + bytes(range(256))


@pytest.fixture()
def bootstrap_mod(repo_root: Path):
    """Load evals/scripts/bootstrap_fixtures.py fresh per test (it is a
    script without a package, so SourceFileLoader like the other bin loads)."""
    script = repo_root / "evals" / "scripts" / "bootstrap_fixtures.py"
    assert script.is_file(), script
    loader = SourceFileLoader("_bootstrap_fixtures_under_test", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _seed_tree(root: Path) -> tuple[Path, Path, Path]:
    """Write a small source tree: one secret-bearing text file, one clean
    text file, one binary file. Returns (root, secret_file, binary_file)."""
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    secret_file = docs / "observability.md"
    secret_file.write_bytes(
        (
            "# plan\n"
            f'LANGFUSE_PUBLIC_KEY="{FAKE_PK}"\n'
            f'LANGFUSE_SECRET_KEY="{FAKE_SK}"\n'
            "LANGFUSE_BASE_URL=https://cloud.langfuse.com\n"
        ).encode("utf-8")
    )
    (root / "README.md").write_bytes(b"# fake repo\nclean content\n")
    binary_file = docs / "blob.bin"
    binary_file.write_bytes(BINARY_BLOB)
    return root, secret_file, binary_file


# ---------------------------------------------------------------- helper unit


def test_scrub_tree_replaces_fake_langfuse_pair(bootstrap_mod, tmp_path: Path) -> None:
    tree, secret_file, _ = _seed_tree(tmp_path / "tree")

    changed = bootstrap_mod._scrub_tree(tree)

    # Only the secret-bearing file was rewritten (clean text + binary skipped).
    assert changed == 1
    scrubbed = secret_file.read_text(encoding="utf-8")
    assert FAKE_SK not in scrubbed
    assert FAKE_PK not in scrubbed
    # Exact replacement: the whole token becomes [REDACTED], quotes survive.
    assert f'LANGFUSE_PUBLIC_KEY="{REDACTED}"' in scrubbed
    assert f'LANGFUSE_SECRET_KEY="{REDACTED}"' in scrubbed
    # Non-secret lines untouched.
    assert "LANGFUSE_BASE_URL=https://cloud.langfuse.com" in scrubbed


def test_scrub_tree_leaves_binary_byte_identical(bootstrap_mod, tmp_path: Path) -> None:
    tree, _, binary_file = _seed_tree(tmp_path / "tree")

    bootstrap_mod._scrub_tree(tree)

    assert binary_file.read_bytes() == BINARY_BLOB


def test_scrub_tree_preserves_lf_and_is_idempotent(bootstrap_mod, tmp_path: Path) -> None:
    tree, secret_file, _ = _seed_tree(tmp_path / "tree")

    first = bootstrap_mod._scrub_tree(tree)
    after_first = secret_file.read_bytes()

    # write_bytes path: no CRLF translation sneaks in on Windows.
    assert b"\r\n" not in after_first
    assert first == 1

    # Second pass finds nothing left to scrub and rewrites nothing.
    second = bootstrap_mod._scrub_tree(tree)
    assert second == 0
    assert secret_file.read_bytes() == after_first


# --------------------------------------------------------- pipeline ordering


def test_build_real_fixture_commits_scrubbed_content(
    bootstrap_mod, tmp_path: Path, monkeypatch, capsys
) -> None:
    """The scrub must run after _copytree_selective and BEFORE
    _git_init_two_commits: the fixture's first commit already holds the
    scrubbed content, so no secret ever enters the fixture's git history."""
    src, _, _ = _seed_tree(tmp_path / "src-repo")
    # A .ts file so the real-fixture second commit takes its normal path.
    (src / "index.ts").write_bytes(b"export const x = 1;\n")

    fix = tmp_path / "fixtures"
    fix.mkdir()
    monkeypatch.setattr(bootstrap_mod, "FIX", fix)

    assert bootstrap_mod._build_real_fixture("fakerepo", src) is True

    dst = fix / "fakerepo"
    on_disk = (dst / "docs" / "observability.md").read_text(encoding="utf-8")
    assert FAKE_SK not in on_disk
    assert FAKE_PK not in on_disk
    assert REDACTED in on_disk

    # Binary survived the full copy+scrub pipeline byte-identical.
    assert (dst / "docs" / "blob.bin").read_bytes() == BINARY_BLOB

    # Committed content (first commit = HEAD~1) is the scrubbed version.
    show = subprocess.run(
        ["git", "-C", str(dst), "show", "HEAD~1:docs/observability.md"],
        capture_output=True, text=True, timeout=30,
    )
    assert show.returncode == 0, show.stderr
    assert FAKE_SK not in show.stdout
    assert FAKE_PK not in show.stdout
    assert REDACTED in show.stdout

    # ASCII-only stderr report names the fixture and the rewrite count.
    err = capsys.readouterr().err
    assert "fakerepo: scrubbed secret-shaped strings in 1 file(s)" in err
    assert err.isascii()


def test_build_synthetic_fixture_is_scrubbed_too(
    bootstrap_mod, tmp_path: Path, monkeypatch
) -> None:
    """Synthetic content is authored (no secrets by construction), but it runs
    through the same scrub as belt-and-suspenders -- prove the invariant holds
    if someone ever authors a token-shaped string into a synthetic fixture."""
    fix = tmp_path / "fixtures"
    fix.mkdir()
    monkeypatch.setattr(bootstrap_mod, "FIX", fix)

    bootstrap_mod._build_synthetic_fixture(
        "synthetic-demo",
        {
            "README.md": "# demo\n",
            "notes.md": f'key="{FAKE_SK}"\n',
        },
    )

    scrubbed = (fix / "synthetic-demo" / "notes.md").read_text(encoding="utf-8")
    assert FAKE_SK not in scrubbed
    assert f'key="{REDACTED}"' in scrubbed
