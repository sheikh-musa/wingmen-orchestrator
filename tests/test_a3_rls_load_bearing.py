"""CAI-1031 core: the composite-hash attestation that lets A3 reclassify a
PUBLIC/anon-EXECUTE SECDEF fn FAIL->INFO('RLS-load-bearing') iff it is (1) RLS
policy-referenced AND (2) caller-scoped. This file exercises the mechanism cai
refined in #24485:

  * composite hash = hash(fn body || EACH recorded-closure callee body), so a
    redefine ANYWHERE in the transitive chain invalidates -> the fn re-surfaces to
    FAIL (create-or-replace-drops-gates).
  * the closure is the ATTESTER's recorded set (fn + callees followed at source);
    NEVER pg_depend fn->fn (cai verified 0 fn->fn edges on ceayj — Postgres does
    not track call deps for classic AS $$..$$ fns, so pg_depend collapses to the
    per-fn blind spot).
  * eligibility: only fns whose WHOLE chain is statically inspectable (LANGUAGE
    sql). A plpgsql link = opaque = NOT eligible -> stays FAIL (fail-closed).

Run against the SUBSTRATE (rolled-back txns creating real fns) — the hashing +
LANGUAGE checks are Postgres-universal, so substrate validity transfers to ceayj.
"""
import os

import psycopg2
import pytest

from scripts.lib import a3_rls_load_bearing as R


