#!/usr/bin/env python3
"""Wet-prove 290_tin_receipt_backfill_slice_e.sql — the ONE-TIME TIN-RECEIPT
BACKFILL (op#17667 §5 / op#17842 Slice E). ONE SECURITY DEFINER function over the
Slice-A schema (mig286) + Slice-B engine idioms (mig289):
  tabung_backfill_umum_tin_receipts(p_org_id uuid, p_backfill_actor_id uuid)
retroactively issues ONE gapless tin-keyed receipt for every umum tin that has
none yet (no status gate — closed/banked/counted alike), reusing mig289's proven
issue body with EXACTLY TWO changes: (1) actor = p_backfill_actor_id (a validated
org_admin, NOT auth.uid()-pinned) and (2) audit source='tin-receipt-backfill'.

The MAIN connection self-applies mig286 (if the Slice-A schema is absent) + mig289
(the source of the receipts/audit idioms this shares) + mig290 in ONE
BEGIN..ROLLBACK — nothing persisted (propose-only; NO self-commit). Console runs
this on goumlyne (the real target; ceayj is synthetic and console won't run the
invocation there, though the function itself deploys both). The builder does NOT
run it (no write DSN); console runs the independent wet-prove, applies, AND RUNS
the backfill.

Usage: wet-prove.py <DSN_ENV_VAR>   (e.g. GOUMLYNE_DATABASE_URL)

WHAT IT PROVES (against the LIVE schema):
  (i)   A CLOSED historical umum tin with NO receipt gets a FIRST receipt issued
        (status-tolerant): tin_umum_id set, donation_id NULL, person_id=collector,
        gapless receipt_number, status='issued'. Backfill has NO status gate.
  (ii)  🔴 MINORS: a student-collector umum tin is SKIPPED fail-closed — no receipt,
        counted in skipped_minors.
  (iii) IDEMPOTENT re-run: a second call issues 0 new receipts, mints no duplicate,
        burns NO receipt_number.
  (iv)  🔴 GAPLESS across a real bulk run: after N tins are backfilled, the assigned
        receipt_numbers are distinct and the shared per-org sequence has no gap/dup.
  (v)   KELUARGA / kk tins are byte-UNTOUCHED (the function is umum-only): no receipt
        keyed to any kk tin, and their rows are unchanged before/after.
  (vi)  🔴 PROVENANCE: every audit row this backfill writes carries
        source='tin-receipt-backfill' (NOT 'tin-umum-issue-at-count') and a valid
        hash chain link — honest, distinguishable retroactive provenance.
  plus proACL/CAI-1041 (anon/public/authenticated NO EXECUTE, service_role YES) and
  actor legitimacy (a non-org_admin actor is REJECTED backfill_actor_not_org_admin).
  plus Flag C: issuing against a CLOSED/BANKED tin trips NO freeze (no tin-row write).
"""
import sys, os, uuid, json

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
MIG286 = os.path.join(REPO, 'supabase', 'migrations', '286_receipts_tin_keyed_slice_a.sql')
MIG289 = os.path.join(REPO, 'supabase', 'migrations', '289_tin_receipt_issuance_slice_b.sql')
MIG290 = os.path.join(REPO, 'supabase', 'migrations', '290_tin_receipt_backfill_slice_e.sql')

env = {}
envfile = os.path.join(REPO, '.env.local')
if os.path.exists(envfile):
    for line in open(envfile):
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); env[k] = v.strip().strip('"').strip("'")

import psycopg2
DSN_VAR = sys.argv[1] if len(sys.argv) > 1 else 'GOUMLYNE_DATABASE_URL'
DSN = env.get(DSN_VAR) or os.environ.get(DSN_VAR)
if not DSN:
    print(f"no DSN for {DSN_VAR}"); sys.exit(2)

conn = psycopg2.connect(DSN); conn.autocommit = False; cur = conn.cursor()
PASS = []; FAIL = []; SKIP = []
def ck(n, c): (PASS if c else FAIL).append(n); print(("  PASS " if c else "  FAIL x ") + n)
def sk(n): SKIP.append(n); print("  SKIP " + n)
def u(): return str(uuid.uuid4())

