#!/usr/bin/env python3
"""work_item_reconcile.py -- ONE reconciliation across the BIG-3 work_item SSOT.

Substrate 2.0 (ask 4494; bus #73169/#73176/#73179/#73195; migration 114; Musa
op#34969). migration 114 dual-writes operator_asks + the requires_response subset of
agent_messages + held_commitments into ONE typed table, work_item, by trigger. The
triggers are deliberately fail-SAFE for the source tables (orch-console gate #73195):
a mirror exception degrades to "work_item missing/stale for that one row", never an
aborted source write. THIS script is the drift backstop that gate named -- it finds
every way work_item can diverge from its three sources and (optionally) repairs it
through the SAME work_item_mirror()/work_item_derive_*() SQL functions the triggers
use, so there is exactly ONE definition of "what state should this row be in".

Divergence classes (each row of the report carries one):

  MISSING       an OPEN source row with no work_item row at all (mirror skipped; a
                closed/historical source row with no work_item row is NOT a finding --
                migration 114 backfilled currently-open rows only, by design).
  STALE         a work_item row whose stored (state, owner_agent) differs from what
                work_item_derive_<kind>() derives from the source row right now.
  SOURCE_GONE   a LIVE work_item row whose source row no longer exists (migration 114
                has no DELETE trigger).
  OUT_OF_SCOPE  a LIVE kind='bus' row whose agent_messages row is no longer
                requires_response (the trigger's WHEN clause stops firing once the
                flag flips false, so the mirror can never close it on its own).
  UNKNOWN_SRC   a work_item row naming a source_table this script does not know
                (defensive; should be impossible under the kind CHECK).
  ABANDONED     the cross-kind DROP-INVARIANT watchdog (orch-console #73179 item (d),
                reading confirmed there: "abandoned mid-state", since done/dropped are
                already attribution-forced by the CHECK constraints): a row in
                owned/in_progress whose updated_at has not moved for longer than the
                activity floor AND whose due_at is NULL or already past (a row parked
                against a future date is waiting, not abandoned). ABANDONED is a PAGE,
                not a repair -- nobody but the owner can legitimately move it.

Modes:
  default            dry-run: report, exit 0 if clean, exit 2 if any divergence.
  --repair           re-mirror MISSING/STALE through work_item_mirror(); mark
                     SOURCE_GONE / OUT_OF_SCOPE rows dropped with an explicit,
                     attributable reason (the DROP-INVARIANT CHECKs force that).
                     ABANDONED is never auto-repaired.
  --page             post ONE bus row to orch-console (P1, requires_response) per
                     run that has any finding, de-duplicated per (class, source row)
                     via --page-state for --repage-hours. Fail-loud, per #73179 (d):
                     detection pages even when --repair is off.

Actor convention: every write this script makes is attributed to the literal tag
ACTOR ('reconcile:work_item_reconcile') -- provenance, not an agent. Readers must
treat 'source:*' AND 'reconcile:*' actors as non-agents (migration 114 header).

Never run --repair against the substrate without an orch-console gate: the store is
tscuymavysscrvoberrr (docs/data-store-registry.md). Dry-run is read-only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row

_REPO_ROOT = Path(__file__).resolve().parent.parent

ACTOR = "reconcile:work_item_reconcile"
NON_AGENT_ACTOR_PREFIXES = ("source:", "reconcile:", "trigger:", "backfill:")

DEFAULT_ABANDONED_FLOOR_HOURS = 72   # proposed; orch-console ratifies (#73179 item d)
DEFAULT_REPAGE_HOURS = 24
LIVE_STATES = ("captured", "owned", "in_progress")
TERMINAL_STATES = ("done", "dropped")

# ── kind registry: ONE place that knows how each source maps onto the derive fn ──────
# `derive` is the SQL expression (over alias `s`) calling the migration-114 function;
# `open_pred` is the canonical "this source row is open" predicate (over alias `s`),
# matching the open-row filter migration 114's backfill used, tightened for the two
# cases the sources themselves treat as terminal (see the equivalence-proof header).
KINDS: Dict[str, Dict[str, str]] = {
    "ask": {
        "table": "operator_asks",
        "derive": ("work_item_derive_ask(s.id, s.ask, s.triage_state, s.closed_at, "
                   "s.delegated_to, s.committed_date, s.triaged_by)"),
        "open_pred": "s.closed_at IS NULL AND s.triage_state NOT IN ('done','not_an_ask')",
        "scope_pred": "TRUE",
    },
    "bus": {
        "table": "agent_messages",
        "derive": ("work_item_derive_bus(s.id, s.subject, s.responded_at, s.skipped_at, "
                   "s.claimed_by, s.response_ref)"),
        "open_pred": "s.requires_response AND s.responded_at IS NULL AND s.skipped_at IS NULL",
        "scope_pred": "s.requires_response",
    },
    "commitment": {
        "table": "held_commitments",
        "derive": ("work_item_derive_commitment(s.id, s.title, s.status, s.owner_agent, "
                   "s.due_at, s.blocked_on, s.discharged_at, s.discharged_by, "
                   "s.cancelled_at, s.cancel_reason)"),
        "open_pred": "s.status IN ('pending','fired')",
        "scope_pred": "TRUE",
    },
}
TABLE_TO_KIND = {v["table"]: k for k, v in KINDS.items()}


@dataclass
class Finding:
    cls: str
    kind: str
    source_table: str
    source_id: Optional[int]
    work_item_id: Optional[int]
    stored_state: Optional[str]
    stored_owner: Optional[str]
    derived_state: Optional[str]
    derived_owner: Optional[str]
    detail: str = ""

    def line(self) -> str:
        return (f"{self.cls:<12} {self.kind:<10} {self.source_table}#{self.source_id} "
                f"wi={self.work_item_id} stored=({self.stored_state},{self.stored_owner}) "
                f"derived=({self.derived_state},{self.derived_owner}) {self.detail}").rstrip()


# ── detection (read-only) ────────────────────────────────────────────────────────────
def _missing_and_stale(cur, kind: str) -> List[Finding]:
    k = KINDS[kind]
    # Derive over IN-SCOPE source rows only (a bus row that left requires_response is
    # OUT_OF_SCOPE's domain, not a derive comparison): every open source row (MISSING
    # candidates) + every source row that already has a mirror (STALE candidates).
    # Closed rows with no mirror are skipped by construction -- historical, never
    # backfilled, not a finding.
    cur.execute(f"""
        WITH src AS (
            SELECT s.id AS source_id, ({k['derive']}) AS d
              FROM {k['table']} s
             WHERE ({k['scope_pred']})
               AND (({k['open_pred']})
                    OR EXISTS (SELECT 1 FROM work_item w
                                WHERE w.source_table = %(tbl)s AND w.source_id = s.id))
        )
        SELECT src.source_id,
               (src.d).state AS derived_state, (src.d).owner_agent AS derived_owner,
               w.id AS work_item_id, w.state AS stored_state, w.owner_agent AS stored_owner
          FROM src
          LEFT JOIN work_item w ON w.source_table = %(tbl)s AND w.source_id = src.source_id
         WHERE (w.id IS NULL AND (src.d).state NOT IN ('done','dropped'))
            OR (w.id IS NOT NULL AND ((src.d).state IS DISTINCT FROM w.state
                                      OR (src.d).owner_agent IS DISTINCT FROM w.owner_agent))
         ORDER BY src.source_id
    """, {"tbl": k["table"]})
    out = []
    for r in cur.fetchall():
        out.append(Finding(
            cls="MISSING" if r["work_item_id"] is None else "STALE",
            kind=kind, source_table=k["table"], source_id=r["source_id"],
            work_item_id=r["work_item_id"], stored_state=r["stored_state"],
            stored_owner=r["stored_owner"], derived_state=r["derived_state"],
            derived_owner=r["derived_owner"]))
    return out


def _source_gone(cur, kind: str) -> List[Finding]:
    k = KINDS[kind]
    cur.execute(f"""
        SELECT w.id, w.source_id, w.state, w.owner_agent
          FROM work_item w
         WHERE w.source_table = %(tbl)s
           AND w.state IN ('captured','owned','in_progress')
           AND NOT EXISTS (SELECT 1 FROM {k['table']} s WHERE s.id = w.source_id)
         ORDER BY w.id
    """, {"tbl": k["table"]})
    # Only LIVE rows: a terminal row whose source was later deleted is history (and is
    # exactly what repair() leaves behind), not a drift.
    return [Finding("SOURCE_GONE", kind, k["table"], r["source_id"], r["id"], r["state"],
                    r["owner_agent"], "dropped", None, detail="source row no longer exists")
            for r in cur.fetchall()]


def _out_of_scope(cur) -> List[Finding]:
    # Only kind='bus' has a scope predicate narrower than "the row exists".
    k = KINDS["bus"]
    cur.execute(f"""
        SELECT w.id, w.source_id, w.state, w.owner_agent
          FROM work_item w
          JOIN {k['table']} s ON s.id = w.source_id
         WHERE w.source_table = %(tbl)s
           AND w.state IN ('captured','owned','in_progress')
           AND NOT ({k['scope_pred']})
         ORDER BY w.id
    """, {"tbl": k["table"]})
    return [Finding("OUT_OF_SCOPE", "bus", k["table"], r["source_id"], r["id"], r["state"],
                    r["owner_agent"], "dropped", None,
                    detail="requires_response flipped false after mirror")
            for r in cur.fetchall()]


def _unknown_source(cur) -> List[Finding]:
    cur.execute("SELECT id, kind, source_table, source_id, state, owner_agent FROM work_item "
                "WHERE source_table <> ALL(%s) ORDER BY id", (list(TABLE_TO_KIND),))
    return [Finding("UNKNOWN_SRC", r["kind"], r["source_table"], r["source_id"], r["id"],
                    r["state"], r["owner_agent"], None, None) for r in cur.fetchall()]


def _abandoned(cur, now: datetime, floor_hours: float) -> List[Finding]:
    cur.execute("""
        SELECT id, kind, source_table, source_id, state, owner_agent, updated_at
          FROM work_item
         WHERE state IN ('owned','in_progress') AND updated_at < %s
           AND (due_at IS NULL OR due_at <= %s)   -- a future due date is parked, not abandoned
         ORDER BY updated_at
    """, (now - timedelta(hours=floor_hours), now))
    out = []
    for r in cur.fetchall():
        age_h = (now - r["updated_at"]).total_seconds() / 3600.0
        out.append(Finding("ABANDONED", r["kind"], r["source_table"], r["source_id"], r["id"],
                           r["state"], r["owner_agent"], None, None,
                           detail=f"no activity for {age_h:.0f}h (floor {floor_hours:g}h)"))
    return out


def find_divergence(conn, *, now: Optional[datetime] = None,
                    abandoned_floor_hours: float = DEFAULT_ABANDONED_FLOOR_HOURS,
                    include_abandoned: bool = True) -> List[Finding]:
    """Every divergence between work_item and its three sources, read-only."""
    now = now or datetime.now(timezone.utc)
    findings: List[Finding] = []
    with conn.cursor(row_factory=dict_row) as cur:
        for kind in KINDS:
            findings += _missing_and_stale(cur, kind)
            findings += _source_gone(cur, kind)
        findings += _out_of_scope(cur)
        findings += _unknown_source(cur)
        if include_abandoned:
            findings += _abandoned(cur, now, abandoned_floor_hours)
    return findings


def is_agent_actor(actor: Optional[str]) -> bool:
    """A 'source:*' / 'reconcile:*' / trigger / backfill tag is provenance, never a
    live agent's own discharge (migration 114 header convention)."""
    return bool(actor) and not actor.startswith(NON_AGENT_ACTOR_PREFIXES)


