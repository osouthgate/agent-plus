#!/usr/bin/env python3
"""Doc-drift CI gate for agent-plus.

Asserts:
  1. Root VERSION file matches the latest annotated git tag (when tags exist).
  2. Root README badges match VERSION (`version-X.Y.Z`) and contain a
     numeric tests badge (`tests-N%20passing`).
  3. Each plugin's `<plugin>/.claude-plugin/plugin.json#version` is valid
     semver (X.Y.Z).
  3a. The keystone plugin `agent-plus-meta`'s `plugin.json#version` MUST
      equal the root VERSION. Other plugins version independently; the
      meta plugin IS the framework's umbrella, so a split between its
      `--version` output and the umbrella tag would deceive every fresh
      installer (the v0.15.5 friction that drove the F1 fix).
  4. The most recent CHANGELOG.md entry across the framework is dated
     within the last 7 days (sanity check that we're shipping, not just
     bumping).

Exits 0 if all checks pass. Exits 1 with line-precise error messages
otherwise. Stdlib-only.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS = (
    "agent-plus-meta",
    "repo-analyze",
    "diff-summary",
    "skill-feedback",
    "skill-plus",
)

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.]+)?$")
DATE_RE = re.compile(r"^##\s+\d+\.\d+\.\d+\s+-\s+(\d{4}-\d{2}-\d{2})", re.MULTILINE)


def _err(msg: str) -> None:
    print(f"doc-drift: ERROR: {msg}", file=sys.stderr)


def check_version_file() -> tuple[str | None, list[str]]:
    """Read and validate VERSION file. Return (version, errors)."""
    errors: list[str] = []
    vfile = REPO_ROOT / "VERSION"
    if not vfile.is_file():
        errors.append("VERSION file missing at repo root")
        return None, errors
    version = vfile.read_text(encoding="utf-8").strip()
    if not SEMVER_RE.match(version):
        errors.append(f"VERSION file content {version!r} is not valid semver")
        return None, errors
    return version, errors


def check_version_matches_tag(version: str) -> list[str]:
    """If git tags exist, the latest tag should match VERSION."""
    errors: list[str] = []
    try:
        out = subprocess.run(
            ["git", "tag", "--sort=-creatordate"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return errors  # No git available — skip silently.
    tags = [t.strip() for t in out.stdout.splitlines() if t.strip()]
    if not tags:
        return errors
    latest = tags[0].lstrip("v")
    # Tolerate the "VERSION leads tag by one PR" case: if VERSION is ahead
    # of the latest tag, that's expected on the unreleased commit. We only
    # fail when VERSION is BEHIND the latest tag.
    try:
        v_tuple = tuple(int(p) for p in version.split("."))
        t_tuple = tuple(int(p) for p in latest.split("."))
    except ValueError:
        return errors
    if v_tuple < t_tuple:
        errors.append(
            f"VERSION ({version}) is behind latest git tag (v{latest}). "
            f"Bump VERSION or fix the tag."
        )
    return errors


def check_readme_badges(version: str) -> list[str]:
    """Root README must contain a version badge (static or dynamic) and a
    tests count.

    A dynamic badge — `img.shields.io/github/v/(tag|release)/...` or
    `img.shields.io/badge/dynamic/...` — auto-syncs to the published tag
    or to a file in the repo, so no manual bump is needed on every
    release. We accept either form; static badges still get a hard
    version-match check for back-compat.
    """
    errors: list[str] = []
    readme = REPO_ROOT / "README.md"
    if not readme.is_file():
        errors.append("README.md missing at repo root")
        return errors
    text = readme.read_text(encoding="utf-8")

    has_dynamic = bool(re.search(
        r"img\.shields\.io/(github/v/(tag|release)|badge/dynamic)",
        text,
    ))
    if not has_dynamic:
        m = re.search(r"version-(\d+\.\d+\.\d+)-", text)
        if not m:
            errors.append(
                "README.md: no version badge found (either a dynamic "
                "`img.shields.io/github/v/release/...` badge or a static "
                "`version-X.Y.Z-` badge)"
            )
        elif m.group(1) != version:
            errors.append(
                f"README.md version badge ({m.group(1)}) does not match "
                f"VERSION ({version}). Either update the `version-X.Y.Z-` "
                f"badge or switch to a dynamic shields.io badge."
            )

    # Tests badge: static count, no auto-sync exists. Just require presence.
    tm = re.search(r"tests-(\d+)%20passing", text)
    if not tm:
        errors.append("README.md: no `tests-N%20passing` badge found")
    return errors


def check_plugin_versions() -> list[str]:
    """Each plugin's plugin.json#version must be valid semver."""
    errors: list[str] = []
    for plugin in PLUGINS:
        pjson = REPO_ROOT / plugin / ".claude-plugin" / "plugin.json"
        if not pjson.is_file():
            errors.append(f"{plugin}/.claude-plugin/plugin.json missing")
            continue
        try:
            data = json.loads(pjson.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f"{pjson.relative_to(REPO_ROOT)}: invalid JSON ({e})")
            continue
        version = data.get("version")
        if not isinstance(version, str) or not SEMVER_RE.match(version):
            errors.append(
                f"{pjson.relative_to(REPO_ROOT)}: version {version!r} is not "
                f"valid semver (X.Y.Z)"
            )
    return errors


