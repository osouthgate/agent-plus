"""Tier 2 envelope-contract conformance tests.

Walks every installed plugin under a plugin root and asserts the cross-plugin
envelope contract:

  1. `<bin> --version` exits 0 and emits `<name> <semver>` (uniform across
     all plugins as of agent-plus 0.8.0 / skill-feedback 0.3.0).
  2. `<bin> --help` exits 0 (smoke).
  3. A read-only / version subcommand emits a JSON envelope with
     `tool.name` matching plugin.json and `tool.version` non-empty.
  4. No `savedTo` key anywhere (renamed to `payloadPath`; this is a public
     contract).
  5. No obvious-secret patterns in any string value.
  6. For plugins that support `--output PATH`, the file is created and
     the stdout envelope carries `payloadPath` (absolute) + `payloadShape`.

Plugin root resolution (in order):
  - $AGENT_PLUS_PLUGINS_DIR
  - ~/.claude/plugins/cache/agent-plus  (dev machine)
  - ~/.claude/plugins/cache              (will scan all marketplaces)

Layouts supported:
  - <root>/<plugin>/.claude-plugin/plugin.json              (flat / source repo)
  - <root>/<plugin>/<version>/.claude-plugin/plugin.json    (Claude Code cache)
  - <root>/<marketplace>/<plugin>/<version>/.claude-plugin/ (cache root)

Stdlib only. No pytest, no fixtures library. Run:

    python -m unittest agent-plus/test/contract/test_envelope_contract.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterable, Optional

# ─── discovery ───────────────────────────────────────────────────────────────


def _candidate_roots() -> list[Path]:
    env = os.environ.get("AGENT_PLUS_PLUGINS_DIR")
    if env:
        return [Path(env).expanduser()]
    home = Path.home()
    return [
        home / ".claude" / "plugins" / "cache" / "agent-plus",
        home / ".claude" / "plugins" / "cache",
    ]


def _find_plugin_jsons(root: Path) -> list[Path]:
    """Return every .claude-plugin/plugin.json under root, at any nesting."""
    if not root.is_dir():
        return []
    out: list[Path] = []
    # Keep depth bounded so we don't walk gigantic trees by accident.
    for pj in root.glob("**/.claude-plugin/plugin.json"):
        # Skip junk: workspaces, temp dirs, etc.
        parts = pj.parts
        if any(p.startswith("temp_git_") for p in parts):
            continue
        out.append(pj)
    return out


def _pick_plugins() -> list[dict]:
    """Resolve the canonical plugin set: latest version per name."""
    seen: dict[str, dict] = {}
    for root in _candidate_roots():
        for pj in _find_plugin_jsons(root):
            try:
                meta = json.loads(pj.read_text(encoding="utf-8"))
            except Exception:
                continue
            name = meta.get("name")
            version = meta.get("version")
            if not name or not version:
                continue
            plugin_dir = pj.parent.parent
            bin_path = plugin_dir / "bin" / name
            if not bin_path.is_file():
                continue
            existing = seen.get(name)
            if existing is None or _ver_key(version) >= _ver_key(existing["version"]):
                seen[name] = {
                    "name": name,
                    "version": version,
                    "bin": bin_path,
                    "dir": plugin_dir,
                }
        if seen:
            # First root that yielded plugins wins (don't mix cache + repos).
            break
    return sorted(seen.values(), key=lambda p: p["name"])


def _ver_key(v: str) -> tuple:
    parts = re.split(r"[-+.]", v)
    out: list = []
    for p in parts:
        out.append((0, int(p)) if p.isdigit() else (1, p))
    return tuple(out)


# ─── probe table ─────────────────────────────────────────────────────────────

# Plugins where a non-trivial read subcommand is safe (no credentials needed).
# Maps plugin name -> tuple(args). Use {OUTPUT} placeholder for tmp file path.
SAFE_PROBES: dict[str, tuple[str, ...]] = {
    "agent-plus": ("list",),
    "skill-feedback": ("path",),
    "repo-analyze": ("--max-tree-depth", "1", "--max-tree-files", "5", "--output", "{OUTPUT}"),
    "diff-summary": ("--path", "{GIT_ROOT}", "--output", "{OUTPUT}"),
    "skill-plus": ("list",),
}

# Plugins that support --output (will be re-probed with --output to assert
# payloadPath + payloadShape envelope shape).
SUPPORTS_OUTPUT: set[str] = {"repo-analyze", "diff-summary"}

# All other plugins fall back to `--version --json` if supported, else `--version`.
# We assert the contract that `--version` always works without credentials.

VERSION_RE = re.compile(r"^[a-z0-9-]+ \d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.]+)?$")

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"Bearer [A-Za-z0-9._-]{20,}"),
    re.compile(r"gh[ps]_[A-Za-z0-9]{36,}"),
    re.compile(r"postgres://[^@\s]+@"),
    re.compile(r"mysql://[^@\s]+@"),
]

TIMEOUT = 10


# ─── helpers ─────────────────────────────────────────────────────────────────


def _run(bin_path: Path, *args: str, cwd: Optional[Path] = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(bin_path), *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        cwd=str(cwd) if cwd else None,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _walk_strings(obj) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(k)
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)


def _walk_keys(obj) -> Iterable[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_keys(v)


def _assert_no_secrets(tc: unittest.TestCase, obj, ctx: str) -> None:
    for s in _walk_strings(obj):
        for pat in SECRET_PATTERNS:
            if pat.search(s):
                tc.fail(f"{ctx}: found secret-like pattern matching {pat.pattern!r}")


def _assert_no_savedTo(tc: unittest.TestCase, obj, ctx: str) -> None:
    for k in _walk_keys(obj):
        if k == "savedTo":
            tc.fail(f"{ctx}: forbidden key `savedTo` found (renamed to `payloadPath`)")


def _find_git_root() -> Optional[Path]:
    """Walk upward from cwd looking for a .git dir. Used by diff-summary probe."""
    p = Path.cwd().resolve()
    for cand in [p, *p.parents]:
        if (cand / ".git").exists():
            return cand
    return None


def _safe_args_for(plugin: dict, output_path: Optional[Path]) -> Optional[tuple[str, ...]]:
    args = SAFE_PROBES.get(plugin["name"])
    if not args:
        return None
    out = []
    for a in args:
        if a == "{OUTPUT}":
            if output_path is None:
                return None
            out.append(str(output_path))
        elif a == "{PLUGIN_DIR}":
            out.append(str(plugin["dir"]))
        elif a == "{GIT_ROOT}":
            gr = _find_git_root()
            if gr is None:
                return None
            out.append(str(gr))
        else:
            out.append(a)
    return tuple(out)


# ─── test class (dynamic methods) ────────────────────────────────────────────


PLUGINS = _pick_plugins()


@unittest.skipUnless(
    PLUGINS,
    "no plugins discoverable under AGENT_PLUS_PLUGINS_DIR or ~/.claude/plugins/cache; "
    "skipping envelope contract suite",
)
class TestEnvelopeContract(unittest.TestCase):
    """Contract conformance per discovered plugin."""


def _make_test_for(plugin: dict):
    name = plugin["name"]
    version = plugin["version"]
    bin_path: Path = plugin["bin"]

    def test(self: unittest.TestCase) -> None:
        # ---- 1. --version exits 0, non-empty, matches expected shape ----
        rc, out, err = _run(bin_path, "--version")
        self.assertEqual(rc, 0, f"[{name}] --version exit {rc}; stderr={err!r}")
        line = (out or err).strip().splitlines()[0] if (out or err).strip() else ""
        self.assertTrue(line, f"[{name}] --version emitted no output")
        self.assertRegex(
            line,
            VERSION_RE,
            f"[{name}] --version output {line!r} does not match `[<name> ]<semver>`",
        )

        # ---- 2. --help exits 0 ----
        rc, out, err = _run(bin_path, "--help")
        self.assertEqual(rc, 0, f"[{name}] --help exit {rc}; stderr={err!r}")
        self.assertTrue((out or err).strip(), f"[{name}] --help emitted no output")

        # ---- 3. JSON-emitting probe ----
        # Pick the safest available read command.
        with tempfile.TemporaryDirectory() as td:
            output_path: Optional[Path] = None
            if name in SUPPORTS_OUTPUT:
                output_path = Path(td) / f"{name}-probe.json"
            args = _safe_args_for(plugin, output_path)

            if args is None:
                # No safe non-version probe — skip the JSON-envelope assertion.
                # We've already proven --version works without credentials,
                # which is itself a contract assertion (no Python traceback,
                # exit 0, version printed).
                return

            rc, out, err = _run(bin_path, *args)
            self.assertEqual(
                rc, 0,
                f"[{name}] probe {args!r} exit {rc}; stderr={err!r}",
            )

            # Parse stdout as JSON envelope.
            try:
                env = json.loads(out)
            except json.JSONDecodeError as e:
                self.fail(f"[{name}] probe stdout is not JSON: {e}; got {out[:200]!r}")

            self.assertIsInstance(env, dict, f"[{name}] envelope is not a dict")
            self.assertIn("tool", env, f"[{name}] envelope missing `tool`")
            tool = env["tool"]
            self.assertIsInstance(tool, dict, f"[{name}] `tool` is not a dict")

            self.assertEqual(
                tool.get("name"), name,
                f"[{name}] tool.name = {tool.get('name')!r}, expected {name!r}",
            )
            tv = tool.get("version")
            self.assertIsInstance(tv, str, f"[{name}] tool.version not a string")
            self.assertTrue(tv, f"[{name}] tool.version is empty")
            # Allow `unknown` fallback per spec.
            if tv != "unknown":
                self.assertIn(
                    tv, {version},
                    f"[{name}] tool.version = {tv!r}, plugin.json says {version!r}",
                )

            # ---- 4. No `savedTo` key anywhere ----
            _assert_no_savedTo(self, env, f"[{name}] envelope")

            # ---- 5. No obvious-secret patterns ----
            _assert_no_secrets(self, env, f"[{name}] envelope")

            # ---- 6. --output contract (where supported) ----
            if name in SUPPORTS_OUTPUT and output_path is not None:
                self.assertTrue(
                    output_path.is_file(),
                    f"[{name}] --output {output_path} did not create a file",
                )
                self.assertIn(
                    "payloadPath", env,
                    f"[{name}] envelope missing payloadPath after --output",
                )
                pp = env["payloadPath"]
                self.assertIsInstance(pp, str)
                self.assertTrue(
                    Path(pp).is_absolute(),
                    f"[{name}] payloadPath {pp!r} is not absolute",
                )
                self.assertIn(
                    "payloadShape", env,
                    f"[{name}] envelope missing payloadShape after --output",
                )
                self.assertIsInstance(env["payloadShape"], dict)

    test.__name__ = f"test_{name.replace('-', '_')}"
    test.__doc__ = f"envelope contract: {name} {version}"
    return test


for _p in PLUGINS:
    _t = _make_test_for(_p)
    setattr(TestEnvelopeContract, _t.__name__, _t)


if __name__ == "__main__":
    unittest.main(verbosity=2)
