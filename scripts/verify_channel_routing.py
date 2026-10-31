#!/usr/bin/env python3
"""TG2.0 section 4 — the routing checklist, mechanised (design report §4).

Onboarding a client onto a shared bot token kept needing the same six-step
proof invented by hand (the brandit-coffeemedia run). This tool makes it
repeatable and reads the database DIRECTLY — the design's own words: "confirm
by reading operator_messages directly (not by trusting a relayed claim)".
That independent read is what caught the real lane_reply.sh finding during the
brandit proof.

  capture — steps 1-2. Lists the chats a token group has logged as UNCLAIMED,
            wherever the ingest wrote them (every tag in the group, not just
            today's fallback tag — live rows have landed under a sibling tag),
            with the chat_id taken from the row itself, never typed by hand.
            --limit keeps the NEWEST rows.

  check   — steps 4-5. After the onboarding row is gated and applied and the
            client has sent a SECOND message, proves:
              * the row declares an allowlist and does not overlap a sibling;
              * each allowed chat resolves to THIS row (match_channel_in_group
                is the gate — this reuses ingest's own function, no divergence);
              * at least one inbound row now sits under this channel's tag;
              * no foreign chat's message under this tag arrived while a
                SIBLING already claimed that chat (judged at arrival time);
              * no message from this chat landed under a sibling tag after this
                row existed (the cross-wire check). Rows from before anyone
                claimed the chat — its pre-onboarding traffic, under whatever
                tag — are reported as evidence, not failures.

Step 3 (create the row enabled=false, gate, apply) and step 6 (promotion to a
less-restricted phase is its own separate gate) are deliberately NOT here:
they belong to onboard_client_channel.py and the console's gate, not to a
read-only verification tool. This tool never writes.

Exit codes: 0 = all checks pass, 1 = a check failed, 2 = usage/DB error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_COLUMNS = ("channel_key", "token_env_key", "channel_tag", "allowed_chat_ids",
            "allowed_usernames", "enabled", "created_at")


def _as_aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _norm_chat(chat_id):
    """Chat ids are stored as text; compare them as canonical strings."""
    if chat_id is None:
        return None
    s = str(chat_id).strip()
    if not s:
        return None
    try:
        return str(int(s))
    except ValueError:
        return s


def load_group(conn, token_env_key):
    """Every bot_channels row sharing one token, deterministically ordered."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT channel_key, token_env_key, channel_tag, "
            "       COALESCE(allowed_chat_ids, '{}') AS allowed_chat_ids, "
            "       COALESCE(allowed_usernames, '{}') AS allowed_usernames, "
            "       enabled, created_at "
            "FROM bot_channels WHERE token_env_key = %s ORDER BY channel_key",
            (token_env_key,),
        )
        rows = [dict(zip(_COLUMNS, r)) for r in cur.fetchall()]
    for r in rows:
        r["allowed_chat_ids"] = [_norm_chat(c) for c in (r["allowed_chat_ids"] or [])]
        r["allowed_usernames"] = list(r["allowed_usernames"] or [])
        r["created_at"] = _as_aware(r["created_at"])
    _attach_invites(conn, rows)
    return rows


def _attach_invites(conn, rows):
    """Everything the invite ledger knows about these rows.

    bound_chats: when an invite bound a chat, THAT is when the row claimed it —
    a row can exist for hours with an empty allowlist while its invite is out
    (live: faizul created 05:41, chat bound by the deep-link tap at 06:45:39).
    created_at alone would report every invite onboarding's own tap as a
    cross-wire.

    invite_pending: an unexpired, unused invite. The ingest already exempts an
    empty allowlist in that state (op#34846) — it is the designed pre-bind
    window, not a row that can never route.
    """
    keys = [r["channel_key"] for r in rows]
    invites = []
    if keys:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT channel_key, bound_chat_id, used_at, expires_at "
                "FROM bot_channel_invites WHERE channel_key = ANY(%s)",
                (keys,),
            )
            invites = cur.fetchall()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.setdefault("bound_chats", {})
        r.setdefault("invite_pending", False)
        r.setdefault("invite_expires_at", None)
    for key, chat, used, exp in invites:
        for r in rows:
            if r["channel_key"] != key:
                continue
            if used is not None and chat is not None:
                r["bound_chats"].setdefault(_norm_chat(chat), _as_aware(used))
            if used is None and exp is not None:
                exp = _as_aware(exp)
                if r["invite_expires_at"] is None or exp > r["invite_expires_at"]:
                    r["invite_expires_at"] = exp
                if exp > now:
                    r["invite_pending"] = True


