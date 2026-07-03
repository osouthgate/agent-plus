#!/usr/bin/env python3
"""
Local-only eval pipeline: bootstrap → verify fixture budget → pytest (JUnit + logs) →
collect real CLI journey data → benchmark timings → manifest.json → USER_JOURNEY_REPORT.md.

Writes under evals/reports/<UTC-timestamp>/ (gitignored). Not for CI — run on your machine.

  python evals/scripts/run_local_eval_report.py
  python evals/scripts/run_local_eval_report.py --skip-ollama
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "evals" / "reports"
BOOTSTRAP = ROOT / "evals" / "scripts" / "bootstrap_fixtures.py"
VERIFY_BUDGET = ROOT / "evals" / "scripts" / "verify_fixture_budget.py"
BENCHMARK = ROOT / "evals" / "scripts" / "benchmark_evals.py"
RA_BIN = ROOT / "repo-analyze" / "bin" / "repo-analyze"
DS_BIN = ROOT / "diff-summary" / "bin" / "diff-summary"
SP_BIN = ROOT / "skill-plus" / "bin" / "skill-plus"
APM_BIN = ROOT / "agent-plus-meta" / "bin" / "agent-plus-meta"
FIXTURES = ROOT / "evals" / "fixtures"


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def _git_head() -> dict[str, str | None]:
    def _one(args: list[str]) -> str:
        r = subprocess.run(
            ["git", *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return r.stdout.strip() if r.returncode == 0 else ""

    return {
        "commit": _one(["rev-parse", "HEAD"]) or None,
        "commitShort": _one(["rev-parse", "--short", "HEAD"]) or None,
        "branch": _one(["rev-parse", "--abbrev-ref", "HEAD"]) or None,
    }


def _fixtures_ready() -> bool:
    names = ("osdb", "rainshift", "tinker-tailor")
    return all((FIXTURES / n / ".git").is_dir() for n in names)


def _run_script(py: Path, cwd: Path, *, timeout: int = 600) -> tuple[int, str, str]:
    r = subprocess.run(
        [sys.executable, str(py)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return r.returncode, r.stdout, r.stderr


def _parse_junit(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"file": str(path.name), "parsed": False}
    if not path.is_file():
        out["error"] = "missing"
        return out
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        tag = root.tag.split("}")[-1]
        node = root
        if tag == "testsuites" and root.attrib.get("tests"):
            node = root
        elif tag == "testsuites":
            node = next((el for el in root.iter() if el.tag.split("}")[-1] == "testsuite"), root)
        else:
            node = root
        out["parsed"] = True
        out["tests"] = int(node.attrib.get("tests", 0))
        out["failures"] = int(node.attrib.get("failures", 0))
        out["errors"] = int(node.attrib.get("errors", 0))
        out["skipped"] = int(node.attrib.get("skipped", 0))
    except (ET.ParseError, OSError, ValueError) as e:
        out["error"] = str(e)
    return out


# ─── journey data collection ─────────────────────────────────────────────────


def _run_json_cmd(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None,
                  timeout: int = 60) -> dict[str, Any]:
    r = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )
    if r.returncode != 0:
        return {"error": f"exit {r.returncode}", "stderr": r.stderr.strip()[-500:]}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        return {"error": f"json_decode: {e}", "rawPreview": r.stdout[:300]}


def _encoded_path(path: Path) -> str:
    """Claude Code's project-dir encoding: every non-alphanumeric character
    of the resolved path replaced with its own dash, one-for-one -- no
    collapsing of runs, no stripping, no re-prepending "C--". Matches
    bin/skill-plus's _encode_project_path (see evals/tests/test_skill_plus_scan_contract.py
    for why eval helpers keep an independent copy of this instead of
    importing it)."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def _load_apm_mod():
    path = APM_BIN
    loader = SourceFileLoader("_report_apm", str(path))
    spec = importlib.util.spec_from_loader("_report_apm", loader)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    loader.exec_module(mod)
    return mod


