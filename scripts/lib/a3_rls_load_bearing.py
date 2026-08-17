#!/usr/bin/env python3
"""CAI-1031 (cai #24474 + #24485 refinement): the mechanism that lets A3 reclassify a
PUBLIC/anon-EXECUTE SECURITY DEFINER function out of FAIL and into INFO
('RLS-load-bearing') — WITHOUT hiding a real leak.

WHY (the outage that motivated it): those grants are load-bearing for RLS policies
(a policy expression evaluates as the querying role; revoking the grant ERRORS the
query, not narrows it). A3 correctly DETECTS the grant; the FAIL *tier* is wrong when
the function is caller-scoped and the grant is what RLS depends on. So this is a TIER
rule, not a detector change (the CAI-1024/1025 shape). Two auditors — cc-fleet-health
proposed the revoke, orch-console ratified it — demonstrated the failure mode before
catching it; disclosed, in the covering proposal.

THE RULE — reclassify FAIL->INFO iff BOTH:
  (1) the fn is referenced in an RLS policy expression  (cond-1; resolved via
      pg_depend policy->fn OID by the caller, NOT a proname LIKE — substring
      collisions; passed in here as `policy_ref_oids`), AND
  (2) it is CALLER-SCOPED (derives from auth.uid(), empty for anon), read at source
      and ATTESTED (cond-2).

COND-2 IS HASH-PINNED, and the hash is COMPOSITE (cai #24485): a redefine ANYWHERE in
the function's transitive call chain must re-surface it to FAIL. Refinements that are
easy to get wrong and are therefore load-bearing here:
  * The closure is the ATTESTER's RECORDED set (fn + every callee they followed at
    source). It is NEVER resolved via pg_depend fn->fn: cai verified 0 fn->fn edges on
    ceayj — Postgres does not record body-call deps for classic `AS $$..$$` functions,
    so pg_depend would collapse the closure to {fn} = the exact per-fn blind spot.
  * SCOPE = ALL transitive callees, not just SECDEF: a SECURITY INVOKER callee invoked
    inside a SECDEF context runs with the outer definer's privileges, so it is equally
    a leak vector.
  * ELIGIBILITY: only functions whose WHOLE chain is statically inspectable (every
    member is LANGUAGE sql). A plpgsql (or any non-sql) link is an opaque body -> NOT
    eligible -> the fn stays FAIL (fail-closed) until handled.

The attestation is `{fn_signature: {"closure": [sig, ...], "composite_sha256": "..."}}`.
`closure` includes the fn itself and every callee followed. The hash is recomputed at
run time over the CURRENT definitions and compared to the pin: any drift invalidates.
"""
from __future__ import annotations

import hashlib
from typing import Dict, Iterable, List, Optional, Set, Tuple


def _oid_of(cur, sig: str) -> Optional[int]:
    """Resolve a function signature (e.g. 'public.auth_user_org_ids()') to its current
    OID via to_regprocedure, or None if it does not resolve (dropped / renamed)."""
    cur.execute("SELECT to_regprocedure(%s)::oid", (sig,))
    row = cur.fetchone()
    oid = row[0] if row else None
    return int(oid) if oid else None


def _defs_for(cur, sigs: Iterable[str]) -> Optional[List[str]]:
    """pg_get_functiondef for every signature, or None if ANY is unresolvable
    (fail-closed: a closure we cannot fully read cannot be attested)."""
    defs: List[str] = []
    for sig in sigs:
        oid = _oid_of(cur, sig)
        if oid is None:
            return None
        cur.execute("SELECT pg_get_functiondef(%s)", (oid,))
        row = cur.fetchone()
        if not row or row[0] is None:
            return None
        defs.append(row[0])
    return defs


def composite_hash(cur, closure_sigs: Iterable[str]) -> Optional[str]:
    """sha256 over the CURRENT pg_get_functiondef of every member of the closure,
    order-independent (defs are sorted before hashing). None if any member does not
    resolve (fail-closed). A redefine of ANY member changes the digest."""
    defs = _defs_for(cur, closure_sigs)
    if defs is None:
        return None
    h = hashlib.sha256()
    for d in sorted(defs):
        h.update(d.encode("utf-8"))
        h.update(b"\x00")  # length-independent separator so concatenations can't alias
    return h.hexdigest()


def closure_is_eligible(cur, closure_sigs: Iterable[str]) -> bool:
    """True iff every member resolves AND is LANGUAGE sql (statically inspectable).
    Any non-sql (e.g. plpgsql) or unresolvable member -> False (fail-closed)."""
    for sig in closure_sigs:
        oid = _oid_of(cur, sig)
        if oid is None:
            return False
        cur.execute(
            "SELECT l.lanname FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang WHERE p.oid = %s",
            (oid,),
        )
        row = cur.fetchone()
        if not row or row[0] != "sql":
            return False
    return True


def policy_referenced_fn_oids(cur) -> Set[int]:
    """cond-1: the set of function OIDs referenced by ANY RLS policy expression, via
    pg_depend (policy -> fn). cai #24485: Postgres DOES record policy->fn deps (a
    policy depends on the functions in its qual/with_check), which is why pg_depend is
    authoritative here — unlike fn->fn deps, which it does NOT record. This is the
    binding replacement for the proname LIKE match (substring collisions)."""
    cur.execute(
        """
        SELECT DISTINCT d.refobjid
          FROM pg_depend d
         WHERE d.classid = 'pg_policy'::regclass
           AND d.refclassid = 'pg_proc'::regclass
        """
    )
    return {int(r[0]) for r in cur.fetchall()}


def is_rls_load_bearing(
    cur,
    fn_sig: str,
    attestation: Dict[str, dict],
    *,
    policy_ref_oids: Set[int],
) -> Tuple[bool, str]:
    """The CAI-1031 gate: (True, reason) iff the fn qualifies for FAIL->INFO, else
    (False, reason). Both conditions required; every failure path is fail-closed."""
    oid = _oid_of(cur, fn_sig)
    if oid is None:
        return False, "unresolvable signature"
    # cond-1 — RLS policy-referenced (resolved by the caller via pg_depend policy->fn)
    if oid not in policy_ref_oids:
        return False, "not referenced by any RLS policy (cond-1 fails)"
    # cond-2 — attested caller-scoped, statically inspectable, composite hash intact
    att = attestation.get(fn_sig)
    if not att:
        return False, "no caller-scope attestation (cond-2 fails)"
    closure = att.get("closure") or [fn_sig]
    if not closure_is_eligible(cur, closure):
        return False, "call chain not statically inspectable (non-sql/opaque link) — fail-closed"
    if composite_hash(cur, closure) != att.get("composite_sha256"):
        return False, "attestation composite-hash drift (a fn or callee was redefined) — re-surfaced to FAIL"
    return True, "RLS-load-bearing: policy-referenced + caller-scoped (attested, composite-hash pinned) [CAI-1031]"