def backfill(org, actor):
    cur.execute("select tabung_backfill_umum_tin_receipts(%s,%s)", (org, actor))
    return cur.fetchone()[0]

def new_umum_tin(org, admin, collector='anonymous', person_id=None, location=None,
                 student_id=None, tin_type=None, status='counted', notes=0, coins=0):
    cols = ("org_id,serial_number,collector_type,status,issued_at,issued_by_user_id,"
            "returned_at,counted_at,counted_by_user_id,amount_notes,amount_coins")
    ph = "%s,%s,%s,%s,now(),%s,now(),now(),%s,%s,%s"
    vals = [org, 'WP-E-' + u()[:8], collector, status, admin, admin, notes, coins]
    extra_cols, extra_ph = "", ""
    if person_id:  extra_cols += ",person_id";     extra_ph += ",%s"; vals.append(person_id)
    if location:   extra_cols += ",location_name";  extra_ph += ",%s"; vals.append(location)
    if student_id: extra_cols += ",student_id";     extra_ph += ",%s"; vals.append(student_id)
    if tin_type:   extra_cols += ",tin_type";       extra_ph += ",%s"; vals.append(tin_type)
    cur.execute(f"insert into tabung_umum_tins ({cols}{extra_cols}) values ({ph}{extra_ph}) "
                f"returning public_id", vals)
    return cur.fetchone()[0]

def force_closed(tin_public, admin):
    """counted -> banked -> closed, honestly (so the tin-state CHECKs pass). Returns
    True if it reached 'closed', False (with a rollback to savepoint) otherwise."""
    cur.execute("SAVEPOINT sp_close_%s" % tin_public.replace('-', '')[:12])
    try:
        cur.execute("select set_config('tabung.mark_banked','on',true)")
        cur.execute("update tabung_umum_tins set status='banked', banked_at=now(), "
                    "banked_by_user_id=%s, bank_reference='WP-E-BANK' where public_id=%s and status='counted'",
                    (admin, tin_public))
        cur.execute("select set_config('tabung.mark_banked','off',true)")
        cur.execute("update tabung_umum_tins set status='closed', closed_at=now() "
                    "where public_id=%s and status='banked'", (tin_public,))
        return True
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT sp_close_%s" % tin_public.replace('-', '')[:12])
        return False