def _collect_repo_analyses() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name in ("osdb", "rainshift", "tinker-tailor"):
        d = FIXTURES / name
        if not d.is_dir():
            results.append({"fixture": name, "error": "missing"})
            continue
        out = _run_json_cmd(
            [sys.executable, str(RA_BIN), "--path", str(d), "--json"],
        )
        results.append({"fixture": name, "output": out})
    return results


def _collect_diff_summaries() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name in ("osdb", "rainshift", "tinker-tailor"):
        d = FIXTURES / name
        if not d.is_dir():
            results.append({"fixture": name, "error": "missing"})
            continue
        out = _run_json_cmd(
            [sys.executable, str(DS_BIN), "--path", ".", "--range", "HEAD~1..HEAD", "--json"],
            cwd=d,
        )
        results.append({"fixture": name, "output": out})
    return results


def _collect_marketplace_suggestions() -> dict[str, Any]:
    result: dict[str, Any] = {}
    bridge = FIXTURES / "langfuse-bridge"
    rainshift = FIXTURES / "rainshift"
    try:
        mod = _load_apm_mod()
        fn = mod.detect_suggested_skills
        result["langfuse-bridge"] = {
            "fixture": "langfuse-bridge",
            "note": "has langfuse.yaml marker",
            "suggestions": fn(bridge) if bridge.is_dir() else [],
        }
        result["rainshift"] = {
            "fixture": "rainshift",
            "note": "Next.js only, no deployment/observability markers",
            "suggestions": fn(rainshift) if rainshift.is_dir() else [],
        }
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
    return result


def _collect_scan_clusters() -> dict[str, Any]:
    bridge = FIXTURES / "langfuse-bridge"
    fx_sess = bridge / "sessions"
    if not fx_sess.is_dir():
        return {"error": "langfuse-bridge/sessions missing"}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        proj = bridge.resolve()
        fake_home = tmp_path / "home"
        fake_home.mkdir(parents=True)
        sess_dir = fake_home / ".claude" / "projects" / _encoded_path(proj)
        sess_dir.mkdir(parents=True, exist_ok=True)
        for f in fx_sess.glob("*.jsonl"):
            shutil.copyfile(f, sess_dir / f.name)
        state = tmp_path / "skill-plus-state"
        env = os.environ.copy()
        env["HOME"] = str(fake_home)
        env["USERPROFILE"] = str(fake_home)
        env["SKILL_PLUS_DIR"] = str(state)
        out = _run_json_cmd(
            [sys.executable, str(SP_BIN), "scan", "--project", str(proj), "--accept-consent"],
            env=env,
            timeout=60,
        )
    return {
        "fixture": "langfuse-bridge/sessions",
        "sessionFiles": [f.name for f in fx_sess.glob("*.jsonl")],
        "output": out,
    }


def _collect_skill_proposals(scan_result: dict[str, Any]) -> dict[str, Any]:
    candidates = (scan_result.get("output") or {}).get("candidates", [])
    if not candidates:
        return {"note": "no candidates from scan to propose"}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cand_path = tmp_path / "candidates.jsonl"
        lines = []
        for c in candidates:
            lines.append(json.dumps(c))
        cand_path.write_text("\n".join(lines), encoding="utf-8")
        env = os.environ.copy()
        env["SKILL_PLUS_DIR"] = str(tmp_path)
        out = _run_json_cmd(
            [sys.executable, str(SP_BIN), "propose", "--json"],
            env=env,
            timeout=60,
        )
    return {"output": out}


def _collect_journey_data(out_dir: Path) -> dict[str, Any]:
    journey: dict[str, Any] = {"collectedAt": _iso()}

    journey["repoAnalyses"] = _collect_repo_analyses()
    journey["diffSummaries"] = _collect_diff_summaries()
    journey["marketplaceSuggestions"] = _collect_marketplace_suggestions()
    scan = _collect_scan_clusters()
    journey["sessionMining"] = scan
    journey["skillProposals"] = _collect_skill_proposals(scan)

    (out_dir / "journey_data.json").write_text(json.dumps(journey, indent=2), encoding="utf-8")
    return journey


