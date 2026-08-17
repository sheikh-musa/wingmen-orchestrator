#!/usr/bin/env python3
"""a3_grant_detector — the catalogue isolation detector for the CAI-985 A3 automation.

Generalises the hand-run A3 assertion ('PUBLIC table grants outside shipforge') into a
deny-by-default whitelist check (cai CAI-RESP-1018/1019): flag ANY grantee that is NOT the
object's owner and NOT in the trusted whitelist, across RELATIONS + SEQUENCES + FUNCTIONS +
SCHEMAS, via aclexplode/relacl — NEVER information_schema.role_table_grants (CAI-1000 D5: it
greens on grants the connecting role cannot see).

WHY DENY-BY-DEFAULT (CAI-1018): a blacklist (PUBLIC-only, or even PUBLIC+anon+authenticated)
misses a FUTURE role provisioned later that inherits Supabase default-privs. A whitelist flags
the grantee you didn't think of AND the one that doesn't exist yet.

THE LOAD-BEARING SUBTLETY (cai doctrine #24184, the mig051 class): aclexplode(NULL) returns
NOTHING, but a NULL acl means DEFAULT privileges, not no-grants. For functions the default is
PUBLIC EXECUTE — so a naive aclexplode(proacl) SILENTLY MISSES every default-public-executable
function (a false-negative, the worst outcome for an isolation detector). Fix: materialise the
object-class default before exploding — aclexplode(COALESCE(acl, acldefault(objtype, owner))).
The owner's own self-grant (always present in acldefault) is excluded: an owner owning its own
object is not a leak.

DB-AGNOSTIC: aclexplode/acldefault are core Postgres — develop/verify vs the substrate catalogue,
RUN vs ceayj under orch-console (the credential-holder; CAI-981 / FORK1=b).
"""
from __future__ import annotations

# One UNION-ALL catalogue query, four object classes, each with its acldefault objtype:
#   relations (tables/views/matviews/foreign/partitioned) -> 'r'
#   sequences                                              -> 's'
#   functions/procedures                                   -> 'f'  (NULL proacl = PUBLIC EXECUTE)
#   schemas (USAGE/CREATE)                                 -> 'n'
# Flag rule per row: grantee <> owner AND rolname (or 'PUBLIC') NOT IN the trusted whitelist,
# object schema NOT IN the excluded set.
_DETECT_SQL = """
WITH untrusted AS (
  SELECT n.nspname AS schema, c.relname AS object, 'relation:'||c.relkind::text AS objtype,
         COALESCE(r.rolname,'PUBLIC') AS grantee, a.privilege_type AS privilege_type
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
   WHERE c.relkind IN ('r','v','m','f','p')
     AND a.grantee <> c.relowner
     AND n.nspname <> ALL(%(excl)s)
     AND COALESCE(r.rolname,'PUBLIC') <> ALL(%(trusted)s)
  UNION ALL
  SELECT n.nspname, c.relname, 'sequence',
         COALESCE(r.rolname,'PUBLIC'), a.privilege_type
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('s', c.relowner))) a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
   WHERE c.relkind = 'S'
     AND a.grantee <> c.relowner
     AND n.nspname <> ALL(%(excl)s)
     AND COALESCE(r.rolname,'PUBLIC') <> ALL(%(trusted)s)
  UNION ALL
  SELECT n.nspname, p.proname, 'function',
         COALESCE(r.rolname,'PUBLIC'), a.privilege_type
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
   WHERE a.grantee <> p.proowner
     AND n.nspname <> ALL(%(excl)s)
     AND COALESCE(r.rolname,'PUBLIC') <> ALL(%(trusted)s)
  UNION ALL
  SELECT n.nspname, n.nspname, 'schema',
         COALESCE(r.rolname,'PUBLIC'), a.privilege_type
    FROM pg_namespace n
    CROSS JOIN LATERAL aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
   WHERE a.grantee <> n.nspowner
     AND n.nspname <> ALL(%(excl)s)
     AND COALESCE(r.rolname,'PUBLIC') <> ALL(%(trusted)s)
)
SELECT schema, object, objtype, grantee, privilege_type
  FROM untrusted
 ORDER BY schema, object, objtype, grantee, privilege_type
"""


