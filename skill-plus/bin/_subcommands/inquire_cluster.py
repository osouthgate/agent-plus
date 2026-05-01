"""Two-tier clustering + Type A/B/C classifier (Gate A.2).

Consumes the canonical transcript tuples produced by `inquire_adapters`
(shape: `[timestamp, source_path, tool_name, command_string, args_dict]`)
and returns a structured cluster summary.

Algorithm (per the delta plan, sections "Clustering algorithm (the F-fix)"
and "Promotion classification"):

  Tier 1 (coarse):  fingerprint = (sql_verb, sorted(set(tables_touched)))
                    Threshold: count >= 3.
  Tier 2 (refined): fingerprint = (sorted(SELECT_cols), sorted(WHERE_cols))
                    Within each Tier 1 cluster. Threshold: count >= 2.

Each Tier 2 cluster is classified A/B/C against the plugin's existing
subcommands:

  A (missing):    no subcommand touches this Tier 1 table-set
  B (misaligned): subcommand exists but column-set isn't covered
  C (aligned):    subcommand exists AND column-set roughly matches

ANTI-CONFIRMATION DISCIPLINE (see plan §"Anti-confirmation-bias"):
- Generic template normalisation only. NO hand-tuned regex per tool.
- NO mention of any specific table name in the algorithm.
- The only hardcoded "knowledge" is generic SQL grammar (verbs, FROM,
  JOIN, WHERE, AS).

Stdlib only. ASCII only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

TIER1_MIN_COUNT = 3        # Tier 1 shape needs >=3 occurrences to surface (avoids one-off noise).
TIER2_MIN_COUNT = 2        # Tier 2 column-fingerprint needs >=2 within parent (smaller sample inside hot shape).
COL_OVERLAP_THRESHOLD = 0.5  # >=50% column overlap with subcommand metadata = Type C aligned, else Type B.

# ---- SQL detection -------------------------------------------------------

# Recognised top-level SQL verbs. Used both to filter SQL-bearing
# command strings and to extract the verb token.
_SQL_VERBS = ("SELECT", "UPDATE", "INSERT", "DELETE")
_SQL_VERB_RE = re.compile(
    r"\b(" + "|".join(_SQL_VERBS) + r")\b", re.IGNORECASE
)

# Match a quoted SQL payload inside a shell command. We accept either
# "..." or '...' immediately following a recognised arg verb such as
# `raw`, `query`, `exec`, `sql`. Non-greedy. We don't try to be a full
# shell parser - if quoting is exotic, we fall back to scanning the
# whole command string.
_QUOTED_SQL_RE = re.compile(
    r"""(?:^|\s)(?:raw|query|exec|sql)\s+      # subcommand-style verb
        (?P<q>["'])                            # opening quote
        (?P<sql>.*?)                           # the payload
        (?P=q)                                 # matching closing quote
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


def extract_sql(command_string: str) -> Optional[str]:
    """Pull the SQL payload out of a shell command, or None if no SQL.

    Two strategies: prefer a quoted arg after raw|query|exec|sql; else
    fall back to the whole command if it contains a SQL verb.
    """
    if not command_string:
        return None
    m = _QUOTED_SQL_RE.search(command_string)
    if m:
        candidate = m.group("sql")
        if _SQL_VERB_RE.search(candidate):
            return candidate.strip()
    # Fallback: maybe the whole command IS the SQL (e.g. piped or
    # otherwise invoked). Only accept if it begins with a SQL verb
    # within the first ~40 chars to avoid false positives on commands
    # that merely mention "select" somewhere later.
    head = command_string.lstrip()[:40]
    if _SQL_VERB_RE.match(head):
        return command_string.strip()
    return None


# ---- SQL parsing (regex; top-level only) ---------------------------------

# Strip block comments and trailing semicolons. Keep the regex simple -
# this isn't a full SQL parser. We bail on anything we can't make sense
# of (caller logs to parse_failures).
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")

# Top-level FROM <list> capture. Stops at WHERE / GROUP / ORDER / HAVING
# / LIMIT / OFFSET / UNION / RETURNING / ; / end-of-string.
_FROM_RE = re.compile(
    r"\bFROM\s+(?P<tables>.+?)"
    r"(?=\bWHERE\b|\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b"
    r"|\bOFFSET\b|\bUNION\b|\bRETURNING\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)
# JOIN ... [tables] capture: standalone clause (after FROM region).
_JOIN_RE = re.compile(
    r"\bJOIN\s+(?P<table>[A-Za-z_][\w\.]*)"
    r"(?:\s+(?:AS\s+)?(?P<alias>[A-Za-z_]\w*))?",
    re.IGNORECASE,
)
_SELECT_RE = re.compile(
    r"\bSELECT\b\s+(?:DISTINCT\s+|TOP\s+\d+\s+)?(?P<cols>.+?)\s+\bFROM\b",
    re.IGNORECASE | re.DOTALL,
)
_UPDATE_RE = re.compile(
    r"\bUPDATE\s+(?P<table>[A-Za-z_][\w\.]*)", re.IGNORECASE
)
_INSERT_RE = re.compile(
    r"\bINSERT\s+INTO\s+(?P<table>[A-Za-z_][\w\.]*)", re.IGNORECASE
)
_DELETE_RE = re.compile(
    r"\bDELETE\s+FROM\s+(?P<table>[A-Za-z_][\w\.]*)", re.IGNORECASE
)
_WHERE_RE = re.compile(
    r"\bWHERE\b\s+(?P<body>.+?)"
    r"(?=\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b|\bOFFSET\b"
    r"|\bUNION\b|\bRETURNING\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)


def _strip_quotes(ident: str) -> str:
    """Strip backticks, double-quotes, and brackets from a SQL identifier."""
    s = ident.strip()
    while len(s) >= 2 and s[0] in '`"[' and s[-1] in '`"]':
        s = s[1:-1].strip()
    return s


def _split_top_level(s: str, sep: str = ",") -> list[str]:
    """Split on `sep` at paren depth 0. Used for SELECT col lists where
    a column might be `COUNT(x), foo`."""
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    i = 0
    in_str: Optional[str] = None
    while i < len(s):
        ch = s[i]
        if in_str:
            cur.append(ch)
            if ch == in_str and (i == 0 or s[i - 1] != "\\"):
                in_str = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            cur.append(ch)
            i += 1
            continue
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == sep and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out


def _extract_table_token(token: str) -> Optional[str]:
    """From "schema.tbl AS x" -> "tbl". From "tbl x" -> "tbl".
    Returns None if token doesn't look like a table ref."""
    t = token.strip()
    if not t:
        return None
    # Drop optional "AS alias" / bare alias.
    m = re.match(
        r"(?P<name>[A-Za-z_`\"\[][\w`\"\]\.]*)"
        r"(?:\s+(?:AS\s+)?[A-Za-z_]\w*)?\s*$",
        t,
        re.IGNORECASE,
    )
    if not m:
        return None
    name = _strip_quotes(m.group("name"))
    # Take last segment of schema-qualified name (`a.b.c` -> `c`).
    if "." in name:
        name = name.split(".")[-1]
    name = _strip_quotes(name)
    if not re.match(r"^[A-Za-z_]\w*$", name):
        return None
    return name.lower()


def _extract_column_name(token: str) -> Optional[str]:
    """For SELECT col tokens. "c.id AS foo" -> "id". "COUNT(*)" -> None
    (we punt on aggregates and expressions for now). "id" -> "id"."""
    t = token.strip()
    if not t or t == "*":
        return None
    # Drop AS alias.
    t = re.sub(r"\s+AS\s+\w+\s*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+\w+\s*$", lambda m: "" if " " in t else m.group(0), t)
    # If there's still a paren the token is an expression - skip.
    if "(" in t or ")" in t:
        return None
    name = _strip_quotes(t.strip())
    if "." in name:
        name = name.split(".")[-1]
    name = _strip_quotes(name)
    if not re.match(r"^[A-Za-z_]\w*$", name):
        return None
    return name.lower()


def _extract_where_columns(body: str) -> list[str]:
    """Pull LHS column names from a WHERE clause.

    Heuristic: find tokens of the form `[alias.]ident` followed by a
    comparator (=, !=, <>, <, >, <=, >=) or by IS / LIKE / IN / BETWEEN.
    """
    cols: list[str] = []
    pat = re.compile(
        r"(?P<col>[A-Za-z_`\"\[][\w`\"\]\.]*)"
        r"\s*(?:=|!=|<>|<=?|>=?|\bIS\b|\bLIKE\b|\bIN\b|\bBETWEEN\b)",
        re.IGNORECASE,
    )
    for m in pat.finditer(body):
        raw = _strip_quotes(m.group("col"))
        if "." in raw:
            raw = raw.split(".")[-1]
        raw = _strip_quotes(raw)
        if re.match(r"^[A-Za-z_]\w*$", raw):
            cols.append(raw.lower())
    # Dedupe, preserve first-seen order.
    seen: set[str] = set()
    out: list[str] = []
    for c in cols:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def parse_sql(sql: str) -> Optional[dict]:
    """Parse a SQL statement into a structured dict, or None on failure.

    Returns:
      {"verb": "select"|"update"|"insert"|"delete",
       "tables": [str, ...],     # sorted, lowercase, deduped
       "select_cols": [str, ...],  # only for SELECT
       "where_cols": [str, ...]}
    """
    if not sql:
        return None
    s = _COMMENT_RE.sub(" ", sql)
    s = _LINE_COMMENT_RE.sub(" ", s)
    s = s.strip().rstrip(";").strip()
    if not s:
        return None
    vm = _SQL_VERB_RE.search(s)
    if not vm:
        return None
    verb = vm.group(1).lower()

    tables: list[str] = []
    select_cols: list[str] = []
    where_cols: list[str] = []

    if verb == "select":
        sm = _SELECT_RE.search(s)
        fm = _FROM_RE.search(s)
        if not fm:
            return None
        # Tables: split FROM list on commas, then catch JOINed tables.
        from_body = fm.group("tables")
        # JOIN clauses sit inside the FROM body under our regex
        # (FROM stops at WHERE etc., not at JOIN). Pull JOINs first,
        # then strip them so the residue is the FROM-list.
        for jm in _JOIN_RE.finditer(from_body):
            t = _extract_table_token(jm.group("table"))
            if t:
                tables.append(t)
        from_residue = _JOIN_RE.sub(" ", from_body)
        # Drop ON clauses entirely.
        from_residue = re.sub(
            r"\bON\b.+?(?=,|$)", " ", from_residue,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for tok in _split_top_level(from_residue, ","):
            t = _extract_table_token(tok)
            if t:
                tables.append(t)
        if sm:
            for tok in _split_top_level(sm.group("cols"), ","):
                c = _extract_column_name(tok)
                if c:
                    select_cols.append(c)
    elif verb == "update":
        m = _UPDATE_RE.search(s)
        if not m:
            return None
        t = _extract_table_token(m.group("table"))
        if t:
            tables.append(t)
    elif verb == "insert":
        m = _INSERT_RE.search(s)
        if not m:
            return None
        t = _extract_table_token(m.group("table"))
        if t:
            tables.append(t)
    elif verb == "delete":
        m = _DELETE_RE.search(s)
        if not m:
            return None
        t = _extract_table_token(m.group("table"))
        if t:
            tables.append(t)

    wm = _WHERE_RE.search(s)
    if wm:
        where_cols = _extract_where_columns(wm.group("body"))

    if not tables:
        return None

    # Dedupe + sort tables (set semantics for the fingerprint).
    tset = sorted(set(tables))

    return {
        "verb": verb,
        "tables": tset,
        "select_cols": select_cols,  # ordered as parsed (deduped at fingerprint time)
        "where_cols": where_cols,
    }


# ---- Tier 1 + Tier 2 fingerprints + classifier ----------------------------


def _tier1_shape(parsed: dict) -> str:
    return f"{parsed['verb']}: {','.join(parsed['tables'])}"


def _tier2_key(parsed: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    sel = tuple(sorted(set(parsed.get("select_cols") or [])))
    whr = tuple(sorted(set(parsed.get("where_cols") or [])))
    return sel, whr


def _classify_cluster(
    parent_tables: list[str],
    cluster_select: list[str],
    cluster_where: list[str],
    existing_subcommands: list[dict],
) -> str:
    """Type A/B/C classifier.

    Heuristic for "subcommand touches table-set":
      - If the subcommand record has an explicit `tables` list, use it.
      - Else fuzzy-match: subcommand name appears as a prefix or substring
        of any table in parent_tables (after stripping plurals), or vice
        versa. Tables in parent_tables get their final segment compared.

    Heuristic for "covers column-set":
      - If subcommand has `columns`, check intersection >= 50% of cluster.
      - Else, fall back to: name match alone is treated as covered (we
        can't know the columns; flag B only when name doesn't match).

    The plan explicitly says "fuzzy by design - better to flag a few
    false positives in B than miss the algorithm's value."
    """
    cluster_cols = set(cluster_select) | set(cluster_where)

    def _norm(name: str) -> str:
        n = name.lower().strip()
        # Drop a single trailing 's' to handle plural <-> singular
        # (organizations vs org). Keep things conservative; no
        # Porter-stemmer territory.
        if n.endswith("s"):
            n = n[:-1]
        return n

    parent_norm = [_norm(t) for t in parent_tables]

    name_match: Optional[dict] = None
    table_match: Optional[dict] = None
    for sub in existing_subcommands or []:
        sub_name = _norm(str(sub.get("name", "")))
        if not sub_name:
            continue
        # Explicit tables list wins.
        sub_tables = sub.get("tables")
        if isinstance(sub_tables, list) and sub_tables:
            sub_norm = [_norm(str(t)) for t in sub_tables]
            if any(t in parent_norm for t in sub_norm):
                table_match = sub
                break
        # Otherwise fuzzy on name.
        for t in parent_norm:
            if sub_name == t or sub_name in t or t in sub_name:
                if name_match is None:
                    name_match = sub
                break

    matched = table_match or name_match
    if matched is None:
        return "missing"

    sub_cols = matched.get("columns")
    if isinstance(sub_cols, list) and sub_cols and cluster_cols:
        sub_col_norm = {str(c).lower() for c in sub_cols}
        overlap = cluster_cols & sub_col_norm
        if not cluster_cols:
            return "aligned"
        if len(overlap) / max(1, len(cluster_cols)) >= COL_OVERLAP_THRESHOLD:
            return "aligned"
        return "misaligned"
    # No column metadata - default to aligned on name match (Type C).
    # The plan says we'll over-mark C rather than over-mark B; the
    # auditor's rec for C is "no recommendation, log as OK".
    return "aligned"


def _primary_table(cluster_tuples: list[dict]) -> str:
    """Pick the most-frequent table that appears in WHERE clauses across
    cluster invocations. Fall back to the first parent table."""
    counts: dict[str, int] = {}
    for parsed in cluster_tuples:
        # Heuristic: any table in `tables` that also appears as an alias
        # prefix in where_cols? Our where parser already strips alias,
        # so we just count tables. Fine - the first table is usually
        # primary by SQL convention.
        for t in parsed["tables"]:
            counts[t] = counts.get(t, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]


def cluster_invocations(
    tuples: list,
    existing_subcommands: list[dict],
) -> dict:
    """Two-tier cluster + classify. See module docstring.

    Args:
      tuples: list of [timestamp, source_path, tool_name, command_string,
        args_dict] (5-elem lists or tuples).
      existing_subcommands: list of dicts with at least `{"name": str}`,
        optionally `{"tables": [...], "columns": [...]}`.

    Returns:
      {"tier1_clusters": [...], "stats": {...}}
    """
    parsed_by_shape: dict[str, list[dict]] = {}
    parsed_samples: dict[str, str] = {}  # shape -> sample SQL string
    total = 0
    parse_failures = 0
    sql_invocations = 0

    for entry in tuples or []:
        if not entry or len(entry) < 5:
            continue
        command_string = entry[3] or ""
        sql = extract_sql(command_string)
        if sql is None:
            continue
        sql_invocations += 1
        try:
            parsed = parse_sql(sql)
        except Exception:  # noqa: BLE001 - never crash the audit
            parse_failures += 1
            continue
        if parsed is None:
            parse_failures += 1
            continue
        total += 1
        shape = _tier1_shape(parsed)
        parsed_by_shape.setdefault(shape, []).append(parsed)
        parsed_samples.setdefault(shape, sql)

    # Apply Tier 1 threshold (count >= TIER1_MIN_COUNT).
    tier1_clusters: list[dict] = []
    unique_tier2 = 0
    for shape, parsed_list in parsed_by_shape.items():
        if len(parsed_list) < TIER1_MIN_COUNT:
            continue
        # Tier 2: bucket by (select_cols, where_cols).
        t2_buckets: dict[tuple, list[dict]] = {}
        for p in parsed_list:
            t2_buckets.setdefault(_tier2_key(p), []).append(p)
        tier2_entries: list[dict] = []
        parent_tables = parsed_list[0]["tables"]
        for (sel, whr), bucket in t2_buckets.items():
            if len(bucket) < TIER2_MIN_COUNT:
                continue
            promo = _classify_cluster(
                parent_tables, list(sel), list(whr), existing_subcommands
            )
            # Find a sample query for this bucket.
            sample_sql = parsed_samples.get(shape, "")
            entry: dict[str, Any] = {
                "select_cols": list(sel),
                "where_cols": list(whr),
                "count": len(bucket),
                "sample_query": sample_sql[:200] if sample_sql else "",
                "promotion_kind": promo,
            }
            # For "missing" clusters, include a recommended canned name.
            if promo == "missing":
                pt = _primary_table(parsed_list)
                if pt:
                    entry["recommended_name"] = f"{parsed_list[0]['verb']} {pt}"
            tier2_entries.append(entry)
            unique_tier2 += 1
        # Sort tier2 by count desc.
        tier2_entries.sort(key=lambda e: -e["count"])
        tier1_clusters.append({
            "shape": shape,
            "count": len(parsed_list),
            "tier2": tier2_entries,
        })

    # Sort tier1 by count desc.
    tier1_clusters.sort(key=lambda c: -c["count"])

    return {
        "tier1_clusters": tier1_clusters,
        "stats": {
            "total_invocations": total,
            "sql_invocations_seen": sql_invocations,
            "unique_tier1": len(tier1_clusters),
            "unique_tier2": unique_tier2,
            "parse_failures": parse_failures,
        },
    }


def discover_subcommands_from_plugin(plugin_path: Optional[str]) -> list[dict]:
    """Best-effort enumerate subcommand records from a plugin's bin/.

    For Gate A.2 this is intentionally minimal - we just collect file
    names under bin/ as candidate "subcommand" labels. A.3 / future
    gates will introspect argparse help to fill `tables` / `columns`.
    Returns [] if path missing or unreadable.
    """
    if not plugin_path:
        return []
    pp = Path(plugin_path)
    if not pp.is_dir():
        return []
    bin_dir = pp / "bin"
    if not bin_dir.is_dir():
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for entry in sorted(bin_dir.iterdir()):
        if not entry.is_file():
            continue
        name = entry.name
        # Strip .py / .sh and skip private modules.
        stem = name.rsplit(".", 1)[0] if "." in name else name
        if stem.startswith("_") or stem in seen:
            continue
        seen.add(stem)
        out.append({"name": stem})
    # Also scan _subcommands/ if present (skill-plus-style layout).
    sub_dir = bin_dir / "_subcommands"
    if sub_dir.is_dir():
        for entry in sorted(sub_dir.iterdir()):
            if not entry.is_file() or not entry.name.endswith(".py"):
                continue
            stem = entry.stem
            if stem.startswith("_") or stem in seen:
                continue
            seen.add(stem)
            out.append({"name": stem})
    return out


__all__ = [
    "cluster_invocations",
    "discover_subcommands_from_plugin",
    "extract_sql",
    "parse_sql",
]
