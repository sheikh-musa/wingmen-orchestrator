"""One-shot backfill of cc_session_costs from historical jsonls.

Operator-invokable per CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME [E].

Usage:
    python scripts/cc_session_costs_backfill.py             # last 7 days
    python scripts/cc_session_costs_backfill.py --days 30   # custom window
    python scripts/cc_session_costs_backfill.py --since-mtime 1700000000

Idempotent: UPSERT keyed on (session_id, source='backfill_v1'). Re-runs
update existing rows in place.
"""
from __future__ import annotations

import argparse
import os
import sys
import time as _time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.cc_session_costs_auto_writer import sweep_projects_root, upsert_rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    p.add_argument(
        "--since-mtime",
        type=float,
        default=None,
        help="Override: epoch-seconds cutoff (else computed from --days)",
    )
    p.add_argument(
        "--projects-root",
        type=str,
        default=str(Path.home() / ".claude" / "projects"),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print summary, do not upsert",
    )
    args = p.parse_args()

    cutoff = (
        args.since_mtime if args.since_mtime is not None else (_time.time() - args.days * 86400)
    )

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn and not args.dry_run:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1

    print(f"scanning {args.projects_root} for jsonls modified since epoch {cutoff:.0f}...")
    rows = sweep_projects_root(Path(args.projects_root), modified_since=cutoff)
    print(f"  found {len(rows)} session rows to upsert")
    if not rows:
        return 0

    summary = defaultdict(
        lambda: {"sessions": 0, "input": 0, "output": 0, "cache_create": 0, "cache_read": 0}
    )
    for r in rows:
        s = summary[r["cc_identity"]]
        s["sessions"] += 1
        s["input"] += r["input_tokens"]
        s["output"] += r["output_tokens"]
        s["cache_create"] += r["cache_creation_input_tokens"]
        s["cache_read"] += r["cache_read_input_tokens"]

    print("\nper-CC summary (pre-upsert):")
    for cc, s in sorted(summary.items()):
        total = s["input"] + s["output"] + s["cache_create"] + s["cache_read"]
        print(
            f"  {cc}: sessions={s['sessions']} "
            f"in={s['input']:,} out={s['output']:,} "
            f"cache_create={s['cache_create']:,} cache_read={s['cache_read']:,} "
            f"total={total:,}"
        )

    if args.dry_run:
        print("\n--dry-run set; not upserting")
        return 0

    written = upsert_rows(dsn, rows, source="backfill_v1")
    print(f"\nupserted {written} rows (source=backfill_v1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