WEB_ROLES = ("anon", "authenticated")

# Schemas Supabase EXPOSES through PostgREST — a web role NEEDS USAGE on these to reach the API,
# so USAGE here is controlled/expected, not a breach (CAI-RESP-1026).
EXPOSED_SCHEMAS = ("public", "graphql_public")

# SAFE-SECDEF whitelist (CAI-1024 whitelist #2, CAI-994 no-blanket-trust): SECURITY DEFINER functions
# safe because an RLS policy calls them AND they scope internally — an EXPLICIT, per-function justified
# list, NOT 'trust auth_*'. Keyed (schema, function_name) -> reason. cai named 'the 8 auth_user_*
# siblings orch identified' — PENDING those exact identities + per-fn reasons from orch-console's
# dry-run. Until listed, that SECDEF web-EXECUTE fn FAILS (safe-but-strict; that is how
# purge_wc_ingest_pii was caught and how the next one will be).
SAFE_SECDEF_FUNCTIONS = {
    # ("public", "auth_user_role"): "called only by RLS policy <X>; scopes to auth.uid() internally",
}


def classify_finding(f):
    """Tier one untrusted grant FAIL vs INFO per cai CAI-RESP-1024 (+1025 no-policy correction, +1026
    control-aware schema/sequence). `f` carries: grantee, schema, object, objtype
    ('relation:<k>'|'sequence'|'function'|'schema'), privilege_type, and where relevant is_secdef /
    rls_enabled / has_policy / is_security_invoker. Returns (tier, reason).

    RESIDENCY-1 means ISOLATION; on Supabase RLS is the control, not the grant. So a grant is a breach
    only where its CONTROL is absent — it ESCAPES RLS (SECDEF fn, definer view, RLS-off table) or
    reaches a target with no gate (non-exposed schema, sequence off a gated table). PUBLIC always
    fails; an unexpected non-web grantee is deny-by-default."""
    grantee = f["grantee"]
    objtype = f.get("objtype", "")
    priv = f.get("privilege_type", "")
    schema = f.get("schema", "")
    obj = f.get("object", "")

    # 1. PUBLIC on any app object always fails — RLS narrows it, it never justifies it.
    if grantee == "PUBLIC":
        return "FAIL", "PUBLIC grant on an app object (PUBLIC is never the right grantee; RLS narrows, does not justify)"

    # 2. Unexpected non-web grantee (not PUBLIC, not the conditional web roles) — deny-by-default.
    if grantee not in WEB_ROLES:
        return "FAIL", f"unexpected non-trusted grantee '{grantee}' (deny-by-default, CAI-1018)"

    # --- grantee is anon/authenticated: CONTROL-AWARE test (a grant is a breach only where its
    #     Supabase control is absent) ---

    # FUNCTION: a SECURITY DEFINER fn runs as OWNER, bypassing RLS -> FAIL unless justified-safe.
    # A SECURITY INVOKER fn runs as the caller, so RLS applies -> controlled -> INFO.
    if objtype == "function":
        if f.get("is_secdef") and priv == "EXECUTE":
            if (schema, obj) in SAFE_SECDEF_FUNCTIONS:
                return "INFO", f"SECURITY DEFINER fn on the justified safe-SECDEF list: {SAFE_SECDEF_FUNCTIONS[(schema, obj)]}"
            return "FAIL", "SECURITY DEFINER function EXECUTE by a web role — bypasses RLS (runs as owner); not on the safe-SECDEF list"
        return "INFO", "SECURITY INVOKER function EXECUTE (runs as caller; the caller's RLS applies)"

    # RELATIONS: tables, views, matviews.
    if objtype.startswith("relation:"):
        relkind = objtype.split(":", 1)[1]
        if relkind in ("r", "p", "f"):  # ordinary / partitioned / foreign TABLES
            if not f.get("rls_enabled"):
                return "FAIL", "web-role grant on an RLS-DISABLED table (RLS off = the table is open)"
            if not f.get("has_policy"):
                # RLS-ENABLED + zero policies is DEFAULT-DENY (most CLOSED; proven live, CAI-1025).
                # Inert today, a LATENT TRAP if a policy is later added — review the grant, not fail it.
                return "INFO", "web-role grant inert under default-deny RLS (no policy) — would open silently if a policy is later added; review the grant"
            return "INFO", "anon/authenticated on an RLS-protected table (policy-correctness unchecked — fast-follow)"
        if relkind == "v":  # VIEW — GAP A (cc-storefront #24276): a DEFINER view reads base tables as
            # OWNER, bypassing the caller's RLS (the CAI-992 boot_briefing/finance_burn class; proven
            # live — a definer view over an RLS-closed table returned all rows to authenticated).
            if not f.get("is_security_invoker"):
                return "FAIL", "web-role grant on a DEFINER view (not security_invoker) — reads base tables as owner, bypasses caller RLS (boot_briefing class)"
            return "INFO", "web-role grant on a security_invoker view (runs as caller; the caller's RLS applies)"
        if relkind == "m":  # MATERIALIZED view — always owner-computed, no per-caller RLS.
            return "FAIL", "web-role grant on a MATERIALIZED view — exposes owner-computed rows (no per-caller RLS)"
        return "INFO", f"web-role grant on relation:{relkind} (reported)"

    # SCHEMA USAGE (CAI-1026): INFO on a PostgREST-exposed schema (a web role NEEDS it for the API);
    # FAIL on a non-exposed/internal schema (a web role has no reason to reach net/internal).
    if objtype == "schema":
        if obj in EXPOSED_SCHEMAS:
            return "INFO", f"web-role USAGE on the PostgREST-exposed schema '{obj}' (required for the API)"
        return "FAIL", f"web-role USAGE on non-exposed schema '{obj}' (no gate; a web role has no reason to reach it)"

    # SEQUENCE (CAI-1026): approximated by schema exposure — a sequence in an exposed schema backs a
    # serial column on a (typically gated) table -> INFO; elsewhere -> FAIL. The precise owning-table
    # RLS linkage (pg_depend -> column -> table.relrowsecurity) is a NAMED FAST-FOLLOW.
    if objtype == "sequence":
        if schema in EXPOSED_SCHEMAS:
            return "INFO", f"web-role grant on a sequence in exposed schema '{schema}' (serial-column backing; owning-table-RLS linkage is a fast-follow)"
        return "FAIL", f"web-role grant on a sequence in non-exposed schema '{schema}' (no RLS gate)"

    return "INFO", f"{grantee} {priv} on {objtype} (reported)"


