"""skill-plus inquire — universal tool inquiry + plugin/skill auditor (Phase A).

Two modes share one probe pipeline:

  skill-plus inquire <tool>                       # generator: probe + scaffold suggestion
  skill-plus inquire <plugin-or-skill> --audit    # auditor: probe an existing target

Each Q (Q1..Q7) runs across every available source class (cli, plugin, web,
openapi, repo, transcripts). Sources stack — at least 2 sources required for
non-`unknown` confidence. Web probe uses DuckDuckGo HTML (D1). Cache lives at
~/.agent-plus/inquire-cache/<tool>.json with 7-day TTL (D2).

Audit-mode targets are auto-detected:
  - **plugin**: directory contains `.claude-plugin/plugin.json`
  - **skill**:  directory contains `SKILL.md` with frontmatter; subcommand
    bin discovered via the `Bash(<name>:*)` allowed-tools entry.

Transcripts (claude_code/codex/cursor/gstack JSONL) are a first-class source.
Disabled by passing `--no-transcripts` or env `AGENT_PLUS_INQUIRE_NO_TRANSCRIPTS=1`.

ENVELOPE_VERSION contract: this is an ADDITIVE-ONLY field set. v1.1 envelopes
add `usage_signal`, `usage_clusters`, `promotions`, per-Q `usage_evidence`,
per-Q `promotion_kind`/`priority`, and the `well_used` verdict — but NEVER
remove or change semantics of v1.0 fields. Any v1.0 consumer must keep
working against v1.1 envelopes (regression-tested in `test_inquire.py`).

Stdlib only — no third-party libraries. Subprocess timeouts everywhere.
MSYS-aware: subprocess uses list form (no shell=True) and we set
MSYS_NO_PATHCONV=1 for any external CLI call carrying a leading-slash arg
(matches the v0.15.6 F2 fix discipline).
"""
from __future__ import annotations

import datetime as _dt
import html
import html.parser
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

# Helpers (_now_iso, _tool_meta) injected by bin/skill-plus loader.

# ─── constants ────────────────────────────────────────────────────────────────

CACHE_TTL_SECONDS = 7 * 24 * 3600
CLI_PROBE_TIMEOUT = 10
WEB_PROBE_TIMEOUT = 5
ENVELOPE_VERSION = "1.1"
PRIORITY_HIGH_MIN = 10     # Tier 1 count >=10 with capability gap = high-priority promotion.
PRIORITY_LOW_MAX = 3       # Tier 1 count <3 with capability gap = low-priority (probably theoretical).
MAX_INLINE_TUPLES = 5000   # In-memory cap for cluster pipeline (NOT persisted — see usage_signal field stripping).

# Q metadata: (id, label, applies_always)
QUESTIONS: list[tuple[str, str, bool]] = [
    ("Q1", "errors_surface", True),
    ("Q2", "lookup_keys", True),
    ("Q3", "wait_async", False),       # only applies if tool has async mutations
    ("Q4", "json_output", True),
    ("Q5", "stays_in_lane", False),    # only applies if multiple data-source paths exist
    ("Q6", "strips_secrets", False),   # only applies if tool handles secrets
    ("Q7", "tool_envelope", True),     # always for plugins; n/a for raw tools
]

# Q1 maturity ladder rungs.
Q1_LADDER = {
    1: "Unstructured log scrape (regex over text)",
    2: "Filter on existing structured field (e.g. level=error)",
    3: "Per-finding source-location records (path/line/level/title/message)",
    4: "Platform-aware hybrid: pick best source per signal class",
}

# Q3 maturity ladder rungs.
Q3_LADDER = {
    1: "Fire-and-forget; user has to poll",
    2: "--wait blocks until done, returns final state",
    3: "--wait streams progress events / status changes",
    4: "--wait emits structured per-step status",
}

# Ladder registry (R3 fix): one entry per Q with a maturity ladder.
# Maps q_id to (ladder_dict, label, default_max_achievable). Adding a Q4-Q7
# ladder is now a single registry entry instead of a copy-paste block.
LADDER_REGISTRY: dict = {
    "Q1": (Q1_LADDER, "Q1 errors_surface", 3),
    "Q3": (Q3_LADDER, "Q3 wait_async", 3),
}

# Per-tool max-achievable overrides (R1 fix). Populated when a known
# platform limit prevents reaching the default top rung. Vercel cannot
# reach Q1 Level 3 because the Vercel API does not expose per-finding
# source-location records (file/line/level/title/message). Recommending
# Level 3 to vercel-remote authors would chase an impossible PR.
MAX_ACHIEVABLE_OVERRIDES: dict = {
    "vercel-remote": {"Q1": 2},
}

# Per-Q confidence ceilings (R9 fix). Some probes deliberately cap their
# behavioral coverage for safety reasons. Q6 (strips_secrets) skips the
# behavioral CLI probe because it would touch real auth state. Confidence
# is structurally capped at "medium" because high requires a behavioral
# corroboration source we never run.
CONFIDENCE_CEILINGS: dict = {"Q6": "medium"}
_CONFIDENCE_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _max_achievable(q_id: str, target: dict, default: int) -> int:
    """Look up per-tool override for a Q's max achievable rung. Falls back
    to the default if no override applies. See R1 fix."""
    tool = target.get("tool") or target.get("name") or ""
    overrides = MAX_ACHIEVABLE_OVERRIDES.get(tool, {})
    return overrides.get(q_id, default)


def _platform_limit_note(q_id: str, target: dict, current: int, max_level: int) -> Optional[str]:
    """When current_level == max_achievable_level, populate a human-
    readable note explaining the ceiling. See R2 fix."""
    if current < max_level:
        return None
    tool = target.get("tool") or target.get("name") or ""
    if tool == "vercel-remote" and q_id == "Q1":
        return ("Vercel's API does not expose per-finding source-location "
                "records (file/line/level/title/message). Plugin is at the "
                "platform ceiling for this question.")
    return f"Plugin is at the platform ceiling for this question (Level {max_level})."


def _cap_confidence(q_id: str, confidence: str) -> str:
    """Cap confidence at the per-Q ceiling. See R9 fix."""
    ceiling = CONFIDENCE_CEILINGS.get(q_id)
    if not ceiling:
        return confidence
    if _CONFIDENCE_RANK.get(confidence, 0) > _CONFIDENCE_RANK.get(ceiling, 3):
        return ceiling
    return confidence


# ─── priority calc (Gate A.3) ─────────────────────────────────────────────────


