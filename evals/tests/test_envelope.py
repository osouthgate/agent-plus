from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SECRET_RES = (
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-+/=]{8,}"),
)


def _collect_strings(obj: Any, out: list[str]) -> None:
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_strings(v, out)


def _assert_no_secrets(text: str) -> None:
    for rx in SECRET_RES:
        assert rx.search(text) is None, f"possible secret pattern matched: {rx.pattern}"


def _run_py(bin_path: Path, args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(bin_path), *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def test_version_help_smoke(plugin_bin, repo_root: Path) -> None:
    plugins = [
        "agent-plus-meta",
        "repo-analyze",
        "diff-summary",
        "skill-feedback",
        "skill-plus",
    ]
    for name in plugins:
        b = plugin_bin(name)
        v = _run_py(b, ["--version"])
        assert v.returncode == 0, f"{name} --version stderr: {v.stderr}"
        assert re.search(r"\d+\.\d+", v.stdout), f"{name} version output: {v.stdout!r}"

        h = _run_py(b, ["--help"])
        assert h.returncode == 0, f"{name} --help failed: {h.stderr}"


def test_json_read_envelopes(plugin_bin, repo_root: Path) -> None:
    osdb = repo_root / "evals" / "fixtures" / "osdb"
    assert osdb.is_dir(), f"missing fixture {osdb}"

    probes: list[tuple[str, list[str], Path | None]] = [
        ("agent-plus-meta", ["--json", "doctor"], repo_root),
        ("repo-analyze", ["--path", str(osdb), "--json"], repo_root),
        ("diff-summary", ["--path", str(osdb), "--base", "HEAD", "--json"], repo_root),
        ("skill-feedback", ["report"], repo_root),
        ("skill-plus", ["list", "--project", str(repo_root), "--json"], repo_root),
    ]

    for name, args, cwd in probes:
        b = plugin_bin(name)
        r = _run_py(b, args, cwd=cwd)
        assert r.returncode == 0, f"{name} {' '.join(args)} stderr={r.stderr}"
        combined = r.stdout + r.stderr
        _assert_no_secrets(combined)

        data = json.loads(r.stdout)
        assert "tool" in data
        assert data["tool"]["name"] == name
        assert data["tool"].get("version"), f"missing tool.version in {name}"
        dumped = json.dumps(data)
        assert "savedTo" not in dumped


def test_output_offload_envelope(plugin_bin, repo_root: Path) -> None:
    osdb = repo_root / "evals" / "fixtures" / "osdb"
    assert osdb.is_dir()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
        out_path = Path(tmp.name)

    try:
        b_ra = plugin_bin("repo-analyze")
        r = _run_py(
            b_ra,
            ["--path", str(osdb), "--json", "--output", str(out_path)],
            cwd=repo_root,
        )
        assert r.returncode == 0, r.stderr
        assert out_path.is_file() and out_path.stat().st_size > 0
        env = json.loads(r.stdout)
        assert env.get("payloadPath")
        assert Path(env["payloadPath"]).resolve() == out_path.resolve()
        assert "savedTo" not in json.dumps(env)

        b_ds = plugin_bin("diff-summary")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp2:
            out2 = Path(tmp2.name)
        try:
            r2 = _run_py(
                b_ds,
                [
                    "--path",
                    str(osdb),
                    "--range",
                    "HEAD~1..HEAD",
                    "--json",
                    "--output",
                    str(out2),
                ],
                cwd=repo_root,
            )
            assert r2.returncode == 0, r2.stderr
            assert out2.is_file()
            e2 = json.loads(r2.stdout)
            assert e2.get("payloadPath")
            assert "savedTo" not in json.dumps(e2)
        finally:
            out2.unlink(missing_ok=True)

        b_sp = plugin_bin("skill-plus")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp3:
            out3 = Path(tmp3.name)
        try:
            r3 = _run_py(
                b_sp,
                ["list", "--project", str(repo_root), "--json", "--output", str(out3)],
                cwd=repo_root,
            )
            assert r3.returncode == 0, r3.stderr
            assert out3.is_file()
            e3 = json.loads(r3.stdout)
            assert e3.get("payloadPath")
            assert "savedTo" not in json.dumps(e3)
        finally:
            out3.unlink(missing_ok=True)
    finally:
        out_path.unlink(missing_ok=True)


def test_agent_plus_meta_doctor_strings_have_no_secrets(plugin_bin, repo_root: Path) -> None:
    b = plugin_bin("agent-plus-meta")
    r = _run_py(b, ["--json", "doctor"], cwd=repo_root)
    assert r.returncode == 0
    data = json.loads(r.stdout)
    strings: list[str] = []
    _collect_strings(data, strings)
    blob = "\n".join(strings)
    _assert_no_secrets(blob)
