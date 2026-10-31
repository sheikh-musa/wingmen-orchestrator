#!/usr/bin/env python3
"""asks_daily_digest.py — a once-daily operator digest of open operator_asks
(op#22669, piece 4 second half: "if I ask 1000 things I expect you to track
1001" — a daily roll-up so nothing quietly ages off the operator's radar
between the priority_sla_watchdog's chase net firings).

Deliberately SIMPLE — this is NOT priority_sla_watchdog's dedup/backoff/
lease-gating machinery. It sends AT MOST ONE digest per UTC calendar day (a
persisted date-stamp in $ORCH/logs/asks_daily_digest_state.json is the only
dedup), so a launchd misfire or manual re-run can never double-send by
construction. Sends via scripts/nazim_send.sh (orch-console bus #43972, PR
#180 change 2) — this launchd job is Mini/console-hosted and the asks ledger
(op#22669) is Nazim's own thread, not the hub's. scripts/tg_send.sh fail-closes
for the console body under ORCH-TOPOLOGY-001 (orch_lease.py), so sending via it
from here would silently fail every single day and never reach the operator.

Scheduled via launchd/dev.wingmen.asks-daily-digest.plist at 09:00 Abu Dhabi
time (UTC+4) — see that file's comment for the host-TZ-hour conversion.
Landing this file does NOT wire it into launchd; a plist must still be
separately `launchctl load`ed at go-live, so this is safe to merge without
firing.

Robustness (bus #44286: the plist fired 8h off because launchd's
StartCalendarInterval is host-local and the host is not UTC): every real
send logs the wall-clock time in Asia/Dubai it actually fired at, and warns
loudly if that's far from the intended 09:00 slot — so a future host-TZ
change or launchd misconfiguration shows up in the log the same day instead
of waiting for the operator to notice a digest at the wrong hour.

CAPPED REBUILD (op#30088 part 1, held_commitment #438, HARD GATE #66732):
this launchd job was disabled+booted-out because the prior render_digest()
fully enumerated every open ask (one line per row, no limit) — with the
ledger at 191 open operator asks that would have been a 191-line Telegram
dump, not a digest. The fix: render_digest() now shows at most TOP_N
(5) items under "WAITING ON YOU" (oldest-first, already the query's own
order) and NEVER enumerates the "other open / in progress" bucket at
all — that bucket is a judged COUNT line only, with a pointer ("ask
cc-fleet-health for the full list") for anything beyond the cap. Same
cap applies to the client-ask section for defense-in-depth, even though
it stays default-OFF (DIGEST_CLIENT_ASKS_ENABLED) until the irsyad
backlog is triaged. Chasing OWNERS of overdue asks is deliberately NOT
duplicated here — scripts/priority_sla_watchdog.py's CLIENT-ASKS-CHASE
already re-nudges delegated_to owners for overdue chase_by rows with its
own dedup/backoff; this digest is Musa-facing only and must never grow a
second, uncoordinated chase mechanism. Per HARD GATE #66732, the launchd
plist stays disabled until Nazim has SEEN the cap logic + a live sample +
after-counts and signs off — re-enabling is a separate, explicit step,
never automatic on this file landing.

Usage:
  asks_daily_digest.py [--dry-run] [--force]
    --dry-run   print the digest, send nothing, stamp nothing (safe to run any time)
    --force     send even if already sent today (manual re-run / testing)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv

ORCH = Path(os.path.expanduser("~/wingmen/orchestrator"))
STATE_FILE = ORCH / "logs" / "asks_daily_digest_state.json"
UAE_TZ = ZoneInfo("Asia/Dubai")
TOP_N = 5  # HARD GATE #66732: never enumerate past this — count+pointer beyond it

load_dotenv(ORCH / ".env")


def _dsn() -> "str | None":
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


DIGEST_CLIENT_ASKS_ENABLED_ENV = "DIGEST_CLIENT_ASKS_ENABLED"


def client_asks_digest_enabled() -> bool:
    """orch-console bus #47267 decision 2: the client-ask section is in scope
    for the same "paging off by default" doctrine as SLA_CLIENT_ASKS_CHASE_ENABLED
    (scripts/priority_sla_watchdog.py) -- until the ~118-row irsyad backlog is
    triaged, showing it unconditionally would put every unsorted item straight
    into Musa's morning roll-up. Exact '1' arms it; sibling flag, same
    convention, same default OFF."""
    return os.environ.get(DIGEST_CLIENT_ASKS_ENABLED_ENV, "0") == "1"


def fetch_open_asks(conn) -> list:
    """Every triaged-open operator_asks row (migration 082: triage_state='ask'
    only — a 'captured' row has not been judged to even BE a request yet, see
    fetch_captured_summary() below), waiting-on-you first (op#22669's top
    priority — mirrors db.py's build_asks_query() 'waiting_on_musa' ordering),
    then oldest-first within each group.

    ask_surface = 'operator' ONLY (migration 084/085): a client-channel row
    must never leak into Musa's own board mixed in as if he'd asked it
    himself — see fetch_client_open_asks() for the separate client section."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, triage_summary, delegated_to, waiting_on_operator, chase_by, created_at "
            "FROM operator_asks WHERE closed_at IS NULL AND triage_state = 'ask' "
            "  AND ask_surface = 'operator' "
            "ORDER BY waiting_on_operator DESC, created_at ASC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_captured_summary(conn) -> dict:
    """count + oldest created_at of untriaged 'captured' rows — never
    enumerated or shown as asks (bus #45557 condition 3), just a nudge that
    something needs a human/agent triage pass via scripts/asks_triage.py.
    ask_surface='operator' only, same scoping as fetch_open_asks()."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), min(created_at) FROM operator_asks "
            "WHERE closed_at IS NULL AND triage_state = 'captured' "
            "  AND ask_surface = 'operator'"
        )
        count, oldest = cur.fetchone()
        return {"count": count or 0, "oldest": oldest}


# ── migration 085: client-channel asks (Musa op#23944, bus #47110/#47114) ────
def fetch_client_open_asks(conn) -> list:
    """Every triaged-open ask_surface='client-channel' row (triage_state='ask'
    only — same no-PII rule as fetch_open_asks: only a human-reviewed
    triage_summary is ever shown here, never the raw client text). Oldest-
    first — bus #47110 item 3b wants AGE since the client asked, visible."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, triage_summary, delegated_to, committed_date, created_at "
            "FROM operator_asks "
            "WHERE closed_at IS NULL AND ask_surface = 'client-channel' AND triage_state = 'ask' "
            "ORDER BY created_at ASC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_client_captured_summary(conn) -> dict:
    """count + oldest created_at of untriaged client-channel rows — same
    no-PII rule as fetch_captured_summary: never enumerated, just a count +
    age nudge to go triage via scripts/asks_triage.py."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), min(created_at) FROM operator_asks "
            "WHERE closed_at IS NULL AND ask_surface = 'client-channel' "
            "  AND triage_state = 'captured'"
        )
        count, oldest = cur.fetchone()
        return {"count": count or 0, "oldest": oldest}


def fetch_turnaround_summary(conn, day=None) -> "dict | None":
    """Yesterday's (Asia/Singapore, matching operator_ask_turnaround_daily's
    own tz) turnaround rollup for the digest (Musa op#34803; orch-console
    #78190 plan). Deliberately EXCLUDES surface='unmatched' (no lane ever
    acked -- an instant Q&A reply with no lane work at all, not a slow
    build) from delivered/median/p90/slowest: mixing the two populations is
    misleading -- an instant reply and a multi-day stale-ask catch-up
    delivered the same day are not the same kind of "how long did it
    take". unmatched is reported as a separate count, context only, never
    folded into the timing stat. Returns None when nothing matched that
    day (no line is shown) -- an all-unmatched day still returns None here,
    same as a quiet day; unmatched volume alone isn't digest-worthy."""
    if day is None:
        day = (datetime.now(ZoneInfo("Asia/Singapore")) - timedelta(days=1)).date()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY total), "
            "percentile_cont(0.9) WITHIN GROUP (ORDER BY total) "
            "FROM operator_ask_turnaround "
            "WHERE (delivered_at AT TIME ZONE 'Asia/Singapore')::date = %s AND surface != 'unmatched'",
            (day,),
        )
        delivered, median_total, p90_total = cur.fetchone()
        if not delivered:
            return None
        cur.execute(
            "SELECT count(*) FROM operator_ask_turnaround "
            "WHERE (delivered_at AT TIME ZONE 'Asia/Singapore')::date = %s AND surface = 'unmatched'",
            (day,),
        )
        (unmatched,) = cur.fetchone()
        cur.execute(
            "SELECT surface, count(*) FROM operator_ask_turnaround "
            "WHERE (delivered_at AT TIME ZONE 'Asia/Singapore')::date = %s AND surface != 'unmatched' "
            "GROUP BY surface ORDER BY count(*) DESC, surface ASC",
            (day,),
        )
        by_surface = cur.fetchall()
        cur.execute(
            "SELECT oa.triage_summary, t.total, t.queue_wait, t.build_to_live "
            "FROM operator_ask_turnaround t JOIN operator_asks oa ON oa.id = t.ask_id "
            "WHERE (t.delivered_at AT TIME ZONE 'Asia/Singapore')::date = %s AND t.surface != 'unmatched' "
            "ORDER BY t.total DESC LIMIT 1",
            (day,),
        )
        slowest = cur.fetchone()
    return {
        "day": day, "delivered": delivered, "median_total": median_total,
        "p90_total": p90_total, "unmatched": unmatched,
        "by_surface": by_surface, "slowest": slowest,
    }


