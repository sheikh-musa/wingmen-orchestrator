#!/usr/bin/env python3
"""check_audit_boundary_mirror.py — FAIL-CLOSED drift check (CAI-RESP-563 §4).

The ihsanos app cannot read the hub substrate at runtime, so it carries a MIRROR
of the declared audit verifiability boundary in
`src/core/audit/verifiability-boundaries.ts`. The substrate table
`audit_chain_boundaries` is the record of truth.

A derived copy nobody can compare to its origin is not a mirror, it's a fork.
So this check compares them and **FAILS CLOSED**: if the hub cannot be reached,
or DATABASE_URL is unset, or the row is missing, it EXITS NON-ZERO. It must never
skip — a skip-when-unreachable check is the absent-dependency-degrades-open
defect, and here the consequence is that the mirror silently becomes the truth.

It lives in the orchestrator (not the ihsanos vitest suite) because the hub always
has substrate creds, so fail-closed is honest here rather than permanently red in
an app CI that has none. The ihsanos suite separately pins the mirror against
literals, catching local edits without needing creds.

Exit: 0 = mirror matches substrate. 1 = drift, missing row, or hub unreachable.
"""
import os
import re
import sys
from pathlib import Path

MIRROR = Path(
    os.environ.get("IHSANOS_REPO", str(Path.home() / "wingmen" / "projects" / "ihsanos"))
) / "src" / "core" / "audit" / "verifiability-boundaries.ts"


def fail(msg: str) -> None:
    print(f"[audit-boundary-mirror] FAIL — {msg}", file=sys.stderr)
    sys.exit(1)


def parse_mirror(text: str) -> dict:
    def grab(key: str):
        m = re.search(rf'{key}:\s*"?([^",\n]+)"?,', text)
        return m.group(1).strip() if m else None
    return {
        "project_ref": grab("projectRef"),
        "org_id": grab("orgId"),
        "content_from": grab("contentVerifiableFromId"),
        "below_count": grab("belowCount"),
        "above_count": grab("aboveCount"),
        "below_latest": grab("belowLatestAt"),
        "above_earliest": grab("aboveEarliestAt"),
        "substrate_record": grab("substrateRecord"),
        "reproducing": grab("reproducingCount"),
        "not_reproducing": grab("notReproducingCount"),
    }


def main() -> None:
    if not MIRROR.exists():
        fail(f"mirror file not found: {MIRROR} (set IHSANOS_REPO)")
    mir = parse_mirror(MIRROR.read_text())
    if not mir["project_ref"]:
        fail("could not parse the mirror file — refusing to pass on an unreadable mirror")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        fail("DATABASE_URL unset — cannot reach the substrate. FAIL CLOSED (never skip).")

    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT project_ref, org_id::text, boundary_id, below_count, above_count,
                          to_char(below_latest_at   AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                          to_char(above_earliest_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                          id, reproducing_count, not_reproducing_count, below_status
                     FROM audit_chain_boundaries
                    WHERE project_ref = %s AND org_id::text = %s""",
                (mir["project_ref"], mir["org_id"]),
            )
            row = cur.fetchone()
    except Exception as e:  # unreachable / auth / missing table
        fail(f"could not read the substrate ({type(e).__name__}: {e}). FAIL CLOSED (never skip).")

    if row is None:
        fail(f"no audit_chain_boundaries row for {mir['project_ref']} / {mir['org_id']}")

    (ref, org, bid, below_n, above_n, below_at, above_at, row_id,
     repro_n, not_repro_n, below_status) = row
    problems = []
    # mirror stores the FIRST verifiable id, substrate stores the BOUNDARY id
    if str(bid + 1) != mir["content_from"]:
        problems.append(f"contentVerifiableFromId={mir['content_from']} but substrate boundary_id={bid} (expected {bid + 1})")
    if str(below_n) != mir["below_count"]:
        problems.append(f"belowCount={mir['below_count']} != substrate {below_n}")
    if str(above_n) != mir["above_count"]:
        problems.append(f"aboveCount={mir['above_count']} != substrate {above_n}")
    if below_at != mir["below_latest"]:
        problems.append(f"belowLatestAt={mir['below_latest']} != substrate {below_at}")
    if above_at != mir["above_earliest"]:
        problems.append(f"aboveEarliestAt={mir['above_earliest']} != substrate {above_at}")
    # CAI-RESP-566 §3: the record must be true in BOTH directions — by boundary
    # position AND by actual reproducibility — or it is not citable to an auditor.
    if str(repro_n) != mir["reproducing"]:
        problems.append(f"reproducingCount={mir['reproducing']} != substrate {repro_n}")
    if str(not_repro_n) != mir["not_reproducing"]:
        problems.append(f"notReproducingCount={mir['not_reproducing']} != substrate {not_repro_n}")
    if repro_n is None or not_repro_n is None:
        problems.append("substrate row is missing the reproducibility partition (CAI-566 §3)")
    elif repro_n + not_repro_n != below_n + above_n:
        problems.append(f"partitions do not reconcile: {repro_n}+{not_repro_n} != {below_n}+{above_n}")
    if "NOT GUARANTEED REPRODUCIBLE" not in (below_status or ""):
        problems.append("below_status overstates: must say NOT GUARANTEED REPRODUCIBLE (CAI-566 §3)")
    if mir["substrate_record"] != f"audit_chain_boundaries#{row_id}":
        problems.append(f"substrateRecord={mir['substrate_record']} != audit_chain_boundaries#{row_id}")

    if problems:
        fail("mirror has DRIFTED from the substrate:\n  - " + "\n  - ".join(problems))
    print(f"[audit-boundary-mirror] OK — mirror matches audit_chain_boundaries#{row_id} "
          f"({ref} boundary_id={bid}, below={below_n}, above={above_n})")


if __name__ == "__main__":
    main()