# ── repair (writes; gated by --repair) ───────────────────────────────────────────────
def repair(conn, findings: List[Finding], *, actor: str = ACTOR) -> Dict[str, int]:
    """Re-mirror MISSING/STALE via the migration's own upsert+audit path; drop
    SOURCE_GONE/OUT_OF_SCOPE live rows with an attributable reason (audit row written).
    ABANDONED is deliberately untouched. Returns counts per class repaired."""
    done: Dict[str, int] = {}
    with conn.cursor() as cur:
        for f in findings:
            if f.cls in ("MISSING", "STALE"):
                k = KINDS[f.kind]
                cur.execute(
                    f"SELECT work_item_mirror(%s, %s, s.id, ({k['derive']}), %s) "
                    f"FROM {k['table']} s WHERE s.id = %s",
                    (f.kind, k["table"], actor, f.source_id))
            elif f.cls in ("SOURCE_GONE", "OUT_OF_SCOPE"):
                if f.stored_state not in LIVE_STATES:
                    continue  # already terminal; nothing to close
                reason = ("source row deleted (no DELETE trigger; migration 114)"
                          if f.cls == "SOURCE_GONE"
                          else "source left requires_response scope after mirror")
                cur.execute("""
                    UPDATE work_item
                       SET state = 'dropped', dropped_at = now(), dropped_by = %s,
                           drop_reason = %s, updated_at = now()
                     WHERE id = %s AND state IN ('captured','owned','in_progress')
                """, (actor, reason, f.work_item_id))
                if cur.rowcount:
                    cur.execute("INSERT INTO work_item_audit (work_item_id, event, actor, note) "
                                "VALUES (%s, 'state_changed', %s, %s)",
                                (f.work_item_id, actor, f"{f.stored_state} -> dropped ({f.cls})"))
            else:
                continue  # ABANDONED / UNKNOWN_SRC: page, never auto-repair
            done[f.cls] = done.get(f.cls, 0) + 1
    conn.commit()
    return done