def _group_for_channel(conn, channel_key):
    with conn.cursor() as cur:
        cur.execute("SELECT token_env_key FROM bot_channels WHERE channel_key = %s",
                    (channel_key,))
        got = cur.fetchone()
    if not got:
        return []
    return load_group(conn, got[0])


def load_group_messages(conn, rows, limit=None):
    """Every inbound row carrying a tag in this group — the FULL history."""
    tags = sorted({r["channel_tag"] for r in rows if r.get("channel_tag")})
    if not tags:
        return []
    if limit:
        # A cap must keep the NEWEST rows: ORDER BY id LIMIT n alone returns
        # the oldest n, so a capped scan would never see today's arrivals.
        sql = ("SELECT id, chat_id, tag, created_at FROM ("
               "SELECT id, chat_id, tag, created_at FROM operator_messages "
               "WHERE direction = 'inbound' AND chat_id IS NOT NULL AND tag = ANY(%s) "
               "ORDER BY id DESC LIMIT %s) newest ORDER BY id")
        params = [tags, int(limit)]
    else:
        sql = ("SELECT id, chat_id, tag, created_at FROM operator_messages "
               "WHERE direction = 'inbound' AND chat_id IS NOT NULL AND tag = ANY(%s) "
               "ORDER BY id")
        params = [tags]
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        out = [{"id": r[0], "chat_id": _norm_chat(r[1]), "tag": r[2],
                "created_at": _as_aware(r[3])} for r in cur.fetchall()]
    return out


def fallback_tag(rows):
    """The tag an unclaimed inbound is logged under: the first row by
    channel_key (the ingest writes unmatched messages there for audit)."""
    if not rows:
        return None
    return sorted(rows, key=lambda r: r["channel_key"])[0]["channel_tag"]


def claimed_chats(rows):
    """chat_id -> [channel_key, ...] over the whole group (canonical strings)."""
    out = {}
    for r in rows:
        for cid in r.get("allowed_chat_ids") or []:
            norm = _norm_chat(cid)
            if norm is not None:
                out.setdefault(norm, []).append(r["channel_key"])
    return out


def unmatched_rows(rows, messages):
    """Inbound rows from chats NO row in the group claims."""
    claimed = set(claimed_chats(rows))
    return [m for m in messages if m.get("chat_id") and m["chat_id"] not in claimed]


def summarise_capture(token_env_key, rows, messages):
    """Steps 1-2: which unclaimed chats have messaged, and where they landed."""
    fallback = fallback_tag(rows)
    by_chat = {}
    for m in unmatched_rows(rows, messages):
        entry = by_chat.setdefault(m["chat_id"], {
            "chat_id": m["chat_id"], "tag": m["tag"],
            "first_seen": m["created_at"], "messages": 0})
        entry["messages"] += 1
        if _as_aware(m["created_at"]) < _as_aware(entry["first_seen"]):
            entry["first_seen"] = m["created_at"]
            entry["tag"] = m["tag"]
    out = sorted(by_chat.values(), key=lambda e: (_as_aware(e["first_seen"]), e["chat_id"]))
    for e in out:
        e["first_seen"] = _as_aware(e["first_seen"]).isoformat()
    return {"token_env_key": token_env_key, "fallback_tag": fallback, "unmatched": out}


def _namespaces(rows):
    """The minimal shape ingest.gate_allows / match_channel_in_group read."""
    def as_int(cid):
        try:
            return int(cid)
        except (TypeError, ValueError):
            return cid
    return [SimpleNamespace(key=r["channel_key"],
                            allowed_chat_ids=[as_int(c) for c in (r.get("allowed_chat_ids") or [])],
                            allowed_usernames=r.get("allowed_usernames") or [])
            for r in sorted(rows, key=lambda r: r["channel_key"])]


def _claims_at(rows, chat_id, when):
    """Rows whose allowlist contained `chat_id` at `when`.

    Routing is judged as of the message's own arrival, not as of today: a chat
    is legitimately logged wherever it landed while NOBODY claimed it (the
    unclaimed-inbound capture path), and a cross-wire only if it was already
    someone else's when it arrived.
    """
    when = _as_aware(when)
    out = []
    for r in rows:
        claimed_at = _claim_time(r, chat_id)
        if claimed_at is not None and when < claimed_at:
            continue
        if chat_id in {_norm_chat(c) for c in (r.get("allowed_chat_ids") or [])}:
            out.append(r)
    return out