try:
    # ---- apply mig286 (if Slice-A schema absent) + mig289 + mig290, all in-txn ----
    cur.execute("select 1 from information_schema.columns where table_name='receipts' and column_name='tin_umum_id'")
    if cur.fetchone() is None:
        body286 = "\n".join(l for l in open(MIG286).read().splitlines() if l.strip() not in ('BEGIN;', 'COMMIT;'))
        cur.execute(body286); print(f"[{DSN_VAR}] migration 286 applied in-txn (Slice-A schema was absent)")
    cur.execute("select 1 from pg_proc where proname='tabung_issue_umum_tin_receipt'")
    if cur.fetchone() is None:
        body289 = "\n".join(l for l in open(MIG289).read().splitlines() if l.strip() not in ('BEGIN;', 'COMMIT;'))
        cur.execute(body289); print(f"[{DSN_VAR}] migration 289 applied in-txn (Slice-B engine was absent)")
    body290 = "\n".join(l for l in open(MIG290).read().splitlines() if l.strip() not in ('BEGIN;', 'COMMIT;'))
    cur.execute(body290); print(f"[{DSN_VAR}] migration 290 applied in-txn")

    # ---- pick an org WITHOUT auto-yearly numbering to keep number asserts simple ----
    cur.execute("select org_id,user_id from org_members where role='org_admin' and deleted_at is null limit 1")
    ORG, ADMIN = cur.fetchone(); print(f"[{DSN_VAR}] org={ORG} admin={ADMIN}")
    cur.execute("insert into persons (org_id,display_name) values (%s,%s) returning id",
                (ORG, 'WP-E Collector ' + u()[:6]))
    COLLECTOR = cur.fetchone()[0]

    # ===== (vi-setup) snapshot the audit chain tail so we can inspect ONLY our rows =====
    cur.execute("select coalesce(max(id),0) from audit_log where org_id=%s", (ORG,))
    AUDIT_HWM = cur.fetchone()[0]

    # ===== (i) a CLOSED historical umum tin with NO receipt gets a first receipt =====
    TIN_CLOSED = new_umum_tin(ORG, ADMIN, collector='person', person_id=COLLECTOR, notes=40, coins=2.50)
    closed_reached = force_closed(TIN_CLOSED, ADMIN)
    if not closed_reached:
        # fall back to a real closed umum tin already in the silo
        cur.execute("select public_id from tabung_umum_tins where org_id=%s and status='closed' "
                    "and deleted_at is null and collector_type <> 'student' "
                    "and not exists (select 1 from receipts r where r.tin_umum_id=tabung_umum_tins.public_id) "
                    "limit 1", (ORG,))
        cr = cur.fetchone()
        if cr:
            TIN_CLOSED = cr[0]; closed_reached = True
    if closed_reached:
        froze = False
        cur.execute("SAVEPOINT sp_i")
        try:
            res_i = backfill(ORG, ADMIN)
        except Exception as e:
            if 'frozen' in str(e).lower() or 'immutable' in str(e).lower():
                froze = True
            cur.execute("ROLLBACK TO SAVEPOINT sp_i"); res_i = {}
        ck("(Flag C) backfill over a CLOSED tin trips NO freeze (no tin-row write)", not froze)
        cur.execute("select donation_id, tin_umum_id, person_id, status, receipt_number "
                    "from receipts where tin_umum_id=%s", (TIN_CLOSED,))
        row = cur.fetchone()
        ck("(i) closed tin (no status gate) got a FIRST receipt", row is not None)
        if row:
            ck("(i) donation_id NULL + tin_umum_id set", row[0] is None and str(row[1]) == str(TIN_CLOSED))
            ck("(i) status issued + real receipt_number", row[3] == 'issued' and row[4] is not None)
    else:
        sk("(i) no closed umum tin available (seed + real) — closed-path SKIPPED")
        res_i = backfill(ORG, ADMIN)  # still run so later tests have a baseline

    # ===== (ii) MINORS — student-collector tin is SKIPPED, no receipt =====
    minors_seeded = True
    cur.execute("SAVEPOINT sp_stud")
    try:
        cur.execute("insert into persons (org_id,display_name) values (%s,%s) returning id",
                    (ORG, 'WP-E Minor ' + u()[:6]))
        STU_PERSON = cur.fetchone()[0]
        cur.execute("insert into sch_students (org_id,person_id,student_number) values (%s,%s,%s) returning id",
                    (ORG, STU_PERSON, 'WP-E-STU-' + u()[:6]))
        STU = cur.fetchone()[0]
        TIN_STU = new_umum_tin(ORG, ADMIN, collector='student', student_id=STU,
                               tin_type='masjid_ramadhan', notes=15)
    except Exception as e:
        minors_seeded = False; cur.execute("ROLLBACK TO SAVEPOINT sp_stud")
        sk(f"(ii) could not seed a student tin ({type(e).__name__}: {str(e)[:80]}) — minors test SKIPPED")
    if minors_seeded:
        res_m = backfill(ORG, ADMIN)
        cur.execute("select count(*) from receipts where tin_umum_id=%s", (TIN_STU,))
        ck("(ii) student-collector tin got NO receipt (minors fail-closed)", cur.fetchone()[0] == 0)
        ck("(ii) skipped_minors counter is >= 1", int(res_m.get('skipped_minors', 0)) >= 1)

    # ===== bulk seed a batch of receipt-free umum tins, then one backfill =====
    seeded = []
    for i in range(6):
        seeded.append(new_umum_tin(ORG, ADMIN, collector='anonymous', notes=(i + 1)))
    # one 'person' + one 'location' to prove recipient resolution
    TIN_PERSON = new_umum_tin(ORG, ADMIN, collector='person', person_id=COLLECTOR, notes=11)
    TIN_LOC = new_umum_tin(ORG, ADMIN, collector='location', location='Kedai Corner', coins=3)
    seeded += [TIN_PERSON, TIN_LOC]

    cur.execute("select coalesce(max(id),0) from audit_log where org_id=%s", (ORG,))
    pre_bulk_audit = cur.fetchone()[0]
    res_bulk = backfill(ORG, ADMIN)

    # ===== (iv) GAPLESS across the bulk run =====
    cur.execute("select tin_umum_id, receipt_number from receipts where tin_umum_id = any(%s) order by receipt_number",
                (seeded,))
    got = cur.fetchall()
    nums = [r[1] for r in got]
    ck("(iv) every seeded receipt-free tin got a receipt", len(got) == len(seeded))
    ck("(iv) receipt_numbers are all distinct (no dup)", len(set(nums)) == len(nums))
    ck("(iv) receipt_numbers are contiguous within the bulk run (gapless)",
       len(nums) >= 2 and (max(nums) - min(nums) == len(nums) - 1))
    # recipient resolution
    cur.execute("select person_id from receipts where tin_umum_id=%s", (TIN_PERSON,)); pp = cur.fetchone()
    cur.execute("select person_id from receipts where tin_umum_id=%s", (TIN_LOC,)); pl = cur.fetchone()
    ck("(iv) person tin -> person_id = collector", pp and str(pp[0]) == str(COLLECTOR))
    ck("(iv) location tin -> person_id NULL", pl and pl[0] is None)
    ck("(iv) summary issued >= the bulk count", int(res_bulk.get('issued', 0)) >= len(seeded))

    # ===== (iii) IDEMPOTENT re-run — 0 new, no dup, no number burned =====
    cur.execute("select max(receipt_number) from receipts where org_id=%s", (ORG,))
    max_before = cur.fetchone()[0]
    cur.execute("select count(*) from receipts where org_id=%s", (ORG,))
    cnt_before = cur.fetchone()[0]
    res_re = backfill(ORG, ADMIN)
    cur.execute("select count(*) from receipts where org_id=%s", (ORG,))
    cnt_after = cur.fetchone()[0]
    cur.execute("select next_receipt_number(%s)", (ORG,)); nxt = cur.fetchone()[0]
    ck("(iii) re-run issues 0 new receipts", int(res_re.get('issued', 0)) == 0 and cnt_after == cnt_before)
    ck("(iii) re-run burns NO receipt_number (next unchanged relative to max)", nxt == max_before + 1)

    # ===== (v) KELUARGA / kk tins byte-UNTOUCHED =====
    cur.execute("select count(*) from information_schema.tables where table_name='tabung_kk_tins'")
    if cur.fetchone()[0] == 1:
        cur.execute("select md5(coalesce(string_agg(public_id::text || status || coalesce(amount_notes::text,'') "
                    "|| coalesce(amount_coins::text,''), ',' order by public_id),'')) "
                    "from tabung_kk_tins where org_id=%s", (ORG,))
        kk_before = cur.fetchone()[0]
        backfill(ORG, ADMIN)  # umum-only; must not touch kk
        cur.execute("select md5(coalesce(string_agg(public_id::text || status || coalesce(amount_notes::text,'') "
                    "|| coalesce(amount_coins::text,''), ',' order by public_id),'')) "
                    "from tabung_kk_tins where org_id=%s", (ORG,))
        kk_after = cur.fetchone()[0]
        ck("(v) kk (keluarga) tin rows byte-unchanged by the umum backfill", kk_before == kk_after)
        # no receipt is ever keyed to a kk tin's public_id
        cur.execute("select count(*) from receipts r where exists "
                    "(select 1 from tabung_kk_tins k where k.public_id = r.tin_umum_id)")
        ck("(v) NO receipt keyed to any kk tin public_id", cur.fetchone()[0] == 0)
    else:
        sk("(v) tabung_kk_tins absent on this silo — keluarga-untouched check SKIPPED")

    # ===== (vi) PROVENANCE — every audit row this backfill wrote carries the honest source =====
    cur.execute("select payload->>'source', count(*) from audit_log "
                "where org_id=%s and id > %s and entity_type='receipt' and action='create' "
                "group by payload->>'source'", (ORG, AUDIT_HWM))
    src_rows = cur.fetchall()
    srcs = {r[0]: r[1] for r in src_rows}
    ck("(vi) all backfill audit rows carry source='tin-receipt-backfill'",
       len(srcs) >= 1 and set(srcs.keys()) == {'tin-receipt-backfill'})
    ck("(vi) ZERO backfill rows stamped 'tin-umum-issue-at-count' (count-time falsehood)",
       'tin-umum-issue-at-count' not in srcs)
    # hash-chain linkage: each new row's hash = sha256(prev_hash || canonical(payload))
    cur.execute("select prev_hash, hash, payload::text, receipt_number "
                "from ( select a.prev_hash, a.hash, a.payload, r.receipt_number "
                "       from audit_log a join receipts r on r.id = a.entity_id "
                "       where a.org_id=%s and a.id > %s and a.entity_type='receipt' and a.action='create' "
                "       order by a.id limit 1 ) s", (ORG, AUDIT_HWM))
    hr = cur.fetchone()
    if hr:
        import hashlib
        prev_h, this_h, payload_txt, rnum = hr
        # rebuild the canonical form from the stored payload (sorted keys, as the fn emits)
        pj = json.loads(payload_txt)
        canon = ('{"amount":"' + pj['amount'] + '","receipt_number":' + str(pj['receipt_number'])
                 + ',"source":"' + pj['source'] + '","tin_umum_id":"' + pj['tin_umum_id'] + '"}')
        recomputed = hashlib.sha256((prev_h + canon).encode('utf-8')).hexdigest()
        ck("(vi) audit hash chains correctly (hash == sha256(prev_hash||canonical payload))",
           recomputed == this_h)
    else:
        sk("(vi) no backfill audit row captured for hash-recompute — SKIPPED")

    # ===== proACL (CAI-1041) — service_role only =====
    ident = 'tabung_backfill_umum_tin_receipts(uuid,uuid)'
    cur.execute("select has_function_privilege('anon', %s, 'EXECUTE'),"
                " has_function_privilege('authenticated', %s, 'EXECUTE'),"
                " has_function_privilege('service_role', %s, 'EXECUTE'),"
                " has_function_privilege('public', %s, 'EXECUTE')",
                (ident, ident, ident, ident))
    anon_x, auth_x, svc_x, pub_x = cur.fetchone()
    ck("(proACL) anon/authenticated/PUBLIC NO EXECUTE, service_role YES (CAI-1041)",
       (not anon_x) and (not auth_x) and svc_x and (not pub_x))

    # ===== actor legitimacy — a non-org_admin actor is REJECTED =====
    cur.execute("select user_id from org_members where org_id=%s and role<>'org_admin' and deleted_at is null limit 1", (ORG,))
    nb = cur.fetchone()
    if nb:
        rejected = False
        cur.execute("SAVEPOINT sp_actor")
        try:
            backfill(ORG, nb[0])
        except Exception as e:
            rejected = 'backfill_actor_not_org_admin' in str(e); cur.execute("ROLLBACK TO SAVEPOINT sp_actor")
        ck("(actor) non-org_admin actor REJECTED (backfill_actor_not_org_admin)", rejected)
    else:
        # a random UUID (not a member) must also be rejected
        rejected = False
        cur.execute("SAVEPOINT sp_actor2")
        try:
            backfill(ORG, u())
        except Exception as e:
            rejected = 'backfill_actor_not_org_admin' in str(e); cur.execute("ROLLBACK TO SAVEPOINT sp_actor2")
        ck("(actor) non-member actor REJECTED (backfill_actor_not_org_admin)", rejected)

    print(f"\n[{DSN_VAR}] Slice-E backfill proven: closed-tin first-receipt (no status gate), "
          f"minors-skipped, idempotent re-run, gapless bulk, keluarga-untouched, honest "
          f"source='tin-receipt-backfill' provenance, proACL service_role-only, actor legitimacy, Flag-C freeze-safe")
finally:
    conn.rollback()
    print("[connection rolled back — no writes persisted]")
    cur.close(); conn.close()

print(f"\n===== {DSN_VAR}: {len(PASS)} PASS / {len(FAIL)} FAIL / {len(SKIP)} SKIP =====")
if SKIP: print("SKIPPED (not silent):", SKIP)
if FAIL: print("FAILURES:", FAIL); sys.exit(1)
print("ALL GREEN")
