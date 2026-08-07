#!/usr/bin/env python3
"""Transactional irsyad residency purge on ceayj (CAI-RESP-382 GO, op #2055).
Catalog-driven (no hand-lists). Dump->verify->FK-ordered delete->zero-sweep,
COMMIT only if every assertion passes, else ROLLBACK. Dry by default."""
import os, sys, json, psycopg

ORG = "8de59182-eae0-422b-9d1c-0ff05094badb"
BAK = "irsyad_purge_bak_20260703"
EXECUTE = "--execute" in sys.argv
dsn = os.environ["IHSANOS_PROD_DATABASE_URL"]

def die(msg):
    print("HALT:", msg); sys.exit(2)

conn = psycopg.connect(dsn, connect_timeout=20)
conn.autocommit = False
cur = conn.cursor()

# 1. DISCOVER org-scoped BASE tables + their org column (catalog-driven)
cur.execute("""
  WITH fk AS (
    SELECT tc.table_name FROM information_schema.table_constraints tc
    JOIN information_schema.constraint_column_usage ccu
      ON tc.constraint_name=ccu.constraint_name AND tc.table_schema=ccu.table_schema
    WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public' AND ccu.table_name='organizations'),
  named AS (
    SELECT table_name FROM information_schema.columns
    WHERE table_schema='public' AND column_name IN ('org_id','organization_id'))
  SELECT DISTINCT c.table_name, c.column_name
  FROM (SELECT table_name FROM fk UNION SELECT table_name FROM named) u
  JOIN information_schema.columns c ON c.table_name=u.table_name AND c.table_schema='public'
       AND c.column_name IN ('org_id','organization_id')
  JOIN information_schema.tables tb ON tb.table_name=u.table_name AND tb.table_schema='public'
       AND tb.table_type='BASE TABLE'
  ORDER BY 1
""")
orgcol = {r[0]: r[1] for r in cur.fetchall()}
print(f"discovered {len(orgcol)} org-scoped base tables")

# 2. FOOTPRINT: count org rows per table
footprint = {}
for t, col in orgcol.items():
    cur.execute(f'SELECT count(*) FROM public."{t}" WHERE "{col}"=%s', (ORG,))
    n = cur.fetchone()[0]
    if n:
        footprint[t] = n
total = sum(footprint.values())
print(f"footprint: {len(footprint)} tables with rows, {total} data rows (+1 org row)")
for t, n in sorted(footprint.items(), key=lambda x: -x[1]):
    print(f"   {t:34} {n}")

# 3. FK topo order among footprint tables (delete children before parents)
cur.execute("""
  SELECT c.conrelid::regclass::text AS child, c.confrelid::regclass::text AS parent
  FROM pg_constraint c WHERE c.contype='f'
""")
edges = [(a.replace('public.',''), b.replace('public.',''))
         for a, b in cur.fetchall()]
fset = set(footprint)
# child depends on parent -> delete child first. Kahn on reversed graph.
import collections
deps = collections.defaultdict(set)   # table -> parents it references (within footprint)
for child, parent in edges:
    if child in fset and parent in fset and child != parent:
        deps[child].add(parent)
order, remaining = [], set(fset)
while remaining:
    free = [t for t in remaining if not (deps[t] & remaining)]  # no parents left -> but we want children first
    # children first = tables that are NOT depended-upon by remaining, i.e. nothing remaining references them
    referenced = set()
    for t in remaining:
        referenced |= (deps[t] & remaining)
    leaves = [t for t in remaining if t not in referenced]
    if not leaves:
        die(f"FK cycle among {sorted(remaining)} — cannot order, manual review")
    for t in sorted(leaves):
        order.append(t); remaining.discard(t)
print(f"delete order (children first): {order}")

if not EXECUTE:
    print("\nDRY RUN — no writes. Re-run with --execute to dump+delete+verify+commit.")
    conn.rollback(); sys.exit(0)

# ===== EXECUTE (single transaction) =====
try:
    # 4. DUMP to access-locked backup schema
    cur.execute(f'DROP SCHEMA IF EXISTS "{BAK}" CASCADE')
    cur.execute(f'CREATE SCHEMA "{BAK}"')
    cur.execute(f'REVOKE ALL ON SCHEMA "{BAK}" FROM PUBLIC')
    for t, col in orgcol.items():
        cur.execute(f'CREATE TABLE "{BAK}"."{t}" AS SELECT * FROM public."{t}" WHERE "{col}"=%s', (ORG,))
    cur.execute(f'CREATE TABLE "{BAK}"."organizations" AS SELECT * FROM public.organizations WHERE id=%s', (ORG,))
    # verify backup counts == footprint
    for t, n in footprint.items():
        cur.execute(f'SELECT count(*) FROM "{BAK}"."{t}"')
        b = cur.fetchone()[0]
        if b != n:
            die(f"backup mismatch {t}: live {n} vs backup {b}")
    cur.execute(f'SELECT count(*) FROM "{BAK}"."organizations"')
    if cur.fetchone()[0] != 1:
        die("org row not backed up")
    print("dump verified: all backup counts == live counts")

    # 5. DELETE children-first
    deleted = {}
    for t in order:
        col = orgcol[t]
        cur.execute(f'DELETE FROM public."{t}" WHERE "{col}"=%s', (ORG,))
        deleted[t] = cur.rowcount
    # org row LAST
    cur.execute('DELETE FROM public.organizations WHERE id=%s', (ORG,))
    deleted['organizations'] = cur.rowcount

    # 6. ZERO-SWEEP: re-count ALL org-scoped tables -> must be 0
    residual = {}
    for t, col in orgcol.items():
        cur.execute(f'SELECT count(*) FROM public."{t}" WHERE "{col}"=%s', (ORG,))
        r = cur.fetchone()[0]
        if r:
            residual[t] = r
    cur.execute('SELECT count(*) FROM public.organizations WHERE id=%s', (ORG,))
    org_left = cur.fetchone()[0]
    if residual or org_left:
        die(f"post-delete NON-ZERO residual: {residual} org_left={org_left}")

    # 7. access-lock the backup (already revoked from PUBLIC); commit
    conn.commit()
    print("PURGE COMMITTED.")
    print("deleted:", json.dumps(deleted))
    print("total deleted rows:", sum(deleted.values()))
    print(f"backup retained: schema {BAK} (access-locked, drop after 30d)")
except SystemExit:
    conn.rollback(); print("ROLLED BACK (halt condition)"); raise
except Exception as e:
    conn.rollback(); print("ROLLED BACK on error:", type(e).__name__, e); raise
finally:
    cur.close(); conn.close()