def _claim_time(r, chat_id):
    """When row `r` claimed `chat_id`: the invite BIND if one exists (that is
    the moment the allowlist gained the chat; the row may predate it), else
    the row's own created_at."""
    created = _as_aware(r.get("created_at"))
    bound = (r.get("bound_chats") or {}).get(_norm_chat(chat_id))
    if bound is not None and (created is None or _as_aware(bound) > created):
        return _as_aware(bound)
    return created


def _check(name, ok, detail, **extra):
    return {"name": name, "ok": bool(ok), "detail": detail, **extra}


def evaluate(rows, messages, channel_key):
    """Steps 4-5 as a pure function of the rows and the message history."""
    from nervous_system.ingest import match_channel_in_group

    this = next((r for r in rows if r["channel_key"] == channel_key), None)
    if this is None:
        return {"ok": False, "channel_key": channel_key,
                "checks": [_check("channel_exists", False,
                                  f"no bot_channels row with channel_key={channel_key!r}")],
                "evidence": {}}

    group = _namespaces(rows)
    others = [r for r in rows if r["channel_key"] != channel_key]
    fallback = fallback_tag(rows)
    mine = {_norm_chat(c) for c in (this.get("allowed_chat_ids") or [])}
    mine.discard(None)
    created = this["created_at"]
    checks = [_check("channel_exists", True,
                     f"bot_channels row found for {channel_key!r}")]

    declared = bool(mine or (this.get("allowed_usernames") or []))
    pending = bool(this.get("invite_pending"))
    pending_until = this.get("invite_expires_at")
    pending_iso = (pending_until.replace(microsecond=0).isoformat()
                   if pending_until else None)
    pending_note = (f" (outstanding invite expires {pending_iso})"
                    if pending and pending_iso else "")
    if declared:
        declared_detail = (f"allowed_chat_ids={sorted(mine)} "
                           f"usernames={len(this.get('allowed_usernames') or [])}")
    elif pending:
        declared_detail = ("no chat claimed yet; invite outstanding until "
                           f"{pending_iso} (designed pre-bind state — "
                           "ingest exempts it too)")
    else:
        declared_detail = ("the row claims no chat and no username — gate_allows() "
                           "can never pass")
    checks.append(_check("allowlist_declared", declared or pending, declared_detail))

    overlaps = {cid: keys for cid, keys in claimed_chats(rows).items() if len(keys) > 1}
    checks.append(_check(
        "no_overlap", not overlaps,
        "no chat id claimed by more than one row" if not overlaps
        else f"ambiguous routing: {overlaps}"))

    unresolved = []
    for cid in sorted(mine):
        got = match_channel_in_group(group, int(cid), None)
        if got is None or got.key != channel_key:
            unresolved.append(f"{cid}->{got.key if got else None}")
    checks.append(_check(
        "resolves_to_self", (not unresolved and bool(mine)) or (pending and not mine),
        "every allowed chat resolves to this channel" if not unresolved and mine
        else "not applicable until the client taps the invite" if pending and not mine
        else f"unresolved/misdirected chats: {unresolved or 'no chat ids'}"))

    mine_msgs = [m for m in messages if m["chat_id"] in mine]
    routed = [m for m in mine_msgs if m["tag"] == this["channel_tag"]]
    if routed:
        routed_detail = (f"{len(routed)} inbound row(s) under "
                         f"{this['channel_tag']!r} from this chat")
    elif pending:
        routed_detail = (f"no inbound row under {this['channel_tag']!r} yet — "
                         f"awaiting the client tap{pending_note}")
    else:
        routed_detail = (f"no inbound row under {this['channel_tag']!r} yet — has the "
                         f"client sent the SECOND message after the ingest restart?")
    checks.append(_check("routed_evidence", bool(routed), routed_detail))

    under_this_tag = [m for m in messages if m["tag"] == this["channel_tag"]]
    foreign = [m for m in under_this_tag if m["chat_id"] not in mine]
    # A foreign row is a cross-wire only if a SIBLING already claimed that chat
    # when the message arrived (live: faizul claimed 1578424630 at 05:41 and
    # its 06:45 message still landed under coffeemedia). A chat nobody had
    # claimed yet legitimately lands wherever the unclaimed path wrote it (live:
    # brandit's first two messages, 30 min before its own row existed) — that
    # is capture evidence, counted and reported, never a gate failure.
    cross_wire, unclaimed = [], []
    for m in foreign:
        claimers = [r for r in _claims_at(rows, m["chat_id"], m["created_at"])
                    if r["channel_key"] != channel_key]
        (cross_wire if claimers else unclaimed).append((m, claimers))
    checks.append(_check(
        "no_foreign_rows_under_this_tag", not cross_wire,
        "every row under this tag is from a chat this channel owned when it arrived"
        if not cross_wire else
        "foreign chat(s) logged under this tag while a sibling claimed them: "
        + ", ".join(
            f"{m['chat_id']} while claimed by "
            f"{sorted({r['channel_key'] for r in claimers})} (msg {m['id']})"
            for m, claimers in cross_wire)))

    sibling_tags = {r["channel_tag"] for r in others}

    def _since_claimed(m):
        claimed_at = _claim_time(this, m["chat_id"]) or created
        return _as_aware(m["created_at"]) >= claimed_at

    post_crosswire = [m for m in mine_msgs
                      if m["tag"] in sibling_tags and _since_claimed(m)]
    checks.append(_check(
        "crosswire_after_onboarding", not post_crosswire,
        "no message from this chat reached a sibling tag after onboarding"
        if not post_crosswire else
        "cross-wire after onboarding: "
        + ", ".join(f"{m['chat_id']}->{m['tag']} (msg {m['id']})" for m in post_crosswire)))

    # Evidence, not failures: where this chat's pre-onboarding traffic landed.
    # The ingest's unclaimed path is group-level, not "the fallback tag" — the
    # live brandit rows landed under 'coffeemedia', which is why asserting a
    # fallback tag here produced false failures.
    pre = [m for m in mine_msgs
           if m["tag"] in sibling_tags and not _since_claimed(m)]

    return {
        "ok": all(c["ok"] for c in checks),
        "channel_key": channel_key,
        "token_env_key": this["token_env_key"],
        "checks": checks,
        "evidence": {
            "group_size": len(rows),
            "routed_rows": len(routed),
            "pre_onboarding_rows": len(pre),
            "pre_onboarding_tags": sorted({m["tag"] for m in pre}),
            "unclaimed_arrivals": len(unclaimed),
            "unclaimed_arrival_chats": sorted({m["chat_id"] for m, _ in unclaimed}),
            "cross_wire_rows": len(cross_wire),
            "sibling_tags": sorted(sibling_tags),
            "fallback_tag": fallback,
            "enabled": bool(this.get("enabled")),
            "invite_pending": pending,
            "invite_expires_at": pending_iso,
        },
    }


