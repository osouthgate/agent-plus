"""skill-plus promote — move a project-local skill into a marketplace clone.

Lifecycle: project-local (.claude/skills/<name>/) → user marketplace clone
(<user>/agent-plus-skills) → community discovery.

Behaviour:
  - Default is dry-run; pass --no-dry-run to actually copy/move.
  - Validates the skill against a minimum-viable promotion contract:
    SKILL.md frontmatter description (>=10 chars), a when_to_use field,
    a "## Killer command" body section, and at least one bin launcher.
  - Writes an entry into <clone>/marketplace.json's `skills` list.
  - Removes the source unless --keep-local is set.

Stdlib only. Cross-platform.

Helpers (`_git_toplevel`, `config_path`, `_now_iso`) are injected by the bin
shell at module load time.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

# ─── frontmatter helpers (mirrors list.py) ────────────────────────────────────

_DESCRIPTION_KEYS = ("description",)
_WHEN_TO_USE_KEYS = ("when_to_use", "when-to-use", "whenToUse")
_VERSION_KEYS = ("version",)
_OBVIATES_KEYS = ("obviates",)

_KILLER_RE = re.compile(
    r"^\s{0,3}#{1,6}\s+killer\s+command\b", re.IGNORECASE | re.MULTILINE
)

# Match a body section header for "## Obviates" or "## When NOT to use this"
# (or "## Do NOT use this for" — the scaffold canonical name); we then consume
# the bullet list that follows.
_OBVIATES_BODY_RE = re.compile(
    r"^\s{0,3}#{1,6}\s+(?:obviates|when\s+not\s+to\s+use\s+this|do\s+not\s+use\s+this(?:\s+for)?)\b[^\n]*\n",
    re.IGNORECASE | re.MULTILINE,
)

_REPO_RE = re.compile(r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$")


def _parse_frontmatter(text: str) -> tuple[dict, str, str | None]:
    """Parse YAML-ish frontmatter. Scalars and (a, b) inline lists become strings;
    block-style lists (key: \\n  - a\\n  - b) become Python lists."""
    if not text.startswith("---"):
        return {}, text, "missing opening '---' frontmatter delimiter"
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text, "missing opening '---' frontmatter delimiter"
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text, "missing closing '---' frontmatter delimiter"
    fm_block_lines = lines[1:end_idx]
    body = "".join(lines[end_idx + 1:])
    fm: dict = {}

    i = 0
    while i < len(fm_block_lines):
        raw = fm_block_lines[i].rstrip("\n")
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        # Block-style list: empty value, followed by indented "- " lines.
        if val == "":
            items: list[str] = []
            while i < len(fm_block_lines):
                peek = fm_block_lines[i].rstrip("\n")
                stripped = peek.lstrip()
                if peek.startswith((" ", "\t")) and stripped.startswith("- "):
                    items.append(_strip_quotes(stripped[2:].strip()))
                    i += 1
                    continue
                break
            fm[key] = items if items else ""
            continue
        # Inline list: [a, b, "c d"]
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                fm[key] = []
            else:
                fm[key] = [_strip_quotes(x.strip()) for x in _split_inline_list(inner)]
            continue
        fm[key] = _strip_quotes(val)
    return fm, body, None


def _strip_quotes(val: str) -> str:
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        return val[1:-1]
    return val


def _split_inline_list(inner: str) -> list[str]:
    """Split on commas not inside quotes."""
    out: list[str] = []
    cur: list[str] = []
    quote: str | None = None
    for ch in inner:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            cur.append(ch)
            continue
        if ch == ",":
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return [x for x in out if x]


def _fm_get(fm: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in fm:
            return fm[k]
    return None


def _parse_obviates_from_body(body: str) -> list[str]:
    """Find a section header like '## Obviates' / '## When NOT to use this' /
    '## Do NOT use this for' and collect the bullets immediately following."""
    m = _OBVIATES_BODY_RE.search(body)
    if not m:
        return []
    rest = body[m.end():]
    items: list[str] = []
    for line in rest.splitlines():
        stripped = line.strip()
        if not stripped:
            # blank line — keep going (some authors leave blank lines between bullets)
            continue
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
            continue
        # any non-bullet, non-blank line ends the section
        break
    return items


# ─── contract validation ──────────────────────────────────────────────────────

def _validate_promotable(skill_dir: Path) -> tuple[list[str], dict]:
    """Return (missing_checks, info_dict). info_dict carries description, version,
    and obviates for downstream marketplace-entry construction when validation passes."""
    missing: list[str] = []
    info: dict = {
        "description": "",
        "name": skill_dir.name,
        "version": "0.1.0",
        "obviates": [],
    }

    skill_md = skill_dir / "SKILL.md"
    text = ""
    if not skill_md.exists():
        missing.append("SKILL.md")
    else:
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            missing.append("SKILL.md")

    fm, body, fm_err = _parse_frontmatter(text) if text else ({}, "", "no frontmatter")
    if fm_err:
        missing.append("frontmatter")

    desc_raw = _fm_get(fm, _DESCRIPTION_KEYS)
    desc = (desc_raw if isinstance(desc_raw, str) else "").strip()
    if len(desc) < 10:
        missing.append("description")
    info["description"] = desc

    when_raw = _fm_get(fm, _WHEN_TO_USE_KEYS)
    when = (when_raw if isinstance(when_raw, str) else "").strip()
    if not when:
        missing.append("when_to_use")

    if not _KILLER_RE.search(body):
        missing.append("killer_command_section")

    # Version: frontmatter, default to 0.1.0.
    version_raw = _fm_get(fm, _VERSION_KEYS)
    if isinstance(version_raw, str) and version_raw.strip():
        info["version"] = version_raw.strip()

    # Obviates: frontmatter list (block or inline), or body section bullets.
    obv_raw = _fm_get(fm, _OBVIATES_KEYS)
    obviates: list[str] = []
    if isinstance(obv_raw, list):
        obviates = [str(x).strip() for x in obv_raw if str(x).strip()]
    elif isinstance(obv_raw, str) and obv_raw.strip():
        # A bare string like "obviates: foo" — treat as single-item list.
        s = obv_raw.strip()
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1].strip()
            if inner:
                obviates = [_strip_quotes(x.strip()) for x in _split_inline_list(inner)]
        else:
            obviates = [s]
    if not obviates:
        obviates = _parse_obviates_from_body(body)

    info["obviates"] = obviates
    if not obviates:
        missing.append("obviates")

    name = skill_dir.name
    bin_dir = skill_dir / "bin"
    if not (
        (bin_dir / name).exists()
        or (bin_dir / f"{name}.py").exists()
        or (bin_dir / f"{name}.cmd").exists()
    ):
        missing.append("bin_launcher")

    return missing, info


# ─── marketplace clone resolution ─────────────────────────────────────────────

def _candidate_clone_paths(target_repo: str) -> list[Path]:
    leaf = target_repo.split("/")[-1]
    return [
        (Path.home() / "dev" / leaf).resolve(),
        (Path.home() / leaf).resolve(),
        (Path("C:/dev") / leaf).resolve(),
    ]


def _read_marketplace(clone: Path) -> tuple[dict | None, str | None]:
    mp_path = clone / "marketplace.json"
    if not mp_path.exists():
        return None, "marketplace.json not found in clone"
    try:
        data = json.loads(mp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"could not read marketplace.json: {exc}"
    if not isinstance(data, dict):
        return None, "marketplace.json is not a JSON object"
    return data, None


def _marketplace_identity(data: dict) -> str | None:
    """Return '<owner>/<name>' if both present in the marketplace.json."""
    owner = data.get("owner")
    name = data.get("name")
    if isinstance(owner, str) and isinstance(name, str) and owner and name:
        return f"{owner}/{name}"
    return None


def _count_files(directory: Path) -> int:
    return sum(1 for p in directory.rglob("*") if p.is_file())


# Top-level key order matching the live marketplace.json. Any unknown keys are
# preserved at the end in their original order.
_MARKETPLACE_KEY_ORDER = (
    "name", "owner", "version", "agent_plus_version", "surface", "skills",
)


def _ordered_marketplace(data: dict) -> dict:
    out: dict = {}
    for k in _MARKETPLACE_KEY_ORDER:
        if k in data:
            out[k] = data[k]
    for k, v in data.items():
        if k not in out:
            out[k] = v
    return out


# ─── entry point ──────────────────────────────────────────────────────────────

def run(args, emit_fn) -> int:  # noqa: C901 — linear control flow with early returns
    name = args.name

    # 1. Resolve source.
    top = _git_toplevel()  # injected
    project = (top if top is not None else Path.cwd()).resolve()
    source_skill = project / ".claude" / "skills" / name

    if not source_skill.is_dir():
        emit_fn({
            "ok": False,
            "error": "skill_not_found",
            "name": name,
            "path": str(source_skill),
        })
        return 2

    # 2. Validate against contract.
    missing, info = _validate_promotable(source_skill)
    if missing:
        emit_fn({
            "ok": False,
            "error": "skill_not_promotable",
            "name": name,
            "missing": missing,
            "hint": "run skill-plus list to see contract violations",
        })
        return 2

    # 3. Resolve destination repo.
    target_repo = args.to_marketplace
    if not target_repo:
        cfg_path = config_path()  # injected
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(cfg, dict):
                    target_repo = cfg.get("defaultMarketplace")
            except (OSError, json.JSONDecodeError):
                pass

    if not target_repo:
        emit_fn({
            "ok": False,
            "error": "no_destination",
            "hint": f"pass --to <user>/<repo> or set defaultMarketplace in {config_path()}",
        })
        return 2

    if not _REPO_RE.match(target_repo):
        emit_fn({
            "ok": False,
            "error": "invalid_destination_format",
            "to": target_repo,
            "hint": "expected '<user>/<repo>' (alphanumeric, dashes, underscores)",
        })
        return 2

    # Resolve clone path.
    if args.marketplace_path:
        clone = Path(args.marketplace_path).expanduser().resolve()
        if not clone.is_dir():
            emit_fn({
                "ok": False,
                "error": "marketplace_clone_not_found",
                "searched": [str(clone)],
                "hint": "pass --marketplace-path /local/clone",
            })
            return 2
    else:
        candidates = _candidate_clone_paths(target_repo)
        clone = next((c for c in candidates if c.is_dir()), None)
        if clone is None:
            emit_fn({
                "ok": False,
                "error": "marketplace_clone_not_found",
                "searched": [str(c) for c in candidates],
                "hint": "pass --marketplace-path /local/clone",
            })
            return 2

    # 4. Verify the clone is the right repo.
    mp_data, mp_err = _read_marketplace(clone)
    if mp_data is None:
        emit_fn({
            "ok": False,
            "error": "marketplace_malformed",
            "path": str(clone / "marketplace.json"),
            "detail": mp_err,
        })
        return 2

    found_id = _marketplace_identity(mp_data)
    if found_id is None or found_id.lower() != target_repo.lower():
        emit_fn({
            "ok": False,
            "error": "marketplace_mismatch",
            "expected": target_repo,
            "found": found_id,
            "path": str(clone / "marketplace.json"),
        })
        return 2

    # The live marketplace shape uses a `skills` array (not `plugins`).
    skills = mp_data.get("skills")
    if skills is None or not isinstance(skills, list):
        emit_fn({
            "ok": False,
            "error": "marketplace_malformed",
            "path": str(clone / "marketplace.json"),
            "detail": "'skills' field is missing or not a list",
        })
        return 2

    # 5. Plan the move.
    dest_skill = clone / name
    if dest_skill.exists():
        emit_fn({
            "ok": False,
            "error": "destination_exists",
            "path": str(dest_skill),
        })
        return 2

    # Refuse if entry with same name already in skills (idempotency check).
    for entry in skills:
        if isinstance(entry, dict) and entry.get("name") == name:
            emit_fn({
                "ok": False,
                "error": "destination_exists",
                "path": str(dest_skill),
                "detail": "marketplace.json already lists a skill with this name",
            })
            return 2

    marketplace_entry = {
        "name": name,
        "version": info["version"],
        "path": f"{name}/",
        "obviates": list(info["obviates"]),
    }

    # 6. Dry-run (default).
    if args.dry_run:
        files_to_copy = [
            str(p.relative_to(source_skill))
            for p in sorted(source_skill.rglob("*"))
            if p.is_file()
        ]
        emit_fn({
            "ok": True,
            "dryRun": True,
            "name": name,
            "source": str(source_skill),
            "dest": str(dest_skill),
            "marketplaceEntryToAdd": marketplace_entry,
            "marketplaceJsonPath": str(clone / "marketplace.json"),
            "filesToCopy": files_to_copy,
            "keepLocal": bool(args.keep_local),
        })
        return 0

    # 7. Real run.
    shutil.copytree(str(source_skill), str(dest_skill))
    files_copied = _count_files(dest_skill)

    skills.append(marketplace_entry)
    mp_path = clone / "marketplace.json"
    mp_data["skills"] = skills
    ordered = _ordered_marketplace(mp_data)
    mp_path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")

    source_removed = False
    if not args.keep_local:
        shutil.rmtree(str(source_skill))
        source_removed = True

    emit_fn({
        "ok": True,
        "dryRun": False,
        "action": "promoted",
        "name": name,
        "source": str(source_skill),
        "dest": str(dest_skill),
        "filesCopied": files_copied,
        "sourceRemoved": source_removed,
        "marketplaceUpdated": True,
        "marketplaceJsonPath": str(mp_path),
        "promotedAt": _now_iso(),  # injected
    })
    return 0
