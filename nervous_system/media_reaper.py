"""media_reaper — retry Telegram media downloads that failed after ingest.py's
own 4 in-line retries.

bus #74111 (Musa directive via orch-console), part 2/3: ingest.py now
persists a re-fetchable file_id into operator_messages EVEN WHEN the
download failed (migration 115 + nervous_system/ingest.py's
message_content_with_media()). This daemon is the half that actually
re-fetches: it finds every operator_messages row with
media_status IN ('pending', 'failed') and retries getFile + download until
bytes land on disk and media_status flips to 'ok'.

Shaped after nervous_system/commitment_sweeper.py (CAI-RESP-1029's own
standard for a sweeper over durable state): idempotent (re-running costs one
cheap query when nothing is due), never silently no-ops on a missing DSN,
and escalates to the console on a row that has been retried past
ESCALATE_AFTER without landing — a media item nobody can ever read is the
same class of loss the sweeper's own commitments guard against.

⚠ 'pending' is accepted defensively but has NO current producer: ingest.py's
  log write and its download attempt are not decoupled (that would need a
  bigger restructure of process_update's commit boundaries — cos_triage
  ordering, the personal-routing C1 rollback, migrate_to_chat_id paging, and
  every downstream step keyed to the final content string all interleave
  there), so every row this daemon will actually see today lands as
  'failed', never 'pending'. Treating both the same costs nothing and keeps
  this reaper correct if that decoupling is ever built later.

WHY A TOKEN PER ROW, NOT PER CHANNEL: operator_messages has no channel_key,
only the channel_tag (`tag`) — bot_channels.channel_tag is NOT always equal
to channel_key (e.g. channel_key='operator-orch' -> channel_tag='orch-
channel'). Resolve per row via a tag->token_env_key lookup rather than
assuming the two are interchangeable.

MEDIA_REAPER_CHANNELS (cc-quality follow-up, bus #74154 — build-it-properly,
not a "zero rows today" deferral): media bytes land on THIS host's local
disk (logs/tg_media), so a reaper polling every tag fleet-wide would, the
moment a second host runs this daemon, download into the wrong host's
disk for a tag some OTHER host's ingest actually received. Mirrors
ingest.py's INGEST_CHANNELS — a comma-separated list of bot_channels.
channel_key values this host's reaper is allowed to touch. Required, not
optional: an unset/empty value or one that resolves to zero channel_tags
refuses to run rather than silently scanning every tag on the box.

Usage:
  MEDIA_REAPER_CHANNELS=<channel_key,channel_key,...> \
    python -m nervous_system.media_reaper            # dry-run: report, change nothing
  MEDIA_REAPER_CHANNELS=<channel_key,channel_key,...> \
    python -m nervous_system.media_reaper --fire      # actually retry + write
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import pathlib
import sys

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from nervous_system import ingest as _ingest  # reuse _download_media (retry+backoff already built in)
from scripts import bus_send

CONSOLE = "orch-console"
FROM_AGENT = "media-reaper"

# How long a row may sit undelivered (first seen at its created_at) before the
# console is told — same shape as commitment_sweeper's ESCALATE_AFTER. Not a
# per-attempt counter (no such column); age-of-row is a cheap, good-enough
# proxy for "this has been retried a lot and still isn't landing."
ESCALATE_AFTER = _dt.timedelta(hours=2)

# DESIGN ITEM — ✅ BUILT 2026-10-31 (held_commitment #964, bus #85768;
# cc-quality obs-2, bus #74111). A Telegram file_id is not eternal, and two
# getFile answers can never be fixed by retrying: 400 "file is too big" (the
# Bot API cannot download over 20 MB) and 400 "invalid file_id" / expired file
# reference, plus 404. Those now map to the TERMINAL media_status
# 'unrecoverable' (migration 141) instead of 'failed':
#   - ingest._unrecoverable_reason(exc) is the single classifier, shared by
#     ingest.py and this reaper so the two can never disagree;
#   - a terminal row leaves RETRYABLE_STATUSES, so it stops being retried here;
#   - and it is paged ONCE at classification (here, and in ingest for the
#     first-attempt case), so terminal does NOT mean silent.
# Transient failures (Errno 54 resets, timeouts, DNS) remain 'failed' and keep
# retrying — the classifier keys off HTTPError only, never OSError.

RETRYABLE_STATUSES = ("pending", "failed")

MAX_ROWS_PER_RUN = 200   # cheap safety cap -- a stuck CDN outage must not turn
                         # one reaper tick into hundreds of serial network calls


def _dsn() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _store_failure_status(cur, msg_id: int, terminal: str | None) -> str:
    """Write the retry-failure status; return what was ACTUALLY stored.

    cc-quality F1 on PR#786 — the reaper half of the deploy-ordering guard.
    ingest already refuses to let a schema that lacks migration 141 take down a
    message; this daemon wrote the same terminal value with no guard, so on a
    silo without 141 the UPDATE raised CheckViolation from inside the `except`
    handler and killed the whole run. Because due rows are selected ORDER BY
    created_at, ONE such row crashed the reaper on every tick and starved every
    newer row of its retry — the opposite of "degrades safely".

    Same shape as ingest's guard: explicit SAVEPOINT, downgrade only OUR
    constraint, re-raise anything else. Returns 'unrecoverable' or 'failed' so
    the CALLER pages only for a status that really landed.
    """
    status = "unrecoverable" if terminal else "failed"
    cur.execute("SAVEPOINT media_status_terminal")
    try:
        cur.execute("UPDATE operator_messages SET media_status=%s WHERE id=%s",
                    (status, msg_id))
        return status
    except psycopg.errors.CheckViolation as e:
        if (status != "unrecoverable"
                or getattr(getattr(e, "diag", None), "constraint_name", None)
                != _ingest._MEDIA_STATUS_CONSTRAINT):
            raise
        cur.execute("ROLLBACK TO SAVEPOINT media_status_terminal")
        print(f"[schema-drift] #{msg_id}: migration 141 not applied on this silo — "
              f"{_ingest._MEDIA_STATUS_CONSTRAINT} rejects 'unrecoverable'; storing "
              f"'failed' and continuing (the row keeps retrying until the migration "
              f"lands; this is NOT silent)", file=sys.stderr)
        cur.execute("UPDATE operator_messages SET media_status='failed' WHERE id=%s", (msg_id,))
        return "failed"


def _notify(subject: str, body: str, priority: str = "P2") -> int:
    # Each call site already commits this independently of its neighboring writes
    # (no shared-transaction requirement), so bus_send.send()'s own connection is a
    # transparent swap (backlog#124, bus #74939).
    mid, _thread = bus_send.send(
        from_agent=FROM_AGENT, to=CONSOLE, mtype="update", subject=subject[:300],
        body=body, priority=priority, req=True, dsn=_dsn())
    return mid


def _token_for_tag(cur, tag: str) -> tuple[str, str] | None:
    """(channel_key, token) for the bot_channels row whose channel_tag==tag,
    or None if no such row or its token env var isn't set on this host."""
    cur.execute(
        "SELECT channel_key, token_env_key FROM bot_channels WHERE channel_tag = %s", (tag,))
    row = cur.fetchone()
    if row is None:
        return None
    token = os.environ.get(row["token_env_key"] or "", "")
    if not token:
        return None
    return row["channel_key"], token


