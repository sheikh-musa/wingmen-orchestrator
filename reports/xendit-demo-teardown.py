#!/usr/bin/env python3
"""
xendit-demo-teardown.py — restore wingmen-personal (brrgastul) to its ORIGINAL
state after the Mon 07-20 Xendit demo. Drops ONLY the ihsanos demo objects the
bootstrap added; PRESERVES the operator's personal data.

Implements reports/xendit-demo-teardown-manifest-20260718.md.

SAFETY:
  * DRY-RUN by default — prints the plan, executes NOTHING. Real run needs
    `--execute --i-understand-this-drops-the-demo`.
  * Refuses any known LIVE silo ref.
  * Drop-set is computed from LIVE metadata (public BASE TABLES minus the 5
    personal tables), so it also catches any synthetic tables. Each drop is
    hard-guarded to never target a personal table.
  * schema_migrations: deletes ONLY created_by='bootstrap-mgmt-api' (95 rows),
    preserving the 2 operator rows and the schema/table itself.

USAGE:
  SUPABASE_ACCESS_TOKEN=... python3 xendit-demo-teardown.py                 # dry-run
  SUPABASE_ACCESS_TOKEN=... python3 xendit-demo-teardown.py --self-test     # dry-run + prove the CASCADE-drop mechanism on a throwaway schema
  SUPABASE_ACCESS_TOKEN=... python3 xendit-demo-teardown.py --execute --i-understand-this-drops-the-demo
"""
import os, sys, json, urllib.request, urllib.error

REF = os.environ.get("SUPABASE_DEMO_PROJECT_REF", "brrgastulcffamlbggyu")
TOKEN = os.environ["SUPABASE_ACCESS_TOKEN"]
LIVE = {"ceayjeamtmcyzzvqflus", "goumlynecruxrlmzlntp", "tscuymavysscrvoberrr"}
if REF in LIVE:
    sys.exit(f"REFUSING: {REF} is a LIVE silo.")

EXECUTE = "--execute" in sys.argv and "--i-understand-this-drops-the-demo" in sys.argv
SELFTEST = "--self-test" in sys.argv

PERSONAL_TABLES = ["life_commitments", "life_entities", "life_relationships", "mamadah_notes", "mamadah_sources"]
BUCKETS = ["payment-proofs", "qurban-milestones", "receipts", "tabung-slips"]
# tier-2: ihsanos functions to drop (NEVER life_*, handle_new_user, update_updated_at, or pgvector fns)
IHSANOS_FUNCS = [
    "auth_user_child_person_ids", "auth_user_hr_employee_id", "auth_user_org_ids",
    "auth_user_org_ids_with_role", "auth_user_org_ids_with_roles", "auth_user_staff_org_ids",
    "auth_user_teacher_student_person_ids", "calculate_platform_fee", "donations_by_category",
    "donations_count", "donations_monthly_trend", "donations_sum", "donations_top_donors",
    "donations_unique_donor_count", "enforce_balanced_journal", "kk_batch_status_counts",
    "next_pos_transaction_number", "next_receipt_number", "place_storefront_order",
    "post_journal_atomic", "provision_tg_merchant_org", "sync_org_memberships_to_jwt",
    "tabung_banked_awaiting_deposit", "tabung_endorse_close_report", "tabung_kk_class_completion",
    "tabung_kk_top_students", "tabung_mark_banked_atomic", "tabung_report_attach_deposit",
    "tabung_report_close_guard", "tabung_report_deposit_ref_immutable",
    "tabung_report_preparer_id_immutable", "tabung_report_was_ever_signed_monotonic",
    "tabung_tin_bank_guard", "tg_append_identity_audit", "umum_status_counts", "umum_top_donors",
    "update_wingmen_features_updated_at",
]


def q(sql):
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=json.dumps({"query": sql}).encode(), method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "User-Agent": "curl/8.4.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def live_public_tables():
    st, rows = q("select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by table_name")
    assert st in (200, 201), rows
    return [r["table_name"] for r in rows]


print(f"== teardown target ref={REF}  mode={'EXECUTE' if EXECUTE else 'DRY-RUN'} ==\n")

# 1. compute drop-set
tables = live_public_tables()
drop_set = [t for t in tables if t not in PERSONAL_TABLES]
preserved = [t for t in tables if t in PERSONAL_TABLES]

