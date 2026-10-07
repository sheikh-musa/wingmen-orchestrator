#!/usr/bin/env python3
"""asks_reconcile_daily.py — daily operator_asks ledger reconciliation
(Musa op#27030, verbatim: "so everyday this list should be reconciled so it
doesn't grow absurdly" — orch-console bus #57235/#57245, triggered by 302
stale open rows piling up behind the 05:00 UTC-ish daily digest).

Scheduled via launchd/dev.wingmen.asks-reconcile.plist at 04:30 UTC, BEFORE
scripts/asks_daily_digest.py's run — so a day's close/flag pass always lands
ahead of the digest the operator actually reads. Owned + loaded by
cc-fleet-health (ops-charter ledger upkeep on orchestrator infra, not product
code — same shape as scripts/fleet_health.py's agent_status sweep, just for
the operator_asks table).

FOUR CHECKS, each independently safe to re-run (idempotent) and each gated by
--apply (default is --dry-run: read + print, write/send nothing):

  1. CLOSE (drift repair): a row whose triage_state already says 'done' or
     'not_an_ask' but closed_at is still NULL. This should never happen via
     scripts/asks_triage.py (which always sets both atomically) — it only
     happens when triage_state was written directly (e.g. a bulk triage pass
     over ~300 rows, bypassing the per-row script for speed). Backstops that
     drift daily. WHERE closed_at IS NULL on the UPDATE makes a re-run inert.

  2. UNTRIAGED GUARD (fail-loud): open 'captured' rows older than 24h,
     grouped by the owning channel (resolved via source_msg_id ->
     operator_messages.tag -> CHANNEL_OWNER). One P1 bus row per owner,
     listing ids + an 80-char snippet each (this is the OWNER's own channel
     data reaching the owner, not the digest — migration 082's "never show
     raw captured text" rule is about asks_daily_digest.py's public-ish
     roll-up, which already only ever shows a count; unaffected here). A tag
     with no entry in CHANNEL_OWNER pages orch-console instead of silently
     dropping the rows — fail LOUD on an unmapped channel, never silent.

  3. OVERDUE: triage_state='ask', open, committed_date in the past. One P1
     bus row per row to delegated_to, posted into the ask's own existing bus
     thread (operator_asks.thread_id) rather than a fresh row.

  4. STALE — decide (never auto-closed): triage_state='ask', open, created
     over 14 days ago, with no outbound operator_messages activity on the
     same channel tag in the last 7 days. This is a RECENCY proxy (any
     outbound traffic on the tag, not a verified per-row answer — there is no
     reliable thread-level reply link for a plain tag-scoped channel), so it
     only ever flags for a human keep/close judgment call in the run summary;
     it never closes anything itself.

Every run ends with ONE summary posted to orch-console (bus, never Telegram
directly — scripts/asks_daily_digest.py owns the operator-facing channel).

Usage:
  asks_reconcile_daily.py [--apply] [--untriaged-hours N] [--stale-days N]
                           [--stale-quiet-days N]
    (no --apply)  dry-run: print what WOULD happen, write/send nothing
    --apply       close the drift rows, send the P1 escalations, post the
                  summary to orch-console
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Page-from identity: deliberately cc-fleet-health's OWN already-registered id,
# not a freshly-minted watchdog identity (cf. ddl-coverage-watchdog/ingest-
# watchdog/sla-watchdog) — this job is loaded and owned by cc-fleet-health
# directly (orch-console bus #57235: "your merge-gate, you load the plist"),
# and CAI-RESP-1473 (same day) just drew a careful line around who may add a
# NEW `agents` row; minting one more identity for this one daily job isn't
# worth reopening that question when an already-authorized id does the job.
PAGE_FROM_AGENT = "cc-fleet-health"
PAGE_TO_CONSOLE = "orch-console"

# op#27030 spec's two named examples, plus 6 more cc-quality's PR#325 review
# found by querying the FULL historical tag distribution on operator_asks
# (not just today's open rows): 9 distinct tags have ever produced an ask;
# only 2 were mapped pre-review. Owners below are each the tag's dominant
# historical delegated_to, VERIFIED against the live `agents` table (not
# just copied) -- several historical delegates are dead/unregistered labels
# (cosem-caai's dominant 'cosem-port', 274 historical asks, has no agents
# row at all; cc-cosem-platform, 12 historical asks, does, and its own
# repo_scope already includes 'cosem-port-lane', so the lane was folded into
# it) and would be undeliverable if copied verbatim. 'tmux-console' kept as
# a defensive alias since the spec names both forms for nazim-console's
# owner. Every value here can itself be a BASE id with 0, 1, or several live
# instances -- main()'s _page() helper resolves/refuses accordingly at send
# time, never guessed here.
CHANNEL_OWNER = {
    "nazim-console": "orch-console",
    "tmux-console": "orch-console",
    "orch-channel": "cc-orchestrator",
    "gazzabyte-irsyad": "cc-irsyad-coord",   # 434 historical asks, 434/434 to irsyad-coord
    "cosem-caai": "cc-cosem-platform",       # 287 historical; dominant 'cosem-port' (274) is dead/unregistered, repo_scope shows the lane folded into cc-cosem-platform
    "oeh": "cc-oeh",                          # 106 historical, 105/106 to oeh
    "cosem-exams": "cc-cosem-exams",          # 79 historical, 77/79 to cosem-exams
    "angullia": "cc-angullia",                # 24 historical, 24/24 to angullia
    "cosem-tdu": "cc-cosem-tdu-coord",        # 22 historical, 22/22 to cosem-tdu-coord
}

UNTRIAGED_SNIPPET_CHARS = 80


def _dsn() -> "str | None":
    """Same fallback shape as asks_triage.py/asks_open.py: prefer an
    already-exported DATABASE_URL/SUPABASE_DB_URL, else load the
    orchestrator's own .env by an absolute path (robust to launchd's cwd)."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if dsn:
        return dsn
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    load_dotenv(env_path)
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---- 1. CLOSE (drift repair) ------------------------------------------------

