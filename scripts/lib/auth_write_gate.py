"""auth_write_gate.py — fail-closed gate for GoTrue auth-admin WRITES against
real-client (non-QA) orgs. CAI-RESP-1118 class-fix.

WHY THIS EXISTS
---------------
2026-08-18: cc-irsyad, running a receipt-404 UI repro on the REAL irsyad silo
(goumlyne `goumlynecruxrlmzlntp`), meant to touch sales@gazzabyte.sg. GoTrue's
admin `/auth/v1/admin/users?email=` filter SILENTLY resolved a DIFFERENT user;
the lane acted WITHOUT asserting the resolved uid and overwrote a REAL staff
member's password (unrecoverable). Two `admin generate_link` calls it believed
only *returned* links actually DISPATCHED real recovery emails to two real
inboxes. The only control that existed was a remembered harness convention — and
a remembered convention is not a control ([[feedback_enforce_process_in_code_not_promises]]).

THE RULE (fail-closed, cai CAI-RESP-1118 item 5)
------------------------------------------------
An auth-admin-API WRITE (password set/reset, recovery/magic-link/invite email
dispatch, user create/delete/ban/modify, session-as-another-person, generate_link
— anything that mutates or emails a real credential) against a NON-QA org MUST NOT
proceed unless BOTH hold:

  (a) EXPLICIT GRANT — a bridge-verified operator authorization artifact
      (reuses require_verified_authorization: an inbound telegram YES from the
      operator, created AFTER the request, referencing this op). A console/tmux
      "YES" is NOT sufficient.

  (b) ASSERTED uid-EQUALITY — the caller states the expected uid, and THIS gate
      independently re-resolves the target by the same selector the write will
      use and proves it resolves to EXACTLY ONE row whose id == the asserted uid.
      0 rows (silent no-op / filter miss), >1 rows (ambiguous), or a mismatch =>
      DENY. This is what the incident lacked.

QA vs REAL is keyed on the Supabase PROJECT REF, never an org name. GoTrue's
`auth.users` is per-PROJECT: an "irsyad-qa" ORG living inside the real goumlyne
project still shares goumlyne's real `auth.users`, so it is NOT a safe auth-write
target. QA for AUTH means a SEPARATE, synthetic-only project. The allowlist is
CODE-PINNED (not env-overridable): letting a caller name its own QA target would
defeat the gate. Adding a project is a reviewed code change.

Missing config, unreachable DB, unresolvable target, unknown project ref: all
DENY. There is no "assume it's fine" path.

USAGE (the gate every auth-admin write MUST pass first)
-------------------------------------------------------
    from scripts.lib.auth_write_gate import authorize_auth_write
    res = authorize_auth_write(
        op_id="irsyad-pw-reset-salsabiila",
        operation="update_password",
        target_ref="goumlynecruxrlmzlntp",          # the silo the write hits
        expected_uid="6760aba1-...",                 # the caller's asserted uid
        selector={"by": "email", "value": "salsabiila@irsyad.edu.sg"},
        after=request_ts,
        approval_phrases=["YES AUTH-WRITE"],
        op_tokens=["salsabiila", "irsyad"],
    )
    if not res.ok:
        sys.exit(f"REFUSED: {res.reason}")           # fail-closed
    # ...only here may the auth-admin write proceed.

NOTE ON SCOPE (read before trusting this as "structurally impossible"):
This module is the SANCTIONED PATH and the semantic core. On its own it stops a
write only when the write goes THROUGH it. Every fleet lane still sources the
shared .env (launch_dangerous_cc.sh), so it ambiently holds real-silo
service-role keys + superuser DSNs and could bypass this by calling the admin API
or `UPDATE auth.users` directly. Making the bypass truly impossible requires the
CREDENTIAL-CUSTODY layer (revoke lane DB-role write on the auth schema across real
silos + move the GoTrue service keys out of ambient lane env into this gate's sole
custody) — a fleet-wide, cai+operator-scoped change. See
reports/authgate-1118-design-and-scope.md. Do NOT claim the freeze can lift on
this library alone.
"""
from __future__ import annotations

import os
from typing import Callable, Iterable, Sequence

# Canonical invocation is `python -m scripts.lib.auth_write_gate`. Support a bare
# `python scripts/lib/auth_write_gate.py` too (agents run ad-hoc): put the repo
# root on sys.path so the absolute import below resolves either way.
if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.lib.require_verified_authorization import (
    AuthResult,
    verified_authorization,
)

