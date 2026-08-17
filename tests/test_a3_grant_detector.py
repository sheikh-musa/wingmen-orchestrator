"""CAI-985 A3 grant detector (cc-fleet-health, 2026-08-17; whitelist ruled CAI-1018/1019,
NULL-acl fix ruled CAI-1016/#24184).

Deny-by-default isolation detector: flag ANY grantee that is NOT the object's owner and NOT in the
trusted whitelist, across relations + sequences + functions + schemas, via aclexplode/relacl NEVER
information_schema. The load-bearing subtlety (cai doctrine, the mig051 class): aclexplode(NULL)
returns nothing, but a NULL acl means DEFAULT privileges — and a function's default is PUBLIC
EXECUTE. So the detector materialises the object-class default before exploding:
aclexplode(COALESCE(acl, acldefault(objtype, owner))) — else every default-public-executable
function is a silent false-negative (a green that misses a leak).

Tests run against the substrate catalog (aclexplode is DB-agnostic) inside ROLLED-BACK txns that
create throwaway objects, so nothing persists and the assertions are deterministic. The detector
RUNS against ceayj under orch-console (FORK1=b); these pin the mechanics.
"""
import os
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs substrate DATABASE_URL")

from scripts.lib.a3_grant_detector import find_untrusted_grants

TRUSTED = ["postgres", "service_role", "console_readonly"]
# exclude the system schemas + a throwaway test schema is NOT excluded so we can plant objects in it
SYS_EXCLUDE = ["pg_catalog", "information_schema", "pg_toast"]


def _conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"])


def test_null_proacl_function_is_flagged_as_public_execute():
    # THE CRUX: a function with default (NULL) proacl is PUBLIC EXECUTE by default. A naive
    # aclexplode(proacl) returns nothing and MISSES it. The detector must materialise acldefault
    # and flag PUBLIC EXECUTE.
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA a3test")
        cur.execute("CREATE FUNCTION a3test.leaky() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        # sanity: proacl really is NULL (default) — we did not grant/revoke anything
        assert cur.execute(
            "SELECT proacl FROM pg_proc WHERE proname='leaky' AND pronamespace='a3test'::regnamespace"
        ).fetchone()[0] is None
        rows = find_untrusted_grants(cur, trusted_roles=TRUSTED, exclude_schemas=SYS_EXCLUDE)
        conn.rollback()
    hits = [r for r in rows if r["schema"] == "a3test" and r["object"] == "leaky"]
    assert hits, "default-PUBLIC-EXECUTE function was NOT flagged — the NULL-acl false-negative"
    assert any(r["grantee"] == "PUBLIC" and r["privilege_type"] == "EXECUTE" for r in hits), \
        f"expected PUBLIC EXECUTE flagged on a3test.leaky, got {hits!r}"


def test_revoked_public_execute_function_is_not_flagged():
    # once PUBLIC EXECUTE is explicitly REVOKEd, proacl is non-NULL (owner only) -> not flagged.
    # proves the detector reads REAL acls, not just 'assume default' — no false-positive.
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA a3test")
        cur.execute("CREATE FUNCTION a3test.safe() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        cur.execute("REVOKE EXECUTE ON FUNCTION a3test.safe() FROM PUBLIC")
        rows = find_untrusted_grants(cur, trusted_roles=TRUSTED, exclude_schemas=SYS_EXCLUDE)
        conn.rollback()
    assert not [r for r in rows if r["object"] == "safe"], \
        "a function with PUBLIC EXECUTE revoked must NOT be flagged (false-positive)"


def test_trusted_grantee_is_not_flagged():
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA a3test")
        cur.execute("CREATE TABLE a3test.t (x int)")
        cur.execute("GRANT SELECT ON a3test.t TO console_readonly")  # whitelisted
        rows = find_untrusted_grants(cur, trusted_roles=TRUSTED, exclude_schemas=SYS_EXCLUDE)
        conn.rollback()
    assert not [r for r in rows if r["object"] == "t" and r["grantee"] == "console_readonly"], \
        "a whitelisted grantee must not be flagged"


def test_untrusted_table_grant_is_flagged():
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA a3test")
        cur.execute("CREATE TABLE a3test.t (x int)")
        cur.execute("GRANT SELECT ON a3test.t TO authenticated")  # NOT whitelisted
        rows = find_untrusted_grants(cur, trusted_roles=TRUSTED, exclude_schemas=SYS_EXCLUDE)
        conn.rollback()
    assert [r for r in rows if r["object"] == "t" and r["grantee"] == "authenticated"], \
        "an untrusted grantee (authenticated SELECT on a table) must be flagged"


def test_owner_self_grant_is_not_flagged():
    # acldefault always includes the owner's own grants; owning your own object is not a leak.
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA a3test")
        cur.execute("CREATE TABLE a3test.t (x int)")  # NULL relacl -> acldefault = owner only
        rows = find_untrusted_grants(cur, trusted_roles=[], exclude_schemas=SYS_EXCLUDE)  # empty whitelist
        conn.rollback()
    assert not [r for r in rows if r["object"] == "t"], \
        "owner self-grant on a default-acl table must not be flagged even with an empty whitelist"


def test_negative_control_same_fn_finds_known_untrusted_grants():
    # D4 (Nazim #24061 / cai): prove the positive path FIRES by running the SAME fn with a scope
    # KNOWN to carry untrusted grants — pg_catalog has PUBLIC SELECT on system relations. Identical
    # function, different scope param (NOT a twin query that could drift). 0 here => detector broken.
    with _conn() as conn, conn.cursor() as cur:
        rows = find_untrusted_grants(cur, trusted_roles=TRUSTED,
                                     exclude_schemas=["information_schema", "pg_toast"])  # pg_catalog NOT excluded
        conn.rollback()
    assert len(rows) > 0, "negative control returned 0 — the detector's positive path is broken (fail-closed)"
    assert any(r["grantee"] == "PUBLIC" for r in rows), "expected PUBLIC grants surfaced in pg_catalog"