# Enriched variant of the detector query: same untrusted-grant rows, plus the metadata the CAI-1024
# classifier needs — is_secdef (functions), rls_enabled + has_policy (relations). NULL where N/A.
_ISOLATION_SQL = """
WITH untrusted AS (
  SELECT n.nspname AS schema, c.relname AS object, 'relation:'||c.relkind::text AS objtype,
         COALESCE(r.rolname,'PUBLIC') AS grantee, a.privilege_type AS privilege_type,
         NULL::bool AS is_secdef,
         c.relrowsecurity AS rls_enabled,
         EXISTS(SELECT 1 FROM pg_policy pol WHERE pol.polrelid = c.oid) AS has_policy,
         -- security_invoker view (PG15+, WITH (security_invoker=on)) runs as the CALLER, so RLS
         -- applies; a definer/pre-15 view runs as OWNER and BYPASSES the caller's RLS. Default false.
         COALESCE((SELECT bool_or(lower(o) IN ('security_invoker=true','security_invoker=on','security_invoker=1'))
                     FROM unnest(c.reloptions) o), false) AS is_security_invoker
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
   WHERE c.relkind IN ('r','v','m','f','p') AND a.grantee <> c.relowner
     AND n.nspname <> ALL(%(excl)s) AND COALESCE(r.rolname,'PUBLIC') <> ALL(%(trusted)s)
  UNION ALL
  SELECT n.nspname, c.relname, 'sequence', COALESCE(r.rolname,'PUBLIC'), a.privilege_type,
         NULL::bool, NULL::bool, NULL::bool, NULL::bool
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('s', c.relowner))) a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
   WHERE c.relkind = 'S' AND a.grantee <> c.relowner
     AND n.nspname <> ALL(%(excl)s) AND COALESCE(r.rolname,'PUBLIC') <> ALL(%(trusted)s)
  UNION ALL
  SELECT n.nspname, p.proname, 'function', COALESCE(r.rolname,'PUBLIC'), a.privilege_type,
         p.prosecdef AS is_secdef, NULL::bool, NULL::bool, NULL::bool
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
   WHERE a.grantee <> p.proowner
     AND n.nspname <> ALL(%(excl)s) AND COALESCE(r.rolname,'PUBLIC') <> ALL(%(trusted)s)
  UNION ALL
  SELECT n.nspname, n.nspname, 'schema', COALESCE(r.rolname,'PUBLIC'), a.privilege_type,
         NULL::bool, NULL::bool, NULL::bool, NULL::bool
    FROM pg_namespace n
    CROSS JOIN LATERAL aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
   WHERE a.grantee <> n.nspowner
     AND n.nspname <> ALL(%(excl)s) AND COALESCE(r.rolname,'PUBLIC') <> ALL(%(trusted)s)
)
SELECT schema, object, objtype, grantee, privilege_type, is_secdef, rls_enabled, has_policy, is_security_invoker
  FROM untrusted
 ORDER BY schema, object, objtype, grantee, privilege_type
"""


