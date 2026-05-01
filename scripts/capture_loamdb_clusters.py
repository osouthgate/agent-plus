"""One-off fixture-capture utility for skill-plus inquire Gate A.4.

Walks ALL the user's transcripts (not the default 500-file cap), runs the
two-tier clusterer with empty existing-subcommands (so every Tier 2 shape
classifies as Type A "missing"), and writes a reference fixture to
skill-plus/test/fixtures/loamdb_db_clusters_reference.json.

Stdlib only. ASCII only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SUBCMD_DIR = REPO / "skill-plus" / "bin" / "_subcommands"
sys.path.insert(0, str(SUBCMD_DIR))

import inquire_adapters  # noqa: E402
import inquire_cluster  # noqa: E402


def main() -> int:
    # Bypass the 500-file default cap — we want full dogfood coverage.
    result = inquire_adapters.collect_tuples(
        max_files=10_000, max_tuples_per_file=100_000
    )
    tuples = result["tuples"]
    print(f"files_scanned={result['files_scanned']} "
          f"files_skipped={result['files_skipped']} "
          f"tuples={len(tuples)} errors={len(result['errors'])}")
    if result["errors"]:
        print("first 3 errors:")
        for e in result["errors"][:3]:
            print(f"  - {e}")

    clusters = inquire_cluster.cluster_invocations(tuples, existing_subcommands=[])
    stats = clusters["stats"]
    t1 = clusters["tier1_clusters"]
    print(f"tier1_count={len(t1)} unique_tier2={stats['unique_tier2']} "
          f"sql_invocations_seen={stats['sql_invocations_seen']} "
          f"parse_failures={stats['parse_failures']}")

    print("\nTop 10 Tier 1 shapes by count:")
    for c in t1[:10]:
        print(f"  {c['count']:5d}  {c['shape']}")
        for t2 in c["tier2"][:3]:
            sel = ",".join(t2["select_cols"][:5]) or "(*)"
            whr = ",".join(t2["where_cols"][:5]) or "(none)"
            print(f"           t2 count={t2['count']:4d} "
                  f"select=[{sel}] where=[{whr}] kind={t2['promotion_kind']}")

    fixture_path = (REPO / "skill-plus" / "test" / "fixtures"
                    / "loamdb_db_clusters_reference.json")
    fixture = {
        "generator": "scripts/capture_loamdb_clusters.py",
        "envelope_version": "1.1",
        "stats": {
            "files_scanned": result["files_scanned"],
            "files_skipped": result["files_skipped"],
            "tuples_collected": len(tuples),
            "tier1_count": len(t1),
            **stats,
        },
        "tier1_clusters": t1,
    }
    fixture_path.write_text(json.dumps(fixture, indent=2), encoding="utf-8")
    size = fixture_path.stat().st_size
    print(f"\nwrote {fixture_path} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
