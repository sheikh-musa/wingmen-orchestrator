#!/usr/bin/env python3
"""gated_inbound_digest.py — once-daily digest of GATED inbound Telegram messages.

WHY (orch-console bus #43647/#43879, PR #174 follow-up): PR #174 stamps handled_at
on gated (deny-by-default) operator_messages rows so a stranger's /start no longer
fires a "Got your message" ack into the operator's own chat. The risk that creates:
a LEGITIMATE new client contact — a real person messaging a client bot from an
unknown chat — is now also silently stamped handled and never surfaced. This digest
closes that gap once a day.

ONE GATE IMPLEMENTATION (Nazim #43879): the gated verdict reuses the REAL
`nervous_system.ingest.gate_allows` (+ `Channel`) — imported, not mirrored, so it
can never drift from the live gate. Importing ingest is side-effect-free at the
daemon level (its `main()` runs only under `if __name__ == "__main__"`).

THREE BUCKETS, lead with the actionable one (Nazim #43879):
  (a) SERVICE, no action  — updates with no sender/chat (chat_id None: my_chat_member
      etc.). Summarized, not actioned.
  (b) OPERATOR DM to a client bot, no action — a private DM from a known operator id
      (MUSA_TELEGRAM_ID). Verified real case: Musa's /start to @angullia_bot (chat
      286619815) is bot-setup, NOT an actionable drop, and must NOT be allowlisted
      onto the client lane. Summarized, not actioned.
  (c) GENUINE unknown sender — the ACTIONABLE ones (a real contact to allowlist).
The digest LEADS with (c); (a)+(b) are summarized below. It posts ONLY when there is
at least one (c) — a digest of pure no-action noise is spam.

PRIVACY (hard rule): count + sender name/username + chat_id + channel ONLY. NEVER
message text.

Run: python scripts/gated_inbound_digest.py [--hours 24] [--dry-run]
Schedule: see the crontab line in the PR — host op, not installed here.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

# ONE gate implementation — the real one (imported, never mirrored). Side-effect-free
# at the daemon level: ingest.main() runs only under `if __name__ == "__main__"`.
from nervous_system.ingest import gate_allows, Channel  # noqa: E402

# Channel.COLS order — what a Channel(row) tuple expects. Used to build Channel
# objects from a bot_channels query so gate_allows sees exactly the live shape.
_CHANNEL_COLS = ("channel_key", "token_env_key", "mode", "inject_target", "inject_prefix",
                 "responder_ref", "allowed_chat_ids", "allowed_usernames", "group_routing",
                 "channel_tag", "log_target", "poll_offset")

# Bucket labels
SERVICE = "service"        # (a) no sender/chat
OPERATOR_DM = "operator_dm"  # (b) known operator's private DM to a client bot
GENUINE = "genuine"        # (c) actionable unknown sender


# ── pure classification (unit-tested; uses the REAL gate_allows) ─────────────
def _chat_int(chat_id):
    """operator_messages.chat_id is stored as text (or None). Return int, or None
    if absent/unparseable (both mean 'no valid allowlistable sender')."""
    if chat_id is None:
        return None
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return None


def sender_gated(ch: Channel, chat_id, username) -> bool:
    """Inverse of ingest's gate decision for ONE channel, mirroring ingest's ORDER
    (`chat_id is None or not gate_allows(...)`): a None/unparseable chat_id is gated;
    otherwise defer to the REAL gate_allows."""
    cid = _chat_int(chat_id)
    if cid is None:
        return True
    return not gate_allows(ch, cid, username)


def row_gated(row: dict, channels_by_tag: dict) -> bool:
    """A row is gated iff NO channel matching its tag would allow the sender. An
    orphan tag (no matching channel) is NOT flagged (not a 'new contact on a known
    channel'). `channels_by_tag[tag]` is a list of Channel objects."""
    matches = channels_by_tag.get(row.get("tag"))
    if not matches:
        return False
    return all(sender_gated(ch, row.get("chat_id"), row.get("from_username")) for ch in matches)


def bucket_of(row: dict, operator_ids: set) -> str:
    """Bucket a GATED row: (a) service (no chat), (b) operator DM (known operator id),
    (c) genuine unknown sender. Pure."""
    cid = _chat_int(row.get("chat_id"))
    if cid is None:
        return SERVICE
    if cid in operator_ids:
        return OPERATOR_DM
    return GENUINE


def classify(rows: list, channels_by_tag: dict, operator_ids: set) -> dict:
    """Return {GENUINE:[], OPERATOR_DM:[], SERVICE:[]} of the GATED rows only."""
    out = {GENUINE: [], OPERATOR_DM: [], SERVICE: []}
    for r in rows:
        if row_gated(r, channels_by_tag):
            out[bucket_of(r, operator_ids)].append(r)
    return out


def channel_key_for_tag(tag: str, channels_by_tag: dict) -> str:
    matches = channels_by_tag.get(tag)
    return matches[0].key if matches else (tag or "?")


def _agg(rows, channels_by_tag):
    agg = {}
    for r in rows:
        ckey = channel_key_for_tag(r.get("tag"), channels_by_tag)
        k = (ckey, r.get("from_name"), r.get("from_username"), str(r.get("chat_id")))
        agg[k] = agg.get(k, 0) + 1
    return agg


def build_digest_body(buckets: dict, channels_by_tag: dict) -> str | None:
    """Render the digest. Returns None unless there is >=1 GENUINE (actionable) row —
    a digest of pure service/operator noise is spam. Leads with (c); summarizes
    (a)+(b) below. NEVER includes message text."""
    genuine = buckets.get(GENUINE) or []
    if not genuine:
        return None
    g_agg = _agg(genuine, channels_by_tag)
    total = sum(g_agg.values())
    lines = [
        f"\U0001F4E5 Gated inbound (last 24h) — {total} ACTIONABLE message(s) from "
        f"{len(g_agg)} unknown sender(s). Review + allowlist any REAL client contacts "
        f"(bot_channels.allowed_chat_ids / allowed_usernames). No message text is included.",
        "",
        "ACTIONABLE — genuine unknown senders:",
    ]
    for (ckey, name, user, cid) in sorted(g_agg, key=lambda x: (x[0], str(x[1]))):
        uh = f" @{user}" if user else ""
        lines.append(f"• {ckey}: {name or '(no name)'}{uh} (chat {cid}) — {g_agg[(ckey, name, user, cid)]} msg(s)")

    op = buckets.get(OPERATOR_DM) or []
    svc = buckets.get(SERVICE) or []
    if op or svc:
        lines += ["", "No action needed (summarized):"]
        if op:
            op_agg = _agg(op, channels_by_tag)
            senders = ", ".join(sorted({f"{k[1] or '(no name)'}" for k in op_agg}))
            lines.append(f"• operator DM to a client bot (bot setup, do NOT allowlist onto the client lane): "
                         f"{sum(op_agg.values())} msg from {senders}")
        if svc:
            lines.append(f"• service updates, no sender (no action): {len(svc)} update(s)")
    return "\n".join(lines)


# ── DB I/O (not unit-tested; the classification above is) ────────────────────
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


def _operator_ids(env: dict) -> set:
    ids = set()
    for key in ("MUSA_TELEGRAM_ID", "OPERATOR_TELEGRAM_IDS"):
        v = env.get(key)
        if not v:
            continue
        for part in str(v).replace(",", " ").split():
            try:
                ids.add(int(part))
            except ValueError:
                pass
    return ids


def load_channels_by_tag(conn) -> dict:
    out: dict = {}
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_CHANNEL_COLS)} FROM bot_channels")
        for row in cur.fetchall():
            ch = Channel(tuple(row))
            out.setdefault(ch.channel_tag, []).append(ch)
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
        import bus_send

        dsn = _dsn(os.environ)
        operator_ids = _operator_ids(os.environ)
        conn = psycopg2.connect(dsn)
        try:
            channels_by_tag = load_channels_by_tag(conn)
            rows = load_inbound(conn, args.hours)
        finally:
            conn.close()

        buckets = classify(rows, channels_by_tag, operator_ids)
        body = build_digest_body(buckets, channels_by_tag)
        if body is None:
            print(f"gated_inbound_digest: no ACTIONABLE gated inbound in last {args.hours}h "
                  f"(genuine=0, operator_dm={len(buckets[OPERATOR_DM])}, service={len(buckets[SERVICE])}) — nothing to post.")
            return 0
        n = len(buckets[GENUINE])
        subject = f"Gated inbound digest ({args.hours}h): {n} actionable unknown-sender msg — allowlist real contacts"
        if args.dry_run:
            print("SUBJECT:", subject)
            print(body)
            return 0
        row_id, _ = bus_send.send(
            from_agent=args.from_agent, to=args.to, mtype="update",
            subject=subject[:200], body=body, priority="P3", req=False, dsn=dsn,
        )
        print(f"gated_inbound_digest: posted bus #{row_id} to {args.to} ({n} actionable rows).")
        return 0
    except SystemExit:
        raise
    except Exception as e:  # fail LOUD but non-fatal (a digest hiccup must not page)
        print(f"gated_inbound_digest: FAILED (non-fatal): {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
