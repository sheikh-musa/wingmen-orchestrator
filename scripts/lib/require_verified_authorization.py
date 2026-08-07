"""require_verified_authorization.py — enforced pre-execution authorization gate
for IRREVERSIBLE operations (SECURITY-CRITICAL, 2026-07-03 near-miss remediation).

WHY THIS EXISTS
---------------
On 2026-07-03 an irreversible PII purge (~2763 stale irsyad rows on the ihsanos
multi-tenant DB `ceayjeamtmcyzzvqflus`) was nearly executed on PHANTOM approvals:
six "YES PURGE" strings appeared TYPED INTO A TMUX CONSOLE, with NO corresponding
operator message in the durable bridge log. A console/tmux "YES" is trivially
producible by any relay, mis-targeted send-keys, or agent — it is NOT proof the
human operator authorized anything.

THE RULE (fail-closed)
----------------------
An irreversible op MUST NOT run unless a BRIDGE-VERIFIED AUTHORIZATION ARTIFACT
exists in `operator_messages`:
  - direction = 'inbound'      (came IN through the Telegram bridge, not a console)
  - channel   = 'telegram'     (a real bridge/ingest artifact, not a hand INSERT)
  - chat_id   = the operator's real Telegram chat id (MUSA_TELEGRAM_ID)
  - created_at > <request time> (the YES came AFTER the op was requested)
  - text contains an approval phrase (e.g. "YES PURGE")
  - text references the specific op (an op-identifying token must appear)

Anything short of that -> DENY. Missing config, unreachable DB, no matching row:
all DENY. There is no "assume yes" path.

USAGE (the gate a purge/irreversible-op script MUST call)
--------------------------------------------------------
    from scripts.lib.require_verified_authorization import verified_authorization
    res = verified_authorization(
        op_id="irsyad-residency-purge",
        after=request_ts,                       # when the op was requested
        approval_phrases=["YES PURGE"],
        op_tokens=["purge", "irsyad"],
    )
    if not res.ok:
        sys.exit(f"REFUSED: {res.reason}")      # fail-closed
    # ... only here may the irreversible op proceed; res.row is the audit artifact
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence


@dataclass
class AuthResult:
    ok: bool
    reason: str
    row: dict | None = None  # the matching operator_messages row, for audit


def _as_aware(ts) -> datetime | None:
    """Normalize a timestamp (datetime or ISO-8601 string) to an aware UTC
    datetime. Returns None if it can't be parsed (caller treats as fail-closed)."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        s = ts.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def find_verified_authorization(
    rows: Iterable[dict],
    *,
    operator_chat_id: str,
    approval_phrases: Sequence[str],
    op_tokens: Sequence[str],
    after: datetime,
) -> dict | None:
    """PURE predicate — the heart of the gate, fully unit-testable with no DB.

    Return the FIRST row in `rows` that is a valid bridge-verified authorization,
    else None. A row qualifies iff ALL hold (see module docstring for the why):
      direction=='inbound', channel=='telegram', chat_id==operator_chat_id,
      created_at>after, text contains an approval phrase AND an op token.

    A console/tmux "YES" never lands in operator_messages with the operator's
    real chat_id via an inbound telegram row, so it can never satisfy this.
    """
    want_chat = str(operator_chat_id)
    phrases = [p.lower() for p in approval_phrases if p]
    tokens = [t.lower() for t in op_tokens if t]
    if not want_chat or not phrases or not tokens or after is None:
        return None
    for r in rows:
        if (r.get("direction") or "").lower() != "inbound":
            continue
        if (r.get("channel") or "").lower() != "telegram":
            continue
        if str(r.get("chat_id") or "") != want_chat:
            continue
        created = _as_aware(r.get("created_at"))
        if created is None or created <= after:
            continue
        text = (r.get("text") or "").lower()
        if not any(p in text for p in phrases):
            continue
        if not any(t in text for t in tokens):
            continue
        return r
    return None


def _fetch_candidate_rows(dsn: str, operator_chat_id: str, after: datetime) -> list[dict]:
    import psycopg
    with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, direction, channel, chat_id, tag, text, created_at "
            "FROM operator_messages "
            "WHERE direction='inbound' AND channel='telegram' "
            "AND chat_id=%s AND created_at > %s "
            "ORDER BY id DESC LIMIT 200",
            (str(operator_chat_id), after),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def verified_authorization(
    op_id: str,
    *,
    after,
    approval_phrases: Sequence[str],
    op_tokens: Sequence[str],
    operator_chat_id: str | None = None,
    dsn: str | None = None,
) -> AuthResult:
    """The GATE. Fail-closed on every uncertainty. Returns AuthResult(ok, reason, row).

    `after` is the moment the op was requested (datetime or ISO string): the
    authorization must be NEWER than that so a stale/old YES can never be replayed.
    """
    after_dt = _as_aware(after)
    if after_dt is None:
        return AuthResult(False, f"[{op_id}] no valid request timestamp ('after') — fail-closed")

    operator_chat_id = operator_chat_id or os.environ.get("MUSA_TELEGRAM_ID")
    if not operator_chat_id:
        return AuthResult(False, f"[{op_id}] operator chat id unknown (MUSA_TELEGRAM_ID unset) — fail-closed")

    dsn = dsn or os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        return AuthResult(False, f"[{op_id}] no substrate DSN — cannot verify authorization — fail-closed")

    if not approval_phrases or not op_tokens:
        return AuthResult(False, f"[{op_id}] misconfigured gate (no approval phrase / op token) — fail-closed")

    try:
        rows = _fetch_candidate_rows(dsn, operator_chat_id, after_dt)
    except Exception as e:  # DB unreachable / query error -> DENY, never assume yes
        return AuthResult(False, f"[{op_id}] authorization DB check failed ({type(e).__name__}: {e}) — fail-closed")

    row = find_verified_authorization(
        rows,
        operator_chat_id=operator_chat_id,
        approval_phrases=approval_phrases,
        op_tokens=op_tokens,
        after=after_dt,
    )
    if row is None:
        return AuthResult(
            False,
            f"[{op_id}] NO bridge-verified operator authorization found "
            f"(need inbound telegram '{'/'.join(approval_phrases)}' from chat "
            f"{operator_chat_id} referencing {list(op_tokens)} after "
            f"{after_dt.isoformat()}). An in-console/tmux YES is NOT sufficient.",
        )
    return AuthResult(True, f"[{op_id}] authorized by operator_messages id={row.get('id')} "
                            f"at {_as_aware(row.get('created_at')).isoformat()}", row)


def main(argv=None) -> int:
    """CLI: check a gate from the shell. Exit 0 = authorized, non-zero = DENIED."""
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Check a bridge-verified authorization gate (fail-closed).")
    ap.add_argument("--op-id", required=True)
    ap.add_argument("--after", required=True, help="ISO-8601 request timestamp; YES must be newer")
    ap.add_argument("--phrase", action="append", required=True, help="approval phrase (repeatable)")
    ap.add_argument("--token", action="append", required=True, help="op-identifying token (repeatable)")
    ap.add_argument("--chat", default=None, help="operator chat id (default MUSA_TELEGRAM_ID)")
    a = ap.parse_args(argv)
    res = verified_authorization(
        a.op_id, after=a.after, approval_phrases=a.phrase, op_tokens=a.token,
        operator_chat_id=a.chat,
    )
    print(json.dumps({"ok": res.ok, "reason": res.reason,
                      "row_id": (res.row or {}).get("id")}, default=str))
    return 0 if res.ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