def fetch_delivered_but_unclosed(conn, limit: int = TOP_N) -> list:
    """Hygiene check (Musa op#34803, data gap #1 found validating the plan,
    #78194): a lane subject implies the work is done/live while the ask
    itself is still fully open (closed_at AND delivered_at both NULL) --
    turnaround can only be as good as close discipline. Oldest-first;
    capped the same way as every other digest bucket (HARD GATE #66732) --
    the caller reports a remainder count past `limit`, never enumerates
    further."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT oa.id, oa.source_msg_id, am.from_agent, am.created_at "
            "FROM operator_asks oa "
            "JOIN LATERAL ("
            "  SELECT from_agent, created_at FROM agent_messages "
            "  WHERE from_agent NOT IN ('orch-console', 'cc-fleet-health') "
            "    AND from_agent NOT LIKE 'cc-fleet-health-%' "
            "    AND subject ~ ('op#' || oa.source_msg_id::text || '([^0-9]|$)') "
            "    AND subject ~* '(done|delivered|is live|merged and live)' "
            "  ORDER BY created_at ASC LIMIT 1"
            ") am ON true "
            "WHERE oa.closed_at IS NULL AND oa.delivered_at IS NULL AND oa.triage_state = 'ask' "
            "ORDER BY am.created_at ASC"
        )
        return cur.fetchall()


def _fmt_td(td) -> str:
    """Pure: format a timedelta as e.g. '1h54', '45m' for the turnaround
    line -- compact, matches the plan's own example exactly."""
    if td is None:
        return "?"
    total_seconds = int(td.total_seconds())
    hours, rem = divmod(total_seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h{minutes:02d}" if minutes else f"{hours}h"
    return f"{minutes}m"


def render_turnaround_line(summary: "dict | None", unclosed: "list | None" = None) -> "str | None":
    """Pure formatting (Musa op#34803). Returns None when there's nothing
    to show (no matched deliveries that day AND no unclosed-hygiene hits) --
    the digest must never force an empty section just because the feature
    exists. unclosed is capped by the caller (fetch_delivered_but_unclosed's
    `limit`); only the remainder count is computed here, not re-capped."""
    unclosed = unclosed or []
    lines: list[str] = []
    if summary:
        surface_str = " / ".join(f"{s} {n}" for s, n in summary["by_surface"])
        slowest_str = ""
        if summary["slowest"]:
            label, total, queue_wait, build_to_live = summary["slowest"]
            label = label or "(no triage summary)"
            slowest_str = (f" · slowest {label} {_fmt_td(total)}"
                           f" (queue {_fmt_td(queue_wait)} / build {_fmt_td(build_to_live)})")
        unmatched_note = f" ({summary['unmatched']} more instant/no-lane-work, not counted)" if summary["unmatched"] else ""
        lines.append(
            f"⏱ Turnaround ({summary['day']}): {summary['delivered']} delivered{unmatched_note} — "
            f"median {_fmt_td(summary['median_total'])}, p90 {_fmt_td(summary['p90_total'])}"
            f"{slowest_str}" + (f" · {surface_str}" if surface_str else "")
        )
    if unclosed:
        shown = unclosed[:TOP_N]
        ids = ", ".join(f"#{ask_id} (op#{op})" for ask_id, op, _agent, _ts in shown)
        remainder = len(unclosed) - len(shown)
        tail = f" …and {remainder} more" if remainder > 0 else ""
        lines.append(f"⚠ {len(unclosed)} ask(s) look delivered but unclosed: {ids}{tail}")
    return "\n".join(lines) if lines else None


def _age_str(ts, now: "datetime | None" = None) -> str:
    """Pure: a human-readable age string for a timestamptz, hours under 48h
    else days (bus #47110 item 3b: AGE since the client asked)."""
    if ts is None:
        return "?"
    now = now or datetime.now(ts.tzinfo or timezone.utc)
    hours = (now - ts).total_seconds() / 3600
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.1f}d"


