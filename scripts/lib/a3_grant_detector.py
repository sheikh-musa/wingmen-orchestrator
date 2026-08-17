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