# ── CODE-PINNED QA allowlist (Supabase project refs, synthetic-only) ─────────
# Source of truth is docs/data-store-registry.md. Only projects whose ENTIRE
# auth.users population is synthetic belong here. Real-client silos
# (goumlynecruxrlmzlntp, ceayjeamtmcyzzvqflus, brrgastulcffamlbggyu) and the
# substrate (tscuymavysscrvoberrr) are deliberately absent. Additions are a
# reviewed code change — NEVER an env var (a caller-settable allowlist is not a
# gate). RATIFIED by cai (CAI-RESP-1124, 2026-08-18): this set is deliberate and
# complete as-is — there is currently NO irsyad auth-QA target, and one is NOT to
# be manufactured under incident pressure. Until a proper irsyad-QA project is
# scoped and built as separate follow-up work, irsyad authenticated repros route
# through the console (op#14352 Rule 3 amendment), not through an allowlist entry.
QA_TARGETS: frozenset[str] = frozenset(
    {
        # (CAI-RESP-1338, 2026-08-31) cosem-platform "ywrpttpxwfcoodovxhsr" REMOVED:
        # it is NO LONGER synthetic-only — that project now holds a real client org
        # (cosem 1478c9b2, real trainee PII). is_qa_target keys on project ref, not
        # org id, so allowlisting the whole project would green-light createUser on
        # real PII (the exact CAI-1118 case). A cosem UAT identity must be a SEPARATE
        # synthetic project with its OWN ref, added here under a cai SS6.6 sign-off
        # (CAI-RESP-1337). Empty set = deny-by-default; all auth-admin writes fall to
        # the real path (the freeze). Pure tightening, cai-preauthorized.
    }
)


def is_qa_target(project_ref: str | None, qa_allowlist: Iterable[str] = QA_TARGETS) -> bool:
    """True ONLY if `project_ref` is an explicitly-allowlisted synthetic project.

    Deny-by-default: empty, whitespace, or unrecognised refs are NOT QA — an
    auth-write we cannot even name a safe target for must fall to the real path.
    """
    ref = (project_ref or "").strip()
    if not ref:
        return False
    return ref in set(qa_allowlist)


def uid_equality_ok(
    resolved_rows: Sequence[dict], expected_uid: str | None
) -> tuple[bool, str]:
    """PURE predicate — the heart of the gate. No DB.

    Given the rows the target selector ACTUALLY resolves to (as read by this gate,
    not as the caller assumed) and the caller's asserted expected uid, return
    (ok, reason). ok iff exactly one row resolves and its id == expected_uid.

    This is precisely what the incident lacked: the `?email=` filter silently
    resolved a different (or no) user and the lane never checked.
    """
    want = (str(expected_uid).strip() if expected_uid is not None else "")
    if not want:
        return False, "no expected_uid asserted — fail-closed (cannot verify target identity)"
    n = len(resolved_rows)
    if n == 0:
        return False, (
            "selector resolved 0 users — silent no-op / filter miss "
            "(the incident shape) — fail-closed"
        )
    if n > 1:
        return False, f"selector resolved {n} users — ambiguous target — fail-closed"
    got = str(resolved_rows[0].get("id") or "").strip()
    if got != want:
        return False, (
            f"resolved uid {got!r} != asserted expected_uid {want!r} — "
            "the selector points at a DIFFERENT user than intended — fail-closed"
        )
    return True, f"uid-equality asserted (exactly one row, id=={want})"


