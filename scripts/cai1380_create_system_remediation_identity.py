#!/usr/bin/env python3
"""cai1380_create_system_remediation_identity.py — GUARDED, ONE-OFF runner for
creating the CAI-1380 system-remediation identity's auth.users row on ceayj
(`ceayjeamtmcyzzvqflus`, ihsanos-platform's real multi-tenant Supabase project).

WHY THIS SHAPE (mirrors irsyad_residency_purge.py deliberately — same class of
problem: a rare, irreversible, real-silo write that must never run on a
console/tmux "looks fine" alone)
----------------------------------------------------------------------------
CAI-RESP-1379 found a genuine platform gap: no legitimate agent-executable
actor identity exists for governance-authorized corrections to real client
data. CAI-RESP-1380 approved a durable fix — one permanent, real-auth.uid()
identity, normally holding zero standing access, granted org-scoped time-boxed
access only under a cai ruling (supabase/migrations/327_system_remediation_
grants.sql, PR#671). CAI-RESP-1389 authorized DESIGN+BUILD of exactly one
`auth.users` CREATE for that identity — explicitly NOT execution. This script
is the design/build deliverable; it must not be able to run until a SEPARATE
follow-up ruling names this exact script and its own 24h challenge window has
closed (CAI-RESP-1389 condition 7).

CAI-RESP-1389(ii) REJECTED building a permanent "create" mode into
scripts/lib/auth_write_gate.py — that module's entire trust basis is its
narrow "0 rows resolved = fail closed" invariant, built for verifying writes
against EXISTING accounts. A create, by definition, has zero existing rows to
resolve; weakening that invariant to also accept "email not yet in use" would
expand the permanent attack surface of the one library everything else's trust
rests on, for a need that will not recur (anti-takalluf). This script is
therefore intentionally NOT layered on authorize_auth_write() — it is its own
narrow, HARDCODED, never-reusable gate, deliberately duplicating a little
structure rather than widening a shared security-critical module.

Execution pre-conditions (ALL required, fail-closed on any uncertainty):
  1. EXECUTION RULING: a cai strategic_decisions row matching
     EXECUTION_DECISION_REF exists, references THIS script by op_id, is not
     superseded/challenged, and its OWN 24h challenge window has closed
     (challengeable_until < now()) — confirmed by an independent DB read at
     run time, never a cached or relayed claim. EXECUTION_DECISION_REF is None
     below by design: the follow-up ruling this script needs does not exist
     yet, so the gate can never pass as committed. Filling it in is itself the
     act that must be paired with that fresh ruling.
  2. UNFREEZE SENTINEL: `.cai1380_create_UNFROZEN` exists at the orchestrator
     repo root — created deliberately by orch-console/cai at the moment the
     follow-up ruling lands, NOT by this script or any agent as a
     side effect. Mirrors irsyad_residency_purge.py's sentinel exactly.
  3. INDEPENDENT PRE-CHECK: --independent-precheck-ref names a bus message id
     from a DIFFERENT agent than whoever runs this script, confirming they
     independently verified TARGET_EMAIL is NOT already in ceayj's
     auth.users (CAI-RESP-1389 condition 4 — "a party other than the
     runner"). This script ALSO re-checks the same fact itself (belt), but
     the self-check alone does not satisfy condition 4.
  4. FRESH CONFIG RE-VERIFY: at run time (not from any earlier snapshot),
     re-reads ceayj's live GoTrue config via the Management API and refuses
     to proceed if mailer_autoconfirm is not true, if a captcha/hook is now
     enabled, or if any other setting checked at design time has drifted
     (CAI-RESP-1389 condition 3).
  5. Explicit `--execute` flag. Default behaviour is DRY-RUN: reports which
     of the above hold and creates nothing.

The actual GoTrue admin createUser call is intentionally NOT implemented in
`_execute_create()` below (it raises NotImplementedError) until the follow-up
execution ruling lands and EXECUTION_DECISION_REF is filled in — matching
irsyad_residency_purge.py's "leave the irreversible body as a hard stop until
a deliberate, separately-authorized unfreeze" pattern exactly. Do NOT
implement the create call as part of "just wiring it up" — that IS the
unfreeze act and must be paired with the fresh ruling CAI-RESP-1389 requires.

POST-CHECK (CAI-RESP-1389 condition 5): this script does not self-certify
success. On a real (future, unfrozen) run it must print the created user's id
+ email + created_at for an INDEPENDENT party to verify: exactly one new row,
email matches exactly, no other auth.users row touched, no session/recovery/
welcome email fired to any real inbox. That verification is not performed by
this script.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

ORCH = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ORCH))

OP_ID = "cai1380-create-system-remediation-identity"

# ── HARDCODED TARGET (CAI-RESP-1389(ii): never a reusable "create any email"
#    tool — a new target requires a new script, not a parameter) ────────────
TARGET_EMAIL = "system-remediation-cai1380@wingmen.test"  # cai CAI-RESP-1389, RFC 2606 reserved TLD
TARGET_PROJECT_REF = "ceayjeamtmcyzzvqflus"  # ihsanos-platform's real multi-tenant project
DESIGN_DECISION_REF = "CAI-RESP-1389"  # this design/build authorization — already ruled
EXECUTION_DECISION_REF: str | None = None  # the STILL-MISSING follow-up ruling naming this script — DELIBERATELY unset

UNFREEZE_SENTINEL = ORCH / ".cai1380_create_UNFROZEN"


@dataclass
class GateResult:
    ok: bool
    reason: str


# ── Pure predicate — no DB, no HTTP. Fully unit-testable. ───────────────────
def execution_ruling_ok(
    decision_row: dict | None,
    *,
    op_id: str,
    now: datetime,
) -> GateResult:
    """Given the strategic_decisions row (or None) for EXECUTION_DECISION_REF,
    decide whether it authorizes THIS script to run right now.

    Fail-closed on every uncertainty — mirrors uid_equality_ok's discipline in
    auth_write_gate.py (0/ambiguous/mismatched all deny).
    """
    if decision_row is None:
        return GateResult(False, "no execution ruling found for EXECUTION_DECISION_REF — fail-closed")

    status = str(decision_row.get("status") or "").strip().lower()
    if status in ("superseded", "rejected", "revoked"):
        return GateResult(False, f"execution ruling status={status!r} — no longer authorizing — fail-closed")

    challenge_status = str(decision_row.get("challenge_status") or "").strip().lower()
    if challenge_status == "challenged":
        return GateResult(False, "execution ruling is under an open challenge — fail-closed")

    challengeable_until = decision_row.get("challengeable_until")
    if challengeable_until is None:
        return GateResult(False, "execution ruling has no challengeable_until — cannot confirm the 24h window closed — fail-closed")
    if isinstance(challengeable_until, str):
        challengeable_until = datetime.fromisoformat(challengeable_until.replace("Z", "+00:00"))
    if challengeable_until.tzinfo is None:
        challengeable_until = challengeable_until.replace(tzinfo=timezone.utc)
    if now < challengeable_until:
        return GateResult(False, f"execution ruling's 24h challenge window has not closed yet (closes {challengeable_until.isoformat()}) — fail-closed")

    decision_text = " ".join(
        str(decision_row.get(k) or "") for k in ("title", "decision")
    ).lower()
    if op_id not in decision_text:
        return GateResult(
            False,
            f"execution ruling does not reference op_id={op_id!r} by name — "
            "cannot confirm it authorizes THIS script rather than some other "
            "action — fail-closed",
        )

    return GateResult(True, f"execution ruling verified: status={status or 'active'}, challenge window closed at {challengeable_until.isoformat()}, references op_id")


def _fetch_execution_decision(decision_ref: str, substrate_dsn: str) -> dict | None:
    """Read-only fetch of the strategic_decisions row for decision_ref."""
    import psycopg

    with psycopg.connect(substrate_dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT decision_ref, title, decision, status, challenge_status, "
            "challengeable_until FROM strategic_decisions WHERE decision_ref = %s",
            (decision_ref,),
        )
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None


def _target_email_already_exists(project_ref: str, email: str, target_dsn: str) -> bool:
    """Independent-of-caller-belief re-check: does TARGET_EMAIL already exist
    in this project's auth.users? Read-only. Raises on any DB error so the
    caller fails closed rather than assuming "not found"."""
    import psycopg

    with psycopg.connect(target_dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute("SELECT id::text AS id FROM auth.users WHERE lower(email) = lower(%s)", (email,))
        return cur.fetchone() is not None


def _reverify_gotrue_config(project_ref: str, access_token: str) -> GateResult:
    """Fresh, at-run-time re-check of the create-time-relevant GoTrue settings
    (CAI-RESP-1389 condition 3) — never trust a design-time snapshot."""
    import requests

    resp = requests.get(
        f"https://api.supabase.com/v1/projects/{project_ref}/config/auth",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    cfg = resp.json()

    if not cfg.get("mailer_autoconfirm"):
        return GateResult(False, "mailer_autoconfirm is no longer true — config has drifted since design time, a confirmation email may now fire — fail-closed")
    if cfg.get("security_captcha_enabled"):
        return GateResult(False, "security_captcha_enabled is now true — config has drifted — fail-closed")
    if cfg.get("hook_password_verification_attempt_enabled"):
        return GateResult(False, "hook_password_verification_attempt_enabled is now true — a custom hook could intercept — fail-closed")

    return GateResult(True, "GoTrue config re-verified clean at run time: mailer_autoconfirm=true, no captcha, no password-verification hook")


def _execute_create() -> int:
    """The irreversible auth.users CREATE. Deliberately NOT implemented while
    frozen. Only ever reached after: execution ruling verified + UNFREEZE
    sentinel present + independent pre-check ref supplied + fresh config
    re-verify passed + --execute. Filling this in is the act of unfreezing
    and MUST be paired with the follow-up ruling this script's gate checks
    for — never implement this as "just wiring it up.\""""
    raise NotImplementedError(
        "create body intentionally absent while frozen — CAI-RESP-1389 "
        "authorized design/build only; fill this in ONLY at the moment "
        "EXECUTION_DECISION_REF names a real, challenge-window-closed ruling "
        "for THIS script, per condition 7"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Guarded, one-off creation of the CAI-1380 system-remediation identity (fail-closed)."
    )
    ap.add_argument(
        "--independent-precheck-ref",
        required=False,
        help="bus message id from a DIFFERENT agent confirming they independently verified "
        "TARGET_EMAIL does not already exist in ceayj auth.users (CAI-RESP-1389 condition 4)",
    )
    ap.add_argument("--execute", action="store_true", help="attempt the irreversible create (still requires every gate above)")
    a = ap.parse_args(argv)

    print(f"op_id={OP_ID}")
    print(f"target_email={TARGET_EMAIL}")
    print(f"target_project={TARGET_PROJECT_REF}")
    print(f"design_decision_ref={DESIGN_DECISION_REF} (already ruled, CAI-RESP-1389)")
    print(f"execution_decision_ref={EXECUTION_DECISION_REF!r} (must be a real, un-superseded, challenge-window-closed ruling naming this script)")

    ok = True

    # 1. Execution ruling.
    if EXECUTION_DECISION_REF is None:
        print("GATE 1 (execution ruling): REFUSED — EXECUTION_DECISION_REF is unset (deliberate default)")
        ok = False
    else:
        substrate_dsn = os.environ.get("DATABASE_URL")
        if not substrate_dsn:
            print("GATE 1 (execution ruling): REFUSED — no DATABASE_URL (substrate bus DB) to read strategic_decisions")
            ok = False
        else:
            row = _fetch_execution_decision(EXECUTION_DECISION_REF, substrate_dsn)
            res = execution_ruling_ok(row, op_id=OP_ID, now=datetime.now(timezone.utc))
            print(f"GATE 1 (execution ruling): {'PASS' if res.ok else 'REFUSED'} — {res.reason}")
            ok = ok and res.ok

    # 2. Unfreeze sentinel.
    if not UNFREEZE_SENTINEL.exists():
        print(f"GATE 2 (unfreeze sentinel): REFUSED — {UNFREEZE_SENTINEL} does not exist")
        ok = False
    else:
        print(f"GATE 2 (unfreeze sentinel): PASS — {UNFREEZE_SENTINEL} present")

    # 3. Independent pre-check reference supplied.
    if not a.independent_precheck_ref:
        print("GATE 3 (independent pre-check): REFUSED — no --independent-precheck-ref supplied")
        ok = False
    else:
        print(f"GATE 3 (independent pre-check): reference supplied ({a.independent_precheck_ref}) — belt self-check follows")
        target_ro_dsn = os.environ.get("IHSANOS_PROD_RO_DATABASE_URL")
        if not target_ro_dsn:
            print("  self-check: REFUSED — no IHSANOS_PROD_RO_DATABASE_URL to verify against")
            ok = False
        else:
            try:
                exists = _target_email_already_exists(TARGET_PROJECT_REF, TARGET_EMAIL, target_ro_dsn)
            except Exception as e:  # noqa: BLE001 — fail closed on any DB error
                print(f"  self-check: REFUSED — DB error resolving target email ({type(e).__name__}: {e})")
                ok = False
            else:
                if exists:
                    print("  self-check: REFUSED — TARGET_EMAIL already exists in auth.users — do NOT create")
                    ok = False
                else:
                    print("  self-check: confirms TARGET_EMAIL not present (does not replace the independent party's own check)")

    # 4. Fresh GoTrue config re-verify.
    access_token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if not access_token:
        print("GATE 4 (fresh config re-verify): REFUSED — no SUPABASE_ACCESS_TOKEN")
        ok = False
    else:
        try:
            res = _reverify_gotrue_config(TARGET_PROJECT_REF, access_token)
        except Exception as e:  # noqa: BLE001
            print(f"GATE 4 (fresh config re-verify): REFUSED — {type(e).__name__}: {e}")
            ok = False
        else:
            print(f"GATE 4 (fresh config re-verify): {'PASS' if res.ok else 'REFUSED'} — {res.reason}")
            ok = ok and res.ok

    if not ok:
        print("\nREFUSED — one or more gates failed. Nothing created.", file=sys.stderr)
        return 3

    print("\nALL GATES PASS.")
    if not a.execute:
        print("dry run (no --execute): gates satisfied but nothing created.")
        return 0

    return _execute_create()


if __name__ == "__main__":
    raise SystemExit(main())