def _priority(promotion_kind: str, tier1_count: int) -> str:
    """Pure mapping from (promotion_kind, tier1_count) -> priority string.

    Rules (per delta plan §"Priority calculation"):
      high   = (gap or misaligned) AND tier1_count >= 10
      medium = gap AND 3 <= tier1_count < 10, OR misaligned AND tier1_count >= 10
      low    = gap AND tier1_count < 3
      n/a    = aligned
    """
    if promotion_kind == "aligned":
        return "n/a"
    if promotion_kind == "missing":
        if tier1_count >= PRIORITY_HIGH_MIN:
            return "high"
        if tier1_count >= PRIORITY_LOW_MAX:
            return "medium"
        return "low"
    if promotion_kind == "misaligned":
        if tier1_count >= PRIORITY_HIGH_MIN:
            return "high"
        # Misaligned with low usage is medium — even modest signal that the
        # canned doesn't fit is worth surfacing because the fix is small.
        return "medium"
    # Unknown promotion_kind — be conservative.
    return "low"


# ─── skill frontmatter reader (Gate A.3) ──────────────────────────────────────

# Minimal YAML-frontmatter reader. Supports:
#   ---
#   key: scalar
#   key: "quoted scalar"
#   key: value with spaces
#   ---
# Anything more exotic (block mappings, lists, multiline) is ignored — we
# only need name/description/allowed-tools for skill auditing.
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<body>.*?)\n---\s*(?:\n|$)", re.DOTALL
)


def _read_skill_frontmatter(skill_md_path: str) -> Optional[dict]:
    """Parse a SKILL.md's --- frontmatter block. Returns dict or None."""
    try:
        text = Path(skill_md_path).read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    body = m.group("body")
    out: dict[str, str] = {}
    for line in body.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        # Don't try to parse nested mappings — only top-level key: value.
        if line.startswith(" ") or line.startswith("\t"):
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        # Strip a single matched pair of surrounding quotes.
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


def _skill_bin_from_allowed_tools(allowed_tools: str, skill_md_dir: Path,
                                  skill_name: str) -> Optional[Path]:
    """Given an allowed-tools string like
    `Bash(.claude/skills/loamdb-db/bin/loamdb-db:*) Bash(other:*)`,
    locate the bin directory we should walk for subcommand discovery.

    Resolution order:
      1. parse first `Bash(<path>:*)` entry; if path resolves under
         <skill-md-dir>, use its parent dir (the bin/ folder containing
         the launcher).
      2. fallback: <skill-md-dir>/bin/<skill_name>.
      3. fallback: <skill-md-dir>/bin/<bare-name-from-allowed-tools>.
    """
    bash_re = re.compile(r"Bash\(([^):]+):\*\)")
    candidates: list[Path] = []
    for m in bash_re.finditer(allowed_tools or ""):
        raw = m.group(1).strip()
        # Bare name (e.g., "skill-plus") — try <dir>/bin/<bare>.
        if "/" not in raw and "\\" not in raw:
            candidates.append(skill_md_dir / "bin" / raw)
            continue
        # Path-like (e.g., ".claude/skills/loamdb-db/bin/loamdb-db").
        # The launcher is the FILE; we want the dir that holds subcommand bins.
        rel = Path(raw)
        # Strip a leading "./" or absolute marker.
        if rel.is_absolute():
            launcher = rel
        else:
            # Relative to the skill md's parent grandparent (repo root):
            # try a few anchor points.
            launcher = skill_md_dir.parent.parent.parent / rel
            # Also try as if rel is rooted at skill_md_dir.parent.parent.
            alt = skill_md_dir.parent.parent / rel
            candidates.append(launcher.parent)
            candidates.append(alt.parent)
            continue
        candidates.append(launcher.parent)
    # Fallbacks.
    if skill_name:
        candidates.append(skill_md_dir / "bin" / skill_name)
        candidates.append(skill_md_dir / "bin")
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _detect_target_kind(path_or_name: str) -> tuple[str, Optional[str]]:
    """Auto-detect whether `path_or_name` points at a plugin or a skill.

    Returns (kind, resolved_path) where kind is "plugin"|"skill"|"unknown".
    """
    if not path_or_name:
        return "unknown", None
    p = Path(path_or_name)
    if p.is_dir():
        if (p / ".claude-plugin" / "plugin.json").exists():
            return "plugin", str(p)
        if (p / "SKILL.md").exists():
            return "skill", str(p)
        return "unknown", str(p)
    if p.is_file() and p.name == "SKILL.md":
        return "skill", str(p.parent)
    if p.is_file() and p.name == "plugin.json":
        return "plugin", str(p.parent.parent)
    return "unknown", None


def _resolve_skill_by_name(name: str) -> Optional[str]:
    """Search ~/.claude/skills/<name>/SKILL.md and CWD-relative locations."""
    candidates = [
        Path.home() / ".claude" / "skills" / name / "SKILL.md",
        Path.cwd() / ".claude" / "skills" / name / "SKILL.md",
        Path.cwd() / name / "SKILL.md",
        Path.cwd() / name / "skills" / name / "SKILL.md",
    ]
    for c in candidates:
        if c.exists():
            return str(c.parent)
    return None


# ─── cache ────────────────────────────────────────────────────────────────────


def cache_dir() -> Path:
    return Path.home() / ".agent-plus" / "inquire-cache"