# ── paging (fail-loud; every class, one bus row per run) ─────────────────────────────
def _default_send_page(subject: str, body: str) -> None:
    # bus_send.py resolves identity fail-closed and makes priority mandatory (CLAUDE.md).
    subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "bus_send.py"),
         "--to", "orch-console", "--type", "alert", "--priority", "P1", "--req",
         "--subject", subject[:300]],
        input=body, text=True, check=True)


def load_page_state(path: Optional[Path]) -> Dict[str, str]:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_page_state(path: Optional[Path], state: Dict[str, str]) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=1, sort_keys=True))


def page_key(f: Finding) -> str:
    return f"{f.cls}:{f.source_table}:{f.source_id}"


def page_findings(findings: List[Finding], *, now: datetime, page_state: Dict[str, str],
                  repage_hours: float = DEFAULT_REPAGE_HOURS,
                  send_page: Callable[[str, str], None] = _default_send_page) -> List[Finding]:
    """ONE bus page per run covering every finding not paged within repage_hours.
    Mutates page_state (page_key -> last paged ISO ts). Returns the rows paged."""
    due = []
    for f in findings:
        last = page_state.get(page_key(f))
        if last and now - datetime.fromisoformat(last) < timedelta(hours=repage_hours):
            continue
        due.append(f)
    if not due:
        return []
    counts = summarize(due)
    structural = [f for f in due if f.cls != "ABANDONED"]
    abandoned = [f for f in due if f.cls == "ABANDONED"]
    parts = ["work_item_reconcile.py (migration 114 drift backstop, #73195; DROP-INVARIANT "
             f"watchdog, #73179 item d): {len(due)} finding(s) -- "
             + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + "."]
    if structural:
        parts.append("\nSTRUCTURAL (work_item disagrees with its source; repairable via "
                     "--repair after a gate, never auto-applied by the scheduled dry-run):")
        parts += [f"- {f.line()}" for f in structural[:100]]
    if abandoned:
        parts.append("\nABANDONED (owned/in_progress, no activity past the floor, no future "
                     "due date; only the owner can move these -- a page, never a repair):")
        parts += [f"- work_item #{f.work_item_id} ({f.kind}, {f.source_table}#{f.source_id}) "
                  f"state={f.stored_state} owner={f.stored_owner}: {f.detail}"
                  for f in abandoned[:100]]
    if len(due) > 200:
        parts.append(f"\n(truncated; {len(due)} total -- run with --json for the full list)")
    subject = "work_item_reconcile: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
    send_page(subject, "\n".join(parts))
    for f in due:
        page_state[page_key(f)] = now.isoformat()
    return due