def _cap_line(shown: int, total: int, noun: str) -> "str | None":
    """HARD GATE #66732: the ONE place remainder-counting happens, so every
    capped bucket reports it the same way. Returns None when there's no
    remainder to report (nothing hidden, no line needed)."""
    remainder = total - shown
    if remainder <= 0:
        return None
    return f"  …and {remainder} more {noun} (ask cc-fleet-health for the full list)"


def render_digest(rows: list, captured: "dict | None" = None,
                   client_rows: "list | None" = None,
                   client_captured: "dict | None" = None,
                   turnaround: "dict | None" = None,
                   unclosed: "list | None" = None) -> str:
    """Pure formatting — no DB/network — so this is trivially unit-testable.
    `rows` must already be triage_state='ask' only — triage_summary is the
    ONLY text ever shown here, never the raw `ask` column (bus #45557
    condition 3 — a captured row's raw text must never leak into the digest).

    CAPPED (op#30088 part 1, HARD GATE #66732): at most TOP_N items are ever
    printed per bucket. "WAITING ON YOU" shows up to TOP_N (oldest-first,
    the caller's own query order) plus a remainder line; everything NOT
    waiting-on-operator is a single judged COUNT line — it is NEVER
    enumerated, full stop, regardless of how many rows there are. This is
    the one invariant the hard gate exists to enforce, so test it, don't
    just trust it.

    `client_rows`/`client_captured` (migration 085, bus #47110 item 3b): the
    separate CLIENT ASKS section, kept visually distinct from Musa's own
    operator-surface asks above (ask_surface scoping must stay legible to the
    reader, not just to the query) — each shows AGE since the client asked
    and flags a committed_date given more than 3 days out (Musa op#23946:
    "no far-out dates"). Capped the same way as the operator section.

    `turnaround`/`unclosed` (Musa op#34803, orch-console #78190 plan): one
    "how long did yesterday's delivered work take" line plus an optional
    hygiene line, via render_turnaround_line() — kept as a single pre-
    rendered block here so this function stays pure formatting, not a
    second place that knows the turnaround schema."""
    captured = captured or {"count": 0, "oldest": None}
    client_rows = client_rows or []
    client_captured = client_captured or {"count": 0, "oldest": None}
    turnaround_block = render_turnaround_line(turnaround, unclosed)
    if (not rows and not captured["count"] and not client_rows and not client_captured["count"]
            and not turnaround_block):
        return "📋 Daily asks digest — nothing open. The ledger is clear."
    now = datetime.now(timezone.utc)
    lines: list[str] = []
    if rows or captured["count"]:
        waiting = [r for r in rows if r.get("waiting_on_operator")]
        others = [r for r in rows if not r.get("waiting_on_operator")]
        lines.append(f"📋 Daily asks digest — {len(rows)} open ({len(waiting)} waiting on you)")
        if waiting:
            lines.append("")
            lines.append("WAITING ON YOU:")
            shown = waiting[:TOP_N]
            for r in shown:
                lines.append(f"  #{r['id']} ({r.get('delegated_to') or '?'}): {r['triage_summary']}")
            cap_line = _cap_line(len(shown), len(waiting), "waiting on you")
            if cap_line:
                lines.append(cap_line)
        if others:
            lines.append("")
            lines.append(f"Open / in progress: {len(others)} (not shown — ask cc-fleet-health for the full list)")
        if captured["count"]:
            age_suffix = f", oldest {_age_str(captured['oldest'], now)}" if captured["oldest"] else ""
            lines.append("")
            lines.append(f"({captured['count']} messages not yet sorted{age_suffix})")
    if client_rows or client_captured["count"]:
        if lines:
            lines.append("")
        lines.append(f"CLIENT ASKS — {len(client_rows)} open (migration 085 / op#23944):")
        shown_client = client_rows[:TOP_N]
        for r in shown_client:
            age = _age_str(r.get("created_at"), now)
            committed = r.get("committed_date")
            created_at = r.get("created_at")
            far_out = (committed is not None and created_at is not None
                       and committed > created_at + timedelta(days=3))
            date_str = f"committed {committed.date()}" if committed else "no date yet"
            flag = " ⚠️ FAR-OUT DATE (op#23946)" if far_out else ""
            lines.append(f"  #{r['id']} ({r.get('delegated_to') or '?'}, age {age}, {date_str})"
                         f"{flag}: {r['triage_summary']}")
        cap_line = _cap_line(len(shown_client), len(client_rows), "client ask(s)")
        if cap_line:
            lines.append(cap_line)
        if client_captured["count"]:
            oldest = client_captured["oldest"]
            age = f", oldest {_age_str(oldest, now)}" if oldest else ""
            lines.append(f"  ({client_captured['count']} client messages not yet sorted{age})")
    if turnaround_block:
        if lines:
            lines.append("")
        lines.append(turnaround_block)
    return "\n".join(lines)


