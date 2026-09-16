"""project_operator_authorization.py — project-scoped, NON-money authorization
gate for a project's BUILD/SCOPE decisions (op#20702 Stage C).

WHY THIS EXISTS
---------------
Per-project governance (op#20702/20704/20706, reports/per-project-governance-
design-op20702.md) lets a project register its own operator(s) — e.g. irsyad's
Shuq + Wan, cosem's Hariz — to authorize THAT project's product/scoping/build
decisions, so the fleet never blocks on Musa for decisions that are genuinely
that project's own operators' call. This module is that gate.

It deliberately mirrors scripts.lib.require_verified_authorization's
evidentiary shape EXACTLY (a bridge-verified inbound operator_messages row —
never a console/tmux-typed claim, per the 2026-07-03 near-miss this pattern
was built to prevent) with one difference: the set of chat_ids that may
satisfy the gate comes from that project's registered operators
(project_governance.operators), not a single hardcoded MUSA_TELEGRAM_ID.

HARD BOUNDARY — this module must NEVER be used for money or irreversible ops.
require_verified_authorization.verified_authorization() stays the ONLY gate
for those (Musa-only, unconditionally) — this module does not import from,
alias, wrap, or in any way loosen it. A project's money_clearance_enabled
toggle (Stage D, separate security-reviewed PR) is a DIFFERENT, not-yet-built
gate; this module does not read or honour that column.

THE RULE (fail-closed, same shape as require_verified_authorization)
----------------------------------------------------------------------
A project's build/scope decision MUST NOT be treated as authorized unless a
BRIDGE-VERIFIED AUTHORIZATION ARTIFACT exists in `operator_messages`:
  - direction = 'inbound'      (came IN through the Telegram bridge)
  - channel   = 'telegram'     (a real bridge/ingest artifact)
  - chat_id   IN the project's registered operator chat_ids
              (project_governance.operators, per-project — NEVER a stranger,
              NEVER an operator registered for a DIFFERENT project)
  - created_at > <request time>
  - text contains an approval phrase AND an op-identifying token

A project with NO registered operators (or no project_governance row at all)
satisfies NOTHING — fail-closed, not a silent fallback to Musa (that would
blur this gate's boundary with the money-only Musa gate).

USAGE
-----
    from scripts.lib.project_operator_authorization import verified_project_authorization
    res = verified_project_authorization(
        "irsyad", "fee-portal-rollout", after=request_ts,
        approval_phrases=["APPROVED"], op_tokens=["fee-portal"],
    )
    if not res.ok:
        sys.exit(f"REFUSED: {res.reason}")
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence


@dataclass
class ProjectAuthResult:
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


def find_verified_project_authorization(
    rows: Iterable[dict],
    *,
    operator_chat_ids: Sequence[str],
    approval_phrases: Sequence[str],
    op_tokens: Sequence[str],
    after: datetime,
) -> dict | None:
    """PURE predicate — fully unit-testable with no DB.

    Return the FIRST row in `rows` that is a valid bridge-verified
    authorization from ANY of `operator_chat_ids` (a project may have
    multiple registered operators; any one's approval is sufficient — this
    is not a dual-sign requirement), else None.
    """
    want_chats = {str(c) for c in operator_chat_ids if c}
    phrases = [p.lower() for p in approval_phrases if p]
    tokens = [t.lower() for t in op_tokens if t]
    if not want_chats or not phrases or not tokens or after is None:
        return None
    for r in rows:
        if (r.get("direction") or "").lower() != "inbound":
            continue
        if (r.get("channel") or "").lower() != "telegram":
            continue
        if str(r.get("chat_id") or "") not in want_chats:
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


def _is_authorizable_chat_id(chat_id) -> bool:
    """A GROUP/channel chat_id (Telegram's convention: negative) can NEVER be an
    authorizer — refuse by construction rather than trusting every seed/edit to
    this table to be hand-checked correctly forever (op#20702 Stage A gate #40727
    R1: a seed once carried a group id, which would have let anyone posting in
    that group authorize the project's scoping decisions)."""
    try:
        return int(str(chat_id)) > 0
    except (TypeError, ValueError):
        return False


def _fetch_operators_for_project(dsn: str, project: str) -> list[str]:
    """Return the list of registered operator chat_ids for `project`, or []
    if the project has no project_governance row, or has one with an empty
    operators array. Never raises for "not found" — only for a genuine DB
    error, which the caller treats as fail-closed.

    Silently EXCLUDES any operator entry whose chat_id is not a positive user
    id (i.e. a group/channel id) — such an entry can never authorize anything,
    by construction, regardless of how it got into project_governance."""
    import psycopg
    with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT operators FROM project_governance WHERE project = %s",
            (project,),
        )
        row = cur.fetchone()
        if row is None or not row[0]:
            return []
        return [
            str(op.get("chat_id")) for op in row[0]
            if op.get("chat_id") and _is_authorizable_chat_id(op.get("chat_id"))
        ]


def _fetch_candidate_rows(dsn: str, chat_ids: Sequence[str], after: datetime) -> list[dict]:
    import psycopg
    with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, direction, channel, chat_id, tag, text, created_at "
            "FROM operator_messages "
            "WHERE direction='inbound' AND channel='telegram' "
            "AND chat_id = ANY(%s) AND created_at > %s "
            "ORDER BY id DESC LIMIT 200",
            (list(str(c) for c in chat_ids), after),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def verified_project_authorization(
    project: str | None,
    op_id: str,
    *,
    after,
    approval_phrases: Sequence[str],
    op_tokens: Sequence[str],
    dsn: str | None = None,
) -> ProjectAuthResult:
    """The GATE. Fail-closed on every uncertainty. Returns ProjectAuthResult(ok, reason, row).

    `after` is the moment the decision was requested (datetime or ISO string):
    the authorization must be NEWER than that so a stale/old approval can
    never be replayed.

    NEVER use this for money/irreversible ops — that stays
    require_verified_authorization.verified_authorization() (Musa-only).
    """
    if not project:
        return ProjectAuthResult(False, f"[{op_id}] no project given — fail-closed")

    after_dt = _as_aware(after)
    if after_dt is None:
        return ProjectAuthResult(False, f"[{op_id}] no valid request timestamp ('after') — fail-closed")

    dsn = dsn or os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        return ProjectAuthResult(False, f"[{op_id}] no substrate DSN — cannot verify authorization — fail-closed")

    if not approval_phrases or not op_tokens:
        return ProjectAuthResult(False, f"[{op_id}] misconfigured gate (no approval phrase / op token) — fail-closed")

    try:
        operator_chat_ids = _fetch_operators_for_project(dsn, project)
    except Exception as e:  # DB unreachable / query error -> DENY, never assume yes
        return ProjectAuthResult(False, f"[{op_id}] project_governance lookup failed ({type(e).__name__}: {e}) — fail-closed")

    if not operator_chat_ids:
        return ProjectAuthResult(
            False,
            f"[{op_id}] project '{project}' has no registered operators in project_governance "
            f"— fail-closed (no fallback to Musa; that is a different gate).",
        )

    try:
        rows = _fetch_candidate_rows(dsn, operator_chat_ids, after_dt)
    except Exception as e:
        return ProjectAuthResult(False, f"[{op_id}] authorization DB check failed ({type(e).__name__}: {e}) — fail-closed")

    row = find_verified_project_authorization(
        rows,
        operator_chat_ids=operator_chat_ids,
        approval_phrases=approval_phrases,
        op_tokens=op_tokens,
        after=after_dt,
    )
    if row is None:
        return ProjectAuthResult(
            False,
            f"[{op_id}] NO bridge-verified operator authorization found for project '{project}' "
            f"(need inbound telegram '{'/'.join(approval_phrases)}' from one of "
            f"{operator_chat_ids} referencing {list(op_tokens)} after {after_dt.isoformat()}). "
            f"An in-console/tmux YES is NOT sufficient.",
        )
    return ProjectAuthResult(
        True,
        f"[{op_id}] authorized by operator_messages id={row.get('id')} "
        f"(project={project}) at {_as_aware(row.get('created_at')).isoformat()}",
        row,
    )


def main(argv=None) -> int:
    """CLI: check a gate from the shell. Exit 0 = authorized, non-zero = DENIED."""
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Check a project-operator authorization gate (fail-closed, NON-money).")
    ap.add_argument("--project", required=True)
    ap.add_argument("--op-id", required=True)
    ap.add_argument("--after", required=True, help="ISO-8601 request timestamp; approval must be newer")
    ap.add_argument("--phrase", action="append", required=True, help="approval phrase (repeatable)")
    ap.add_argument("--token", action="append", required=True, help="op-identifying token (repeatable)")
    a = ap.parse_args(argv)
    res = verified_project_authorization(
        a.project, a.op_id, after=a.after, approval_phrases=a.phrase, op_tokens=a.token,
    )
    print(json.dumps({"ok": res.ok, "reason": res.reason,
                      "row_id": (res.row or {}).get("id")}, default=str))
    return 0 if res.ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