def check_meta_version_matches_root(version: str) -> list[str]:
    """agent-plus-meta IS the framework's keystone plugin — its plugin.json
    version must match the root VERSION. If they drift, fresh users see
    `agent-plus-meta --version` report a stale number while `upgrade-check`
    fetches the (newer) root VERSION, surfacing a phantom "upgrade
    available" prompt seconds after install. See F1 in the v0.15.5 DX
    audit for the original incident.
    """
    errors: list[str] = []
    pjson = REPO_ROOT / "agent-plus-meta" / ".claude-plugin" / "plugin.json"
    if not pjson.is_file():
        return errors  # already flagged by check_plugin_versions
    try:
        data = json.loads(pjson.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return errors  # already flagged
    meta_v = data.get("version")
    if meta_v != version:
        errors.append(
            f"agent-plus-meta/.claude-plugin/plugin.json#version ({meta_v!r}) "
            f"!= root VERSION ({version!r}). The keystone plugin's version "
            f"must match the umbrella — bump plugin.json on every framework "
            f"release. (Other plugins version independently; meta does not.)"
        )
    return errors


def check_recent_changelog() -> list[str]:
    """At least one plugin CHANGELOG must have an entry dated in the last
    7 days. Sanity check that we're shipping."""
    errors: list[str] = []
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=7)
    most_recent: dt.date | None = None
    most_recent_file: str | None = None
    for plugin in PLUGINS:
        cl = REPO_ROOT / plugin / "CHANGELOG.md"
        if not cl.is_file():
            continue
        text = cl.read_text(encoding="utf-8")
        for m in DATE_RE.finditer(text):
            try:
                d = dt.date.fromisoformat(m.group(1))
            except ValueError:
                continue
            if most_recent is None or d > most_recent:
                most_recent = d
                most_recent_file = str(cl.relative_to(REPO_ROOT))
    if most_recent is None:
        errors.append("no parseable CHANGELOG.md entries found across plugins")
        return errors
    if most_recent < cutoff:
        errors.append(
            f"most recent CHANGELOG entry ({most_recent}) in "
            f"{most_recent_file} is older than 7 days. "
            f"Are we shipping?"
        )
    return errors


def main() -> int:
    all_errors: list[str] = []
    version, errs = check_version_file()
    all_errors.extend(errs)
    if version is not None:
        all_errors.extend(check_version_matches_tag(version))
        all_errors.extend(check_readme_badges(version))
        all_errors.extend(check_meta_version_matches_root(version))
    all_errors.extend(check_plugin_versions())
    all_errors.extend(check_recent_changelog())

    if all_errors:
        for e in all_errors:
            _err(e)
        print(f"\ndoc-drift: {len(all_errors)} drift error(s) detected.",
              file=sys.stderr)
        return 1
    print("doc-drift: ok — no drift detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