def _scoped_tags(cur) -> set[str] | None:
    """channel_tags this host's reaper may touch, resolved from
    MEDIA_REAPER_CHANNELS (channel_keys, same format as INGEST_CHANNELS).
    None means "not configured" — the caller must fail closed, never scan
    every tag on the box by default."""
    raw = os.environ.get("MEDIA_REAPER_CHANNELS", "").strip()
    if not raw:
        return None
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        return None
    cur.execute("SELECT DISTINCT channel_tag FROM bot_channels WHERE channel_key = ANY(%s)", (keys,))
    return {row["channel_tag"] for row in cur.fetchall()}


def reap(fire: bool) -> int:
    dsn = _dsn()
    if not dsn:
        print("media_reaper: MISSING DSN — cannot run; refusing to no-op silently",
              file=sys.stderr)
        return 2

    recovered = escalated = skipped_no_token = 0

    with psycopg.connect(dsn, connect_timeout=20, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            allowed_tags = _scoped_tags(cur)
            if not allowed_tags:
                print("media_reaper: MISSING/empty MEDIA_REAPER_CHANNELS — refusing to run "
                      "fleet-wide; set it to this host's own INGEST_CHANNELS channel_key list "
                      "(bus #74154: media bytes land on THIS host's disk, a cross-host reaper "
                      "would download into the wrong host's logs/tg_media)", file=sys.stderr)
                return 2

            cur.execute(
                """SELECT id, tag, text, file_id, file_unique_id, media_kind,
                          media_status, created_at
                     FROM operator_messages
                    WHERE media_status = ANY(%s) AND tag = ANY(%s)
                    ORDER BY created_at
                    LIMIT %s""",
                (list(RETRYABLE_STATUSES), list(allowed_tags), MAX_ROWS_PER_RUN))
            due = cur.fetchall()

            for row in due:
                age = _dt.datetime.now(_dt.timezone.utc) - row["created_at"]
                resolved = _token_for_tag(cur, row["tag"])
                if resolved is None:
                    skipped_no_token += 1
                    print(f"[skip] #{row['id']} tag={row['tag']!r} — no bot_channels row "
                          f"with this channel_tag, or its token env var is unset on this host")
                    if fire and age > ESCALATE_AFTER:
                        _notify(
                            f"MEDIA #{row['id']} UNRESOLVABLE: no token for tag {row['tag']!r}",
                            f"operator_messages #{row['id']} (tag={row['tag']}, kind={row['media_kind']}, "
                            f"age={age}) cannot be retried — no bot_channels row matches this "
                            f"channel_tag, or its token_env_key is unset on THIS host. file_id is "
                            f"still safe in the row; this just needs the right host/token to retry.")
                        escalated += 1
                    continue

                channel_key, token = resolved
                if not fire:
                    print(f"[dry-run] would retry #{row['id']} kind={row['media_kind']} "
                          f"channel={channel_key} age={age}")
                    continue

                try:
                    path = _ingest._download_media(
                        token, row["file_id"], row["file_unique_id"], channel_key, row["id"])
                except Exception as e:
                    # held_commitment #964: a PERMANENT failure (file too big for
                    # the Bot API, invalid/expired file_id, 404) must become the
                    # TERMINAL 'unrecoverable' — otherwise this row re-enters the
                    # retry loop forever and re-pages every ESCALATE_AFTER about
                    # something no retry can fix. Transient failures (Errno 54,
                    # timeouts) stay 'failed' and keep retrying.
                    # after_retry=True: THIS call is already a retry of ingest's
                    # own getFile, so an ambiguous "wrong file_id or the file is
                    # temporarily unavailable" is no longer credible as a
                    # transient blip (cc-quality F2 on PR#786).
                    terminal = _ingest._unrecoverable_reason(e, after_retry=True)
                    stored = _store_failure_status(cur, row["id"], terminal)
                    conn.commit()
                    terminal = terminal if stored == "unrecoverable" else None
                    print(f"[retry-failed] #{row['id']} — {type(e).__name__}: {e}"
                          + (f" — TERMINAL, will not retry: {terminal}" if terminal else ""))
                    if terminal:
                        _notify(
                            f"MEDIA #{row['id']} UNRECOVERABLE — operator must resend or "
                            f"confirm redundant",
                            f"operator_messages #{row['id']} (tag={row['tag']}, kind="
                            f"{row['media_kind']}, age={age}) can NEVER be downloaded: "
                            f"{terminal}. Marked terminal, so this reaper will NOT retry it "
                            f"again and there will be no further pages for this row. One "
                            f"action, once: ask the operator to resend it smaller (the Bot "
                            f"API cannot download anything over 20 MB), or confirm it was "
                            f"redundant/already covered and close the message without it.")
                        escalated += 1
                    elif age > ESCALATE_AFTER:
                        _notify(
                            f"MEDIA #{row['id']} STILL FAILING after {age}",
                            f"operator_messages #{row['id']} (tag={row['tag']}, kind="
                            f"{row['media_kind']}) has been retriable for {age} and still "
                            f"won't download: {type(e).__name__}: {e}. file_id/file_unique_id "
                            f"are intact in the row for a manual getFile if this is a dead file_id "
                            f"(Telegram file_ids expire eventually) rather than a transient CDN issue.")
                        escalated += 1
                    continue

                marker = (f" — RECOVERED by media-reaper → {path}"
                          if "download failed" in (row["text"] or "") else "")
                cur.execute(
                    "UPDATE operator_messages SET media_status='ok', text = text || %s "
                    "WHERE id=%s AND media_status != 'ok'",
                    (marker, row["id"]))
                conn.commit()
                print(f"[recovered] #{row['id']} kind={row['media_kind']} -> {path}")
                recovered += 1

    print(f"\nmedia_reaper: due={len(due)} recovered={recovered} escalated={escalated} "
          f"skipped_no_token={skipped_no_token} mode={'FIRE' if fire else 'dry-run'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fire", action="store_true",
                    help="actually retry + write (default is a dry-run that changes nothing)")
    args = ap.parse_args()
    return reap(fire=args.fire)


if __name__ == "__main__":
    raise SystemExit(main())