def check(conn, channel_key):
    rows = _group_for_channel(conn, channel_key)
    messages = load_group_messages(conn, rows)
    return evaluate(rows, messages, channel_key)


def capture(conn, token_env_key, limit=None):
    rows = load_group(conn, token_env_key)
    messages = load_group_messages(conn, rows, limit=limit)
    return summarise_capture(token_env_key, rows, messages)


def _print_human(obj, kind):
    if kind == "capture":
        print(f"token {obj['token_env_key']} — fallback tag {obj['fallback_tag']!r}")
        if not obj["unmatched"]:
            print("no unclaimed chats have messaged this token group")
        for u in obj["unmatched"]:
            print(f"  chat {u['chat_id']}: {u['messages']} message(s), "
                  f"first seen {u['first_seen']}, logged under {u['tag']!r}")
        return
    print(f"Routing check for {obj['channel_key']}: "
          f"{'PASS' if obj['ok'] else 'FAIL'}")
    for c in obj["checks"]:
        print(f"  [{'ok' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
    if obj.get("evidence"):
        print(f"  evidence: {json.dumps(obj['evidence'], sort_keys=True)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="list unclaimed chats for a token group")
    cap.add_argument("--token", required=True, help="token_env_key, e.g. WINGMENDEVBOT_TOKEN")
    cap.add_argument("--limit", type=int, default=0, help="scan at most N inbound rows")

    chk = sub.add_parser("check", help="run the routing checklist for a channel")
    chk.add_argument("--channel", required=True, help="channel_key")

    for p in (cap, chk):
        p.add_argument("--dsn", default=None, help="defaults to $DATABASE_URL")
        p.add_argument("--json", action="store_true", help="machine-readable output")

    args = ap.parse_args(argv)
    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        print("verify_channel_routing: no DSN (pass --dsn or set DATABASE_URL)", file=sys.stderr)
        return 2
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            if args.cmd == "capture":
                obj = capture(conn, args.token, limit=args.limit or None)
            else:
                obj = check(conn, args.channel)
    except Exception as e:                                          # noqa: BLE001
        print(f"verify_channel_routing: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))
    else:
        _print_human(obj, args.cmd)
    return 0 if obj.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
