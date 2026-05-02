"""skill-plus review -- multi-persona pre-ship reviewer (v0.1, Option B).

Two modes share one entry point:

  skill-plus review <path>                    # dispatch mode
  skill-plus review <path> --synth-from <dir> # synthesis mode

Dispatch mode (Option B contract):
  Reads persona briefs (default 4: security, agent-ux, docs-clarity, edge-cases)
  and emits a dispatch_envelope telling the calling orchestrator (Claude Code)
  to spawn N sub-agents -- one per persona -- and then call the synth mode.
  The process itself does NOT spawn sub-agents.

Synthesis mode:
  Reads N JSON findings files from <findings-dir> (one per persona), merges
  them, computes a verdict, and emits the final ENVELOPE_VERSION 1.1 envelope.

Persona lookup precedence (highest first):
  1. Plugin-local: <target>/personas/<name>.md
  2. User-global: ~/.agent-plus/review-personas/<name>.md
  3. Shipped default: <this-file's-dir>/../../personas/<name>.md

Stdlib only. ASCII-only stdout/stderr. No subprocess, no network.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

# Helpers (_now_iso, _tool_meta) injected by bin/skill-plus loader.

# ─── constants ────────────────────────────────────────────────────────────────

ENVELOPE_VERSION = "1.1"
DEFAULT_PERSONAS = ("security", "agent-ux", "docs-clarity", "edge-cases")

# Paths to relevant file kinds to include in each persona's context list.
# Sub-agents are expected to read these files themselves given the paths.
INTERESTING_GLOBS = [
    "SKILL.md",
    "plugin.json",
    ".claude-plugin/plugin.json",
    "bin/*",
    "bin/**/*",
    "README.md",
    "CHANGELOG.md",
]


# ─── persona resolution ──────────────────────────────────────────────────────


def _shipped_personas_dir() -> Path:
    """Directory containing the default shipped persona briefs."""
    # bin/_subcommands/review.py -> bin/_subcommands -> bin -> skill-plus -> personas/
    here = Path(__file__).resolve().parent
    return here.parent.parent / "personas"


def _user_personas_dir() -> Path:
    return Path.home() / ".agent-plus" / "review-personas"


def resolve_persona_path(name: str, target_path: Optional[str]) -> Optional[Path]:
    """Return the path to the persona brief for `name`, following precedence:
      1. Plugin-local  <target>/personas/<name>.md
      2. User-global   ~/.agent-plus/review-personas/<name>.md
      3. Shipped       skill-plus/personas/<name>.md

    Returns None if the persona is not found in any location.
    """
    candidates: list[Path] = []
    if target_path:
        candidates.append(Path(target_path) / "personas" / f"{name}.md")
    candidates.append(_user_personas_dir() / f"{name}.md")
    candidates.append(_shipped_personas_dir() / f"{name}.md")
    for c in candidates:
        if c.exists():
            return c
    return None


def list_default_personas(target_path: Optional[str]) -> list[dict]:
    """Return a list of persona dicts for all DEFAULT_PERSONAS found."""
    out = []
    for name in DEFAULT_PERSONAS:
        p = resolve_persona_path(name, target_path)
        if p is not None:
            out.append({"name": name, "brief_path": str(p)})
    return out


def list_user_personas(target_path: Optional[str]) -> list[str]:
    """Return names of user-defined personas (user-global + plugin-local)."""
    names: list[str] = []
    seen: set[str] = set()
    dirs: list[Path] = []
    if target_path:
        dirs.append(Path(target_path) / "personas")
    dirs.append(_user_personas_dir())
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            n = f.stem
            if n not in seen:
                seen.add(n)
                names.append(n)
    return names


def discover_all_persona_names(target_path: Optional[str]) -> list[str]:
    """All persona names from shipped defaults + user extensions (deduped, ordered)."""
    seen: set[str] = set(DEFAULT_PERSONAS)
    names = list(DEFAULT_PERSONAS)
    for extra in list_user_personas(target_path):
        if extra not in seen:
            seen.add(extra)
            names.append(extra)
    return names


# ─── target detection (mirrors inquire._detect_target_kind) ──────────────────


def _detect_target(path_str: str) -> tuple[str, str, str]:
    """Return (kind, name, resolved_path).

    kind: 'plugin' | 'skill' | 'directory'
    name: best-guess name for the target
    resolved_path: absolute path string
    """
    p = Path(path_str).expanduser().resolve()
    kind = "directory"
    name = p.name
    if (p / ".claude-plugin" / "plugin.json").exists():
        kind = "plugin"
        try:
            manifest = json.loads(
                (p / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
            )
            name = manifest.get("name") or p.name
        except (OSError, json.JSONDecodeError):
            pass
    elif (p / "SKILL.md").exists():
        kind = "skill"
        # Try to read name from frontmatter.
        try:
            text = (p / "SKILL.md").read_text(encoding="utf-8")
            m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
            if m:
                name = m.group(1).strip().strip("'\"")
        except OSError:
            pass
    return kind, name, str(p)


def _collect_target_files(resolved_path: str) -> list[str]:
    """Collect relevant file paths under the target for sub-agent context."""
    p = Path(resolved_path)
    if not p.is_dir():
        return []
    files: list[str] = []
    priority = ["SKILL.md", "README.md", "CHANGELOG.md"]
    for fname in priority:
        f = p / fname
        if f.exists():
            files.append(str(f))
    # plugin.json
    for pj in [p / "plugin.json", p / ".claude-plugin" / "plugin.json"]:
        if pj.exists():
            files.append(str(pj))
    # bin/ files (up to 2 levels deep)
    bin_dir = p / "bin"
    if bin_dir.is_dir():
        for entry in sorted(bin_dir.iterdir()):
            if entry.is_file():
                files.append(str(entry))
            elif entry.is_dir():
                for sub in sorted(entry.iterdir()):
                    if sub.is_file():
                        files.append(str(sub))
    return files


# ─── dispatch envelope ──────────────────────────────────────────────────────


def _build_dispatch_envelope(
    args,
    kind: str,
    name: str,
    resolved_path: str,
    personas: list[dict],
    findings_dir: str,
) -> dict:
    """Build the dispatch envelope telling the orchestrator what to spawn."""
    target_files = _collect_target_files(resolved_path)
    synth_cmd = f"skill-plus review {resolved_path} --synth-from {findings_dir}"
    persona_briefs: list[dict] = []
    for p in personas:
        persona_briefs.append({
            "name": p["name"],
            "brief_path": p["brief_path"],
            "target_path": resolved_path,
            "target_files": target_files,
            "output_path": str(Path(findings_dir) / f"{p['name']}.json"),
        })

    return {
        "envelope_version": ENVELOPE_VERSION,
        "mode": "dispatch",
        "target": {
            "kind": kind,
            "name": name,
            "path": resolved_path,
        },
        "personas": persona_briefs,
        "findings_dir": findings_dir,
        "synth_command": synth_cmd,
        "instructions": (
            "Spawn one sub-agent per persona (in parallel). "
            "Each sub-agent should: (1) read the brief at brief_path, "
            "(2) read the files listed in target_files, "
            "(3) produce findings JSON matching the schema in the brief, "
            "(4) write the result to output_path. "
            "Then call: " + synth_cmd
        ),
    }


# ─── synthesis ───────────────────────────────────────────────────────────────


def _load_findings_dir(findings_dir: str) -> list[dict]:
    """Load all *.json files from findings_dir. Returns list of finding dicts."""
    d = Path(findings_dir)
    if not d.is_dir():
        raise FileNotFoundError(f"findings_dir not found: {findings_dir}")
    out: list[dict] = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            # Skip malformed files but note them.
            out.append({"persona": f.stem, "_load_error": str(e), "findings": [], "praise": [], "anti_confirmation": ""})
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _compute_verdict(findings: list[dict]) -> str:
    """Verdict from merged findings.

    - any p0 -> request_changes
    - any p1 -> request_changes
    - only p2 -> approve_with_nits
    - nothing -> approve
    """
    has_p0 = False
    has_p1 = False
    has_p2 = False
    for persona_result in findings:
        for f in (persona_result.get("findings") or []):
            sev = (f.get("severity") or "").lower()
            if sev == "p0":
                has_p0 = True
            elif sev == "p1":
                has_p1 = True
            elif sev == "p2":
                has_p2 = True
    if has_p0 or has_p1:
        return "request_changes"
    if has_p2:
        return "approve_with_nits"
    return "approve"


def _compute_summary(findings: list[dict]) -> dict:
    """Count findings by severity and praise count."""
    p0 = p1 = p2 = praise = findings_total = 0
    for persona_result in findings:
        for f in (persona_result.get("findings") or []):
            sev = (f.get("severity") or "").lower()
            if sev == "p0":
                p0 += 1
            elif sev == "p1":
                p1 += 1
            elif sev == "p2":
                p2 += 1
            findings_total += 1
        praise += len(persona_result.get("praise") or [])
    return {
        "findings_total": findings_total,
        "p0": p0,
        "p1": p1,
        "p2": p2,
        "praise": praise,
    }


def _flatten_findings(findings: list[dict]) -> list[dict]:
    """Flatten per-persona findings into a single list with persona tag."""
    out: list[dict] = []
    for persona_result in findings:
        persona_name = persona_result.get("persona", "unknown")
        for f in (persona_result.get("findings") or []):
            row = dict(f)
            row["persona"] = persona_name
            out.append(row)
    # Sort: p0 first, then p1, then p2.
    _SEV_ORDER = {"p0": 0, "p1": 1, "p2": 2}
    out.sort(key=lambda x: _SEV_ORDER.get((x.get("severity") or "").lower(), 99))
    return out


def _merge_anti_confirmation(findings: list[dict]) -> str:
    """Merge per-persona anti_confirmation strings."""
    parts: list[str] = []
    for persona_result in findings:
        ac = (persona_result.get("anti_confirmation") or "").strip()
        persona_name = persona_result.get("persona", "unknown")
        if ac and ac.lower() not in ("nothing beyond the focus list.", ""):
            parts.append(f"[{persona_name}] {ac}")
    if not parts:
        return "Nothing beyond focus lists."
    return " | ".join(parts)


def _build_pr_body_draft(
    target_name: str,
    verdict: str,
    summary: dict,
    flat_findings: list[dict],
    personas_run: list[str],
) -> str:
    lines = [
        "## Review verdict",
        "",
        f"skill-plus review: **{verdict}**",
        "",
        f"Personas: {', '.join(personas_run)}",
        f"Findings: {summary['findings_total']} total "
        f"(p0={summary['p0']}, p1={summary['p1']}, p2={summary['p2']})",
        f"Praise: {summary['praise']} observations",
        "",
    ]
    if not flat_findings:
        lines.append(f"No findings -- {target_name} passed all persona reviews.")
    else:
        lines.extend(["## Findings", ""])
        for f in flat_findings:
            sev = (f.get("severity") or "??").upper()
            file_ = f.get("file") or "?"
            line_ = f.get("line")
            issue = f.get("issue") or ""
            suggestion = f.get("suggestion") or ""
            persona = f.get("persona") or "?"
            loc = f"{file_}:{line_}" if line_ else file_
            lines.append(f"- **{sev}** [{persona}] `{loc}`: {issue}")
            if suggestion:
                lines.append(f"  - Suggestion: {suggestion}")
    lines.extend([
        "",
        "## Evidence",
        "",
        "Full review envelope attached (skill-plus review --synth-from).",
    ])
    return "\n".join(lines)


def _synth_envelope(
    findings_dir: str,
    target_path: Optional[str] = None,
) -> dict:
    """Build the final merged review envelope from a findings directory."""
    persona_results = _load_findings_dir(findings_dir)
    personas_run = [r.get("persona", Path(findings_dir).name) for r in persona_results]
    verdict = _compute_verdict(persona_results)
    summary = _compute_summary(persona_results)
    flat_findings = _flatten_findings(persona_results)
    anti_confirmation = _merge_anti_confirmation(persona_results)

    # Detect target if path supplied.
    target_block: dict = {"kind": "unknown", "name": "unknown", "path": target_path or ""}
    if target_path:
        kind, name, resolved = _detect_target(target_path)
        target_block = {"kind": kind, "name": name, "path": resolved}

    pr_body = _build_pr_body_draft(
        target_block["name"],
        verdict,
        summary,
        flat_findings,
        personas_run,
    )

    return {
        "envelope_version": ENVELOPE_VERSION,
        "verdict": verdict,
        "mode": "review",
        "target": target_block,
        "personas_run": personas_run,
        "summary": summary,
        "findings": flat_findings,
        "anti_confirmation": anti_confirmation,
        "pr_body_draft": pr_body,
        "cached_at": _now_iso(),  # noqa: F821 -- injected
    }


# ─── entrypoint ──────────────────────────────────────────────────────────────


def run(args, emit_fn) -> int:
    synth_from: Optional[str] = getattr(args, "synth_from", None)
    path_arg: str = getattr(args, "path", ".")
    personas_arg: Optional[str] = getattr(args, "personas", None)

    # ── synthesis mode ──────────────────────────────────────────────────────
    if synth_from:
        try:
            envelope = _synth_envelope(synth_from, target_path=path_arg)
        except FileNotFoundError as e:
            emit_fn({"ok": False, "error": str(e)})
            return 2
        emit_fn(envelope)
        return 0

    # ── dispatch mode ───────────────────────────────────────────────────────
    if not path_arg:
        emit_fn({"ok": False, "error": "path argument is required"})
        return 2

    # Resolve target.
    p = Path(path_arg).expanduser()
    if not p.exists():
        emit_fn({"ok": False, "error": f"path does not exist: {path_arg}"})
        return 2

    kind, name, resolved_path = _detect_target(str(p))

    # Resolve personas.
    if personas_arg:
        requested = [n.strip() for n in personas_arg.split(",") if n.strip()]
        personas: list[dict] = []
        for pname in requested:
            brief = resolve_persona_path(pname, resolved_path)
            if brief is None:
                emit_fn({
                    "ok": False,
                    "error": f"persona not found: {pname} "
                             f"(looked in plugin-local, ~/.agent-plus/review-personas/, and shipped defaults)",
                })
                return 2
            personas.append({"name": pname, "brief_path": str(brief)})
    else:
        personas = list_default_personas(resolved_path)
        # Also include any user-defined extras not in the default set.
        default_names = set(DEFAULT_PERSONAS)
        for extra_name in list_user_personas(resolved_path):
            if extra_name not in default_names:
                brief = resolve_persona_path(extra_name, resolved_path)
                if brief:
                    personas.append({"name": extra_name, "brief_path": str(brief)})

    if not personas:
        emit_fn({"ok": False, "error": "no personas found -- check skill-plus/personas/ exists"})
        return 2

    # Determine findings dir: use a temp-style path under ~/.agent-plus/review-findings/
    import datetime as _dt
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    findings_dir = str(
        Path.home() / ".agent-plus" / "review-findings" / f"{safe_name}-{ts}"
    )

    envelope = _build_dispatch_envelope(
        args=args,
        kind=kind,
        name=name,
        resolved_path=resolved_path,
        personas=personas,
        findings_dir=findings_dir,
    )
    emit_fn(envelope)
    return 0
