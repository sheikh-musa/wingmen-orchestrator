#!/usr/bin/env python3
"""update_boundary_row.py — GATED refresh of the amnesty_* columns on the orch
substrate's `audit_chain_boundaries` row id=1 (goumlyne / irsyad silo) from the
authoritative frozen list in the ihsanos repo (console #81726).

The frozen row-id list is regenerated in the ihsanos repo
(src/core/audit/frozen-amnesty-goumlyne.json, by scripts/audit/regen-frozen-amnesty.ts).
This script copies that list's ids + snapshot metadata into the substrate row
so the citable record matches the list the verifier actually uses.

Default is a DRY RUN: it reads row id=1 (read-only transaction) and prints the
current amnesty fields next to the new values. Nothing is written.

--apply requires --gate <agent_messages id>. The gate row must exist on the bus,
be from orch-console, be message_type 'decision' or 'agreed', be at most
GATE_MAX_AGE_DAYS old, and mention 'audit_chain_boundaries' in its body. Only
then is row id=1's amnesty_* columns UPDATEd, in ONE transaction, read back and
asserted (count / fingerprint / tip id / tip hash) before COMMIT.

Fingerprint = sha256(",".join(sorted ids))[:8] — the same short fingerprint the
coordinator cites on the bus.

Usage (from a clone of ihsanos, frozen list at a git ref):
    python3 ops/audit-chain-amnesty/update_boundary_row.py \\
        --frozen-git-repo ~/wingmen/projects/ihsanos-irsyad.wt-coord --frozen-git-ref origin/main \\
        --expect-fp 66e57e02 --expect-count 5035 \\
        --tip-hash <64 hex> --decision-ref "console #81492/#81525/#81726" \\
        --rederivation-ref "ihsanos PR#1191 squash 1fe7f11a ..." \\
        [--gate <id> --apply]

DB: the orchestrator bus/substrate DSN via scripts/bus_send.dburl(); the DSN is
never printed, and connection errors are reported without their message text
(libpq errors can carry host/user).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

BOUNDARY_ROW_ID = 1
PROJECT_REF = "goumlynecruxrlmzlntp"
ORG_ID = "73339164-7c1f-40ba-a093-33f1f292dd4c"
FROZEN_PATH_IN_IHSANOS = "src/core/audit/frozen-amnesty-goumlyne.json"

GATE_OWNER = "orch-console"
GATE_TYPES = ("decision", "agreed")
GATE_BODY_TOKEN = "audit_chain_boundaries"
GATE_MAX_AGE_DAYS = 14  # mirrors scripts/apply_migration.py's staleness floor

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class Refuse(Exception):
    """A precondition failed; nothing was written."""


# ── pure helpers (unit-tested) ────────────────────────────────────────────


def fingerprint(ids) -> str:
    """sha256 of the numerically-sorted ids, comma-joined, first 8 hex."""
    joined = ",".join(str(i) for i in sorted(int(x) for x in ids))
    return hashlib.sha256(joined.encode()).hexdigest()[:8]


def parse_frozen(doc: dict) -> dict:
    """Validate the ihsanos frozen-amnesty JSON and return the fields we copy."""
    if not isinstance(doc, dict):
        raise Refuse("frozen file is not a JSON object")
    ids = doc.get("frozen_row_ids")
    if not isinstance(ids, list) or not ids:
        raise Refuse("frozen file has no frozen_row_ids list")
    if any(isinstance(i, bool) or not isinstance(i, int) or i <= 0 for i in ids):
        raise Refuse("frozen_row_ids must be positive integers")
    if len(set(ids)) != len(ids):
        raise Refuse("frozen_row_ids contains duplicates")
    declared = doc.get("count")
    if declared is not None and declared != len(ids):
        raise Refuse(f"frozen file declares count={declared} but lists {len(ids)} ids")
    if doc.get("org_id") not in (None, ORG_ID):
        raise Refuse(f"frozen file org_id {doc.get('org_id')!r} is not the goumlyne org")
    tip = doc.get("amnesty_snapshot_tip_id")
    if not isinstance(tip, int) or isinstance(tip, bool):
        raise Refuse("frozen file has no integer amnesty_snapshot_tip_id")
    if max(ids) > tip:
        raise Refuse(f"frozen id {max(ids)} is above the snapshot tip {tip}")
    snap = doc.get("amnesty_row_id_snapshot_at")
    if not isinstance(snap, str) or not snap:
        raise Refuse("frozen file has no amnesty_row_id_snapshot_at")
    try:
        date.fromisoformat(snap[:10])
    except ValueError as e:
        raise Refuse(f"amnesty_row_id_snapshot_at {snap!r} is not an ISO date") from e
    return {
        "ids": sorted(ids),
        "count": len(ids),
        "fp": fingerprint(ids),
        "tip_id": tip,
        "snapshot_at": snap,
        "mechanism": doc.get("amnesty_mechanism") or "row-id-list",
    }


def check_expectations(frozen: dict, *, expect_fp: str, expect_count: int | None) -> None:
    if frozen["fp"] != expect_fp.lower():
        raise Refuse(f"fingerprint {frozen['fp']} != expected {expect_fp} — wrong or stale frozen list")
    if expect_count is not None and frozen["count"] != expect_count:
        raise Refuse(f"count {frozen['count']} != expected {expect_count}")


def check_tip_hash(tip_hash: str) -> str:
    h = (tip_hash or "").strip().lower()
    if not _HEX64.match(h):
        raise Refuse("--tip-hash must be 64 lowercase hex chars (sha256)")
    return h


def validate_gate_row(row, gate_id: int, *, now: datetime | None = None) -> dict:
    """row = (id, from_agent, message_type, body, created_at) or None."""
    if row is None:
        raise Refuse(f"--gate {gate_id} does not exist in agent_messages")
    row_id, from_agent, mtype, body, created_at = row
    if from_agent != GATE_OWNER:
        raise Refuse(f"--gate {gate_id} is from {from_agent!r}, not {GATE_OWNER!r}")
    if mtype not in GATE_TYPES:
        raise Refuse(f"--gate {gate_id} is message_type={mtype!r}, not one of {GATE_TYPES}")
    if GATE_BODY_TOKEN not in (body or ""):
        raise Refuse(f"--gate {gate_id}'s body does not mention {GATE_BODY_TOKEN!r} — not an authorization for this row")
    now = now or datetime.now(timezone.utc)
    if created_at is not None:
        age = now - created_at
        if age.days > GATE_MAX_AGE_DAYS:
            raise Refuse(f"--gate {gate_id} is {age.days}d old, over the {GATE_MAX_AGE_DAYS}d staleness floor")
    return {"id": row_id, "from_agent": from_agent, "message_type": mtype}


def default_notes(frozen: dict, decision_ref: str) -> str:
    return (
        f"Frozen row-id list refreshed: {frozen['count']} ids, fingerprint {frozen['fp']}, "
        f"snapshot tip {frozen['tip_id']} @ {frozen['snapshot_at']} ({decision_ref}). "
        f"Authoritative list: ihsanos {FROZEN_PATH_IN_IHSANOS} (scripts/audit/regen-frozen-amnesty.ts). "
        "UNPROVEN caller-text rows are held in a separate known bucket, NOT on this list. "
        "See orchestrator ops/audit-chain-amnesty/README.md."
    )


def new_values(frozen: dict, *, tip_hash: str, decision_ref: str, rederivation_ref: str,
               annotated_by: str, notes: str | None) -> dict:
    return {
        "amnesty_mechanism": frozen["mechanism"],
        "amnesty_decision_ref": decision_ref,
        "amnesty_row_id_snapshot_at": frozen["snapshot_at"],
        "amnesty_snapshot_tip_id": frozen["tip_id"],
        "amnesty_snapshot_tip_hash": tip_hash,
        "amnesty_row_id_count": frozen["count"],
        "amnesty_row_ids": frozen["ids"],
        "amnesty_rederivation_ref": rederivation_ref,
        "amnesty_annotated_by": annotated_by,
        "amnesty_notes": notes or default_notes(frozen, decision_ref),
    }


def summarize_row(cur_row: dict | None) -> dict:
    """Payload-free summary of the stored amnesty fields (ids reduced to count+fp)."""
    if cur_row is None:
        return {}
    ids = cur_row.get("amnesty_row_ids") or []
    return {
        "amnesty_mechanism": cur_row.get("amnesty_mechanism"),
        "amnesty_decision_ref": cur_row.get("amnesty_decision_ref"),
        "amnesty_row_id_snapshot_at": str(cur_row.get("amnesty_row_id_snapshot_at")),
        "amnesty_snapshot_tip_id": cur_row.get("amnesty_snapshot_tip_id"),
        "amnesty_snapshot_tip_hash": cur_row.get("amnesty_snapshot_tip_hash"),
        "amnesty_row_id_count": cur_row.get("amnesty_row_id_count"),
        "stored_ids_len": len(ids),
        "stored_ids_fp": fingerprint(ids) if ids else None,
        "amnesty_rederivation_ref": cur_row.get("amnesty_rederivation_ref"),
        "amnesty_annotated_by": cur_row.get("amnesty_annotated_by"),
    }


def assert_readback(stored: dict, frozen: dict, tip_hash: str) -> None:
    ids = stored.get("amnesty_row_ids") or []
    problems = []
    if stored.get("amnesty_row_id_count") != frozen["count"]:
        problems.append(f"count {stored.get('amnesty_row_id_count')} != {frozen['count']}")
    if len(ids) != frozen["count"]:
        problems.append(f"ids len {len(ids)} != {frozen['count']}")
    if not ids or fingerprint(ids) != frozen["fp"]:
        problems.append(f"ids fp {fingerprint(ids) if ids else None} != {frozen['fp']}")
    if stored.get("amnesty_snapshot_tip_id") != frozen["tip_id"]:
        problems.append(f"tip {stored.get('amnesty_snapshot_tip_id')} != {frozen['tip_id']}")
    if stored.get("amnesty_snapshot_tip_hash") != tip_hash:
        problems.append("tip hash mismatch")
    if problems:
        raise Refuse("read-back mismatch, rolled back: " + "; ".join(problems))


# ── I/O ────────────────────────────────────────────────────────────────────

_COLS = (
    "amnesty_mechanism", "amnesty_decision_ref", "amnesty_row_id_snapshot_at",
    "amnesty_snapshot_tip_id", "amnesty_snapshot_tip_hash", "amnesty_row_id_count",
    "amnesty_row_ids", "amnesty_rederivation_ref", "amnesty_annotated_by", "amnesty_notes",
)


def load_frozen_doc(args) -> dict:
    if args.frozen_file:
        return json.loads(Path(args.frozen_file).read_text())
    out = subprocess.run(
        ["git", "-C", args.frozen_git_repo, "show", f"{args.frozen_git_ref}:{FROZEN_PATH_IN_IHSANOS}"],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def _connect():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import psycopg2  # noqa: E402
    from bus_send import dburl  # noqa: E402

    try:
        return psycopg2.connect(dburl(os.environ.get("DATABASE_URL")))
    except Exception as e:  # never surface libpq text (can carry host/user)
        raise Refuse(f"could not connect to the substrate ({type(e).__name__}); DSN not shown") from None


def _fetch_row(cur, *, lock: bool) -> dict | None:
    cur.execute(
        f"SELECT project_ref, org_id::text, {', '.join(_COLS)} FROM audit_chain_boundaries "
        f"WHERE id = %s{' FOR UPDATE' if lock else ''}",
        (BOUNDARY_ROW_ID,),
    )
    r = cur.fetchone()
    if r is None:
        return None
    d = dict(zip(("project_ref", "org_id") + _COLS, r))
    if d["project_ref"] != PROJECT_REF or d["org_id"] != ORG_ID:
        raise Refuse(f"audit_chain_boundaries id={BOUNDARY_ROW_ID} is not the goumlyne row "
                     f"({d['project_ref']}, {d['org_id']}) — refusing")
    return d


def _print_compare(current: dict, new: dict, frozen: dict) -> None:
    cur = summarize_row(current)
    proposed = {k: v for k, v in new.items() if k not in ("amnesty_row_ids", "amnesty_notes")}
    proposed["stored_ids_len"] = frozen["count"]
    proposed["stored_ids_fp"] = frozen["fp"]
    print(f"audit_chain_boundaries id={BOUNDARY_ROW_ID} ({PROJECT_REF})")
    keys = list(dict.fromkeys(list(cur) + list(proposed)))
    for k in keys:
        a, b = cur.get(k), proposed.get(k)
        mark = " " if str(a) == str(b) else "*"
        print(f" {mark} {k}: {a}  ->  {b}")
    print(f"   amnesty_notes -> {new['amnesty_notes']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--frozen-file", help="path to an ihsanos frozen-amnesty-goumlyne.json")
    src.add_argument("--frozen-git-repo", help="ihsanos checkout to read the frozen list from (with --frozen-git-ref)")
    p.add_argument("--frozen-git-ref", default="origin/main")
    p.add_argument("--expect-fp", required=True, help="expected 8-hex fingerprint; refuses on mismatch")
    p.add_argument("--expect-count", type=int, help="expected id count; refuses on mismatch")
    p.add_argument("--tip-hash", required=True, help="audit_log hash of the snapshot tip row")
    p.add_argument("--decision-ref", required=True)
    p.add_argument("--rederivation-ref", required=True)
    p.add_argument("--annotated-by", default="cc-irsyad-coord")
    p.add_argument("--notes", help="amnesty_notes text (default: generated summary)")
    p.add_argument("--gate", type=int, help="agent_messages id authorizing the write (required with --apply)")
    p.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = p.parse_args(argv)

    try:
        if args.apply and args.gate is None:
            raise Refuse("--apply requires --gate <agent_messages id>")
        frozen = parse_frozen(load_frozen_doc(args))
        check_expectations(frozen, expect_fp=args.expect_fp, expect_count=args.expect_count)
        tip_hash = check_tip_hash(args.tip_hash)
        new = new_values(frozen, tip_hash=tip_hash, decision_ref=args.decision_ref,
                         rederivation_ref=args.rederivation_ref, annotated_by=args.annotated_by,
                         notes=args.notes)

        conn = _connect()
        try:
            if not args.apply:
                conn.set_session(readonly=True)
                with conn.cursor() as cur:
                    current = _fetch_row(cur, lock=False)
                conn.rollback()
                if current is None:
                    raise Refuse(f"audit_chain_boundaries id={BOUNDARY_ROW_ID} not found")
                print("DRY RUN (read-only; nothing written)")
                _print_compare(current, new, frozen)
                return 0

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, from_agent, message_type, body, created_at FROM agent_messages WHERE id = %s",
                    (args.gate,),
                )
                validate_gate_row(cur.fetchone(), args.gate)
                current = _fetch_row(cur, lock=True)
                if current is None:
                    raise Refuse(f"audit_chain_boundaries id={BOUNDARY_ROW_ID} not found")
                sets = ", ".join(f"{c} = %({c})s" for c in _COLS)
                cur.execute(
                    f"UPDATE audit_chain_boundaries SET {sets} WHERE id = %(row_id)s "
                    "AND project_ref = %(project_ref)s AND org_id = %(org_id)s::uuid",
                    {**new, "row_id": BOUNDARY_ROW_ID, "project_ref": PROJECT_REF, "org_id": ORG_ID},
                )
                if cur.rowcount != 1:
                    raise Refuse(f"UPDATE touched {cur.rowcount} rows, expected 1")
                stored = _fetch_row(cur, lock=False)
                assert_readback(stored, frozen, tip_hash)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        print(f"APPLIED gate={args.gate}: audit_chain_boundaries id={BOUNDARY_ROW_ID} amnesty "
              f"count={frozen['count']} fp={frozen['fp']} tip={frozen['tip_id']} "
              f"snapshot={frozen['snapshot_at']} (read-back verified)")
        return 0
    except Refuse as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
