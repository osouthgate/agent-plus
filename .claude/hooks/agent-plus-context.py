#!/usr/bin/env python3
"""SessionStart hook: surface .agent-plus/ workspace context as additional
context for the agent's first turn.

Reads .agent-plus/{manifest,services,env-status}.json (any subset that exists)
and prints a tight summary to stdout. SessionStart hook stdout is injected
as additional context for Claude. Stays silent if no workspace is found —
that is the correct no-op for repos that haven't run `agent-plus init`.

Resolution order (matches bin/agent-plus and bin/skill-feedback):
  1. <git-toplevel>/.agent-plus/  (if cwd is inside a git repo)
  2. <cwd>/.agent-plus/           (project without git)
  3. ~/.agent-plus/               (last-resort fallback)

Never raises. Never exits non-zero. Worst case prints nothing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _git_toplevel(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _staging_banner(cwd: Path) -> str | None:
    """Return a one-line STAGING MODE notice if this repo's remote points at
    `plans-agent-plus` (the private staging clone). Otherwise None.

    The banner is the runtime nudge that coding agents are working in the
    private clone whose work is promoted to the public agent-plus repo.
    See STAGING.md for the durable reference and the promotion ritual.
    """
    try:
        out = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=cwd, capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    url = (out.stdout or "").strip().lower()
    if "plans-agent-plus" in url:
        return ("STAGING MODE: this is the private agent-plus staging clone. "
                "Work lands here first; promote to public via cherry-pick. "
                "See STAGING.md for the release ritual.")
    return None


def _resolve_workspace(cwd: Path) -> Path | None:
    top = _git_toplevel(cwd)
    if top and (top / ".agent-plus").is_dir():
        return top / ".agent-plus"
    if (cwd / ".agent-plus").is_dir():
        return cwd / ".agent-plus"
    home = Path.home() / ".agent-plus"
    if home.is_dir():
        return home
    return None


def _load(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _format(workspace: Path) -> str:
    manifest = _load(workspace / "manifest.json") or {}
    services = _load(workspace / "services.json") or {}
    env_status = _load(workspace / "env-status.json") or {}

    lines: list[str] = []
    lines.append(f"agent-plus workspace: {workspace}")

    plugins = manifest.get("plugins") or []
    if plugins:
        lines.append(f"Plugins ({len(plugins)}): {', '.join(plugins[:8])}"
                     + ("…" if len(plugins) > 8 else ""))

    svc = services.get("services") or {}
    resolved = []
    gh = svc.get("github-remote") or svc.get("github")
    if isinstance(gh, dict):
        owner = gh.get("owner")
        repo = gh.get("repo") or gh.get("owner_repo") or gh.get("name")
        if owner and repo:
            resolved.append(f"github={owner}/{repo}")
        elif repo:
            resolved.append(f"github={repo}")
    vc = svc.get("vercel-remote") or svc.get("vercel")
    if isinstance(vc, dict):
        projects = vc.get("projects") or []
        if projects:
            resolved.append(f"vercel={len(projects)} project(s)")
        elif vc.get("status") == "unconfigured":
            resolved.append("vercel=unconfigured")
    if resolved:
        lines.append("Services: " + ", ".join(resolved))

    missing = env_status.get("missing") or []
    if missing:
        names = [m.get("name", m) if isinstance(m, dict) else m for m in missing[:6]]
        more = f" (+{len(missing) - 6} more)" if len(missing) > 6 else ""
        lines.append(f"Missing env: {', '.join(names)}{more}")
    elif env_status.get("checked"):
        lines.append("Env: all required vars set")

    refreshed = services.get("refreshedAt")
    checked = env_status.get("checkedAt")
    if refreshed or checked:
        stamps = []
        if refreshed:
            stamps.append(f"refreshed {refreshed}")
        if checked:
            stamps.append(f"envcheck {checked}")
        lines.append("Last: " + "; ".join(stamps))

    return "\n".join(lines)


def main() -> int:
    cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())).resolve()

    parts: list[str] = []

    try:
        banner = _staging_banner(cwd)
    except Exception:
        banner = None
    if banner:
        parts.append(banner)

    workspace = _resolve_workspace(cwd)
    if workspace is not None:
        try:
            ws_out = _format(workspace)
        except Exception:
            ws_out = ""
        if ws_out:
            parts.append(ws_out)

    if parts:
        print("\n\n".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
