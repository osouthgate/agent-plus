"""skill-plus inquire — universal tool inquiry + plugin auditor (Phase A).

Two modes share one probe pipeline:

  skill-plus inquire <tool>                       # generator: probe + scaffold suggestion
  skill-plus inquire <plugin> --audit             # auditor: probe an existing plugin

Each Q (Q1..Q7) runs across every available source class (cli, plugin, web,
openapi, repo). Sources stack — at least 2 sources required for non-`unknown`
confidence. Web probe uses DuckDuckGo HTML (D1). Cache lives at
~/.agent-plus/inquire-cache/<tool>.json with 7-day TTL (D2).

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
ENVELOPE_VERSION = "1.0"

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
    """Concatenate the text of files under <plugin_path>/bin/ for grep purposes."""
    pp = Path(plugin_path)
    if not pp.is_dir():
        return None
    bin_dir = pp / "bin"
    if not bin_dir.is_dir():
        return None
    chunks: list[str] = []
    for entry in sorted(bin_dir.iterdir()):
        if entry.is_file():
            try:
                chunks.append(entry.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
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


def _verdict(summary: dict) -> str:
    if summary.get("unknown", 0) >= 4:
        return "mostly_unknown"
    if summary.get("gaps", 0) > 0:
        return "gaps_found"
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


def build_envelope(target: dict, *, mode: str) -> dict:
    sources_used: set[str] = set()
    sources_unavailable: set[str] = set()
    results: list[dict] = []
    for q_id, _label, _always in QUESTIONS:
        results.append(run_question(q_id, target,
                                    sources_used=sources_used,
                                    sources_unavailable=sources_unavailable))
    summary = _summarize(results)
    verdict = _verdict(summary)

    target_block: dict = {
        "kind": "plugin" if mode == "audit" else "tool",
        "name": target.get("name") or target.get("tool"),
        "sources_used": sorted(sources_used),
        "sources_unavailable": sorted(sources_unavailable - sources_used),
    }
    if mode == "audit":
        target_block["path"] = target.get("plugin_path")
        manifest = _read_plugin_manifest(target.get("plugin_path") or "")
        target_block["version"] = manifest.get("version", "unknown")

    envelope: dict = {
        "verdict": verdict,
        "mode": mode,
        "target": target_block,
        "summary": summary,
        "results": results,
        "pr_body_draft": _pr_body_draft(target_block["name"] or "tool", results),
    }

    # Generator mode adds a recommended skill scaffold.
    if mode == "generate":
        envelope["recommended_skill"] = _recommended_skill(target_block["name"] or "tool", results)

    return envelope


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
        target["plugin_path"] = plugin_path

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