# ── CLI ──────────────────────────────────────────────────────────────────────────────
def summarize(findings: List[Finding]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for f in findings:
        out[f.cls] = out.get(f.cls, 0) + 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dsn", default=None, help="defaults to $DATABASE_URL")
    ap.add_argument("--repair", action="store_true", help="write repairs (gated; see header)")
    ap.add_argument("--page", action="store_true", help="page orch-console on any finding")
    ap.add_argument("--abandoned-floor-hours", type=float, default=DEFAULT_ABANDONED_FLOOR_HOURS)
    ap.add_argument("--repage-hours", type=float, default=DEFAULT_REPAGE_HOURS)
    ap.add_argument("--page-state", type=Path,
                    default=_REPO_ROOT / "state" / "work_item_reconcile_pages.json")
    ap.add_argument("--json", action="store_true", help="machine-readable findings on stdout")
    args = ap.parse_args(argv)

    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        print("work_item_reconcile: no --dsn and DATABASE_URL unset", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc)
    with psycopg.connect(dsn) as conn:
        if not args.repair:
            conn.read_only = True
        findings = find_divergence(conn, now=now, abandoned_floor_hours=args.abandoned_floor_hours)
        repaired: Dict[str, int] = {}
        if args.repair and findings:
            repaired = repair(conn, findings)

    paged: List[Finding] = []
    if args.page:
        state = load_page_state(args.page_state)
        paged = page_findings(findings, now=now, page_state=state, repage_hours=args.repage_hours)
        save_page_state(args.page_state, state)

    if args.json:
        print(json.dumps({"at": now.isoformat(), "summary": summarize(findings),
                          "repaired": repaired, "paged": len(paged),
                          "findings": [asdict(f) for f in findings]}, indent=1, default=str))
    else:
        print(f"work_item_reconcile @ {now.isoformat()} -- "
              f"{'CLEAN' if not findings else 'DIVERGENT'} {summarize(findings) or ''}")
        for f in findings:
            print("  " + f.line())
        if repaired:
            print(f"repaired: {repaired}")
        if paged:
            print(f"paged orch-console for {len(paged)} finding(s)")
    return 0 if not findings else 2


if __name__ == "__main__":
    raise SystemExit(main())
