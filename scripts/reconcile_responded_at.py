#!/usr/bin/env python3
"""reconcile_responded_at.py — fleet-wide RR (requires_response) auto-stamp sweeper
(held_commitment #446, audit-20261006 item 6, bus#54648; cc-fleet-health memory
responded-at-reconcile-gap-and-backlog.md).

THE GAP: a bus row with requires_response=true only gets `responded_at` stamped
when the reply is sent with bus_send's --reply-to (see bus_send.send()). In
practice recipients often answer in-thread without --reply-to'ing the specific
row (e.g. msg #72459, 2026-10-20: "the e2e it approved was ... just never
individually --reply-to'd since I replied further down the same thread
instead"), or a `commitment-sweeper` fire gets closed via `held_commitments`
without ever touching the bus row it produced. Both leave a genuinely-handled
row stuck looking like an unanswered ask forever — a slowly-growing backlog
(~470-600 rows per the memory note) that defeats bus_send's own stamp-or-die
soak (bus #58159) by making its "open RR" signal noise instead of truth.

MATCH TIERS (deliberately only 3, all high-confidence — "never blind-stamp" is
the hard rule; a 4th "fuzzy temporal+subject proximity" tier was considered and
dropped: it is exactly the kind of guess that could mask a genuine drop):
  1. commitment  — from_agent='commitment-sweeper', subject has "COMMITMENT DUE
     #<id>", and held_commitments.id=<id> is discharged. The commitment row IS
     the source of truth for these (they have no human "reply" to find).
  2. thread       — a later message in the SAME thread, from the row's own
     to_agent. This is the common case (see #72459 above): the recipient did
     answer, just not via --reply-to.
  3. backref      — a later message from the row's to_agent, in ANY thread,
     that explicitly cites "#<id>" (word-bounded, so #42 never matches #4200).
Tiers are tried in that order (most specific signal first); first hit wins.

UNMATCHED rows split further:
  - to_page: priority in (P0,P1) AND older than RECONCILE_DROPPED_MIN_AGE_MIN
    (default 360min/6h — deliberately ABOVE priority_sla_watchdog's own
    MAX_VIOLATION_AGE_MIN=300min window (audit-20261006 item 4), so this
    sweeper only ever catches what that real-time watchdog has already aged
    out of, never double-pages something it is still actively working).
  - needs_triage: everything else unmatched — reported, never touched.

ACTIONS (both detect-only by default, staged arming like the rest of this
family — worktree_nodemodules_reaper.py, node_modules_content_dedup.py):
  --apply  stamps verified matches for real: responded_at=now(),
           response_ref=<matched id>, idempotent (WHERE responded_at IS NULL),
           one UPDATE per row so one failure never aborts the batch (fail-loud,
           logged, independent). Every stamp is appended to an audit ledger
           (logs/reconcile_responded_at_ledger.jsonl) so a false match is
           reviewable and reversible by a human/agent re-nulling responded_at.
  --page   sends ONE coalesced re-nudge per recipient for their to_page rows
           (not a page-per-row — exactly the coalescing commitment #445 asks
           for elsewhere, via the shared priority_breakdown helper). Gated on
           fleet_health_lease (CAI-RESP-501): this is a watchdog-pen ACTION,
           so if a different live body holds the pen this run silently
           downgrades to dry-run for paging (stamps still apply normally —
           stamping is bus hygiene, not a pen action). A JSON state file
           tracks already-paged ids so a nightly re-run doesn't re-spam.

Usage (from ~/wingmen/orchestrator):
    .venv/bin/python3 -m scripts.reconcile_responded_at              # DETECT-ONLY
    .venv/bin/python3 -m scripts.reconcile_responded_at --apply       # stamp verified matches
    .venv/bin/python3 -m scripts.reconcile_responded_at --apply --page  # + coalesced drop-pages
    .venv/bin/python3 -m scripts.reconcile_responded_at --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ORCH = Path(os.path.expanduser("~/wingmen/orchestrator"))
STATE_FILE = ORCH / "logs" / "reconcile_responded_at_paged.json"
LEDGER_FILE = ORCH / "logs" / "reconcile_responded_at_ledger.jsonl"
LOG_FILE = ORCH / "logs" / "reconcile_responded_at.log"

sys.path.insert(0, str(ORCH))

COMMITMENT_SUBJECT_RE = re.compile(r"COMMITMENT DUE #(\d+)")


def _envint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# See module docstring "to_page" — deliberately above SLA_MAX_VIOLATION_AGE_MIN (300).
DROPPED_MIN_AGE_MIN = _envint("RECONCILE_DROPPED_MIN_AGE_MIN", 360)

# See classify()'s pageable_max_age docstring note — caps active paging to a
# window where the ask is still plausibly live/actionable. Default 14 days.
PAGEABLE_MAX_AGE_MIN = _envint("RECONCILE_PAGEABLE_MAX_AGE_MIN", 14 * 24 * 60)


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} | {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


@dataclass
class Match:
    candidate_id: int
    response_ref: str
    tier: str  # "commitment" | "thread" | "backref"
    matched_id: "int | None"
    note: str


# ---------------------------------------------------------------------------
# Pure logic — no DB, no network. Fully unit-tested (tests/test_reconcile_responded_at.py).
# ---------------------------------------------------------------------------

def parse_commitment_id(subject: str) -> "int | None":
    m = COMMITMENT_SUBJECT_RE.search(subject or "")
    return int(m.group(1)) if m else None


def index_replies_by_thread(replies: list[dict]) -> dict[tuple, list[dict]]:
    idx: dict[tuple, list[dict]] = defaultdict(list)
    for r in replies:
        idx[(r["thread_id"], r["from_agent"])].append(r)
    for k in idx:
        idx[k].sort(key=lambda r: r["created_at"])
    return idx


def match_via_commitment(candidate: dict, commitments: dict[int, dict]) -> "Match | None":
    if candidate.get("from_agent") != "commitment-sweeper":
        return None
    cid = parse_commitment_id(candidate.get("subject") or "")
    if cid is None:
        return None
    c = commitments.get(cid)
    if not c or not c.get("discharged_at"):
        return None
    return Match(
        candidate["id"],
        f"commitment:{cid}:discharged_by={c.get('discharged_by')}",
        "commitment",
        None,
        f"held_commitments #{cid} discharged_at={c['discharged_at']}",
    )


def match_via_thread(candidate: dict, thread_index: dict) -> "Match | None":
    key = (candidate["thread_id"], candidate["to_agent"])
    for r in thread_index.get(key, []):
        if r["created_at"] > candidate["created_at"]:
            return Match(
                candidate["id"], str(r["id"]), "thread", r["id"],
                f"same-thread reply from {candidate['to_agent']} (id={r['id']})",
            )
    return None


_BACKREF_TOKEN_RE = re.compile(r"(?<!\d)#(\d+)(?!\d)")


def index_backreferences(replies: list[dict]) -> dict[int, list[dict]]:
    """One pass over every reply, extracting every '#<id>' token it cites, so
    matching a candidate is an O(1) dict lookup instead of re-scanning every
    reply from that recipient per candidate (the naive version was O(candidates
    x replies-per-agent) and visibly thrashed CPU on the ~2300-row live backlog)."""
    idx: dict[int, list[dict]] = defaultdict(list)
    for r in replies:
        text = f"{r.get('subject') or ''} {r.get('body') or ''}"
        for tok in _BACKREF_TOKEN_RE.finditer(text):
            idx[int(tok.group(1))].append(r)
    for k in idx:
        idx[k].sort(key=lambda r: r["created_at"])
    return idx


def match_via_backreference(candidate: dict, backref_index: dict) -> "Match | None":
    for r in backref_index.get(candidate["id"], []):
        if r["from_agent"] != candidate["to_agent"]:
            continue
        if r["created_at"] <= candidate["created_at"]:
            continue
        return Match(
            candidate["id"], str(r["id"]), "backref", r["id"],
            f"explicit #{candidate['id']} backreference from {candidate['to_agent']} (id={r['id']})",
        )
    return None


def classify(candidates: list[dict], *, thread_index: dict, backref_index: dict,
             commitments: dict[int, dict], now: datetime,
             dropped_min_age: timedelta, pageable_max_age: timedelta) -> dict:
    """pageable_max_age caps how OLD an unmatched P0/P1 may be and still get an
    active re-nudge page. Live data (first dry-run, 2026-10-20) showed the
    unmatched-P0/P1 pool is dominated by 60-126 day-old rows addressed to
    long-gone ephemeral worker lanes (cc-irsyad-6, cc-shipforge, cc-reviewer,
    ...) — paging a dead/renamed recipient about a 4-month-old ask is noise,
    not help (the exact "paging a dead body" problem #444 targets elsewhere).
    Anything older than pageable_max_age is reported as stale_backlog instead:
    visible for human/archival triage, never auto-paged."""
    matches: list[Match] = []
    unmatched: list[dict] = []
    for c in candidates:
        m = (match_via_commitment(c, commitments)
             or match_via_thread(c, thread_index)
             or match_via_backreference(c, backref_index))
        if m:
            matches.append(m)
        else:
            unmatched.append(c)

    to_page: list[dict] = []
    stale_backlog: list[dict] = []
    needs_triage: list[dict] = []
    for c in unmatched:
        age = now - c["created_at"]
        if c.get("priority") in ("P0", "P1") and age >= dropped_min_age:
            if age <= pageable_max_age:
                to_page.append(c)
            else:
                stale_backlog.append(c)
        else:
            needs_triage.append(c)
    return {
        "matches": matches, "to_page": to_page,
        "stale_backlog": stale_backlog, "needs_triage": needs_triage,
    }


def coalesce_by_recipient(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[r["to_agent"]].append(r)
    return grouped


def build_page_body(recipient: str, rows: list[dict], *, now: datetime) -> tuple[str, str]:
    from scripts.lib.priority_breakdown import breakdown_line
    breakdown = breakdown_line([r["priority"] for r in rows])
    subject = f"[rr-reconcile] {len(rows)} dropped requires-response row(s) unanswered ({breakdown})"
    lines = [
        f"TL;DR: {len(rows)} old ask(s) addressed to you never got a tracked response "
        f"({breakdown}) — please answer them, or reply in-thread/with --reply-to if "
        f"already handled so this stops re-firing.",
        "",
    ]
    for r in sorted(rows, key=lambda x: x["created_at"]):
        age = now - r["created_at"]
        lines.append(
            f"  #{r['id']} from={r['from_agent']} prio={r['priority']} "
            f"age={age.days}d subject={r['subject']!r}"
        )
    return subject, "\n".join(lines)


# ---------------------------------------------------------------------------
# DB I/O
# ---------------------------------------------------------------------------

def _dsn() -> str:
    from dotenv import load_dotenv
    load_dotenv(str(ORCH / ".env"))
    return os.environ.get("SUPABASE_DB_URL") or os.environ["DATABASE_URL"]


def _connect():
    import psycopg
    return psycopg.connect(_dsn(), connect_timeout=15)


def fetch_candidates(conn) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT id, thread_id, from_agent, to_agent, subject, body, priority, created_at "
        "FROM agent_messages WHERE requires_response = true AND responded_at IS NULL "
        "ORDER BY created_at"
    )
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_replies(conn, agents: set[str], since: datetime) -> list[dict]:
    if not agents:
        return []
    cur = conn.cursor()
    cur.execute(
        "SELECT id, thread_id, from_agent, subject, body, created_at "
        "FROM agent_messages WHERE from_agent = ANY(%s) AND created_at > %s",
        (list(agents), since),
    )
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_commitments(conn, ids: set[int]) -> dict[int, dict]:
    if not ids:
        return {}
    cur = conn.cursor()
    cur.execute(
        "SELECT id, discharged_at, discharged_by FROM held_commitments WHERE id = ANY(%s)",
        (list(ids),),
    )
    out = {}
    for row_id, discharged_at, discharged_by in cur.fetchall():
        out[row_id] = {"discharged_at": discharged_at, "discharged_by": discharged_by}
    return out


def apply_stamps(conn, matches: list[Match]) -> dict:
    applied, errors = [], []
    cur = conn.cursor()
    for m in matches:
        try:
            cur.execute(
                "UPDATE agent_messages SET responded_at = now(), response_ref = %s "
                "WHERE id = %s AND responded_at IS NULL",
                (m.response_ref, m.candidate_id),
            )
            conn.commit()
            if cur.rowcount:
                applied.append(m)
                try:
                    LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with open(LEDGER_FILE, "a") as f:
                        f.write(json.dumps({
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "candidate_id": m.candidate_id,
                            "response_ref": m.response_ref,
                            "tier": m.tier,
                            "note": m.note,
                        }) + "\n")
                except Exception as e:
                    log(f"ledger-write-failed for #{m.candidate_id}: {e}")
        except Exception as e:
            conn.rollback()
            errors.append((m.candidate_id, str(e)))
            log(f"stamp-FAILED #{m.candidate_id}: {e}")
    return {"applied": applied, "errors": errors}


def _load_paged_state() -> set[int]:
    try:
        return set(json.loads(STATE_FILE.read_text()).get("paged_ids", []))
    except Exception:
        return set()


def _save_paged_state(ids: set[int]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"paged_ids": sorted(ids)}))
    except Exception as e:
        log(f"paged-state-write-failed: {e}")


def send_pages(to_page: list[dict], *, now: datetime, dsn: str) -> dict:
    from scripts import bus_send

    already = _load_paged_state()
    fresh = [r for r in to_page if r["id"] not in already]
    sent, errors = [], []
    for recipient, rows in coalesce_by_recipient(fresh).items():
        subject, body = build_page_body(recipient, rows, now=now)
        try:
            row_id, _thread = bus_send.send(
                "cc-fleet-health", recipient, "blocker", subject, body, "P1",
                req=True, dsn=dsn,
            )
            sent.append({"recipient": recipient, "bus_id": row_id, "ids": [r["id"] for r in rows]})
            already.update(r["id"] for r in rows)
        except Exception as e:
            errors.append({"recipient": recipient, "error": str(e), "ids": [r["id"] for r in rows]})
            log(f"page-FAILED recipient={recipient}: {e}")
    _save_paged_state(already)
    return {"sent": sent, "errors": errors}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="stamp verified matches for real")
    ap.add_argument("--page", action="store_true", help="send coalesced drop-pages for real")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)

    dsn = _dsn()
    try:
        conn = _connect()
    except Exception as e:
        log(f"db-connect-failed: {e}")
        return 1

    now = datetime.now(timezone.utc)
    candidates = fetch_candidates(conn)
    recipients = {c["to_agent"] for c in candidates}
    since = min((c["created_at"] for c in candidates), default=now) - timedelta(minutes=1)
    replies = fetch_replies(conn, recipients, since)
    commitment_ids = {
        cid for c in candidates
        if c.get("from_agent") == "commitment-sweeper"
        and (cid := parse_commitment_id(c.get("subject") or "")) is not None
    }
    commitments = fetch_commitments(conn, commitment_ids)

    result = classify(
        candidates,
        thread_index=index_replies_by_thread(replies),
        backref_index=index_backreferences(replies),
        commitments=commitments,
        now=now,
        dropped_min_age=timedelta(minutes=DROPPED_MIN_AGE_MIN),
        pageable_max_age=timedelta(minutes=PAGEABLE_MAX_AGE_MIN),
    )

    report = {
        "candidates": len(candidates),
        "matches": len(result["matches"]),
        "by_tier": {t: sum(1 for m in result["matches"] if m.tier == t)
                    for t in ("commitment", "thread", "backref")},
        "to_page": len(result["to_page"]),
        "stale_backlog": len(result["stale_backlog"]),
        "needs_triage": len(result["needs_triage"]),
        "mode": "APPLY" if args.apply else "DETECT-ONLY",
    }

    if args.apply:
        applied = apply_stamps(conn, result["matches"])
        report["applied"] = len(applied["applied"])
        report["stamp_errors"] = applied["errors"]
    else:
        report["would_apply"] = len(result["matches"])

    if args.page:
        from scripts.lib import fleet_health_lease
        ok, why = fleet_health_lease.gate()
        if not ok:
            log(f"pen-gate: paging DEFERRED (CAI-RESP-501) — {why}; dry-run for --page this run")
            report["page_mode"] = "DEFERRED(pen-gate)"
            report["would_page"] = len(coalesce_by_recipient(result["to_page"]))
        else:
            paged = send_pages(result["to_page"], now=now, dsn=dsn)
            report["page_mode"] = "SENT"
            report["paged"] = paged["sent"]
            report["page_errors"] = paged["errors"]
    else:
        report["would_page"] = len(coalesce_by_recipient(result["to_page"]))

    conn.close()

    if args.json:
        print(json.dumps(report, default=str))
    else:
        log(f"candidates={report['candidates']} matches={report['matches']} "
            f"by_tier={report['by_tier']} to_page={report['to_page']} "
            f"needs_triage={report['needs_triage']} mode={report['mode']}")
        if args.apply:
            log(f"applied={report['applied']} stamp_errors={len(report['stamp_errors'])}")
        if args.page:
            log(f"page_mode={report['page_mode']}")

    return 0 if not (report.get("stamp_errors") or report.get("page_errors")) else 1


if __name__ == "__main__":
    sys.exit(main())
