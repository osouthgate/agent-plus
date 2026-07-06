#!/usr/bin/env python3
"""Rebuild eval git fixtures. Stdlib only.

Strategy (per fixture name, in order):
  1. Real source at REAL_REPO_ROOTS[name] — selective copy, fresh git, meaningful 2nd commit.
  2. Synthetic fallback — minimal tree, two commits.

A `.fixture-source` file is written in each fixture dir so tests know which mode was used.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"

# Candidate real repo paths (first that exists wins).
REAL_REPO_ROOTS: dict[str, list[Path]] = {
    "rainshift":     [Path("C:/dev/rainshift"),     Path("/mnt/c/dev/rainshift")],
    "tinker-tailor": [Path("C:/dev/Tinker-Tailor"), Path("/mnt/c/dev/Tinker-Tailor")],
    # osdb: no real repo known — always synthetic
}

# Dirs excluded wholesale when copying a real repo.
EXCLUDE_DIRS = {
    "node_modules", ".next", ".git", ".claude", ".agent-plus",
    "dist", "build", "out", ".cache", "coverage", ".turbo",
    "e2e", "recordings",
}

# Filenames excluded regardless of location.
EXCLUDE_FILES = {
    "pnpm-lock.yaml", "yarn.lock", "package-lock.json",
    ".env", ".env.local", ".env.production", ".env.development",
    "tsconfig.tsbuildinfo",
}

# Extensions excluded (binary / secret / generated).
EXCLUDE_EXTS = {
    ".env",
    ".docx", ".pptx", ".xlsx", ".xls",
    ".pdf",
    ".zip", ".tar", ".gz",
    ".mp4", ".webm", ".mov",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".eot",
    ".trace",
}

# ── secret scrub ──────────────────────────────────────────────────────────────
#
# Real repos contain secret-shaped strings in ordinary text files (a 2026-07-03
# review found a live Langfuse sk-lf-/pk-lf- pair copied verbatim into
# evals/fixtures/rainshift/ by this script). Every text file is therefore
# scrubbed after the copy and BEFORE the fixture's git history is created.
#
# _SECRET_PATTERNS + scrub_text are DUPLICATED from the canonical copy in
# skill-plus/bin/skill-plus (its "secret redaction" section). That bin script
# has no .py extension and lives in another plugin's tree, so it cannot be
# imported from here; the repo's accepted idiom is to duplicate the list with
# a provenance note (skill-plus/bin/_subcommands/scaffold.py's generated-skill
# template does the same). Keep the copies in sync until centralized.
#
# List order matters: the value-consuming header-name pattern runs first, so
# it swallows the whole "Name: <value>" span before the narrower token-shaped
# patterns below get a chance to see a partial value.

_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"\b(Authorization|Proxy-Authorization|Cookie|Set-Cookie|X-Api-Key|"
            r"X-Auth-Token|Private-Token|CF-Access-Client-Secret|"
            r"CF-Access-Client-Id|X-Amz-Security-Token)\s*:\s*[^\r\n'\"]+",
            re.IGNORECASE,
        ),
        r"\g<1>: [REDACTED]",
    ),
    (
        re.compile(
            r"\b([A-Za-z][A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|"
            r"API[-_]?KEY|ACCESS[-_]?KEY|PRIVATE[-_]?KEY|AUTH))\s*=\s*[^\s\"']+",
            re.IGNORECASE,
        ),
        r"\g<1>=[REDACTED]",
    ),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED]"),
    (re.compile(r"gh[ousr]_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED]"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"sk-lf-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"pk-lf-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    # webhook URLs (Slack/Discord) — token in URL path
    (re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+"), "[REDACTED]"),
    (re.compile(r"https://discord(?:app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9._-]+"), "[REDACTED]"),
    (re.compile(r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"sk-or-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"sbp_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"sntrys_[A-Za-z0-9._-]{20,}"), "[REDACTED]"),
    (re.compile(r"nfp_[A-Za-z0-9_-]{20,}"), "[REDACTED]"),  # Netlify personal access token
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "[REDACTED]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED]"),
    # Laravel Sanctum / other opaque "<id>|<hash>" bearer tokens (e.g. "4|<40-char-hash>").
    (re.compile(r"\b\d+\|[A-Za-z0-9]{20,}\b"), "[REDACTED]"),
    # Widened: any non-space/quote token of 8+ chars, not just the old
    # [A-Za-z0-9._-]{20,} charset -- that excluded "|" (Sanctum tokens) and
    # missed short-but-real tokens under 20 chars.
    (re.compile(r"Bearer\s+[^\s\"']{8,}", re.IGNORECASE), "[REDACTED]"),
    # connection strings
    (re.compile(r"(postgres|mysql|redis|mongodb(?:\+srv)?)://[^\s'\"]+@", re.IGNORECASE), "[REDACTED]"),
    # argv-style secret pairs
    (re.compile(r"--(?:password|token|secret|api[-_]?key)[= ]\S+", re.IGNORECASE), "[REDACTED]"),
]


def scrub_text(s: str | None) -> str | None:
    if s is None:
        return None
    out = s
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _scrub_tree(dst: Path) -> int:
    """Scrub secret-shaped strings from every text file under dst, in place.

    Must run BEFORE _git_init_two_commits so the scrubbed content is what gets
    committed into the fixture's history. Files that do not decode as utf-8
    are treated as binary and skipped byte-identical. Rewrites via write_bytes
    only when the scrub changed something, so line endings and unchanged files
    stay untouched. Returns the number of files rewritten.
    """
    changed = 0
    for p in sorted(dst.rglob("*")):
        if not p.is_file():
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue  # binary -> leave byte-identical
        scrubbed = scrub_text(text)
        if scrubbed is not None and scrubbed != text:
            p.write_bytes(scrubbed.encode("utf-8"))
            changed += 1
    return changed


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    e = os.environ.copy()
    if env:
        e.update(env)
    subprocess.run(cmd, cwd=str(cwd), check=True, env=e,
                   capture_output=True)


def _rm_tree(path: Path) -> None:
    if not path.is_dir():
        return
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(path)],
                       check=False, capture_output=True)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    else:
        shutil.rmtree(path)


def _find_real_root(name: str) -> Path | None:
    for p in REAL_REPO_ROOTS.get(name, []):
        if p.is_dir():
            return p
    return None


def _copytree_selective(src: Path, dst: Path) -> int:
    """Copy src → dst excluding heavy / secret dirs and files. Returns file count."""
    def _ignore(directory: str, contents: list[str]) -> set[str]:
        skip: set[str] = set()
        for name in contents:
            if name in EXCLUDE_DIRS or name in EXCLUDE_FILES:
                skip.add(name)
                continue
            # Exclude anything that starts with .next (e.g. .next-walkthrough, .next-cache)
            if name.startswith(".next"):
                skip.add(name)
                continue
            full = Path(directory) / name
            if full.is_file():
                if name.startswith(".env"):
                    skip.add(name)
                    continue
                if full.suffix.lower() in EXCLUDE_EXTS:
                    skip.add(name)
                    continue
        return skip

    shutil.copytree(src, dst, ignore=_ignore, dirs_exist_ok=False)
    return sum(1 for _ in dst.rglob("*") if _.is_file())


def _find_editable_ts(dst: Path) -> Path | None:
    """Find a .ts/.tsx source file suitable for a second-commit edit (not declaration files)."""
    for pattern in ("**/*.ts", "**/*.tsx"):
        for p in sorted(dst.glob(pattern)):
            if p.name.endswith(".d.ts"):
                continue
            rel = p.relative_to(dst)
            parts = rel.parts
            if any(d in EXCLUDE_DIRS for d in parts):
                continue
            return p
    return None


def _git_init_two_commits(dst: Path, name: str, *, real: bool) -> None:
    """Init a fresh git repo in dst with two commits.

    First commit: all files.
    Second commit: a meaningful source-file edit so diff-summary shows a real change.
    """
    _run(["git", "init"], cwd=dst)
    git_env = {"GIT_AUTHOR_NAME": "eval fixtures", "GIT_AUTHOR_EMAIL": "eval@fixture.local",
               "GIT_COMMITTER_NAME": "eval fixtures", "GIT_COMMITTER_EMAIL": "eval@fixture.local"}
    _run(["git", "config", "user.email", "eval@fixture.local"], cwd=dst)
    _run(["git", "config", "user.name", "eval fixtures"], cwd=dst)
    _run(["git", "add", "."], cwd=dst, env=git_env)
    _run(["git", "commit", "-m", f"bootstrap: {name}"], cwd=dst, env=git_env)

    if real:
        # Second commit: edit a real TypeScript file (shows logic/config role in diff-summary).
        ts_file = _find_editable_ts(dst)
        if ts_file:
            existing = ts_file.read_text(encoding="utf-8", errors="replace")
            ts_file.write_text(existing + "\n// @eval-fixture-marker\n", encoding="utf-8")
            _run(["git", "add", str(ts_file.relative_to(dst))], cwd=dst, env=git_env)
            _run(["git", "commit", "-m", "chore: eval fixture second commit (ts)"], cwd=dst, env=git_env)
        else:
            # Fallback: README
            readme = dst / "README.md"
            if not readme.is_file():
                readme.write_text(f"# {name}\n", encoding="utf-8")
            readme.write_text(readme.read_text(encoding="utf-8") + "\nsecond commit\n", encoding="utf-8")
            _run(["git", "add", "README.md"], cwd=dst, env=git_env)
            _run(["git", "commit", "-m", "docs: eval fixture second commit"], cwd=dst, env=git_env)
    else:
        readme = dst / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\nsecond commit\n", encoding="utf-8")
        _run(["git", "add", "README.md"], cwd=dst, env=git_env)
        _run(["git", "commit", "-m", "second"], cwd=dst, env=git_env)


def _write_source_marker(dst: Path, source: str) -> None:
    (dst / ".fixture-source").write_text(source, encoding="utf-8")


def _build_real_fixture(name: str, src: Path) -> bool:
    dst = FIX / name
    if dst.is_dir():
        _rm_tree(dst)
    # Do NOT mkdir here — _copytree_selective / shutil.copytree creates dst.
    try:
        n = _copytree_selective(src, dst)
        scrubbed = _scrub_tree(dst)
        if scrubbed:
            print(f"  {name}: scrubbed secret-shaped strings in {scrubbed} file(s)",
                  file=sys.stderr)
        _write_source_marker(dst, f"real:{src}")
        _git_init_two_commits(dst, name, real=True)
        print(f"  {name}: copied {n} files from {src}", file=sys.stderr)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  {name}: real copy failed ({exc}), falling back to synthetic", file=sys.stderr)
        _rm_tree(dst)
        return False


def _build_synthetic_fixture(name: str, files: dict[str, str]) -> None:
    dst = FIX / name
    if dst.is_dir():
        _rm_tree(dst)
    dst.mkdir(parents=True)
    for rel, content in files.items():
        p = dst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _write_source_marker(dst, "synthetic")
    # Synthetic content is authored above (no secrets by construction), but
    # running it through the same scrub as real repos costs nothing and keeps
    # one invariant: nothing under evals/fixtures/ gets committed unscrubbed.
    scrubbed = _scrub_tree(dst)
    if scrubbed:
        print(f"  {name}: scrubbed secret-shaped strings in {scrubbed} file(s)",
              file=sys.stderr)
    _git_init_two_commits(dst, name, real=False)
    print(f"  {name}: synthetic fixture written", file=sys.stderr)


def main() -> int:
    FIX.mkdir(parents=True, exist_ok=True)

    # ── rainshift ──────────────────────────────────────────────────────────────
    src = _find_real_root("rainshift")
    if src is None or not _build_real_fixture("rainshift", src):
        _build_synthetic_fixture(
            "rainshift",
            {
                "package.json": json.dumps(
                    {"name": "eval-rainshift", "private": True,
                     "dependencies": {"next": "14.2.0"},
                     "devDependencies": {"typescript": "5.3.3"}},
                    indent=2,
                ),
                "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
                "README.md": "# eval fixture rainshift\n",
                "index.ts": "export const App = () => null;\n",
            },
        )

    # ── tinker-tailor ──────────────────────────────────────────────────────────
    src = _find_real_root("tinker-tailor")
    if src is None or not _build_real_fixture("tinker-tailor", src):
        _build_synthetic_fixture(
            "tinker-tailor",
            {
                "package.json": json.dumps({"name": "eval-tinker", "private": True}, indent=2),
                "README.md": "# eval fixture tinker-tailor\n",
                "app.tsx": "export function Page() { return null; }\n",
            },
        )

    # ── osdb (always synthetic — no real repo) ─────────────────────────────────
    _build_synthetic_fixture(
        "osdb",
        {
            "package.json": json.dumps(
                {"name": "eval-osdb", "private": True,
                 "devDependencies": {"typescript": "5.3.3", "vitest": "1.2.0"}},
                indent=2,
            ),
            "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
            "README.md": "# eval fixture osdb\n",
            "index.ts": "export const x = 1;\n",
        },
    )

    print(f"\nFixtures written under {FIX}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