def _default_resolve_users(target_dsn: str, selector: dict) -> list[dict]:
    """Re-resolve the target user against the silo's auth.users (read-only).

    selector = {"by": "email"|"id", "value": ...}. Email match is
    case-insensitive (GoTrue stores lowercased). Raises on any DB/selector error
    so the caller fail-closes.
    """
    import psycopg

    by = (selector or {}).get("by")
    value = (selector or {}).get("value")
    if by not in ("email", "id") or value in (None, ""):
        raise ValueError(f"unsupported/empty selector: {selector!r}")

    if by == "email":
        sql = "SELECT id::text AS id, email FROM auth.users WHERE lower(email) = lower(%s)"
    else:
        sql = "SELECT id::text AS id, email FROM auth.users WHERE id = %s"

    with psycopg.connect(target_dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(sql, (value,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def authorize_auth_write(
    op_id: str,
    *,
    operation: str,
    target_ref: str | None,
    expected_uid: str | None,
    selector: dict,
    after,
    approval_phrases: Sequence[str],
    op_tokens: Sequence[str],
    target_dsn: str | None = None,
    substrate_dsn: str | None = None,
    operator_chat_id: str | None = None,
    qa_allowlist: Iterable[str] = QA_TARGETS,
    resolve_users: Callable[[str, dict], list[dict]] | None = None,
    grant_fn: Callable[..., AuthResult] | None = None,
) -> AuthResult:
    """THE GATE. Fail-closed on every uncertainty. Returns AuthResult(ok, reason, row).

    QA/synthetic target  -> permitted (outside the freeze scope).
    Real-client target   -> permitted iff BOTH an explicit operator grant exists
                            AND this gate's own re-resolution proves uid-equality.
    """
    # 1. Classify the target. Deny-by-default: unknown/unnamed => real path.
    if is_qa_target(target_ref, qa_allowlist):
        return AuthResult(
            True,
            f"[{op_id}] QA/synthetic target {str(target_ref).strip()!r} — "
            f"auth-write permitted (outside CAI-1118 freeze scope)",
        )

    ref_label = (target_ref or "").strip() or "<unnamed>"

    # 2. REAL-CLIENT target. The caller MUST assert which uid it intends.
    if not (expected_uid and str(expected_uid).strip()):
        return AuthResult(
            False,
            f"[{op_id}] real-client target {ref_label!r}: no asserted expected_uid — "
            "fail-closed (an auth-write must name the exact uid it intends)",
        )

    # 3. EXPLICIT GRANT — a bridge-verified operator authorization (reused gate).
    grant = grant_fn or verified_authorization
    try:
        g = grant(
            op_id,
            after=after,
            approval_phrases=approval_phrases,
            op_tokens=op_tokens,
            operator_chat_id=operator_chat_id,
            dsn=substrate_dsn,
        )
    except Exception as e:  # grant check itself failed -> DENY
        return AuthResult(
            False,
            f"[{op_id}] real-client target {ref_label!r}: grant check errored "
            f"({type(e).__name__}: {e}) — fail-closed",
        )
    if not g.ok:
        return AuthResult(
            False,
            f"[{op_id}] real-client target {ref_label!r}: NO explicit grant — {g.reason}",
        )

    # 4. ASSERTED uid-EQUALITY — re-resolve the target ourselves; do not trust
    #    that the caller's selector points where the caller thinks.
    if not (target_dsn and str(target_dsn).strip()):
        return AuthResult(
            False,
            f"[{op_id}] real-client target {ref_label!r}: no target DSN — cannot "
            "re-resolve target to assert uid-equality — fail-closed",
        )
    resolver = resolve_users or _default_resolve_users
    try:
        rows = resolver(target_dsn, selector)
    except Exception as e:
        return AuthResult(
            False,
            f"[{op_id}] real-client target {ref_label!r}: target re-resolution "
            f"failed ({type(e).__name__}: {e}) — fail-closed",
        )

    ok, reason = uid_equality_ok(rows, expected_uid)
    if not ok:
        return AuthResult(
            False,
            f"[{op_id}] real-client target {ref_label!r} ({operation}): {reason}",
        )

    return AuthResult(
        True,
        f"[{op_id}] real-client target {ref_label!r} ({operation}) AUTHORIZED: "
        f"grant {g.reason}; {reason}",
        {"grant": g.row, "resolved": rows[0]},
    )


def main(argv=None) -> int:
    """CLI: check an auth-write gate from the shell. Exit 0 = authorized, else DENIED.

    The target user is re-resolved against --target-dsn; the grant is checked
    against the substrate DSN (DATABASE_URL). Fail-closed on everything.
    """
    import argparse
    import json

    ap = argparse.ArgumentParser(
        description="Fail-closed gate for auth-admin writes on real-client orgs (CAI-1118)."
    )
    ap.add_argument("--op-id", required=True)
    ap.add_argument("--operation", required=True, help="e.g. update_password, generate_link")
    ap.add_argument("--target-ref", required=True, help="Supabase project ref the write hits")
    ap.add_argument("--expected-uid", required=True, help="the uid the caller intends to write")
    ap.add_argument("--by", choices=["email", "id"], required=True)
    ap.add_argument("--value", required=True, help="the selector value (email or id)")
    ap.add_argument("--after", required=True, help="ISO-8601 request timestamp; grant must be newer")
    ap.add_argument("--phrase", action="append", required=True, help="approval phrase (repeatable)")
    ap.add_argument("--token", action="append", required=True, help="op-identifying token (repeatable)")
    ap.add_argument("--target-dsn", default=None, help="DSN for the target silo (default TARGET_DSN env)")
    ap.add_argument("--chat", default=None, help="operator chat id (default MUSA_TELEGRAM_ID)")
    a = ap.parse_args(argv)

    res = authorize_auth_write(
        a.op_id,
        operation=a.operation,
        target_ref=a.target_ref,
        expected_uid=a.expected_uid,
        selector={"by": a.by, "value": a.value},
        after=a.after,
        approval_phrases=a.phrase,
        op_tokens=a.token,
        target_dsn=a.target_dsn or os.environ.get("TARGET_DSN"),
        substrate_dsn=os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL"),
        operator_chat_id=a.chat,
    )
    print(json.dumps({"ok": res.ok, "reason": res.reason}, default=str))
    return 0 if res.ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