def fetch_close_candidates(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, triage_state FROM operator_asks "
            "WHERE triage_state IN ('done', 'not_an_ask') AND closed_at IS NULL "
            "ORDER BY id"
        )
        return _rows(cur)


def apply_close(conn, rows: list[dict]) -> int:
    """Idempotent: the closed_at IS NULL guard on the UPDATE means a row this
    call already closed is simply skipped (rowcount 0) on any later re-run."""
    if not rows:
        return 0
    today = datetime.now(timezone.utc).date().isoformat()
    closed = 0
    with conn.cursor() as cur:
        for r in rows:
            reason = (f"auto: triaged done {today}" if r["triage_state"] == "done"
                       else "auto: not an ask")
            cur.execute(
                "UPDATE operator_asks SET closed_at = now(), closed_reason = %s "
                "WHERE id = %s AND closed_at IS NULL",
                (reason, r["id"]),
            )
            closed += cur.rowcount
    return closed


# ---- 2. UNTRIAGED GUARD -----------------------------------------------------

def fetch_untriaged(conn, stale_hours: int) -> list[dict]:
    """Open 'captured' rows older than stale_hours, with the owning channel
    tag resolved via source_msg_id -> operator_messages.tag. A captured row
    predates triage, so it never has a channel of its own; LEFT JOIN so a row
    with no/missing source message still surfaces (tag=None -> unmapped,
    never silently dropped)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT oa.id, oa.ask, oa.created_at, om.tag "
            "FROM operator_asks oa LEFT JOIN operator_messages om ON om.id = oa.source_msg_id "
            "WHERE oa.triage_state = 'captured' AND oa.closed_at IS NULL "
            "  AND oa.created_at < now() - (%s * interval '1 hour') "
            "ORDER BY oa.created_at ASC",
            (stale_hours,),
        )
        return _rows(cur)


def group_untriaged_by_owner(rows: list[dict]) -> dict:
    """Groups by CHANNEL_OWNER[tag]; an unmapped/missing tag lands under the
    None key so the caller fails LOUD (pages orch-console) instead of
    silently dropping those rows."""
    grouped: dict = {}
    for r in rows:
        owner = CHANNEL_OWNER.get(r.get("tag")) if r.get("tag") else None
        grouped.setdefault(owner, []).append(r)
    return grouped


def render_untriaged_page(owner: str, rows: list[dict]) -> str:
    lines = [
        f"TL;DR: {len(rows)} untriaged ask(s) on your channel sitting >24h "
        f"with no triage_state decision — run scripts/asks_triage.py on each "
        f"(ask / not / done).",
        "",
    ]
    for r in rows:
        snippet = (r["ask"] or "")[:UNTRIAGED_SNIPPET_CHARS]
        lines.append(f"  #{r['id']} ({r['created_at']}): {snippet}")
    return "\n".join(lines)


def render_unmapped_channel_page(rows: list[dict]) -> str:
    tags = sorted({r.get("tag") or "(no source_msg_id / no tag)" for r in rows})
    lines = [
        f"TL;DR: asks_reconcile_daily found {len(rows)} untriaged ask(s) whose "
        f"channel tag has no entry in CHANNEL_OWNER — fail-loud per spec, "
        f"routed to you rather than silently dropped. Tags seen: {', '.join(tags)}.",
        "",
        "Either triage these directly, or tell me the owner mapping for the "
        "tag(s) above and I'll add it to scripts/asks_reconcile_daily.py's "
        "CHANNEL_OWNER dict.",
        "",
    ]
    for r in rows:
        snippet = (r["ask"] or "")[:UNTRIAGED_SNIPPET_CHARS]
        lines.append(f"  #{r['id']} (tag={r.get('tag')!r}, {r['created_at']}): {snippet}")
    return "\n".join(lines)


# ---- 3. OVERDUE --------------------------------------------------------------

def fetch_overdue(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, committed_date, triage_summary, delegated_to, thread_id "
            "FROM operator_asks "
            "WHERE triage_state = 'ask' AND closed_at IS NULL "
            "  AND committed_date IS NOT NULL AND committed_date < now() "
            "ORDER BY committed_date ASC"
        )
        return _rows(cur)


def render_overdue_page(row: dict) -> str:
    return (
        f"TL;DR: ask #{row['id']} committed to {row['committed_date']} is now "
        f"OVERDUE — \"{row['triage_summary']}\". Deliver it, or re-triage with a "
        f"new --committed-date (scripts/asks_triage.py) if the date needs to move."
    )


# ---- 4. STALE — decide (never auto-closed) ----------------------------------

def fetch_stale(conn, age_days: int, quiet_days: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT oa.id, oa.triage_summary, oa.delegated_to, oa.created_at, om.tag "
            "FROM operator_asks oa LEFT JOIN operator_messages om ON om.id = oa.source_msg_id "
            "WHERE oa.triage_state = 'ask' AND oa.closed_at IS NULL "
            "  AND oa.created_at < now() - (%(age)s * interval '1 day') "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM operator_messages om2 "
            "     WHERE om2.direction = 'outbound' AND om2.tag = om.tag "
            "       AND om2.created_at > now() - (%(quiet)s * interval '1 day')"
            "  ) "
            "ORDER BY oa.created_at ASC",
            {"age": age_days, "quiet": quiet_days},
        )
        return _rows(cur)


# ---- summary (pure formatting, mirrors asks_daily_digest.py's render_digest) -

def render_summary(*, applied: bool, close_candidates: list[dict], closed: int,
                    untriaged_by_owner: dict, overdue: list[dict],
                    stale: list[dict]) -> str:
    mode = "APPLIED" if applied else "DRY-RUN"
    lines = [f"asks_reconcile_daily [{mode}]"]
    lines.append(
        f"  close: {len(close_candidates)} drift row(s) "
        f"({'closed ' + str(closed) if applied else 'would close'})"
    )
    untriaged_total = sum(len(v) for v in untriaged_by_owner.values())
    lines.append(f"  untriaged >24h: {untriaged_total} across {len(untriaged_by_owner)} owner(s)")
    if None in untriaged_by_owner:
        lines.append(f"    ⚠ {len(untriaged_by_owner[None])} with an UNMAPPED channel tag")
    lines.append(f"  overdue: {len(overdue)}")
    unowned_overdue = [r for r in overdue if not r.get("delegated_to")]
    if unowned_overdue:
        lines.append(
            f"    ⚠ {len(unowned_overdue)} overdue row(s) have no delegated_to — "
            f"cannot page, needs an owner decision: "
            + ", ".join(f"#{r['id']}" for r in unowned_overdue)
        )
    lines.append(f"  stale (>14d, quiet >7d) — decide keep/close: {len(stale)}")
    if stale:
        lines.append("")
        lines.append("STALE — decide:")
        for r in stale:
            lines.append(f"  #{r['id']} ({r.get('delegated_to') or 'unassigned'}): {r['triage_summary']}")
    return "\n".join(lines)


# ---- main --------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                     help="write the closes + send the bus escalations/summary; default is dry-run")
    ap.add_argument("--untriaged-hours", type=int, default=24)
    ap.add_argument("--stale-days", type=int, default=14)
    ap.add_argument("--stale-quiet-days", type=int, default=7)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    dsn = _dsn()
    if not dsn:
        print("asks_reconcile_daily: no DATABASE_URL/SUPABASE_DB_URL", file=sys.stderr)
        return 2

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        close_candidates = fetch_close_candidates(conn)
        untriaged = fetch_untriaged(conn, args.untriaged_hours)
        overdue = fetch_overdue(conn)
        stale = fetch_stale(conn, args.stale_days, args.stale_quiet_days)

        closed = 0
        if args.apply:
            closed = apply_close(conn, close_candidates)
            conn.commit()

    grouped_untriaged = group_untriaged_by_owner(untriaged)
    summary = render_summary(
        applied=args.apply, close_candidates=close_candidates, closed=closed,
        untriaged_by_owner=grouped_untriaged, overdue=overdue, stale=stale,
    )
    print(summary)

    if not args.apply:
        for owner, rows in grouped_untriaged.items():
            print("\n--- would page", owner or "orch-console (unmapped channel)", "---")
            print(render_unmapped_channel_page(rows) if owner is None else render_untriaged_page(owner, rows))
        for row in overdue:
            print("\n--- would page", row.get("delegated_to"), "---")
            print(render_overdue_page(row))
        return 0

    # --apply: send everything. A send failure is loud (uncaught) and non-fatal
    # to the rest of the run — one bad send must never block the others, so
    # each is wrapped and reported rather than aborting the whole job.
    from scripts import bus_send

    def _page(to: str, mtype: str, subject: str, body: str, **kw) -> None:
        """Every automated page goes through this, not bus_send.send()
        directly: `to` is resolved to its single live instance first (CC_AGENT_ID-
        suffixed, e.g. cc-oeh -> cc-oeh-1) when it has exactly one — several
        CHANNEL_OWNER/delegated_to values name a BASE id whose only live body
        is actually addressed by a numbered instance (confirmed empirically
        for 5 of 6 channels added in this same change: cc-irsyad-coord,
        cc-oeh, cc-cosem-exams, cc-angullia, cc-cosem-tdu-coord each currently
        have exactly one live instance, not the bare base). Resolving dynamically
        rather than hardcoding the instance number keeps this correct across a
        future recycle that reallocates a different N. A genuinely-ambiguous
        base (2+ live instances, e.g. cc-cosem-platform) is left unresolved —
        there's no single correct guess — and refuse_if_base_has_live_instances
        (bus #49220) then correctly refuses it, same as a hand-typed --to would;
        send() itself does NOT run that check when called directly like this,
        only the CLI's main() does, so it's called explicitly here."""
        instances = bus_send.live_instance_ids(to, dsn=dsn)
        target = instances[0] if len(instances) == 1 else to
        bus_send.refuse_if_base_has_live_instances(target, to_base=False, dsn=dsn)
        bus_send.send(PAGE_FROM_AGENT, target, mtype, subject, body, "P1", dsn=dsn, **kw)

    failures: list[str] = []
    for owner, rows in grouped_untriaged.items():
        try:
            if owner is None:
                _page(PAGE_TO_CONSOLE, "blocker",
                      "asks_reconcile_daily: untriaged rows with an unmapped channel tag",
                      render_unmapped_channel_page(rows), req=True)
            else:
                _page(owner, "blocker",
                      f"{len(rows)} untriaged ask(s) >{args.untriaged_hours}h old on your channel",
                      render_untriaged_page(owner, rows), req=True)
        except (Exception, SystemExit) as e:  # noqa: BLE001
            failures.append(f"untriaged page to {owner!r}: {type(e).__name__}: {e}")

    for row in overdue:
        if not row.get("delegated_to"):
            continue
        try:
            _page(row["delegated_to"], "blocker",
                  f"ask #{row['id']} OVERDUE (committed {row['committed_date']})",
                  render_overdue_page(row), req=True,
                  thread=str(row["thread_id"]) if row.get("thread_id") else None)
        except (Exception, SystemExit) as e:  # noqa: BLE001
            failures.append(f"overdue page for #{row['id']}: {type(e).__name__}: {e}")

    try:
        bus_send.send(PAGE_FROM_AGENT, PAGE_TO_CONSOLE, "update",
                       "asks_reconcile_daily: daily run summary", summary, "P2", dsn=dsn)
    except (Exception, SystemExit) as e:  # noqa: BLE001
        failures.append(f"summary to console: {type(e).__name__}: {e}")

    if failures:
        print("asks_reconcile_daily: FAILURES (fail loud, non-fatal to other sends):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