# ─── report rendering ─────────────────────────────────────────────────────────


def _fmt_langs(langs: dict) -> str:
    if not langs:
        return "—"
    return ", ".join(
        f"{k} ({v['percent']}%)" for k, v in list(langs.items())[:4]
    )


def _fmt_frameworks(fw: list) -> str:
    if not fw:
        return "—"
    return ", ".join(f"{f['name']} ({f.get('confidence','?')})" for f in fw)


def _fmt_deps(deps: dict) -> str:
    node = (deps or {}).get("node") or {}
    rt = node.get("runtime") or []
    dev = node.get("dev") or []
    parts = []
    if rt:
        parts.append(f"runtime: {', '.join(rt[:5])}")
    if dev:
        parts.append(f"dev: {', '.join(dev[:5])}")
    return "; ".join(parts) if parts else "—"


def _build_journey_report(journey: dict[str, Any], manifest: dict[str, Any]) -> str:
    lines: list[str] = []
    ts = manifest.get("generatedAt", journey.get("collectedAt", ""))
    branch = (manifest.get("git") or {}).get("branch") or "—"
    commit = (manifest.get("git") or {}).get("commitShort") or "—"
    pytest_ok = (manifest.get("pytest") or {}).get("exitCode") == 0
    junit = (manifest.get("pytest") or {}).get("junit") or {}

    lines += [
        f"# User Journey Report — {ts}",
        "",
        f"Branch `{branch}` · commit `{commit}` · "
        f"{'✓ all tests passed' if pytest_ok else '✗ test failures — see pytest_junit.xml'}",
        "",
        "What the agent-plus toolchain actually found and suggested, "
        "simulating a real user session from repo orientation through skill scaffolding.",
        "",
    ]

    # ── 1. Repo orientation ──────────────────────────────────────────────────
    lines += [
        "## 1. Repo orientation (`repo-analyze`)",
        "",
        "What an agent sees when it first enters each fixture repository:",
        "",
        "| Fixture | Languages (top 4) | Frameworks | Build | Runtime deps | Next steps |",
        "|---------|-------------------|------------|-------|--------------|------------|",
    ]
    for item in journey.get("repoAnalyses") or []:
        name = item.get("fixture", "?")
        o = item.get("output") or {}
        if o.get("error"):
            lines.append(f"| {name} | _(error: {o['error']})_ | | | | |")
            continue
        langs = _fmt_langs(o.get("languages") or {})
        fw = _fmt_frameworks(o.get("frameworks") or [])
        bt = ", ".join(t["name"] for t in (o.get("buildTools") or []))
        deps = _fmt_deps(o.get("deps") or {})
        nexts = "; ".join(
            s.split(" --")[0] for s in (o.get("nextSteps") or [])
        ) or "—"
        lines.append(f"| **{name}** | {langs} | {fw} | {bt or '—'} | {deps} | {nexts} |")
    lines.append("")

    # ── 2. Change triage ─────────────────────────────────────────────────────
    lines += [
        "## 2. Change triage (`diff-summary HEAD~1..HEAD`)",
        "",
        "What the agent sees when summarising the most recent commit on each fixture:",
        "",
        "| Fixture | Files changed | Roles seen | Highest risk |",
        "|---------|---------------|------------|--------------|",
    ]
    for item in journey.get("diffSummaries") or []:
        name = item.get("fixture", "?")
        o = item.get("output") or {}
        if o.get("error"):
            lines.append(f"| {name} | _(error: {o['error']})_ | | |")
            continue
        files = o.get("files") or []
        roles = sorted({f.get("role", "?") for f in files})
        risks = [f.get("risk", "low") for f in files]
        risk_order = {"critical": 4, "high": 3, "medium": 2, "low": 1, "none": 0}
        top_risk = max(risks, key=lambda r: risk_order.get(r, 0), default="—")
        lines.append(
            f"| **{name}** | {len(files)} | {', '.join(roles) or '—'} | {top_risk} |"
        )
    lines.append("")

    # ── 3. Marketplace suggestions ───────────────────────────────────────────
    lines += [
        "## 3. Marketplace skill suggestions (`detect_suggested_skills`)",
        "",
        "Deterministic stack-marker detection — no LLM, no network.",
        "",
    ]
    ms = journey.get("marketplaceSuggestions") or {}
    if ms.get("error"):
        lines += [f"> Error loading agent-plus-meta: {ms['error']}", ""]
    else:
        for key, val in ms.items():
            if key == "error":
                continue
            note = val.get("note", "")
            suggestions = val.get("suggestions") or []
            lines.append(f"**`{key}`** ({note})")
            if suggestions:
                for s in suggestions:
                    lines.append(f"- **{s['name']}** — {s['reason']}")
                    lines.append(f"  `{s['install_hint']}`")
            else:
                lines.append("- _(no suggestions — correct for this fixture)_")
            lines.append("")

    # ── 4. Session mining ────────────────────────────────────────────────────
    lines += [
        "## 4. Session mining (`skill-plus scan`)",
        "",
    ]
    sm = journey.get("sessionMining") or {}
    if sm.get("error"):
        lines += [f"> Error: {sm['error']}", ""]
    else:
        sess_files = sm.get("sessionFiles") or []
        scan_out = sm.get("output") or {}
        candidates = scan_out.get("candidates") or []
        lines.append(
            f"Fixture: `{sm.get('fixture', '?')}` — "
            f"{len(sess_files)} session file(s): {', '.join(sess_files) or '—'}"
        )
        lines.append(
            f"Sessions scanned: **{scan_out.get('sessionsScanned', '?')}** · "
            f"New clusters: **{scan_out.get('candidatesNew', '?')}**"
        )
        lines.append("")
        if candidates:
            lines += [
                "| Cluster key | Count | Sessions |",
                "|-------------|-------|---------|",
            ]
            for c in candidates:
                sess_list = ", ".join(sorted(c.get("sessions") or []))
                lines.append(f"| `{c['key']}` | {c['count']} | {sess_list} |")
        else:
            lines.append("_(no clusters met the threshold)_")
        lines.append("")
        if scan_out.get("error"):
            lines += [f"> Scan error: {scan_out['error']}", ""]

    # ── 5. Skill proposals ───────────────────────────────────────────────────
    lines += [
        "## 5. Skill proposals (`skill-plus propose`)",
        "",
        "What `propose` would surface to the user from the mined clusters above:",
        "",
    ]
    sp = journey.get("skillProposals") or {}
    if sp.get("note"):
        lines += [f"_{sp['note']}_", ""]
    elif sp.get("output"):
        po = sp["output"]
        if po.get("error"):
            lines += [f"> Error: {po['error']}", ""]
        else:
            ranked = po.get("ranked") or po.get("candidates") or []
            if ranked:
                lines += [
                    "| # | Key / suggested name | Score | Count | Sessions |",
                    "|---|----------------------|-------|-------|---------|",
                ]
                for i, r in enumerate(ranked[:10], 1):
                    key = r.get("key") or r.get("name") or "?"
                    score = r.get("score", "—")
                    count = r.get("count", "—")
                    sessions = r.get("sessions", [])
                    if isinstance(sessions, list):
                        sessions = ", ".join(sorted(sessions))
                    lines.append(f"| {i} | `{key}` | {score} | {count} | {sessions} |")
            else:
                lines.append("_(no ranked proposals returned)_")
            if po.get("note"):
                lines += [f"_{po['note']}_", ""]
    lines.append("")

    # ── footer ───────────────────────────────────────────────────────────────
    n_tests = junit.get("tests", "?")
    n_skip = junit.get("skipped", 0)
    lines += [
        "---",
        "",
        f"Tests: **{n_tests}** run · **{n_skip}** skipped · "
        f"generated by `evals/scripts/run_local_eval_report.py`",
    ]
    return "\n".join(lines)


