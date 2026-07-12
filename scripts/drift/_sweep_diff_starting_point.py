import os, json, sys
import psycopg

CEAYJ = os.environ["IHSANOS_PROD_DATABASE_URL"]
GOUM = os.environ["GOUMLYNE_DATABASE_URL"]

def q(dsn, sql, params=None):
    with psycopg.connect(dsn, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

SQL = {
"tables": """
  SELECT table_name FROM information_schema.tables
  WHERE table_schema='public' AND table_type='BASE TABLE'
  ORDER BY table_name
""",
"columns": """
  SELECT table_name, column_name, data_type, is_nullable, column_default
  FROM information_schema.columns
  WHERE table_schema='public'
  ORDER BY table_name, ordinal_position
""",
"indexes": """
  SELECT tablename, indexname, indexdef
  FROM pg_indexes WHERE schemaname='public'
  ORDER BY tablename, indexname
""",
"policies": """
  SELECT tablename, policyname, cmd, permissive, roles, qual, with_check
  FROM pg_policies WHERE schemaname='public'
  ORDER BY tablename, policyname
""",
"table_grants": """
  SELECT table_name, grantee, privilege_type
  FROM information_schema.role_table_grants
  WHERE table_schema='public' AND grantee IN ('anon','authenticated')
  ORDER BY table_name, grantee, privilege_type
""",
"col_grants": """
  SELECT table_name, column_name, grantee, privilege_type
  FROM information_schema.role_column_grants
  WHERE table_schema='public' AND grantee IN ('anon','authenticated')
  ORDER BY table_name, column_name, grantee, privilege_type
""",
"routines": """
  SELECT r.routine_name,
         p.prosecdef AS secdef,
         pg_get_function_identity_arguments(p.oid) AS args
  FROM information_schema.routines r
  JOIN pg_proc p ON p.proname = r.routine_name
  JOIN pg_namespace n ON n.oid=p.pronamespace AND n.nspname='public'
  WHERE r.routine_schema='public' AND r.routine_type='FUNCTION'
  ORDER BY r.routine_name
""",
}

out = {}
for name, dsn in [("ceayj", CEAYJ), ("goumlyne", GOUM)]:
    out[name] = {}
    for key, sql in SQL.items():
        try:
            out[name][key] = q(dsn, sql)
        except Exception as e:
            out[name][key] = {"__error__": str(e)}
    print(f"{name}: fetched", file=sys.stderr)

path = os.path.join(os.path.dirname(__file__), "raw.json")
with open(path, "w") as f:
    json.dump(out, f, default=str)
print("wrote", path, file=sys.stderr)
