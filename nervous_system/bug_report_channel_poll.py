"""bug_report_channel_poll — post new in-app bug reports to the irsyad client channel.

Musa op#27000/#27004, orch-console #57120/#57143. The ihsanOS /api/bug-report
endpoint (PR#1064) inserts a coord_dispatch_queue row with
`spec_ref = 'bug-report:<bug_reports.id>'` for every new in-app bug report
(repo=ihsanos). This poller reads those rows and posts a one-line 🐞 notice to
the gazzabyte-irsyad Telegram channel so Shuq/Wan see the report and can act —
replacing the dead auto-fix `jobs`/ralph pipeline that silently dropped every
submission since ~2026-05-06.

Three things it refuses to do, each deliberate:

  1. It posts each queue row EXACTLY ONCE. The marker table
     bug_report_channel_posts (migration 095) records every posted queue_id;
     a row already marked — by this poller OR by a coord hand-post under the
     interim path (B) — is skipped. Re-running never double-posts.

  2. It never sends reporter PII. Only page_url + the first ~140 chars of the
     reporter's own description go out, and any NRIC/BC-shaped token in that
     text is masked (S****567A) defensively — a bug description should carry
     none, but Satr is fail-safe, not fail-open.

  3. It fails LOUD, never silent. If the enqueue raises (DB error / channel
     lookup / the can't-open-file refusal), it alerts orch-console on the bus
     and does NOT stamp the marker, so the row is retried next tick rather than
     lost. Telegram delivery itself is tg_out's drain daemon + dead-letter.

The actual send goes through nervous_system.tg_out.enqueue — the ONE sanctioned
outbound gateway (CAI-RESP-357), which resolves the channel token from
bot_channels server-side. No bot token lives in the product app (that was the
Satr line we held in PR#1064).

Usage:
  python -m nervous_system.bug_report_channel_poll          # dry-run: report, change nothing
  python -m nervous_system.bug_report_channel_poll --fire   # post + stamp
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent

# launchd inherits NOTHING: load the DSN (and the channel token env) from the
# repo-root .env explicitly rather than trusting the caller's environment, or
# this works by hand and fails on its first scheduled fire.
load_dotenv(ROOT / ".env")

from nervous_system import tg_out  # noqa: E402  (after load_dotenv, by design)

CHANNEL = "gazzabyte-irsyad"
# NRIC/BC shape: a prefix letter (S/T/F/G/M), 7 digits, a checksum letter.
_NRIC_RE = re.compile(r"[STFGM]\d{7}[A-Z]", re.IGNORECASE)
_MAX_DESC = 140


def _dsn() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _mask(text: str) -> str:
    """Mask any NRIC/BC-shaped token: S1234567A -> S****567A."""
    return _NRIC_RE.sub(lambda m: m.group(0)[0] + "****" + m.group(0)[-4:], text)


def compose(page_url: str | None, description: str | None) -> str:
    page = (page_url or "").strip() or "(unknown page)"
    desc = " ".join((description or "").split())  # collapse newlines/whitespace
    if len(desc) > _MAX_DESC:
        desc = desc[:_MAX_DESC].rstrip() + "…"
    desc = _mask(desc)
    return f'🐞 Logged issue (via the in-app Bug button) — Page: {page}. "{desc}"'


def _alert_console(dsn: str, text: str) -> None:
    """Fail-loud: bus a P1 blocker to orch-console. Best-effort, never raises."""
    try:
        with psycopg.connect(dsn, connect_timeout=20) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,priority,"
                "requires_response,subject,body) VALUES "
                "('bug-report-poller','orch-console','blocker','P1',true,%s,%s)",
                ("bug-report channel poller: send failure", text),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — alerting must never crash the poller
        print(f"WARN: could not alert console: {exc}", file=sys.stderr)


def sweep(fire: bool = False, conn_factory=None) -> int:
    dsn = _dsn()
    if not dsn:
        print("FATAL: DATABASE_URL/SUPABASE_DB_URL not set", file=sys.stderr)
        return 2
    connect = conn_factory or (
        lambda: psycopg.connect(dsn, connect_timeout=20, row_factory=dict_row)
    )

    posted = errors = skipped = 0
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT q.id AS queue_id, q.spec_ref AS spec_ref "
                "FROM coord_dispatch_queue q "
                "LEFT JOIN bug_report_channel_posts p ON p.queue_id = q.id "
                "WHERE q.spec_ref LIKE 'bug-report:%' AND p.queue_id IS NULL "
                "ORDER BY q.id"
            )
            rows = cur.fetchall()

        for row in rows:
            queue_id = row["queue_id"]
            spec_ref = row["spec_ref"] or ""
            bug_id = spec_ref.split("bug-report:", 1)[1].strip() if "bug-report:" in spec_ref else ""

            detail = None
            if bug_id:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT page_url, description FROM bug_reports WHERE id = %s",
                        (bug_id,),
                    )
                    detail = cur.fetchone()

            text = compose(detail["page_url"], detail["description"]) if detail else None
            note = None if detail else "no bug_reports row for spec_ref"

            if not fire:
                print(f"[dry-run] queue={queue_id} bug={bug_id}: {text or '(skip: '+note+')'}")
                continue

            # CLAIM-FIRST (cc-quality #57189 LOW): insert the marker BEFORE the send, so
            # the marker INSERT — not the Telegram call — is the serialization point. Two
            # overlapping ticks (or the interim hand-post path B racing a sweep) can't both
            # enqueue: only the winner's ON CONFLICT ... RETURNING yields a row.
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO bug_report_channel_posts (queue_id, bug_id, note) "
                    "VALUES (%s,%s,%s) ON CONFLICT (queue_id) DO NOTHING RETURNING queue_id",
                    (queue_id, bug_id or None, note),
                )
                claimed = cur.fetchone()
            conn.commit()
            if not claimed:
                skipped += 1
                print(f"skip queue={queue_id} (already claimed by another tick)")
                continue

            if text is None:
                # Malformed spec_ref / missing bug row — claimed with a note so it leaves
                # the next sweep's SELECT; nothing is posted.
                skipped += 1
                print(f"skip queue={queue_id} (no bug_reports row for '{spec_ref}')")
                continue

            try:
                tg_id = tg_out.enqueue(CHANNEL, text)
            except Exception as exc:  # noqa: BLE001 — fail loud; UN-CLAIM so the row retries
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM bug_report_channel_posts WHERE queue_id = %s", (queue_id,))
                conn.commit()
                errors += 1
                _alert_console(dsn, f"enqueue failed for queue {queue_id} bug {bug_id}: {exc}")
                print(f"ERROR queue={queue_id}: {exc}", file=sys.stderr)
                continue

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE bug_report_channel_posts SET tg_out_id = %s WHERE queue_id = %s",
                    (tg_id, queue_id),
                )
            conn.commit()
            posted += 1
            print(f"posted queue={queue_id} bug={bug_id} tg_out={tg_id}")

    print(f"posted={posted} skipped={skipped} errors={errors}")
    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fire", action="store_true", help="post + stamp (default: dry-run)")
    args = ap.parse_args()
    return sweep(fire=args.fire)


if __name__ == "__main__":
    raise SystemExit(main())