def find_isolation_findings(cur, *, trusted_roles, exclude_schemas):
    """Like find_untrusted_grants but each row also carries the CAI-1024 classification inputs
    (is_secdef / rls_enabled / has_policy). Pass the rows through classify_all to get FAIL/INFO."""
    cur.execute(_ISOLATION_SQL, {"trusted": list(trusted_roles), "excl": list(exclude_schemas)})
    cols = ("schema", "object", "objtype", "grantee", "privilege_type",
            "is_secdef", "rls_enabled", "has_policy", "is_security_invoker")
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def classify_all(findings):
    """Split enriched findings into {'fail': [...], 'info': [...]} (CAI-1024). Each finding gets a
    'tier' and 'reason' added. RESIDENCY-1 FAILs iff 'fail' is non-empty; 'info' is reported."""
    out = {"fail": [], "info": []}
    for f in findings:
        tier, reason = classify_finding(f)
        f = dict(f, tier=tier, reason=reason)
        out["fail" if tier == "FAIL" else "info"].append(f)
    return out


def find_untrusted_grants(cur, *, trusted_roles, exclude_schemas):
    """Return every untrusted grant as a list of dicts {schema, object, objtype, grantee,
    privilege_type} — a grantee that is NOT the object owner and NOT in `trusted_roles`, on any
    relation/sequence/function/schema whose schema is NOT in `exclude_schemas`. Uses the
    materialised-default ACL so NULL-acl objects (e.g. default-PUBLIC-EXECUTE functions) are
    evaluated, not skipped. Empty list == clean (no leak) for the given scope. The caller owns
    the connection/txn; this only reads the catalogue."""
    cur.execute(_DETECT_SQL, {"trusted": list(trusted_roles), "excl": list(exclude_schemas)})
    cols = ("schema", "object", "objtype", "grantee", "privilege_type")
    return [dict(zip(cols, row)) for row in cur.fetchall()]