# ─── Ollama narrative (enhancement over the structured report) ────────────────


def _ollama_narrative(journey: dict[str, Any], manifest: dict[str, Any],
                      *, base_url: str, model: str) -> str:
    sys.path.insert(0, str(ROOT))
    from evals.llm.ollama_chat import chat  # type: ignore[import]

    base_report = _build_journey_report(journey, manifest)

    user = (
        "You are reviewing the output of agent-plus, a set of Claude Code plugins.\n"
        "Below is a structured user-journey report showing what the toolchain actually found "
        "when run against synthetic repos and session logs.\n\n"
        "Write a SHORT Markdown narrative (max ~25 lines) aimed at the developer who shipped "
        "these plugins. Focus on:\n"
        "- What the scan/suggest pipeline found that a real user would care about\n"
        "- Whether the marketplace suggestions and session clusters are coherent "
        "(e.g. does langfuse.yaml → langfuse-remote feel right?)\n"
        "- One concrete thing a user could do next\n\n"
        "Do NOT restate every number. The structured report is already below the narrative.\n"
        "Do NOT claim tests ran in CI. End with `---` and one line: "
        f"generated by local eval report + {model}.\n\n"
        "STRUCTURED REPORT:\n"
        f"{base_report}"
    )
    result = chat(
        base_url,
        model,
        [{"role": "user", "content": user}],
        timeout=180.0,
    )
    if not result or not result.strip():
        return base_report
    return result + "\n\n---\n\n" + base_report