# 2. hard invariants
assert set(PERSONAL_TABLES).issubset(set(tables)), "a personal table is MISSING — abort"
assert not (set(drop_set) & set(PERSONAL_TABLES)), "drop-set intersects personal tables — abort"
print(f"[tables] live public = {len(tables)}  | DROP = {len(drop_set)}  | PRESERVE = {sorted(preserved)}")
if sorted(preserved) != sorted(PERSONAL_TABLES):
    sys.exit("PRESERVE set != the 5 personal tables — abort")

# invariant snapshot
st, inv = q("select (select count(*) from mamadah_notes) mn, "
            "(select count(*) from supabase_migrations.schema_migrations where created_by='sheikh.musa@outlook.com') op, "
            "(select count(*) from supabase_migrations.schema_migrations where created_by='bootstrap-mgmt-api') bs")
print(f"[preserve invariants] mamadah_notes={inv[0]['mn']}  operator_tracker_rows={inv[0]['op']}  bootstrap_rows_to_delete={inv[0]['bs']}")

# 3. build DDL
table_ddl = [f'DROP TABLE IF EXISTS public."{t}" CASCADE;' for t in drop_set]
func_ddl = [f'DROP FUNCTION IF EXISTS public."{f}" CASCADE;' for f in IHSANOS_FUNCS]
tracker_ddl = "DELETE FROM supabase_migrations.schema_migrations WHERE created_by='bootstrap-mgmt-api';"
bucket_ddl = [f"DELETE FROM storage.objects WHERE bucket_id='{b}'; DELETE FROM storage.buckets WHERE id='{b}';" for b in BUCKETS]

print(f"\n[plan] {len(table_ddl)} table drops, {len(func_ddl)} function drops (tier-2), 1 tracker delete, {len(BUCKETS)} bucket removals")
print("\n--- sample DDL (first 3 tables) ---")
for s in table_ddl[:3]:
    print("  " + s)
print("  ...")
print("  " + tracker_ddl)

# 4. self-test: prove the CASCADE-drop pattern executes on a THROWAWAY schema (zero risk to public)
if SELFTEST:
    print("\n[self-test] proving DROP ... CASCADE on a throwaway schema (public untouched)...")
    st, _ = q("create schema if not exists teardown_dryrun; "
              "create table teardown_dryrun.parent(id int primary key); "
              "create table teardown_dryrun.child(id int primary key, pid int references teardown_dryrun.parent(id));")
    st2, chk = q("select count(*) c from information_schema.tables where table_schema='teardown_dryrun'")
    st3, _ = q('DROP TABLE IF EXISTS teardown_dryrun."parent" CASCADE; DROP TABLE IF EXISTS teardown_dryrun."child" CASCADE;')
    st4, chk2 = q("select count(*) c from information_schema.tables where table_schema='teardown_dryrun'")
    q("drop schema if exists teardown_dryrun cascade;")
    ok = st in (200, 201) and st3 in (200, 201) and chk[0]["c"] == 2 and chk2[0]["c"] == 0
    print(f"  created 2 FK-linked tables -> {chk[0]['c']}, CASCADE-dropped -> {chk2[0]['c']}  => mechanism {'OK' if ok else 'FAILED'}")

# 5. execute or predict
if not EXECUTE:
    remaining = [t for t in tables if t in PERSONAL_TABLES]
    print(f"\n[DRY-RUN] would drop {len(drop_set)} tables + {len(func_ddl)} funcs + {len(BUCKETS)} buckets + {inv[0]['bs']} tracker rows.")
    print(f"[DRY-RUN] PREDICTED post-state: public BASE TABLES = {sorted(remaining)} (={len(remaining)}); mamadah_notes stays {inv[0]['mn']}; operator tracker rows stay {inv[0]['op']}.")
    print("[DRY-RUN] nothing executed. Re-run with --execute --i-understand-this-drops-the-demo AFTER the Monday call.")
    sys.exit(0)

# ---- REAL EXECUTION (post-Monday) ----
print("\n[EXECUTE] dropping demo objects...")
for s in table_ddl:
    t = s.split('"')[1]
    if t in PERSONAL_TABLES:
        sys.exit(f"GUARD: refusing to drop personal table {t}")
    st, r = q(s)
    if st not in (200, 201):
        print(f"  [warn] {s} -> {st} {str(r)[:120]}")
q(tracker_ddl)
for b in bucket_ddl:
    q(b)
for s in func_ddl:
    q(s)
# verify
st, after = q("select array_agg(table_name order by table_name) t from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")
st2, mn = q("select count(*) c from mamadah_notes")
print(f"[EXECUTE] done. public now: {after[0]['t']}")
print(f"[EXECUTE] mamadah_notes rows: {mn[0]['c']} (expect 33)")
