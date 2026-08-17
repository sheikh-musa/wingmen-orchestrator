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


def classify_finding(f):
    """Tier one untrusted grant FAIL vs INFO per cai CAI-RESP-1024 (see test_a3_classify for the
    rules). `f` carries: grantee, objtype ('relation:<k>'|'sequence'|'function'|'schema'),
    privilege_type, and (where relevant) is_secdef / rls_enabled / has_policy. Returns (tier, reason).

    RESIDENCY-1 means ISOLATION; on Supabase RLS is the control, not the grant. So a grant is a
    breach only when it ESCAPES or LACKS RLS. Order matters: PUBLIC and SECDEF-bypass are breaches
    regardless of RLS; an unexpected non-web grantee is deny-by-default; a web-role table grant is a
    breach only when RLS does not cover it."""
    grantee = f["grantee"]
    objtype = f.get("objtype", "")
    priv = f.get("privilege_type", "")

    # 1. PUBLIC is broader than any role — always a breach (incl PUBLIC-EXECUTE functions).
    if grantee == "PUBLIC":
        return "FAIL", "PUBLIC grant (broader than any role)"

    # 2. A SECURITY DEFINER function runs as its OWNER, bypassing RLS entirely; a web-role EXECUTE on
    #    it is privileged code anyone can run (the purge_wc_ingest_pii class). FAIL regardless of RLS.
    if objtype == "function" and f.get("is_secdef") and priv == "EXECUTE" and grantee in WEB_ROLES:
        return "FAIL", "SECURITY DEFINER function EXECUTE by a web role — bypasses RLS (runs as owner)"

    # 3. Any grantee that is neither PUBLIC nor the known-conditional web roles is unaccounted for —
    #    deny-by-default (the future-role trap CAI-1018 exists for).
    if grantee not in WEB_ROLES:
        return "FAIL", f"unexpected non-trusted grantee '{grantee}' (deny-by-default)"

    # 4. anon/authenticated on a data-holding TABLE (relkind r/p/f — RLS-capable) is a breach ONLY
    #    if RLS does not cover it (disabled OR no policy — an RLS-on table with zero policies is
    #    still open, cai's sharpening). Views/matviews (v/m) have no RLS of their own; their access
    #    is governed by the underlying tables' RLS (criterion 3 catches those base tables), so a
    #    view grant is INFO — not a false FAIL from the view's inherent relrowsecurity=false.
    if objtype.startswith("relation:"):
        relkind = objtype.split(":", 1)[1]
        if relkind in ("r", "p", "f"):  # ordinary / partitioned / foreign tables
            if not f.get("rls_enabled") or not f.get("has_policy"):
                return "FAIL", "web-role grant on a table with RLS disabled or no policy (grant without the control)"
            # RLS on + a policy exists: the normal architecture. INFO, with the honest caveat that
            # policy CORRECTNESS (a permissive USING(true)) is a named fast-follow, not checked here.
            return "INFO", "anon/authenticated on an RLS-protected table (policy-correctness unchecked — fast-follow)"
        return "INFO", "anon/authenticated on a view/matview (underlying-table RLS governs; policy-correctness unchecked)"

    # 5. web-role EXECUTE on a SECURITY INVOKER function (runs as caller, RLS applies), or USAGE on a
    #    sequence/schema (access enablers) — not a breach on their own. INFO.
    return "INFO", f"{grantee} {priv} on {objtype} (reported; RLS/caller-context governs, not a breach on its own)"


# Enriched variant of the detector query: same untrusted-grant rows, plus the metadata the CAI-1024
# classifier needs — is_secdef (functions), rls_enabled + has_policy (relations). NULL where N/A.
_ISOLATION_SQL = """
WITH untrusted AS (
  SELECT n.nspname AS schema, c.relname AS object, 'relation:'||c.relkind::text AS objtype,
         COALESCE(r.rolname,'PUBLIC') AS grantee, a.privilege_type AS privilege_type,
         NULL::bool AS is_secdef,
         c.relrowsecurity AS rls_enabled,
         EXISTS(SELECT 1 FROM pg_policy pol WHERE pol.polrelid = c.oid) AS has_policy
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
   WHERE c.relkind IN ('r','v','m','f','p') AND a.grantee <> c.relowner
     AND n.nspname <> ALL(%(excl)s) AND COALESCE(r.rolname,'PUBLIC') <> ALL(%(trusted)s)
  UNION ALL
  SELECT n.nspname, c.relname, 'sequence', COALESCE(r.rolname,'PUBLIC'), a.privilege_type,
         NULL::bool, NULL::bool, NULL::bool
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('s', c.relowner))) a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
   WHERE c.relkind = 'S' AND a.grantee <> c.relowner
     AND n.nspname <> ALL(%(excl)s) AND COALESCE(r.rolname,'PUBLIC') <> ALL(%(trusted)s)
  UNION ALL
  SELECT n.nspname, p.proname, 'function', COALESCE(r.rolname,'PUBLIC'), a.privilege_type,
         p.prosecdef AS is_secdef, NULL::bool, NULL::bool
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
   WHERE a.grantee <> p.proowner
     AND n.nspname <> ALL(%(excl)s) AND COALESCE(r.rolname,'PUBLIC') <> ALL(%(trusted)s)
  UNION ALL
  SELECT n.nspname, n.nspname, 'schema', COALESCE(r.rolname,'PUBLIC'), a.privilege_type,
         NULL::bool, NULL::bool, NULL::bool
    FROM pg_namespace n
    CROSS JOIN LATERAL aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
   WHERE a.grantee <> n.nspowner
     AND n.nspname <> ALL(%(excl)s) AND COALESCE(r.rolname,'PUBLIC') <> ALL(%(trusted)s)
)
SELECT schema, object, objtype, grantee, privilege_type, is_secdef, rls_enabled, has_policy
  FROM untrusted
 ORDER BY schema, object, objtype, grantee, privilege_type
"""


def find_isolation_findings(cur, *, trusted_roles, exclude_schemas):
    """Like find_untrusted_grants but each row also carries the CAI-1024 classification inputs
    (is_secdef / rls_enabled / has_policy). Pass the rows through classify_all to get FAIL/INFO."""
    cur.execute(_ISOLATION_SQL, {"trusted": list(trusted_roles), "excl": list(exclude_schemas)})
    cols = ("schema", "object", "objtype", "grantee", "privilege_type",
            "is_secdef", "rls_enabled", "has_policy")
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