def _fallback_report(journey: dict[str, Any], manifest: dict[str, Any]) -> str:
    return _build_journey_report(journey, manifest)


# ─── main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="Local eval report: pytest + journey data + optional Ollama narrative.")
    ap.add_argument("--skip-bootstrap", action="store_true")
    ap.add_argument("--skip-verify-budget", action="store_true")
    ap.add_argument("--skip-benchmark", action="store_true")
    ap.add_argument("--skip-ollama", action="store_true")
    ap.add_argument("--skip-journey", action="store_true", help="Skip CLI journey data collection")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from evals.load_eval_env import load_ollama_env  # type: ignore[import]
    load_ollama_env(ROOT)

    out_dir = args.out or (REPORTS / _slug_ts())
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "generatedAt": _iso(),
        "slug": out_dir.name,
        "git": _git_head(),
        "python": sys.version.split()[0],
        "steps": [],
        "pytest": {},
        "benchmark": {},
    }

    # 1) Bootstrap
    if not args.skip_bootstrap and not _fixtures_ready():
        rc, out, err = _run_script(BOOTSTRAP, ROOT)
        manifest["steps"].append(
            {"name": "bootstrap_fixtures", "ok": rc == 0, "stdoutTail": out[-2000:], "stderrTail": err[-1000:]}
        )
        if rc != 0:
            (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print(err, file=sys.stderr)
            return 1
    elif not args.skip_bootstrap:
        manifest["steps"].append({"name": "bootstrap_fixtures", "ok": True, "note": "fixtures already present"})

    # 2) Verify budget
    if not args.skip_verify_budget:
        rc, out, err = _run_script(VERIFY_BUDGET, ROOT, timeout=60)
        manifest["steps"].append({"name": "verify_fixture_budget", "ok": rc == 0, "stderr": err.strip()[-1500:]})
        if rc != 0:
            (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print(err, file=sys.stderr)
            return 1

    # 3) Pytest + JUnit
    junit_path = out_dir / "pytest_junit.xml"
    t0 = time.perf_counter()
    pr = subprocess.run(
        [
            sys.executable, "-m", "pytest", "evals/tests/", "-v",
            "--tb=short", f"--junit-xml={junit_path}",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    elapsed_s = round(elapsed_ms / 1000.0, 3)
    (out_dir / "pytest_stdout.log").write_text(pr.stdout, encoding="utf-8")
    (out_dir / "pytest_stderr.log").write_text(pr.stderr, encoding="utf-8")

    junit_summary = _parse_junit(junit_path)
    manifest["pytest"] = {"exitCode": pr.returncode, "durationSeconds": elapsed_s, "junit": junit_summary}
    manifest["steps"].append({"name": "pytest_evals", "ok": pr.returncode == 0, "durationSeconds": elapsed_s})

    # 4) Journey data collection
    journey: dict[str, Any] = {}
    if not args.skip_journey:
        print("Collecting journey data from CLIs...", file=sys.stderr)
        journey = _collect_journey_data(out_dir)
        manifest["steps"].append({"name": "journey_data", "ok": True, "file": "journey_data.json"})

    # 5) Benchmark
    bench_obj: dict[str, Any] | None = None
    if not args.skip_benchmark:
        br = subprocess.run(
            [sys.executable, str(BENCHMARK), "--no-pytest", "--output", str(out_dir / "benchmark_line.jsonl")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        (out_dir / "benchmark_stdout.log").write_text(br.stdout, encoding="utf-8")
        (out_dir / "benchmark_stderr.log").write_text(br.stderr, encoding="utf-8")
        try:
            bench_obj = json.loads(br.stdout.strip())
        except json.JSONDecodeError:
            bench_obj = {"parseError": True, "stdoutPreview": br.stdout[:800]}
        if isinstance(bench_obj, dict) and "timingsMs" in bench_obj:
            tm = dict(bench_obj["timingsMs"])
            tm["pytest_evals_ms"] = elapsed_ms
            bench_obj["timingsMs"] = tm
            (out_dir / "benchmark.json").write_text(json.dumps(bench_obj, indent=2), encoding="utf-8")
        manifest["benchmark"] = {
            "exitCode": br.returncode,
            "timingsMs": (bench_obj or {}).get("timingsMs") if isinstance(bench_obj, dict) else None,
            "recordedAt": (bench_obj or {}).get("recordedAt") if isinstance(bench_obj, dict) else None,
            "commit": (bench_obj or {}).get("commitShort") if isinstance(bench_obj, dict) else None,
        }
        manifest["steps"].append({"name": "benchmark_evals", "ok": br.returncode == 0})
    else:
        manifest["benchmark"] = {"skipped": True, "timingsMs": {"pytest_evals_ms": elapsed_ms}}

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # 6) USER_JOURNEY_REPORT.md
    report_path = out_dir / "USER_JOURNEY_REPORT.md"
    model = (os.environ.get("OLLAMA_CHAT_MODEL") or "").strip()
    base = (os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434").strip()
    use_llm = not args.skip_ollama and bool(model) and bool(journey)
    if use_llm:
        sys.path.insert(0, str(ROOT))
        from evals.llm.ollama_chat import ping  # type: ignore[import]
        if not ping(base):
            use_llm = False
            manifest["ollama"] = {"skipped": True, "reason": f"unreachable at {base}"}
    if use_llm:
        try:
            text = _ollama_narrative(journey, manifest, base_url=base, model=model)
            report_path.write_text(text or _fallback_report(journey, manifest), encoding="utf-8")
            manifest["ollama"] = {"used": True, "model": model, "baseUrl": base}
        except Exception as e:  # noqa: BLE001
            manifest["ollama"] = {"used": False, "error": str(e)}
            report_path.write_text(_fallback_report(journey, manifest), encoding="utf-8")
    else:
        if not args.skip_ollama and not model:
            manifest["ollama"] = {"skipped": True, "reason": "OLLAMA_CHAT_MODEL unset"}
        report_path.write_text(_fallback_report(journey, manifest), encoding="utf-8")

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(json.dumps({
        "reportDir": str(out_dir),
        "pytestExit": pr.returncode,
        "manifest": str(out_dir / "manifest.json"),
        "journeyReport": str(report_path),
    }, indent=2))
    return 0 if pr.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
