"""CAI-985 A3 finding classifier (cc-fleet-health, 2026-08-17; scope ruled cai CAI-RESP-1024).

Pure tiering of an untrusted grant into FAIL (a real isolation breach) vs INFO (reported, not a
breach — the normal Supabase architecture). RESIDENCY-1 means ISOLATION and on Supabase RLS is the
control, not the grant, so a raw grant-whitelist over-fires (81% by-design). CAI-1024 FAIL floor:
  1. PUBLIC grant (broader than any role) — always FAIL.
  2. SECURITY DEFINER function with web-role EXECUTE — FAIL (SECDEF runs as owner, BYPASSES RLS;
     the purge_wc_ingest_pii class).
  3. anon/authenticated grant on a table with RLS DISABLED *or NO policy* — FAIL (grant without the
     control; an RLS-on table with zero policies is still open).
  * anon/authenticated on an RLS-on, policied table — INFO (the architecture); carries a
    'policy-correctness unchecked' caveat so a green never over-claims (a permissive USING(true) is
    a named FAST-FOLLOW, not covered here).
  * any other non-trusted grantee we cannot account for — FAIL (deny-by-default, CAI-1018).
"""
from scripts.lib.a3_grant_detector import classify_finding


def _f(grantee, objtype="relation:r", privilege_type="SELECT", is_secdef=False,
       rls_enabled=True, has_policy=True):
    return {"schema": "s", "object": "o", "objtype": objtype, "grantee": grantee,
            "privilege_type": privilege_type, "is_secdef": is_secdef,
            "rls_enabled": rls_enabled, "has_policy": has_policy}


def test_public_grant_always_fails():
    assert classify_finding(_f("PUBLIC", objtype="relation:r"))[0] == "FAIL"
    assert classify_finding(_f("PUBLIC", objtype="function", privilege_type="EXECUTE"))[0] == "FAIL"


def test_secdef_function_web_execute_fails():
    tier, reason = classify_finding(_f("anon", objtype="function", privilege_type="EXECUTE", is_secdef=True))
    assert tier == "FAIL" and "definer" in reason.lower()


def test_secinvoker_function_web_execute_is_info():
    # SECURITY INVOKER runs as the caller, so RLS applies to whatever it touches -> not a breach.
    tier, _ = classify_finding(_f("anon", objtype="function", privilege_type="EXECUTE", is_secdef=False))
    assert tier == "INFO"


def test_anon_grant_on_rls_disabled_table_fails():
    tier, reason = classify_finding(_f("anon", objtype="relation:r", rls_enabled=False, has_policy=False))
    assert tier == "FAIL" and "rls" in reason.lower()


def test_anon_grant_on_rls_enabled_no_policy_is_info_default_deny():
    # CORRECTED (Nazim #24275, proven live + independently reproduced): RLS-ENABLED + ZERO policies
    # is DEFAULT-DENY — the most CLOSED state, not open. My earlier 'still open' sharpening was
    # INVERTED. The grant is inert today (a latent trap if a policy is later added) -> INFO, not FAIL.
    tier, reason = classify_finding(_f("authenticated", objtype="relation:r", rls_enabled=True, has_policy=False))
    assert tier == "INFO", f"RLS-on-no-policy is default-deny (closed) -> INFO, got {tier}"
    assert "inert" in reason.lower() or "default-deny" in reason.lower(), f"reason must name the latent-trap framing: {reason}"


def test_anon_grant_on_rls_protected_table_is_info_with_caveat():
    tier, reason = classify_finding(_f("anon", objtype="relation:r", rls_enabled=True, has_policy=True))
    assert tier == "INFO"
    assert "policy-correctness" in reason.lower(), f"INFO must carry the unchecked caveat: {reason}"


def test_unexpected_nontrusted_role_fails_deny_by_default():
    # a grantee that is neither PUBLIC nor the known-conditional anon/authenticated is unaccounted
    # for -> FAIL (deny-by-default; the future-role trap CAI-1018 exists for).
    tier, _ = classify_finding(_f("some_rogue_role", objtype="relation:r"))
    assert tier == "FAIL"


def test_anon_on_view_is_info_underlying_rls_governs():
    # a VIEW/matview has no RLS of its own (relrowsecurity is always false); its access is governed
    # by the underlying tables' RLS (which criterion 3 checks on the base tables). Treat as INFO,
    # not a false FAIL from the view's inherent rls_enabled=false.
    tier, reason = classify_finding(_f("anon", objtype="relation:v", rls_enabled=False, has_policy=False))
    assert tier == "INFO", f"anon on a view must be INFO (underlying-table RLS governs), got {tier}: {reason}"
