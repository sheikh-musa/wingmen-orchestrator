#!/usr/bin/env python3
"""a3_isolation_check — the CAI-985 A3 automation runner (D1-D7).

Reads the ceayj tenant's catalogue for untrusted grants outside schema shipforge (the isolation
invariant RESIDENCY-1), decides PASS/FAIL/ERROR, and writes the substrate sink so cc-quality can
re-audit from the substrate side. AUTHORED by cc-fleet-health; EXECUTES under orch-console — the
ceayj credential-holder (CAI-981 'move the work to the credential' / FORK1=b). It reads ceayj via
IHSANOS_PROD_DATABASE_URL and writes the substrate via DATABASE_URL — both live in the orch plane.

The D1-D7 bar (CAI-RESP-1000): D1 a named scheduled runner (this + its plist); D2 an execution
RECORD (the invariant_assertion_runs row); D3 emits on PASS as well as FAIL (a run row every time);
D4 a negative control proving the detector CAN fire (the SAME detector fn on a scope known to carry
untrusted grants — pg_catalog — must return >0, else ERROR); D5 built on the catalogue via
aclexplode, never information_schema (see a3_grant_detector); D6 fail-closed (any error -> ERROR ->
gate_status='pending', never a green); D7 evidence substrate-readable + joinable (the sink).

⚠ WHITELIST IS ceayj-SPECIFIC — CONFIRM BEFORE FIRING. The trusted set below is cai's CAI-1019
outside-shipforge ruling {postgres, service_role, console_readonly} — shipforge_app EXCLUDED so it
is flagged reaching outside its own schema (folds A1 into A3). But ceayj's real infra roles must be
confirmed with orch-console (who can see ceayj): a legitimate ceayj infra role not listed here would
be flagged as a false-positive, and a role that should NOT span schemas must NOT be added. Pass
--trusted to override. Deny-by-default means the DEFAULT errs toward flagging (safe: a false-positive
is a review, a false-negative is a missed leak).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Run directly as `python3 scripts/a3_isolation_check.py` (the launchd form) puts scripts/ on
# sys.path, not the repo root, so `from scripts.lib...` fails. Put the repo root first (the same
# bootstrap backlog_swipe.py / context_health_watchdog.py use) — else the job crashes every run
# (the exact last-ten-feet failure).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv

from scripts.lib.a3_grant_detector import (
    find_untrusted_grants, find_isolation_findings, classify_all)
from scripts.lib.a3_invariant_sink import record_a3_run, PASS, FAIL, ERROR, SRE_AGENT_ID

RESIDENCY_INVARIANT_REF = "RESIDENCY-1"
SCOPE_DESC = "(PUBLIC / SECURITY-DEFINER web-EXECUTE / web-role on RLS-off tables) outside schema shipforge [CAI-1024]"

# Trusted-role whitelist for ceayj.
#   * cai CAI-1019 core: postgres, service_role, console_readonly (shipforge_app EXCLUDED -> A1 folded).
#   * Supabase PLATFORM-INFRA roles (confirmed against the ceayj dry-run, orch-console #24250):
#     these legitimately span schemas and are not web-facing; NOT baking them made 297/2173 noise.
#   * anon / authenticated are DELIBERATELY ABSENT — and that is now CORRECT by cai CAI-RESP-1024:
#     they are NOT whitelisted (so every anon/auth grant is EVALUATED), but the CLASSIFIER
#     (classify_finding) tiers them — FAIL only when RLS does not cover them (RLS off / no policy),
#     INFO on RLS-protected tables (the architecture). Whitelisting them would GUT the check; the
#     classifier is what keeps A3 meaningful AND passable on a correct DB. Do NOT add them here.
DEFAULT_TRUSTED = [
    "postgres", "service_role", "console_readonly",
    "dashboard_user", "pgbouncer", "authenticator",
    "supabase_auth_admin", "supabase_realtime_admin", "supabase_storage_admin",
    "pg_monitor", "pg_read_all_stats",
]
# The A3 scope (CAI-1025): app data OUTSIDE shipforge, minus the postgres system schemas AND the
# Supabase-MANAGED schemas we cannot remediate (storage/extensions/realtime/auth/graphql — 139 FAILs
# that would reproduce the original unpassable problem one layer in). graphql_public STAYS in scope
# (it is the exposed API schema; CAI-1026 treats its web-role USAGE as INFO). A3 fails on `public`
# and any schema we create.
DEFAULT_EXCLUDE = ["shipforge", "pg_catalog", "information_schema", "pg_toast",
                   "storage", "extensions", "realtime", "auth", "graphql"]
# The D4 negative-control scope: the SAME as the main scan but WITHOUT excluding pg_catalog, which
# legitimately carries PUBLIC grants — so a working detector MUST return >0 here.
NEGCONTROL_EXCLUDE = ["shipforge", "information_schema", "pg_toast"]

_EVIDENCE_CAP = 200  # cap the stored finding rows so evidence can't bloat the sink


def decide_outcome(findings_count: int, negcontrol_count: int):
    """Pure PASS/FAIL/ERROR decision. The D4 negative control DOMINATES (D6 fail-closed): if the
    control scope — known to carry untrusted grants — comes back empty, the detector's positive
    path is broken and NO result is trustworthy, so even a clean main scan is ERROR, never PASS."""
    if negcontrol_count <= 0:
        return ERROR, ("D4 negative control returned 0 — the detector's positive path is broken; "
                       "a 'clean' result cannot be trusted (fail-closed)")
    if findings_count > 0:
        return FAIL, f"{findings_count} FAIL-tier isolation breach(es) {SCOPE_DESC}"
    return PASS, f"no FAIL-tier isolation breach {SCOPE_DESC} (negative control fired: {negcontrol_count})"


def run_a3_check(*, ceayj_dsn: str, substrate_dsn: str,
                 trusted_roles=None, exclude_schemas=None, negcontrol_exclude=None,
                 invariant_ref: str = RESIDENCY_INVARIANT_REF, run_by: str = SRE_AGENT_ID,
                 dry_run: bool = False) -> dict:
    """Run the A3 check against ceayj and (unless dry_run) write the substrate sink. D6 fail-closed:
    ANY error connecting/reading ceayj becomes outcome=ERROR (never a silent green). Returns a dict
    summary. The substrate write is a separate connection/txn (the sink owns the encoding)."""
    import psycopg
    trusted = list(trusted_roles or DEFAULT_TRUSTED)
    excl = list(exclude_schemas or DEFAULT_EXCLUDE)
    negexcl = list(negcontrol_exclude or NEGCONTROL_EXCLUDE)

    buckets = {"fail": [], "info": []}
    negcontrol_count = None
    try:
        with psycopg.connect(ceayj_dsn) as cc, cc.cursor() as ccur:
            findings = find_isolation_findings(ccur, trusted_roles=trusted, exclude_schemas=excl)
            buckets = classify_all(findings)
            # D4: the negative control just needs 'does the detector find grants' in a dirty scope.
            negcontrol_count = len(find_untrusted_grants(ccur, trusted_roles=trusted, exclude_schemas=negexcl))
        # CAI-1024: RESIDENCY-1 FAILs iff the FAIL tier is non-empty; INFO is reported, not-failing.
        outcome, detail = decide_outcome(len(buckets["fail"]), negcontrol_count)
        finding_count = len(buckets["fail"]) if outcome == FAIL else 0
        evidence = {"detail": detail, "negcontrol_count": negcontrol_count,
                    "trusted": trusted, "excluded": excl,
                    "fail_count": len(buckets["fail"]), "info_count": len(buckets["info"]),
                    "fail": buckets["fail"][:_EVIDENCE_CAP],
                    "fail_truncated": len(buckets["fail"]) > _EVIDENCE_CAP,
                    # never-green-on-absence-of-signal, applied to A3 itself (CAI-1024): a PASS here
                    # means no FAIL-tier breach — it does NOT certify RLS POLICY correctness (a
                    # permissive USING(true) is a named fast-follow, unchecked).
                    "info_caveat": "INFO tier + policy-correctness (permissive USING(true) on money/PII) is a named FAST-FOLLOW, not checked",
                    "info_sample": buckets["info"][:20]}
    except Exception as e:  # D6 fail-closed: could-not-measure is ERROR, never a green
        outcome, detail, finding_count = ERROR, f"could not measure ceayj: {e!r}", 0
        evidence = {"detail": detail, "error": repr(e), "negcontrol_count": negcontrol_count}

    summary = {"outcome": outcome, "detail": evidence["detail"], "finding_count": finding_count,
               "fail_count": len(buckets["fail"]), "info_count": len(buckets["info"]),
               "negcontrol_count": negcontrol_count}
    if dry_run:
        summary["dry_run"] = True
        return summary

    # Write the substrate sink (separate txn; the sink asserts its own ops boundary + encoding).
    with psycopg.connect(substrate_dsn) as sc, sc.cursor() as scur:
        gate = record_a3_run(scur, outcome=outcome, scope_checked=SCOPE_DESC,
                             finding_count=finding_count, evidence=evidence,
                             invariant_ref=invariant_ref, run_by=run_by)
        sc.commit()
    summary["gate_status"] = gate
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CAI-985 A3 isolation check (runs under orch-console)")
    ap.add_argument("--ceayj-dsn-env", default="IHSANOS_PROD_RO_DATABASE_URL",  # CAI-1225 RO-move: read-only auditor_ro (was write-DSN); a3 reads ceayj catalogue, writes only substrate
                    help="env var holding the ceayj DSN (default IHSANOS_PROD_RO_DATABASE_URL)")
    ap.add_argument("--substrate-dsn-env", default="DATABASE_URL",
                    help="env var holding the substrate DSN (default DATABASE_URL)")
    ap.add_argument("--trusted", nargs="*", default=None,
                    help="override the trusted-role whitelist (default: cai CAI-1019 set)")
    ap.add_argument("--dry-run", action="store_true", help="scan + decide but do NOT write the sink")
    args = ap.parse_args(argv)

    # launchd inherits a MINIMAL environment — nothing from the interactive shell that sourced .env.
    # So load the DSNs from the WorkingDirectory .env HERE (Nazim #24321: 'works by hand, fails under
    # the scheduler' — same class as the worktree/.venv fire-window fail-open). load_dotenv does NOT
    # override already-set vars, so a hand run is unaffected. The secret STAYS in the 0600 .env — do
    # NOT move the ceayj money-tenant DSN into the 0644 plist to satisfy the scheduler.
    load_dotenv(os.path.join(_REPO_ROOT, ".env"))

    ceayj = os.environ.get(args.ceayj_dsn_env)
    substrate = os.environ.get(args.substrate_dsn_env)
    if not ceayj or not substrate:
        # missing credential is itself fail-closed-LOUD: do not silently no-op
        print(f"a3_isolation_check: MISSING DSN ({args.ceayj_dsn_env} or {args.substrate_dsn_env}) "
              f"— cannot run; refusing to no-op silently", file=sys.stderr)
        return 2
    summary = run_a3_check(ceayj_dsn=ceayj, substrate_dsn=substrate,
                           trusted_roles=args.trusted, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2, default=str))
    # D3: a run always emits. Exit non-zero on FAIL/ERROR so a scheduler/log makes the not-green loud.
    return 0 if summary["outcome"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
