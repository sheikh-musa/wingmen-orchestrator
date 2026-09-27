#!/usr/bin/env python3
"""gated_inbound_digest.py — once-daily digest of GATED inbound Telegram messages.

WHY (orch-console bus #43647, PR #174 follow-up): PR #174 stamps handled_at on
gated (deny-by-default) operator_messages rows so a stranger's /start no longer
fires a "Got your message" ack into the operator's own chat. The risk that
creates: a LEGITIMATE new client contact — a real person messaging a client bot
from an unknown chat — is now also silently stamped handled and never surfaced.
This digest closes that gap: once a day it reports WHO was gated (count + sender
name/username + channel) so a real new contact gets seen and allowlisted.

PRIVACY (hard rule, Nazim #43647): the digest carries ONLY count + sender
name/username + chat_id + channel. It NEVER includes message text.

Gated classification (no DB marker exists — ingest only writes a _log_line and
sets delivered=true at INSERT for all inbound): re-derived here by MIRRORING
nervous_system.ingest.gate_allows (chat_id in allowed_chat_ids OR normalized
username in allowed_usernames). `is_gated` below is a faithful inverse of that
function, kept as a pure function so it is unit-tested without a DB. A message
is gated iff NO channel matching its tag would have allowed its sender.

Signal, not noise: if there were ZERO gated inbound in the window, NOTHING is
posted (a daily zero-post is spam).

Run: python scripts/gated_inbound_digest.py [--hours 24] [--dry-run]
Schedule: see the crontab line in the PR description (host op — not installed here).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


# ── pure classification (mirrors nervous_system.ingest.gate_allows; unit-tested) ──
def _norm_user(u):
    return u.lstrip("@").lower() if u else u


def is_gated(chat_id, username, allowed_chat_ids, allowed_usernames) -> bool:
    """True when this sender would be DENIED by the gate (the inverse of
    ingest.gate_allows). chat_id None => gated (ingest treats it so). Faithful
    mirror: allow iff chat_id in allowed_chat_ids OR normalized username in
    normalized allowed_usernames; gated otherwise."""
    if chat_id is None:
        return True
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return True  # unparseable chat id was never a valid allowlisted sender
    if cid in (allowed_chat_ids or []):
        return False
    if username:
        norm = _norm_user(username)
        if norm in [_norm_user(u) for u in (allowed_usernames or [])]:
            return False
    return True


def row_is_gated(row: dict, channels_by_tag: dict) -> bool:
    """Pure: a row is gated iff NO channel matching its tag would allow the
    sender. `channels_by_tag[tag]` is a list of (channel_key, allowed_chat_ids,
    allowed_usernames). A tag with no matching channel is treated as NOT gated
    here (orphan tag — surfaced separately, never as a false 'new contact')."""
    matches = channels_by_tag.get(row.get("tag"))
    if not matches:
        return False
    for _key, chat_ids, usernames in matches:
        if not is_gated(row.get("chat_id"), row.get("from_username"), chat_ids, usernames):
            return False  # allowed by at least one matching channel => not gated
    return True


def channel_key_for_tag(tag: str, channels_by_tag: dict) -> str:
    matches = channels_by_tag.get(tag)
    return matches[0][0] if matches else (tag or "?")


def build_digest_body(gated_rows: list, channels_by_tag: dict) -> str | None:
    """Aggregate gated rows by (channel, from_name, from_username, chat_id) and
    render the digest body. Returns None when there is nothing to report (so the
    caller posts nothing). NEVER includes message text."""
    if not gated_rows:
        return None
    agg: dict = {}
    for r in gated_rows:
        ckey = channel_key_for_tag(r.get("tag"), channels_by_tag)
        k = (ckey, r.get("from_name"), r.get("from_username"), str(r.get("chat_id")))
        agg[k] = agg.get(k, 0) + 1
    total = sum(agg.values())
    senders = len(agg)
    lines = [
        f"\U0001F4E5 Gated inbound (last 24h): {total} message(s) from {senders} "
        f"un-allowlisted sender(s). Review + allowlist any REAL client contacts "
        f"(bot_channels.allowed_chat_ids / allowed_usernames). No message text is included."
    ]
    for (ckey, name, user, cid) in sorted(agg, key=lambda x: (x[0], str(x[1]))):
        who = name or "(no name)"
        uh = f" @{user}" if user else ""
        lines.append(f"• {ckey}: {who}{uh} (chat {cid}) — {agg[(ckey, name, user, cid)]} msg(s)")
    return "\n".join(lines)


# ── DB I/O (not unit-tested; the classification above is) ─────────────────────
def _dsn(env: dict) -> str:
    v = env.get("SUPABASE_DB_URL") or env.get("DATABASE_URL")
    if v:
        return v
    envf = _ROOT / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            for key in ("SUPABASE_DB_URL=", "DATABASE_URL="):
                if line.startswith(key):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("gated_inbound_digest: no SUPABASE_DB_URL/DATABASE_URL in env or .env")


def load_channels_by_tag(conn) -> dict:
    out: dict = {}
    with conn.cursor() as cur:
        cur.execute("SELECT channel_key, channel_tag, allowed_chat_ids, allowed_usernames FROM bot_channels")
        for key, tag, chat_ids, usernames in cur.fetchall():
            out.setdefault(tag, []).append((key, chat_ids or [], usernames or []))
    return out


def load_inbound(conn, hours: int) -> list:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, tag, chat_id, from_username, from_name FROM operator_messages "
            "WHERE direction='inbound' AND created_at > now() - make_interval(hours => %s)",
            (hours,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Daily gated-inbound digest to orch-console.")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--to", default="orch-console")
    ap.add_argument("--from-agent", default=os.environ.get("AGENT_ID") or "cc-orchestrator")
    ap.add_argument("--dry-run", action="store_true", help="print the digest, do not post")
    args = ap.parse_args(argv)

    try:
        import psycopg2
        sys.path.insert(0, str(_ROOT / "scripts"))
        import bus_send

        dsn = _dsn(os.environ)
        conn = psycopg2.connect(dsn)
        try:
            channels_by_tag = load_channels_by_tag(conn)
            rows = load_inbound(conn, args.hours)
        finally:
            conn.close()

        gated = [r for r in rows if row_is_gated(r, channels_by_tag)]
        body = build_digest_body(gated, channels_by_tag)
        if body is None:
            print(f"gated_inbound_digest: 0 gated inbound in last {args.hours}h — nothing to post.")
            return 0
        subject = (f"Gated inbound digest ({args.hours}h): "
                   f"{sum(1 for _ in gated)} msg from {len(set((channel_key_for_tag(r['tag'], channels_by_tag), r.get('chat_id')) for r in gated))} sender(s) — allowlist real contacts")
        if args.dry_run:
            print("SUBJECT:", subject)
            print(body)
            return 0
        row_id, _ = bus_send.send(
            from_agent=args.from_agent, to=args.to, mtype="update",
            subject=subject[:200], body=body, priority="P3", req=False, dsn=dsn,
        )
        print(f"gated_inbound_digest: posted bus #{row_id} to {args.to} ({len(gated)} gated rows).")
        return 0
    except SystemExit:
        raise
    except Exception as e:  # fail LOUD but non-fatal (a digest hiccup must not page)
        print(f"gated_inbound_digest: FAILED (non-fatal): {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