@pytest.fixture
def cur():
    """A rolled-back cursor on the substrate; every test's fns vanish at teardown."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = False
    c = conn.cursor()
    yield c
    conn.rollback()
    conn.close()


# ── composite hash ─────────────────────────────────────────────────────────────

def test_composite_hash_covers_the_whole_recorded_closure(cur):
    """A redefine of a CALLEE changes the fn's composite hash even though the fn's
    OWN body is byte-identical — the transitive-invalidation property (#24485)."""
    cur.execute("CREATE FUNCTION _sre_b() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$")
    cur.execute("CREATE FUNCTION _sre_a() RETURNS int LANGUAGE sql AS $$ SELECT _sre_b() $$")
    closure = ["_sre_a()", "_sre_b()"]
    h1 = R.composite_hash(cur, closure)
    assert h1 is not None
    # redefine the CALLEE only; _sre_a's own body is unchanged
    cur.execute("CREATE OR REPLACE FUNCTION _sre_b() RETURNS int LANGUAGE sql AS $$ SELECT 2 $$")
    h2 = R.composite_hash(cur, closure)
    assert h2 != h1, "a callee redefine must invalidate the composite hash"


def test_composite_hash_is_order_independent_and_deterministic(cur):
    cur.execute("CREATE FUNCTION _sre_b() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$")
    cur.execute("CREATE FUNCTION _sre_a() RETURNS int LANGUAGE sql AS $$ SELECT _sre_b() $$")
    a = R.composite_hash(cur, ["_sre_a()", "_sre_b()"])
    b = R.composite_hash(cur, ["_sre_b()", "_sre_a()"])
    assert a == b and a is not None


def test_unrelated_redefine_does_not_change_the_hash(cur):
    cur.execute("CREATE FUNCTION _sre_a() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$")
    cur.execute("CREATE FUNCTION _sre_c() RETURNS int LANGUAGE sql AS $$ SELECT 9 $$")
    h1 = R.composite_hash(cur, ["_sre_a()"])
    cur.execute("CREATE OR REPLACE FUNCTION _sre_c() RETURNS int LANGUAGE sql AS $$ SELECT 10 $$")
    assert R.composite_hash(cur, ["_sre_a()"]) == h1


def test_missing_closure_member_hashes_to_none_fail_closed(cur):
    """A signature that does not resolve -> None (cannot attest -> fail-closed)."""
    cur.execute("CREATE FUNCTION _sre_a() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$")
    assert R.composite_hash(cur, ["_sre_a()", "_sre_gone()"]) is None


# ── eligibility (statically inspectable = LANGUAGE sql only) ────────────────────

def test_all_sql_chain_is_eligible(cur):
    cur.execute("CREATE FUNCTION _sre_b() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$")
    cur.execute("CREATE FUNCTION _sre_a() RETURNS int LANGUAGE sql AS $$ SELECT _sre_b() $$")
    assert R.closure_is_eligible(cur, ["_sre_a()", "_sre_b()"]) is True


def test_plpgsql_link_in_chain_is_not_eligible(cur):
    """A plpgsql callee = opaque body = NOT eligible -> the fn stays FAIL (#24485)."""
    cur.execute("CREATE FUNCTION _sre_b() RETURNS int LANGUAGE plpgsql AS $$ BEGIN RETURN 1; END $$")
    cur.execute("CREATE FUNCTION _sre_a() RETURNS int LANGUAGE sql AS $$ SELECT _sre_b() $$")
    assert R.closure_is_eligible(cur, ["_sre_a()", "_sre_b()"]) is False


def test_unresolvable_member_is_not_eligible(cur):
    cur.execute("CREATE FUNCTION _sre_a() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$")
    assert R.closure_is_eligible(cur, ["_sre_a()", "_sre_gone()"]) is False


# ── the gate: is_rls_load_bearing (cond-1 AND cond-2) ──────────────────────────

def test_gate_true_only_when_policy_referenced_and_hash_matches(cur):
    cur.execute("CREATE FUNCTION _sre_a() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$")
    cur.execute("SELECT to_regprocedure('_sre_a()')::oid")
    a_oid = cur.fetchone()[0]
    closure = ["_sre_a()"]
    pinned = R.composite_hash(cur, closure)
    attestation = {"_sre_a()": {"closure": closure, "composite_sha256": pinned}}

    # cond-1 satisfied (a_oid is policy-referenced) + hash matches -> INFO-eligible
    ok, reason = R.is_rls_load_bearing(cur, "_sre_a()", attestation, policy_ref_oids={a_oid})
    assert ok is True, reason

    # cond-1 fails (not policy-referenced) -> stays FAIL
    ok2, _ = R.is_rls_load_bearing(cur, "_sre_a()", attestation, policy_ref_oids=set())
    assert ok2 is False

    # hash drift (redefine) -> attestation invalid -> stays FAIL even though referenced
    cur.execute("CREATE OR REPLACE FUNCTION _sre_a() RETURNS int LANGUAGE sql AS $$ SELECT 2 $$")
    ok3, _ = R.is_rls_load_bearing(cur, "_sre_a()", attestation, policy_ref_oids={a_oid})
    assert ok3 is False

    # not attested at all -> stays FAIL
    ok4, _ = R.is_rls_load_bearing(cur, "_sre_a()", {}, policy_ref_oids={a_oid})
    assert ok4 is False


# ── cond-1: policy_referenced_fn_oids via pg_depend (policy->fn) ───────────────

def test_policy_referenced_fn_oids_finds_a_fn_called_in_a_policy(cur):
    """A fn referenced in an RLS policy expression is returned; an unreferenced fn is
    not. cai #24485: pg_depend DOES record policy->fn deps (unlike fn->fn)."""
    cur.execute("CREATE FUNCTION _sre_allowed() RETURNS int[] LANGUAGE sql SECURITY DEFINER AS $$ SELECT ARRAY[1] $$")
    cur.execute("CREATE FUNCTION _sre_unref() RETURNS int LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$")
    cur.execute("CREATE TABLE _sre_t (id int, org int)")
    cur.execute("ALTER TABLE _sre_t ENABLE ROW LEVEL SECURITY")
    cur.execute("CREATE POLICY p ON _sre_t FOR SELECT TO public USING (org = ANY(_sre_allowed()))")
    cur.execute("SELECT to_regprocedure('_sre_allowed()')::oid, to_regprocedure('_sre_unref()')::oid")
    allowed_oid, unref_oid = cur.fetchone()
    refs = R.policy_referenced_fn_oids(cur)
    assert allowed_oid in refs, "a fn called in a policy USING expr must be policy-referenced"
    assert unref_oid not in refs, "a fn no policy references must NOT appear"