def _already_sent_today(today: str) -> bool:
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        return False
    return state.get("last_sent_date") == today


def _mark_sent(today: str) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"last_sent_date": today}))
    except Exception:
        pass


def _log_uae_send_time(now_utc: "datetime | None" = None) -> None:
    """bus #44286: log the actual send time in Asia/Dubai, and warn loudly if
    it's far from the intended 09:00 UAE slot — a host-TZ or launchd-Hour
    drift then shows up in the log the day it happens, not when the operator
    notices a digest at the wrong hour."""
    now_uae = (now_utc or datetime.now(timezone.utc)).astimezone(UAE_TZ)
    line = f"asks_daily_digest: send time {now_uae.strftime('%Y-%m-%d %H:%M %Z')} (target ~09:00 Asia/Dubai)"
    if abs(now_uae.hour - 9) >= 2:
        line += (" -- WARNING: more than 1h off the intended 09:00 UAE slot; "
                 "check launchd Hour vs host TZ (bus #44286)")
    print(line)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                     help="print the digest, send nothing, stamp nothing")
    ap.add_argument("--force", action="store_true",
                     help="send even if already sent today (manual re-run)")
    args = ap.parse_args(argv)

    today = datetime.now(timezone.utc).date().isoformat()
    if not args.force and not args.dry_run and _already_sent_today(today):
        print(f"asks_daily_digest: already sent for {today} — skipping (use --force to resend)")
        return 0

    dsn = _dsn()
    if not dsn:
        print("asks_daily_digest: no DATABASE_URL/SUPABASE_DB_URL", file=sys.stderr)
        return 2

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        rows = fetch_open_asks(conn)
        captured = fetch_captured_summary(conn)
        if client_asks_digest_enabled():
            client_rows = fetch_client_open_asks(conn)
            client_captured = fetch_client_captured_summary(conn)
        else:
            client_rows, client_captured = [], {"count": 0, "oldest": None}
        turnaround = fetch_turnaround_summary(conn)
        unclosed = fetch_delivered_but_unclosed(conn)
    digest = render_digest(rows, captured, client_rows, client_captured, turnaround, unclosed)

    if args.dry_run:
        print(digest)
        return 0

    _log_uae_send_time()
    result = subprocess.run(["bash", str(ORCH / "scripts" / "nazim_send.sh"), digest])
    if result.returncode != 0:
        print("asks_daily_digest: nazim_send.sh failed — not stamping (retry next run)", file=sys.stderr)
        return 1
    _mark_sent(today)
    print(f"asks_daily_digest: sent ({len(rows)} open asks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
