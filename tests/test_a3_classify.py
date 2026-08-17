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
       rls_enabled=True, has_policy=True, is_security_invoker=False, schema="s", object="o"):
    return {"schema": schema, "object": object, "objtype": objtype, "grantee": grantee,
            "privilege_type": privilege_type, "is_secdef": is_secdef,
            "rls_enabled": rls_enabled, "has_policy": has_policy,
            "is_security_invoker": is_security_invoker}


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


def test_anon_on_definer_view_FAILS():
    # GAP A (cc-storefront #24276, proven live): a DEFINER view (not security_invoker) reads base
    # tables as OWNER, bypassing the caller's RLS -> FAIL (the boot_briefing class). My earlier
    # 'all views are INFO' was a material false-green.
    tier, reason = classify_finding(_f("anon", objtype="relation:v", is_security_invoker=False))
    assert tier == "FAIL" and "definer" in reason.lower(), f"definer view must FAIL, got {tier}: {reason}"


def test_anon_on_security_invoker_view_is_info():
    tier, _ = classify_finding(_f("anon", objtype="relation:v", is_security_invoker=True))
    assert tier == "INFO"


def test_anon_on_materialized_view_FAILS():
    tier, _ = classify_finding(_f("anon", objtype="relation:m"))
    assert tier == "FAIL"  # matview is always owner-computed, no per-caller RLS


def test_safe_secdef_function_is_info_when_listed(monkeypatch):
    import scripts.lib.a3_grant_detector as det
    monkeypatch.setitem(det.SAFE_SECDEF_FUNCTIONS, ("public", "auth_user_role"),
                        "called only by RLS policy X; scopes internally")
    tier, reason = classify_finding(_f("anon", objtype="function", privilege_type="EXECUTE",
                                       is_secdef=True, schema="public", object="auth_user_role"))
    assert tier == "INFO" and "safe-secdef" in reason.lower()


def test_web_usage_on_exposed_schema_is_info():
    tier, _ = classify_finding(_f("anon", objtype="schema", privilege_type="USAGE", object="public"))
    assert tier == "INFO"
    tier, _ = classify_finding(_f("anon", objtype="schema", privilege_type="USAGE", object="graphql_public"))
    assert tier == "INFO"


def test_web_usage_on_internal_schema_FAILS():
    tier, reason = classify_finding(_f("anon", objtype="schema", privilege_type="USAGE", object="net"))
    assert tier == "FAIL" and "net" in reason


def test_sequence_in_exposed_schema_is_info_else_fail():
    assert classify_finding(_f("anon", objtype="sequence", privilege_type="USAGE", schema="public"))[0] == "INFO"
    assert classify_finding(_f("anon", objtype="sequence", privilege_type="USAGE", schema="internal"))[0] == "FAIL"


def test_real_safe_secdef_list_exempts_the_8_auth_user_fns():
    # the 8 justified-safe public.auth_user_* fns (orch-console #24312) -> INFO
    from scripts.lib.a3_grant_detector import SAFE_SECDEF_FUNCTIONS
    assert len(SAFE_SECDEF_FUNCTIONS) == 8, f"expected exactly 8 safe fns, got {len(SAFE_SECDEF_FUNCTIONS)}"
    for (schema, fn) in SAFE_SECDEF_FUNCTIONS:
        tier, _ = classify_finding(_f("authenticated", objtype="function", privilege_type="EXECUTE",
                                      is_secdef=True, schema=schema, object=fn))
        assert tier == "INFO", f"safe-listed {schema}.{fn} must be INFO"


def test_unassessed_mutating_secdef_still_fails():
    # the 12 unassessed SECDEF fns (merge_persons etc, orch-console #24312) are NOT safe-listed ->
    # stay FAIL (the purge_wc_ingest_pii class). Whitelisting them would be a false green.
    for fn in ("merge_persons", "reverse_merge", "write_audit_log_secure", "purge_wc_ingest_pii"):
        tier, _ = classify_finding(_f("authenticated", objtype="function", privilege_type="EXECUTE",
                                      is_secdef=True, schema="public", object=fn))
        assert tier == "FAIL", f"unassessed SECDEF {fn} must FAIL until read for caller-scope"
