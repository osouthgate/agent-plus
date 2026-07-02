"""Tests for skill-plus secret redaction (v0.19.7 hotfix, part 2).

Field-verified bug: skill-plus scan persisted a LIVE bearer token to
candidates.jsonl because (1) the Bearer pattern's charset excluded "|" so
Laravel Sanctum-style "<id>|<hash>" tokens never matched, and (2) the
Authorization pattern only consumed the scheme word ("Bearer"), leaving the
token value untouched. All tokens in this file are FAKE (synthetic hex/letter
runs), never realistic live credentials.
"""
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin" / "skill-plus"
SCAFFOLD_PY = Path(__file__).resolve().parent.parent / "bin" / "_subcommands" / "scaffold.py"


def _load_bin_module():
    """The bin file has no .py extension; load it via SourceFileLoader so
    tests can call scrub_text() directly instead of shelling out."""
    loader = SourceFileLoader("skill_plus_bin_redaction", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _load_scaffold_module():
    loader = SourceFileLoader("skill_plus_scaffold_redaction", str(SCAFFOLD_PY))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _load_generated_module(src: str, path: Path):
    """Write a scaffold-rendered skill .py to disk and load it as a module,
    the same way a real scaffolded skill would be imported/run."""
    path.write_text(src, encoding="utf-8")
    loader = SourceFileLoader(path.stem, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ─── field repro: the exact shape from the security bug ───────────────────────


def test_field_repro_sanctum_token_behind_authorization_header():
    """Exact repro shape from the field report: curl -H "Authorization:
    Bearer <id>|<hash>" ... Must leave neither the "<id>|" prefix nor the
    hash substring anywhere in the scrubbed output."""
    mod = _load_bin_module()
    fake_hash = "a" * 40
    cmd = f'curl -s -H "Authorization: Bearer 12|{fake_hash}" https://example.test'
    out = mod.scrub_text(cmd)
    assert "12|" not in out
    assert fake_hash not in out
    assert "[REDACTED]" in out
    # Header name stays readable per the task spec.
    assert "Authorization" in out
    assert "https://example.test" in out  # unrelated content untouched


def test_standalone_sanctum_token_redacted():
    """A bare "<digits>|<hash>" token with no header name in front (e.g.
    embedded in a URL query string or argv) must still be caught."""
    mod = _load_bin_module()
    fake_hash = "b" * 40
    token = f"7|{fake_hash}"
    out = mod.scrub_text(f"curl https://example.test/api?token={token}")
    assert token not in out
    assert fake_hash not in out
    assert "[REDACTED]" in out


def test_short_bearer_token_redacted():
    """A Bearer token under the old {20,} minimum must now be redacted.
    Deliberately has no "Authorization:" prefix so the header-name pattern
    (which would swallow the whole thing regardless) can't mask a
    regression in the widened Bearer pattern itself."""
    mod = _load_bin_module()
    token = "abc12345"  # 8 chars -- in [8, 20), below the old {20,} floor.
    assert 8 <= len(token) < 20
    out = mod.scrub_text(f"log: used token Bearer {token} for the request")
    assert token not in out
    assert "[REDACTED]" in out


# ─── sensitive header NAMES: value redacted, name kept readable ───────────────


def test_cookie_header_value_redacted_name_kept():
    mod = _load_bin_module()
    value = "deadbeefcafefeed12345"
    out = mod.scrub_text(f'curl -H "Cookie: session={value}" https://example.test')
    assert value not in out
    assert "Cookie" in out
    assert "[REDACTED]" in out


def test_x_api_key_header_value_redacted_name_kept():
    mod = _load_bin_module()
    value = "sk-liveFAKEKEY1234567890abcdef"
    out = mod.scrub_text(f'curl -H "X-Api-Key: {value}" https://example.test')
    assert value not in out
    assert "X-Api-Key" in out
    assert "[REDACTED]" in out


def test_set_cookie_and_private_token_headers_redacted():
    mod = _load_bin_module()
    out = mod.scrub_text('Set-Cookie: sid=abcdefghijklmnop; Path=/')
    assert "abcdefghijklmnop" not in out
    assert "Set-Cookie" in out

    out2 = mod.scrub_text("Private-Token: glpat-FAKEFAKEFAKEFAKEFAKE")
    assert "glpat-FAKEFAKEFAKEFAKEFAKE" not in out2
    assert "Private-Token" in out2


# ─── clean prose: no false positives ───────────────────────────────────────────


def test_clean_prose_unchanged():
    mod = _load_bin_module()
    lines = [
        "Please update the authorization documentation before merging.",
        "The cookie jar on the counter is empty.",
        "git commit -m 'fix: tighten the redaction patterns'",
        "Run the private token issuance ceremony next sprint.",
        "railway logs --service api --since 5m",
    ]
    for line in lines:
        assert mod.scrub_text(line) == line, f"unexpected change: {line!r}"


# ─── pattern ordering ──────────────────────────────────────────────────────────


def test_jwt_with_bearer_prefix_fully_redacted():
    """A JWT preceded by "Bearer " must be fully redacted regardless of
    which pattern(s) in the list actually fire -- guards the header-name
    pattern running first without breaking the JWT-specific pattern."""
    mod = _load_bin_module()
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    out = mod.scrub_text(f"Bearer {jwt}")
    assert jwt not in out
    assert "eyJ" not in out
    assert "[REDACTED]" in out


# ─── scaffold scrubber parity (Task 2) ─────────────────────────────────────────


def test_scaffold_generated_skill_scrub_text_parity(tmp_path: Path):
    """bin/_subcommands/scaffold.py keeps its own hand-synced copy of
    _SECRET_PATTERNS for skills it scaffolds (no cross-module import in this
    hotfix). The same fake Sanctum-shaped token run through the SCAFFOLDED
    skill's own scrub_text() must come out redacted too."""
    scaffold_mod = _load_scaffold_module()
    generated_src = scaffold_mod._render_python_entry(
        "parity-skill", "A test skill for redaction parity."
    )
    gen_mod = _load_generated_module(generated_src, tmp_path / "parity_skill_generated.py")

    fake_hash = "c" * 40
    cmd = f'curl -s -H "Authorization: Bearer 9|{fake_hash}" https://example.test'
    out = gen_mod.scrub_text(cmd)
    assert "9|" not in out
    assert fake_hash not in out
    assert "[REDACTED]" in out
    assert "Authorization" in out