def cache_path_for(tool: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", tool)
    return cache_dir() / f"{safe}.json"


def cache_load(tool: str) -> Optional[dict]:
    p = cache_path_for(tool)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    expires = data.get("expires_at")
    if not expires:
        return None
    try:
        exp_dt = _dt.datetime.strptime(expires, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc
        )
    except (TypeError, ValueError):
        return None
    if _dt.datetime.now(_dt.timezone.utc) > exp_dt:
        return None
    return data


def cache_store(tool: str, envelope: dict) -> None:
    p = cache_path_for(tool)
    p.parent.mkdir(parents=True, exist_ok=True)
    now = _dt.datetime.now(_dt.timezone.utc)  # noqa: F841
    cached_at = _now_iso()  # noqa: F821 — injected
    expires = (
        _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=CACHE_TTL_SECONDS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "tool": tool,
        "cached_at": cached_at,
        "expires_at": expires,
        "envelope_version": ENVELOPE_VERSION,
        "result": envelope,
    }
    p.write_text(json.dumps(record, indent=2), encoding="utf-8")


def cache_clear() -> int:
    d = cache_dir()
    if not d.is_dir():
        return 0
    n = 0
    for f in d.glob("*.json"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


# ─── subprocess helper ────────────────────────────────────────────────────────


def run_cli(argv: list[str], *, timeout: int = CLI_PROBE_TIMEOUT) -> tuple[int, str, str]:
    """Run a CLI command, returning (rc, stdout, stderr). Never raises."""
    env = os.environ.copy()
    # MSYS-aware: stop Git Bash from rewriting leading-slash args.
    env["MSYS_NO_PATHCONV"] = "1"
    try:
        r = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, env=env,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except (OSError, subprocess.SubprocessError) as e:
        return 127, "", str(e)


def cli_on_path(name: str) -> Optional[str]:
    return shutil.which(name)


# ─── DuckDuckGo HTML parser ───────────────────────────────────────────────────


class _DDGParser(html.parser.HTMLParser):
    """Pulls result snippets from DuckDuckGo HTML response."""

    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._cur: list[str] = []
        self.snippets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "a":
            attrd = dict(attrs)
            cls = (attrd.get("class") or "").lower()
            if "result__snippet" in cls or "result__a" in cls:
                self._capture = True
                self._cur = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture:
            text = "".join(self._cur).strip()
            if text:
                self.snippets.append(html.unescape(text))
            self._capture = False
            self._cur = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._cur.append(data)


def web_search(query: str, *, timeout: int = WEB_PROBE_TIMEOUT) -> list[str]:
    """Run a DuckDuckGo HTML query, return up to 3 snippet strings.

    Returns empty list on any failure (rate limit, network, parse error).
    """
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "skill-plus-inquire/0.4.0 (stdlib)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError):
        return []
    p = _DDGParser()
    try:
        p.feed(body)
    except Exception:  # noqa: BLE001 — fail-soft on parse errors
        return []
    return p.snippets[:3]


# ─── per-Q probes ─────────────────────────────────────────────────────────────


def _make_evidence(source: str, detail: str) -> dict:
    return {"source": source, "detail": detail}


# Q1 — errors_surface --------------------------------------------------------


def probe_q1_cli(target: dict) -> Optional[dict]:
    cli = target.get("cli")
    if not cli or not cli_on_path(cli):
        return None
    rc, out, err = run_cli([cli, "--help"])
    text = (out + "\n" + err).lower()
    annotations_hit = False
    structured_hit = False
    log_grep_hit = False
    # GitHub-style API introspection — only if this is the gh CLI.
    if cli == "gh":
        rc2, out2, _ = run_cli(
            ["gh", "api", "graphql", "-f",
             'query={ __type(name: "CheckAnnotation") { fields { name } } }']
        )
        if rc2 == 0 and "fields" in out2 and "null" not in out2:
            annotations_hit = True
    if any(t in text for t in ["annotations", "check-runs", "/annotations"]):
        annotations_hit = True
    if any(t in text for t in ["--errors-only", "level=error", "--level"]):
        structured_hit = True
    if any(t in text for t in ["logs", "log"]):
        log_grep_hit = True
    if annotations_hit:
        return {"answer": "ok", "level": 3,
                "evidence": _make_evidence("cli", f"{cli} exposes structured annotations / source-location records")}
    if structured_hit:
        return {"answer": "improvable", "level": 2,
                "evidence": _make_evidence("cli", f"{cli} --help mentions error-level filter (Level 2)")}
    if log_grep_hit:
        return {"answer": "improvable", "level": 1,
                "evidence": _make_evidence("cli", f"{cli} only exposes raw log surface (Level 1)")}
    return {"answer": "unknown", "level": None,
            "evidence": _make_evidence("cli", f"{cli} --help did not mention errors/logs/annotations")}


def probe_q1_plugin(target: dict) -> Optional[dict]:
    p = target.get("plugin_path")
    if not p:
        return None
    bin_text = _read_plugin_bins(p)
    if bin_text is None:
        return None
    # R6 fix: tightened L3 detection. The path string alone (e.g. in a
    # comment or docstring) is not enough — require a co-located call-site
    # pattern so we don't over-credit plugins that just *mention* the
    # annotations endpoint. `annotations\s*=` removed entirely (matches
    # `annotations = []` initializations).
    has_annotations_path = bool(
        re.search(r"/check-runs/[^/]+/annotations", bin_text, re.IGNORECASE)
    )
    has_api_call_site = bool(
        re.search(
            r"urllib\.request\.\w+|gh\s*api|_api\(|graphql|requests?\.(?:get|post)",
            bin_text,
            re.IGNORECASE,
        )
    )
    if has_annotations_path and has_api_call_site:
        return {"answer": "ok", "level": 3,
                "evidence": _make_evidence(
                    "plugin",
                    "plugin makes API calls to /check-runs/{id}/annotations (Level 3)"
                )}
    if re.search(r"--errors[-_]only|level\s*==?\s*['\"]?error|entry\[['\"]level['\"]\]",
                 bin_text, re.IGNORECASE):
        return {"answer": "improvable", "level": 2,
                "evidence": _make_evidence("plugin", "plugin filters on a structured level field (Level 2)")}
    if re.search(r"\\b\(error\|failed\|fatal\)\\b|re\.findall.*error|grep.*error",
                 bin_text, re.IGNORECASE):
        return {"answer": "improvable", "level": 1,
                "evidence": _make_evidence("plugin", "plugin scrapes log text via regex (Level 1)")}
    return {"answer": "unknown", "level": None,
            "evidence": _make_evidence("plugin", "no error-surface pattern found in plugin bin")}


def probe_q1_web(target: dict) -> Optional[dict]:
    tool = target.get("tool")
    if not tool:
        return None
    snippets = web_search(f"{tool} structured errors api annotations")
    if not snippets:
        return None
    joined = " ".join(snippets).lower()
    if "annotation" in joined or "check-run" in joined:
        return {"answer": "ok", "level": 3,
                "evidence": _make_evidence("web", snippets[0][:200])}
    return {"answer": "unknown", "level": None,
            "evidence": _make_evidence("web", snippets[0][:200])}


# Q2 — lookup_keys -----------------------------------------------------------


def probe_q2_cli(target: dict) -> Optional[dict]:
    cli = target.get("cli")
    if not cli or not cli_on_path(cli):
        return None
    rc, out, err = run_cli([cli, "--help"])
    text = (out + "\n" + err).lower()
    if any(t in text for t in ["--by-name", "resolve", "<name>", "<branch>", "lookup"]):
        return {"answer": "ok",
                "evidence": _make_evidence("cli", f"{cli} --help mentions name/branch lookup")}
    return {"answer": "unknown",
            "evidence": _make_evidence("cli", f"{cli} --help shows only id-based access")}


def probe_q2_plugin(target: dict) -> Optional[dict]:
    p = target.get("plugin_path")
    if not p:
        return None
    bin_text = _read_plugin_bins(p)
    if bin_text is None:
        return None
    if re.search(r"_resolve|_is_int|by[-_]name|resolve_by", bin_text, re.IGNORECASE):
        return {"answer": "ok",
                "evidence": _make_evidence("plugin", "plugin defines resolver helpers (_resolve / by-name)")}
    return {"answer": "improvable",
            "evidence": _make_evidence("plugin", "no resolver helpers found — id-only?")}


def probe_q2_web(target: dict) -> Optional[dict]:
    tool = target.get("tool")
    if not tool:
        return None
    snippets = web_search(f"{tool} CLI resolve by name")
    if not snippets:
        return None
    joined = " ".join(snippets).lower()
    if "by name" in joined or "resolve" in joined or "lookup" in joined:
        return {"answer": "ok",
                "evidence": _make_evidence("web", snippets[0][:200])}
    return {"answer": "unknown",
            "evidence": _make_evidence("web", snippets[0][:200])}


# Q3 — wait_async ------------------------------------------------------------


def probe_q3_cli(target: dict) -> Optional[dict]:
    cli = target.get("cli")
    if not cli or not cli_on_path(cli):
        return None
    rc, out, err = run_cli([cli, "--help"])
    text = (out + "\n" + err).lower()
    if "--wait" in text or "--follow" in text or "--watch" in text:
        # Look for progress-event language.
        if "progress" in text or "stream" in text or "tail" in text:
            return {"answer": "ok", "level": 3,
                    "evidence": _make_evidence("cli", f"{cli} supports --wait with streaming/progress")}
        return {"answer": "improvable", "level": 2,
                "evidence": _make_evidence("cli", f"{cli} supports --wait (Level 2)")}
    return {"answer": "improvable", "level": 1,
            "evidence": _make_evidence("cli", f"{cli} --help shows no --wait — fire-and-forget")}


def probe_q3_plugin(target: dict) -> Optional[dict]:
    p = target.get("plugin_path")
    if not p:
        return None
    bin_text = _read_plugin_bins(p)
    if bin_text is None:
        return None
    has_wait = bool(re.search(r"--wait|_poll|poll_until|wait_for", bin_text, re.IGNORECASE))
    has_progress = bool(re.search(r"progress|stream|yield\s+", bin_text, re.IGNORECASE))
    if has_wait and has_progress:
        return {"answer": "ok", "level": 3,
                "evidence": _make_evidence("plugin", "plugin has --wait + progress streaming")}
    if has_wait:
        return {"answer": "improvable", "level": 2,
                "evidence": _make_evidence("plugin", "plugin has --wait blocking poll (Level 2)")}
    return {"answer": "improvable", "level": 1,
            "evidence": _make_evidence("plugin", "no --wait helpers in plugin (Level 1)")}


def probe_q3_web(target: dict) -> Optional[dict]:
    tool = target.get("tool")
    if not tool:
        return None
    snippets = web_search(f"{tool} wait for completion async")
    if not snippets:
        return None
    return {"answer": "unknown", "level": None,
            "evidence": _make_evidence("web", snippets[0][:200])}


# Q4 — json_output -----------------------------------------------------------


def probe_q4_cli(target: dict) -> Optional[dict]:
    cli = target.get("cli")
    if not cli or not cli_on_path(cli):
        return None
    rc, out, err = run_cli([cli, "--help"])
    text = (out + "\n" + err).lower()
    if "--json" in text or "--output json" in text or "-o json" in text:
        return {"answer": "ok",
                "evidence": _make_evidence("cli", f"{cli} --help advertises --json")}
    return {"answer": "improvable",
            "evidence": _make_evidence("cli", f"{cli} appears human-only — no --json")}


def probe_q4_plugin(target: dict) -> Optional[dict]:
    p = target.get("plugin_path")
    if not p:
        return None
    bin_text = _read_plugin_bins(p)
    if bin_text is None:
        return None
    if "json.dumps" in bin_text or "--json" in bin_text:
        return {"answer": "ok",
                "evidence": _make_evidence("plugin", "plugin emits JSON envelopes")}
    return {"answer": "improvable",
            "evidence": _make_evidence("plugin", "no json.dumps in plugin bin — text output?")}


def probe_q4_web(target: dict) -> Optional[dict]:
    tool = target.get("tool")
    if not tool:
        return None
    snippets = web_search(f"{tool} JSON output machine readable")
    if not snippets:
        return None
    joined = " ".join(snippets).lower()
    if "json" in joined:
        return {"answer": "ok",
                "evidence": _make_evidence("web", snippets[0][:200])}
    return {"answer": "unknown",
            "evidence": _make_evidence("web", snippets[0][:200])}


# Q5 — stays_in_lane ---------------------------------------------------------


def probe_q5_plugin(target: dict) -> Optional[dict]:
    p = target.get("plugin_path")
    if not p:
        return None
    bin_text = _read_plugin_bins(p)
    if bin_text is None:
        return None
    scrape_hits = bool(re.search(r"BeautifulSoup|re\.findall.*<|html\.parser",
                                 bin_text, re.IGNORECASE))
    api_hits = bool(re.search(r"api\.|/api/|/v\d+/|graphql", bin_text, re.IGNORECASE))
    if scrape_hits and api_hits:
        return {"answer": "improvable",
                "evidence": _make_evidence("plugin", "plugin mixes scrape + API — pick the canonical primitive")}
    if api_hits:
        return {"answer": "ok",
                "evidence": _make_evidence("plugin", "plugin uses API path (canonical primitive)")}
    if scrape_hits:
        return {"answer": "improvable",
                "evidence": _make_evidence("plugin", "plugin scrapes HTML — prefer API if exists")}
    return {"answer": "unknown",
            "evidence": _make_evidence("plugin", "couldn't detect data source")}


def probe_q5_web(target: dict) -> Optional[dict]:
    tool = target.get("tool")
    if not tool:
        return None
    snippets = web_search(f"{tool} REST API documentation")
    if not snippets:
        return None
    joined = " ".join(snippets).lower()
    if "api" in joined:
        return {"answer": "ok",
                "evidence": _make_evidence("web", snippets[0][:200])}
    return {"answer": "unknown",
            "evidence": _make_evidence("web", snippets[0][:200])}


# Q6 — strips_secrets --------------------------------------------------------


def probe_q6_plugin(target: dict) -> Optional[dict]:
    p = target.get("plugin_path")
    if not p:
        return None
    bin_text = _read_plugin_bins(p)
    if bin_text is None:
        return None
    if re.search(r"scrub|redact|REDACTED|_strip_secret|_sanitize", bin_text, re.IGNORECASE):
        return {"answer": "ok",
                "evidence": _make_evidence("plugin", "plugin defines scrubber/redactor")}
    return {"answer": "improvable",
            "evidence": _make_evidence("plugin", "no redactor helpers found")}


def probe_q6_cli(target: dict) -> Optional[dict]:
    cli = target.get("cli")
    if not cli or not cli_on_path(cli):
        return None
    # Conservative: don't actually invoke commands that touch real secrets.
    return {"answer": "unknown",
            "evidence": _make_evidence("cli",
                f"skipped behavioral probe of {cli} secret-handling for safety")}


# Q7 — tool_envelope ---------------------------------------------------------


def probe_q7_plugin(target: dict) -> Optional[dict]:
    p = target.get("plugin_path")
    if not p:
        return None
    bin_text = _read_plugin_bins(p)
    if bin_text is None:
        return None
    if re.search(r"['\"]tool['\"]\s*:\s*\{?\s*['\"]name['\"]", bin_text):
        return {"answer": "ok",
                "evidence": _make_evidence("plugin", "plugin emits tool: {name, version} envelope")}
    if "TOOL_NAME" in bin_text and "version" in bin_text.lower():
        return {"answer": "ok",
                "evidence": _make_evidence("plugin", "plugin exposes TOOL_NAME + version")}
    return {"answer": "improvable",
            "evidence": _make_evidence("plugin", "no tool: {name, version} envelope detected")}


def probe_q7_cli(target: dict) -> Optional[dict]:
    cli = target.get("cli")
    if not cli or not cli_on_path(cli):
        return None
    rc, out, err = run_cli([cli, "--version"])
    if rc == 0 and (out.strip() or err.strip()):
        return {"answer": "ok",
                "evidence": _make_evidence("cli", f"{cli} --version returns parseable output")}
    return {"answer": "unknown",
            "evidence": _make_evidence("cli", f"{cli} --version did not return version")}


# ─── plugin-bin reader ──────────────────────────────────────────────────────


def _read_plugin_bins(plugin_path: str) -> Optional[str]:
    """Concatenate the text of files under <plugin_path>/bin/ for grep purposes.

    Walks recursively one level deep so skill layouts like
    `<skill-dir>/bin/<name>/<subcommand>.py` are also picked up.
    """
    pp = Path(plugin_path)
    if not pp.is_dir():
        return None
    bin_dir = pp / "bin"
    if not bin_dir.is_dir():
        return None
    chunks: list[str] = []

    def _read_into(d: Path) -> None:
        for entry in sorted(d.iterdir()):
            if entry.is_file():
                try:
                    chunks.append(entry.read_text(encoding="utf-8",
                                                  errors="replace"))
                except OSError:
                    continue
            elif entry.is_dir():
                # one level of subdir (skill's bin/<name>/, or _subcommands/)
                for sub in sorted(entry.iterdir()):
                    if sub.is_file():
                        try:
                            chunks.append(sub.read_text(encoding="utf-8",
                                                         errors="replace"))
                        except OSError:
                            continue

    _read_into(bin_dir)
    return "\n".join(chunks) if chunks else None


def _read_plugin_manifest(plugin_path: str) -> dict:
    pp = Path(plugin_path)
    manifest = pp / ".claude-plugin" / "plugin.json"
    if not manifest.exists():
        return {}
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ─── per-Q runner: stack sources, derive confidence ─────────────────────────


# (q_id, q_label, [probe_callables])
PROBE_TABLE: dict[str, dict[str, Any]] = {
    "Q1": {"label": "errors_surface",
           "probes": {"cli": probe_q1_cli, "plugin": probe_q1_plugin, "web": probe_q1_web}},
    "Q2": {"label": "lookup_keys",
           "probes": {"cli": probe_q2_cli, "plugin": probe_q2_plugin, "web": probe_q2_web}},
    "Q3": {"label": "wait_async",
           "probes": {"cli": probe_q3_cli, "plugin": probe_q3_plugin, "web": probe_q3_web}},
    "Q4": {"label": "json_output",
           "probes": {"cli": probe_q4_cli, "plugin": probe_q4_plugin, "web": probe_q4_web}},
    "Q5": {"label": "stays_in_lane",
           "probes": {"plugin": probe_q5_plugin, "web": probe_q5_web}},
    "Q6": {"label": "strips_secrets",
           "probes": {"plugin": probe_q6_plugin, "cli": probe_q6_cli}},
    "Q7": {"label": "tool_envelope",
           "probes": {"plugin": probe_q7_plugin, "cli": probe_q7_cli}},
}


def stack_sources(per_source: dict[str, dict]) -> tuple[str, str, list[dict]]:
    """Combine per-source results into (answer, confidence, evidence_list).

    Rules:
    - Drop sources that returned None (not applicable / not available).
    - confidence: high if >=2 sources agree on the same non-unknown answer;
      medium if 1 authoritative source (cli|plugin|openapi); low if only web;
      none/unknown if 0 known answers.
    """
    evidence: list[dict] = []
    known: list[tuple[str, str]] = []  # (source, answer)
    for source, result in per_source.items():
        if result is None:
            continue
        ans = result.get("answer", "unknown")
        ev = result.get("evidence")
        if isinstance(ev, dict):
            evidence.append(ev)
        elif isinstance(ev, list):
            evidence.extend(ev)
        if ans != "unknown":
            known.append((source, ans))

    if not known:
        return "unknown", "none", evidence

    # Group by answer.
    counts: dict[str, list[str]] = {}
    for src, ans in known:
        counts.setdefault(ans, []).append(src)
    # Pick the most-supported answer.
    best_answer = max(counts.keys(), key=lambda a: len(counts[a]))
    supporters = counts[best_answer]
    authoritative = {"cli", "plugin", "openapi"}
    if len(supporters) >= 2:
        confidence = "high"
    elif any(s in authoritative for s in supporters):
        confidence = "medium"
    else:
        confidence = "low"
    return best_answer, confidence, evidence


def applicability(q_id: str, target: dict) -> bool:
    """Per-Q applicability rules (F6 fix)."""
    if q_id == "Q3":
        # Async only applies if tool has mutating verbs.
        # Heuristic: if plugin bin mentions wait/poll/deploy/create/trigger, applies.
        p = target.get("plugin_path")
        if p:
            text = _read_plugin_bins(p) or ""
            return bool(re.search(r"deploy|create|trigger|wait|poll|merge|run", text, re.IGNORECASE))
        return True  # default applies for raw tools
    if q_id == "Q5":
        # Multiple data-source paths exist?
        p = target.get("plugin_path")
        if p:
            text = _read_plugin_bins(p) or ""
            return bool(re.search(r"api|graphql|http|scrape", text, re.IGNORECASE))
        return True
    if q_id == "Q6":
        p = target.get("plugin_path")
        if p:
            text = _read_plugin_bins(p) or ""
            return bool(re.search(r"token|secret|api[-_]?key|GITHUB_TOKEN|password",
                                  text, re.IGNORECASE))
        return True
    if q_id == "Q7":
        # tool_envelope only meaningful for plugin context; n/a for raw tool generator mode.
        return target.get("plugin_path") is not None
    return True


def run_question(q_id: str, target: dict, *, sources_used: set[str], sources_unavailable: set[str]) -> dict:
    label = PROBE_TABLE[q_id]["label"]
    if not applicability(q_id, target):
        return {
            "q_id": q_id,
            "q_label": label,
            "answer": "na",
            "confidence": "none",
            "evidence": [],
        }
    probes = PROBE_TABLE[q_id]["probes"]
    per_source: dict[str, dict] = {}
    levels: list[int] = []
    for source, fn in probes.items():
        try:
            result = fn(target)
        except Exception as e:  # noqa: BLE001 — fail-soft per probe
            result = {"answer": "unknown",
                      "evidence": _make_evidence(source, f"probe error: {type(e).__name__}: {e}")}
        if result is None:
            sources_unavailable.add(source)
            continue
        sources_used.add(source)
        per_source[source] = result
        lvl = result.get("level")
        if isinstance(lvl, int):
            levels.append(lvl)

    answer, confidence, evidence = stack_sources(per_source)
    confidence = _cap_confidence(q_id, confidence)  # R9: per-Q confidence ceiling

    # R8 fix: Q's without a maturity ladder report binary `gap`, not the
    # laddered `improvable`. Improvable implies a level-up; without rungs
    # defined the audit row was rendering with empty cur/rec fields.
    if answer == "improvable" and q_id not in LADDER_REGISTRY:
        answer = "gap"

    out: dict = {
        "q_id": q_id,
        "q_label": label,
        "answer": answer,
        "confidence": confidence,
        "evidence": evidence,
    }
    if q_id in CONFIDENCE_CEILINGS:
        out["confidence_ceiling"] = CONFIDENCE_CEILINGS[q_id]

    # Maturity ladder placement via registry (R3 fix).
    if q_id in LADDER_REGISTRY and levels:
        ladder_dict, label_text, default_max = LADDER_REGISTRY[q_id]
        max_level = _max_achievable(q_id, target, default_max)  # R1
        cur = min(levels)  # most pessimistic source wins for "current"
        rec = min(cur + 1, max_level)
        out["current_level"] = cur
        out["recommended_level"] = rec
        out["max_achievable_level"] = max_level
        out["ladder"] = label_text
        out["current_pattern"] = ladder_dict.get(cur, "unknown")
        out["recommended_pattern"] = ladder_dict.get(rec, "unknown")
        out["platform_limit_note"] = _platform_limit_note(q_id, target, cur, max_level)
        # R1+R2: at-the-ceiling cases collapse to ok with a platform-limit note.
        # The plugin has done all it can given the platform's exposed surface.
        if cur >= max_level:
            answer = "ok"
            out["answer"] = "ok"

    # Recommended action stub (only for actionable answers).
    if answer in ("improvable", "gap"):
        out["recommended_action"] = {
            "kind": "enhance" if q_id != "Q1" else "new_subcommand",
            "name": label,
            "rationale": f"{label} probe found a gap across sources: {', '.join(sources_used) or 'web'}",
        }

    return out


# ─── envelope assembly ──────────────────────────────────────────────────────


def _summarize(results: list[dict]) -> dict:
    s = {"questions_asked": len(results),
         "ok": 0, "gaps": 0, "na": 0, "unknown": 0, "high_confidence_gaps": 0}
    for r in results:
        a = r.get("answer")
        c = r.get("confidence")
        if a == "ok":
            s["ok"] += 1
        elif a in ("gap", "improvable"):
            s["gaps"] += 1
            if c == "high":
                s["high_confidence_gaps"] += 1
        elif a == "na":
            s["na"] += 1
        else:
            s["unknown"] += 1
    return s


def _verdict(summary: dict, *, usage_clusters: Optional[dict] = None) -> str:
    """Audit verdict (Gate A.3 adds `well_used`).

    well_used: no capability gaps in Q1-Q7 AND usage_clusters non-empty AND
    >= 50% of Tier 2 clusters classify as `aligned`. Indicates the tool is
    in good shape and people are using its canned commands properly.
    """
    if summary.get("unknown", 0) >= 4:
        return "mostly_unknown"
    if summary.get("gaps", 0) > 0:
        return "gaps_found"
    # No gaps. Check whether transcripts show healthy canned usage.
    if usage_clusters and isinstance(usage_clusters, dict):
        all_t2: list[dict] = []
        for t1 in usage_clusters.get("tier1_clusters") or []:
            all_t2.extend(t1.get("tier2") or [])
        if all_t2:
            aligned = sum(1 for c in all_t2
                          if c.get("promotion_kind") == "aligned")
            if aligned / len(all_t2) >= 0.5:
                return "well_used"
    return "ok"


def _pr_body_draft(target_name: str, results: list[dict]) -> str:
    gap_results = [r for r in results if r.get("answer") in ("gap", "improvable")]
    # R5 fix: 0-gap audits return a single line, not boilerplate "## Summary
    # / ## Gaps (empty) / ## Changes - TODO". Avoid emitting paste-ready PR
    # bodies for plugins that have nothing to fix.
    if not gap_results:
        return (f"No PR needed - {target_name} is already at target maturity "
                f"across all questions.")
    lines = [
        "## Summary",
        "",
        f"`skill-plus inquire --audit` flagged {len(gap_results)} gap(s) in {target_name}.",
        "",
        "## Gaps",
        "",
    ]
    # R4 fix: ASCII arrow / dash. Em-dash mojibakes on Windows console
    # (renders as literal `?`) and violates the project's voice rule.
    for r in gap_results:
        cur = r.get("current_level")
        rec = r.get("recommended_level")
        if cur and rec:
            lines.append(
                f"- **{r['q_label']}**: at Level {cur}/{r.get('max_achievable_level','?')}, "
                f"recommended Level {rec} - {r.get('recommended_pattern','enhance')}"
            )
        else:
            lines.append(f"- **{r['q_label']}**: {r.get('answer')} "
                         f"(confidence: {r.get('confidence')})")
        for ev in (r.get("evidence") or [])[:2]:
            lines.append(f"  - evidence ({ev.get('source')}): {ev.get('detail')}")
    lines.extend([
        "",
        "## Changes",
        "",
        "- TODO: implement recommended actions per gap above",
        "",
        "## Evidence",
        "",
        "Full audit envelope attached.",
    ])
    return "\n".join(lines)


def _collect_transcripts() -> dict:
    """Run transcript adapters via the inquire_adapters package. Returns
    the same structure as inquire_adapters.collect_tuples(), or an empty
    skeleton if the package is missing/broken (fail-soft).

    Set AGENT_PLUS_INQUIRE_NO_TRANSCRIPTS=1 to skip the walk entirely (used
    by the test suite to avoid hitting the real ~/.claude/projects tree).
    """
    if os.environ.get("AGENT_PLUS_INQUIRE_NO_TRANSCRIPTS") == "1":
        return {
            "files_scanned": 0,
            "files_skipped": 0,
            "tuples": [],
            "by_format": {},
            "errors": [],
        }
    try:
        # Ensure the parent _subcommands directory is on sys.path so the
        # `inquire_adapters` package is importable. The bin/skill-plus
        # loader runs this module via spec_from_file_location which does
        # NOT add the file's parent to sys.path automatically.
        import importlib
        import sys as _sys
        from pathlib import Path as _Path
        here = _Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        adapters = importlib.import_module("inquire_adapters")
        return adapters.collect_tuples()
    except Exception as e:  # noqa: BLE001 — fail-soft.
        return {
            "files_scanned": 0,
            "files_skipped": 0,
            "tuples": [],
            "by_format": {},
            "errors": [f"adapter_load_failed: {type(e).__name__}: {str(e)[:80]}"],
        }


def build_envelope(target: dict, *, mode: str) -> dict:
    sources_used: set[str] = set()
    sources_unavailable: set[str] = set()
    results: list[dict] = []
    for q_id, _label, _always in QUESTIONS:
        results.append(run_question(q_id, target,
                                    sources_used=sources_used,
                                    sources_unavailable=sources_unavailable))

    # Gate A.1: collect transcript usage tuples. A.2 will cluster these.
    # The probe is always run; if no tuples come back, the transcripts
    # source is unavailable rather than used.
    transcript_signal = _collect_transcripts()
    if transcript_signal["tuples"]:
        sources_used.add("transcripts")
    else:
        sources_unavailable.add("transcripts")

    summary = _summarize(results)

    # Gate A.3: target kind (plugin|skill) inferred from the target dict.
    target_kind = target.get("kind") or ("plugin" if mode == "audit" else "tool")
    target_block: dict = {
        "kind": target_kind,
        "name": target.get("name") or target.get("tool"),
        "sources_used": sorted(sources_used),
        "sources_unavailable": sorted(sources_unavailable - sources_used),
    }
    if mode == "audit":
        # Use plugin_path as canonical "where the bin sits" for both kinds —
        # it points at the plugin dir for plugins, or the skill md dir for
        # skills (the skill's bin is a child of skill_md_dir).
        target_block["path"] = target.get("plugin_path")
        if target_kind == "skill":
            fm = target.get("skill_frontmatter") or {}
            target_block["version"] = fm.get("version", "unknown")
            target_block["description"] = fm.get("description", "")
        else:
            manifest = _read_plugin_manifest(target.get("plugin_path") or "")
            target_block["version"] = manifest.get("version", "unknown")

    envelope: dict = {
        "envelope_version": ENVELOPE_VERSION,
        # `verdict` is finalized below once usage_clusters is computed
        # (the well_used state needs cluster alignment data).
        "verdict": "ok",  # placeholder — replaced below
        "mode": mode,
        "target": target_block,
        "summary": summary,
        "results": results,
        "pr_body_draft": _pr_body_draft(target_block["name"] or "tool", results),
    }

    # Gate A.1: stash raw usage tuples for A.2 clustering. Note the
    # envelope shape is additive - no existing field mutated. ENVELOPE_VERSION
    # is NOT bumped here; A.3 owns the version bump.
    #
    # Don't persist raw command strings — they can contain API keys, tokens, DSNs.
    # Tuples are passed in-memory to the clustering pipeline only; the cluster
    # output (structured + secret-free) is what gets persisted on the envelope.
    raw_tuples = transcript_signal["tuples"][:MAX_INLINE_TUPLES]
    envelope["usage_signal"] = {
        "files_scanned": transcript_signal["files_scanned"],
        "files_skipped": transcript_signal["files_skipped"],
        "tuple_count": len(transcript_signal["tuples"]),
        "by_format": transcript_signal["by_format"],
        "errors": transcript_signal["errors"],
    }

    # Gate A.2: cluster the tuples into Tier 1 / Tier 2 with A/B/C
    # classification when --audit is set. Generator mode skips this -
    # there's no "existing subcommands" set to compare against.
    # Failure here is fail-soft: clustering is additive intel.
    if mode == "audit":
        try:
            import importlib
            import sys as _sys
            from pathlib import Path as _Path
            here = _Path(__file__).resolve().parent
            if str(here) not in _sys.path:
                _sys.path.insert(0, str(here))
            cluster_mod = importlib.import_module("inquire_cluster")
            # Skill targets discover bins under their resolved skill_bin
            # path; plugins use the plugin root (cluster module finds bin/).
            sub_root = target.get("skill_bin") or target.get("plugin_path")
            existing_subs = cluster_mod.discover_subcommands_from_plugin(sub_root)
            envelope["usage_clusters"] = cluster_mod.cluster_invocations(
                raw_tuples, existing_subs
            )
        except Exception as e:  # noqa: BLE001 - never crash the audit
            envelope["usage_clusters"] = {
                "tier1_clusters": [],
                "stats": {
                    "total_invocations": 0,
                    "unique_tier1": 0,
                    "unique_tier2": 0,
                    "parse_failures": 0,
                    "error": f"cluster_failed: {type(e).__name__}: {str(e)[:80]}",
                },
            }

        # Gate A.3: build envelope-level `promotions` from usage_clusters.
        # Q <-> cluster mapping note: clusters describe raw commands
        # observed across the whole tool, not per-Q. So the canonical
        # home for cluster-driven recommendations is the envelope-level
        # `promotions[]` field. Per-Q `usage_evidence` is OPTIONAL — we
        # attach it ONLY when a cluster's domain words match a Q's
        # label (currently a simple keyword overlap; future gates can
        # tighten this). This keeps Q rows scoped to capability gaps and
        # lets `promotions[]` carry the friction signal.
        envelope["promotions"] = _build_promotions(
            envelope.get("usage_clusters") or {},
            transcripts_scanned=transcript_signal["files_scanned"],
            tuples=raw_tuples,
        )

        # Optionally enrich per-Q rows with usage_evidence / promotion_kind /
        # priority when a cluster's domain matches the Q's label.
        _attach_usage_evidence_to_questions(results, envelope["promotions"])

    # Now that usage_clusters is final (or absent), pick the verdict.
    envelope["verdict"] = _verdict(summary,
                                   usage_clusters=envelope.get("usage_clusters"))

    # Generator mode adds a recommended skill scaffold.
    if mode == "generate":
        envelope["recommended_skill"] = _recommended_skill(target_block["name"] or "tool", results)

    return envelope


def _build_promotions(usage_clusters: dict, *,
                      transcripts_scanned: int,
                      tuples: list) -> list[dict]:
    """Flatten usage_clusters into a list of envelope-level promotion entries.

    Each entry has the shape documented in the delta plan:
      {
        "usage_evidence": {tier1_shape, tier1_count, tier2_clusters,
                           transcripts_scanned, date_range},
        "promotion_kind": "missing"|"misaligned"|"aligned",
        "priority": "high"|"medium"|"low"|"n/a",
      }

    The `priority` is computed per Tier-2 cluster (each tier2 entry yields
    one promotion). `tier1_count` is the parent Tier-1 cluster count.
    """
    promotions: list[dict] = []
    date_range = _date_range_from_tuples(tuples)
    for t1 in usage_clusters.get("tier1_clusters") or []:
        shape = t1.get("shape", "")
        t1_count = int(t1.get("count") or 0)
        t2_list = t1.get("tier2") or []
        for t2 in t2_list:
            promo_kind = t2.get("promotion_kind", "missing")
            entry = {
                "usage_evidence": {
                    "tier1_shape": shape,
                    "tier1_count": t1_count,
                    "tier2_clusters": [{
                        "select_cols": t2.get("select_cols") or [],
                        "where_cols": t2.get("where_cols") or [],
                        "count": t2.get("count") or 0,
                        "sample_query": t2.get("sample_query") or "",
                    }],
                    "transcripts_scanned": transcripts_scanned,
                    "date_range": date_range,
                },
                "promotion_kind": promo_kind,
                "priority": _priority(promo_kind, t1_count),
            }
            if t2.get("recommended_name"):
                entry["recommended_name"] = t2["recommended_name"]
            promotions.append(entry)
    return promotions


def _date_range_from_tuples(tuples: list) -> str:
    """Best-effort YYYY-MM-DD..YYYY-MM-DD from the (timestamp, ...) tuples."""
    dates: list[str] = []
    for t in tuples or []:
        if not t:
            continue
        ts = t[0] if isinstance(t, (list, tuple)) and t else None
        if isinstance(ts, str) and len(ts) >= 10:
            dates.append(ts[:10])
    if not dates:
        return ""
    dates.sort()
    return f"{dates[0]}..{dates[-1]}"


# Q label -> domain keywords. Used ONLY to decide whether a cluster's
# tier1 shape might be domain-relevant to a Q row. Generic — no plugin
# names, no table names. Keep this list short; broader matches dilute
# signal-to-noise.
_Q_DOMAIN_KEYWORDS: dict = {
    "Q3": ("wait", "poll", "status", "run", "job", "queue"),
    # Q1/Q2/Q4-Q7 are not currently mapped — leaving them out is the
    # default behaviour and keeps the per-Q usage_evidence signal sparse.
}


def _attach_usage_evidence_to_questions(results: list[dict],
                                        promotions: list[dict]) -> None:
    """OPTIONALLY decorate Q rows with usage_evidence when a cluster
    plausibly maps to that Q's domain. Most Qs get nothing — that's the
    point. The bulk of usage signal lives in envelope-level `promotions`.
    """
    for r in results:
        q_id = r.get("q_id")
        keywords = _Q_DOMAIN_KEYWORDS.get(q_id)
        if not keywords:
            continue
        matches = [
            p for p in promotions
            if any(kw in (p.get("usage_evidence", {}).get("tier1_shape") or "").lower()
                   for kw in keywords)
        ]
        if not matches:
            continue
        # Pick the highest-count match for inline summary.
        best = max(matches,
                   key=lambda p: p.get("usage_evidence", {}).get("tier1_count", 0))
        r["usage_evidence"] = best["usage_evidence"]
        r["promotion_kind"] = best["promotion_kind"]
        r["priority"] = best["priority"]


def _recommended_skill(tool_name: str, results: list[dict]) -> dict:
    suggested_subcommands: list[str] = []
    for r in results:
        if r.get("answer") in ("gap", "improvable"):
            ra = r.get("recommended_action")
            if ra and ra.get("name"):
                suggested_subcommands.append(ra["name"])
    return {
        "name": tool_name,
        "killer_command": f"{tool_name} overview",
        "suggested_subcommands": suggested_subcommands or ["overview"],
        "rationale": "Derived from inquiry probe results — see results[] for evidence",
    }


# ─── entrypoint ─────────────────────────────────────────────────────────────


def run(args, emit_fn) -> int:
    # --clear-cache short-circuit.
    if getattr(args, "clear_cache", False):
        n = cache_clear()
        emit_fn({"verdict": "cache_cleared", "files_removed": n,
                 "cache_dir": str(cache_dir())})
        return 0

    tool_name: Optional[str] = getattr(args, "tool", None)
    if not tool_name:
        emit_fn({"verdict": "error_missing_tool",
                 "error": "tool name is required (or pass --clear-cache)"})
        return 2

    audit_mode = bool(getattr(args, "audit", False))
    plugin_path = getattr(args, "plugin_path", None)
    cli_override = getattr(args, "cli", None)
    spec_url = getattr(args, "spec", None)  # noqa: F841 — Phase A doesn't fully wire openapi
    repo_path = getattr(args, "repo", None)  # noqa: F841 — Phase A doesn't fully wire repo signals
    no_cache = bool(getattr(args, "no_cache", False))
    refresh = bool(getattr(args, "refresh", False))
    no_transcripts = bool(getattr(args, "no_transcripts", False))
    if no_transcripts:
        os.environ["AGENT_PLUS_INQUIRE_NO_TRANSCRIPTS"] = "1"

    # Cache key includes the audit mode so a generate result doesn't masquerade
    # as an audit result and vice versa.
    cache_key = f"{tool_name}{':audit' if audit_mode else ''}"

    if not no_cache and not refresh:
        cached = cache_load(cache_key)
        if cached is not None:
            envelope = cached.get("result", {})
            envelope["from_cache"] = True
            envelope["cached_at"] = cached.get("cached_at")
            emit_fn(envelope)
            return 0

    target: dict = {
        "tool": tool_name,
        "name": tool_name,
        "cli": cli_override or tool_name,
    }
    if audit_mode:
        # Gate A.3: auto-detect plugin vs skill target. Precedence:
        #   1. explicit --plugin-path that points at a plugin OR a skill dir.
        #   2. tool_name interpreted as a directory path.
        #   3. tool_name as a name -> plugin lookup, then skill lookup.
        candidate_path = plugin_path
        if not candidate_path:
            # Try interpreting tool_name as a path first.
            p = Path(tool_name)
            if p.exists():
                candidate_path = str(p)
        kind: str = "unknown"
        resolved: Optional[str] = None
        if candidate_path:
            kind, resolved = _detect_target_kind(candidate_path)
        if kind == "unknown":
            # Skill-by-name lookup.
            sk = _resolve_skill_by_name(tool_name)
            if sk:
                kind, resolved = "skill", sk
        if kind == "skill" and resolved:
            target["kind"] = "skill"
            target["plugin_path"] = resolved  # carries skill md dir
            fm = _read_skill_frontmatter(str(Path(resolved) / "SKILL.md")) or {}
            target["skill_frontmatter"] = fm
            # Resolve subcommand bin dir from allowed-tools.
            sb = _skill_bin_from_allowed_tools(
                fm.get("allowed-tools", ""), Path(resolved),
                fm.get("name") or tool_name,
            )
            if sb:
                target["skill_bin"] = str(sb)
            # Override name from frontmatter when present.
            if fm.get("name"):
                target["name"] = fm["name"]
                target["tool"] = fm["name"]
        elif kind == "plugin" and resolved:
            target["kind"] = "plugin"
            target["plugin_path"] = resolved
        else:
            # Default: keep prior behaviour (plugin_path may be None).
            target["kind"] = "plugin"
            target["plugin_path"] = candidate_path

    mode = "audit" if audit_mode else "generate"
    envelope = build_envelope(target, mode=mode)
    envelope["from_cache"] = False

    if not no_cache:
        try:
            cache_store(cache_key, envelope)
        except OSError:
            pass

    emit_fn(envelope)
    return 0
