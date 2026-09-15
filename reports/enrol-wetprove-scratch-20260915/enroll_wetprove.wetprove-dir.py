#!/usr/bin/env python3
"""REAL-PG SYNTHETIC WET-PROVE — sch enroll name+class MATCH-AND-ENRICH (mig337).

Proves the enroll match-and-enrich migration (mig337, commit fb9eacd6; op#39942 /
op#39983, "MINORS-PATH") is SAFE on real Postgres BEFORE it touches a real student,
using a fabricated synthetic org + fabricated students/BCs/classes, all inside a
single BEGIN..ROLLBACK. NOTHING persists — conn.commit() is NEVER called.

WHY THIS EXISTS (and why it is NOT vitest)
------------------------------------------
The vitest enroll suite MOCKS the RPC (no real Postgres). The dangerous, load-
bearing behaviours of this feature live INSIDE a SECURITY DEFINER plpgsql RPC and
in the revert function — enrich-by-target COALESCE never-clobber, the DUAL revert
semantics (created→delete, backfilled→value-aware RESTORE), proACL, caller fail-
closed. Those CANNOT be proven by a mocked RPC. They must be proven against a real
synthetic DB. That is this harness. (Per op#39983 §C / console #40008: RPC-level
proofs MOVE to the real-PG both-silo wet-prove; do NOT fake them in vitest.)

THE LOCKED CONTRACT (mig337, verified at source fb9eacd6 — see RPC_CONTRACT below)
---------------------------------------------------------------------------------
  * Enrich + create flow through ONE function:
      public.sch_bulk_enroll_students(p_org_id uuid, p_rows jsonb,
                                      p_commit boolean DEFAULT false,
                                      p_import_batch_id uuid DEFAULT NULL)
    Each p_rows element: {fields:{display_name,gender,date_of_birth,address,status,
      enrollment_date,class_name,+Tier-A}, nric_encrypted, nric_hash_v2,
      target_student_number?}.
      - target_student_number NON-empty → ENRICH-BY-TARGET: resolve the LIVE
        student in p_org_id by student_number → never-clobber COALESCE-fill the
        whitelist → stamp import_batch_id + import_batch_role='backfilled' +
        import_batch_enriched_fields (the jsonb filled-set). NEVER creates.
      - target_student_number absent/empty → the existing BC-hash classify/create
        path (role='created' when it creates under a batch id).
    (NOTE: coord's contract-lock message paraphrased the 3rd param as `p_dry_run`;
     the source is `p_commit boolean DEFAULT false` — same position/type, and
     p_commit=true COMMITS while p_commit=false is the dry-run. Bound to source.)
  * Dual-revert: public.sch_reconcile_revert(p_org_id uuid,
      p_reconciliation_ref uuid, p_commit boolean DEFAULT false).
      - role='created'    → SOFT-DELETE the created rows (deleted_at set; the row
        survives as a tombstone — never a hard delete).
      - role='backfilled' → RESTORE: value-aware for NEW rows (re-NULL EXACTLY the
        recorded import_batch_enriched_fields filled-set), mig330 fallback for
        LEGACY rows (filled-set IS NULL). The pre-existing student is NEVER deleted;
        pre-existing NON-NULL values are left intact.
  * Additive nullable column import_batch_enriched_fields jsonb on persons AND
    sch_students (ADD COLUMN IF NOT EXISTS).

WHAT IT PROVES (each an individual PASS/FAIL line; fabricated data, rolled back):
  1. SETUP — a synthetic org with NAME-ONLY existing students (display_name +
     student_number + class_id; NO nric/BC, NO DOB, NO Tier-A), mirroring the real
     ~889 name-only cohort.
  2. ENRICH-BY-TARGET + NEVER-CLOBBER — a file row whose name+class matches an
     existing name-only student is resolved (app-side) to that student_number and
     the RPC ENRICHES that record across the whitelist; a pre-existing NON-NULL
     field is NEVER overwritten by a file value; the filled-set is recorded.
  3. NAME+CLASS → 3 BUCKETS — exact-name+class → auto-enrich; name-match/class-
     differ → REVIEW (not written); file-side name-collision → REVIEW (not
     written); non-match → create-new ONLY on an explicit decision.
  4. DUAL REVERT SEMANTICS (the non-negotiable proof, console #40008) —
        role='created'    → the row is DELETED (soft-delete tombstone);
        role='backfilled' → the enriched record's pre-enrich values are RESTORED
                            (exact snapshot match) and the pre-existing student is
                            NOT deleted, with its original non-null values intact;
        revert is batch-scoped (a 2nd batch is untouched);
        empty / nonexistent batch = safe no-op;
        audit hash-chain intact + appended after revert.
  5. proACL — the enroll + revert RPCs have EXECUTE revoked from anon/public/
     service_role (granted only to authenticated), live and at call time.
  6. CALLER-ROLE FAIL-CLOSED — a non-authorized caller/role (cashier; org_admin of
     a DIFFERENT org) is REJECTED (42501); the SECURITY DEFINER fn enforces org-
     scoping + caller-authz internally and does not widen access to minors' PII.
  7. CONVERGENCE — re-running the same file converges (no duplicate persons, no
     orphans).

HOW TO RUN (coord/console, per silo — the builder has NO write DSN):
    # Against a SCRATCH / SYNTHETIC database ONLY. NEVER a production DSN, NEVER a
    # *_RO_* DSN (those point at real students). The name-guard below refuses those.
    export SCRATCH_DATABASE_URL='postgres://...@host:5432/ihsanos_scratch'
    python3 scripts/wetprove/sch-enroll-merge-wetprove.py SCRATCH_DATABASE_URL

    # Both silos: run once per silo's synthetic/scratch DB env var, e.g.
    #   python3 ... GOUMLYNE_SCRATCH_DATABASE_URL
    #   python3 ... CEAYJ_SCRATCH_DATABASE_URL

  Exit 0 = all assertions passed. Exit 1 = at least one FAIL (list printed).

  Prereqs on the scratch DB: the CORE schema (organizations / persons /
  sch_students / person_roles / sch_classes / audit_log / org_members) + the
  auth_user_org_ids_with_roles helper + write_audit_log_secure + >=2 auth.users
  rows (reused as synthetic identities). The enroll migrations are applied by this
  harness INSIDE the rolled-back txn (mig329 for the base import_batch_id/role
  columns + base fn, then the REAL mig337 bytes from fb9eacd6 which CoR-replace the
  fn and create the value-aware revert fn), so the harness is self-contained and
  the migration is never persisted here.

RECONCILE-POINTS (all isolated in RPC_CONTRACT — a name/sig change is a 1-line edit)
  * If mig337 is later renumbered or its commit changes, update
    RPC_CONTRACT["migrations"] (the git_ref / file). Once mig337 is merged to disk,
    the on-disk file is used automatically (git_ref is the fallback).
  * If a signature changes, edit the *_sig / *_sql / build_*_params for that fn.

RESIDENCY: CORE-schema only (both silos, goumlyne + ceayj). RPC-only feature; the
whitelist columns (persons.address + sch_students.status + mig318 Tier-A +
mig329 import_batch_id/role + mig337 import_batch_enriched_fields) all exist on
both silos.

CAI / Satr: fabricated names + fabricated ("T-series") BCs only. No real student
data ever enters this harness, its transcript, or its logs. Rolled back.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid

import psycopg2

# =============================================================================
# 🔧🔧 RPC_CONTRACT — THE ONE RECONCILE BLOCK 🔧🔧
# -----------------------------------------------------------------------------
# Bound to mig337 (fb9eacd6), verified at source. If the migration is renumbered,
# recommitted, or a signature changes, edit ONLY this dict + the build_*_params
# helpers below. Every proof reads names/sigs/whitelist from here.
# =============================================================================
RPC_CONTRACT = {
    # ── Migrations applied (stripped of BEGIN;/COMMIT;) inside the rolled-back txn.
    #    Order matters: 329 adds the import_batch_id/role columns + the base fn;
    #    337 CoR-replaces sch_bulk_enroll_students AND creates sch_reconcile_revert
    #    (complete bodies) + adds the import_batch_enriched_fields column. mig330 is
    #    intentionally NOT applied: mig337 supersedes its revert fn with a full
    #    CREATE OR REPLACE, and mig330's other fn (sch_reconcile_enroll, unused
    #    here) would drag in mig325/mig320 deps — a check_function_bodies risk on a
    #    bare scratch DB. Each entry loads from disk if present, else via
    #    `git show <git_ref>:<git_path>` (mig337 is committed on the feature branch,
    #    not on this harness's base — the object store is shared).
    "migrations": [
        {"file": "329_sch_bulk_enroll_students.sql"},
        {"file": "337_sch_enroll_match_enrich_by_target.sql",
         "git_ref": "fb9eacd6",
         "git_path": "supabase/migrations/337_sch_enroll_match_enrich_by_target.sql"},
    ],

    # ── The enroll RPC (enrich-by-target + create in ONE call; per-row branch on
    #    target_student_number). Source sig: (uuid, jsonb, boolean, uuid).
    "enroll_name": "sch_bulk_enroll_students",
    "enroll_sig": "sch_bulk_enroll_students(uuid,jsonb,boolean,uuid)",
    "enroll_sql": (
        "select * from sch_bulk_enroll_students("
        "%(org)s::uuid, %(rows)s::jsonb, %(commit)s::boolean, %(batch)s::uuid)"
    ),

    # ── The revert RPC. Source sig: (uuid, uuid, boolean).
    "revert_name": "sch_reconcile_revert",
    "revert_sig": "sch_reconcile_revert(uuid,uuid,boolean)",
    "revert_sql": (
        "select * from sch_reconcile_revert("
        "%(org)s::uuid, %(ref)s::uuid, %(commit)s::boolean)"
    ),

    # ── Caller-authz: the role the RPC requires the caller to hold IN the org.
    "required_role": "org_admin",

    # ── proACL: EXECUTE must be denied to these, granted to this.
    "proacl_denied": ["anon", "service_role"],
    "proacl_granted": "authenticated",

    # ── ENRICH WHITELIST (mig337). The columns the enrich-by-target path fills
    #    (never-clobber). Split by table. display_name / student_number are the
    #    match keys (never enriched). status is NOT NULL on every student → its
    #    COALESCE is always a no-op → never enters the filled-set → treated as a
    #    never-clobber-only column below (the harness pre-sets it to 'active').
    "enrich_persons_cols": [
        "date_of_birth", "gender", "address",
        "nric_encrypted", "nric_hash_v2", "nric_source",
    ],
    "enrich_students_cols": [
        "class_id", "enrollment_date", "status",
        "house", "citizenship", "nationality", "country_of_birth", "race",
        "fee_code", "admission_year", "sibling_count", "withdrawal_date",
        "emergency_contact_name", "emergency_contact_number",
        "emergency_contact_email", "emergency_contact_relationship",
        "emergency_contact_note",
    ],
}


def build_enroll_params(org, rows, commit, batch):
    """Map (rows, commit, batch) onto the enroll RPC's named placeholders.
    RECONCILE: edit alongside RPC_CONTRACT['enroll_sql']."""
    return {"org": org, "rows": json.dumps(rows), "commit": commit, "batch": batch}


def build_revert_params(org, ref, commit):
    return {"org": org, "ref": ref, "commit": commit}


# =============================================================================
# Boilerplate — connection, PASS/FAIL, role/JWT impersonation (mig311 pattern)
# =============================================================================
DSN_VAR = sys.argv[1] if len(sys.argv) > 1 else "SCRATCH_DATABASE_URL"
# Guard-rail: refuse an obviously read-only or production DSN var name. A wet-prove
# runs writes (rolled back); an RO DSN fails mid-run and a prod DSN must never be
# touched even in a rolled-back txn. Checked BEFORE connecting.
if re.search(r"(_RO_|READONLY|READ_ONLY|PROD)", DSN_VAR, re.IGNORECASE):
    raise SystemExit(
        f"Refusing to run against {DSN_VAR}: name looks read-only/production. "
        f"Use a dedicated scratch/synthetic DB DSN — never real students."
    )
if DSN_VAR not in os.environ:
    raise SystemExit(
        f"{DSN_VAR} not set. Point it at a SCRATCH/SYNTHETIC DB only — NEVER a "
        f"production or *_RO_* DSN (those carry real students)."
    )

conn = psycopg2.connect(os.environ[DSN_VAR])
conn.autocommit = False
cur = conn.cursor()
PASS, FAIL = [], []
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def ck(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  PASS   " if cond else "  FAIL x ") + name)


def u():
    return str(uuid.uuid4())


def fake_bc(tag):
    """A fabricated ('T-series') BC → (ciphertext blob, v2 hash). NEVER a real BC.
    The RPC stores blobs opaquely; the exact values are irrelevant to the proof."""
    enc = "CIPHERTEXT-FAKE-" + tag
    h = ("%064x" % (abs(hash("FAKE-BC-" + tag)) % (16 ** 64)))
    return enc, h


# -----------------------------------------------------------------------------
# Hash-chain verifier — a byte-for-byte MIRROR of src/shared/lib/hashchain.ts, so
# [4e] checks the chain the SAME way the real system's verifier does (NOT via the
# jsonb-spaced/jsonb-key-order payload::text, which never equals the hashed input).
#   canonicalPayloadJson (hashchain.ts L11-13):
#     JSON.stringify(payload, Object.keys(payload).sort())
#     → keys sorted ASCII-ascending; NO `space` arg ⇒ spaceless (separators ',' ':');
#       JS default leaves non-ASCII raw ⇒ ensure_ascii=False.
#     (These audit payloads are FLAT — string / int / array-of-string values, no
#      nested objects — so the replacer-array allowlist has no filtering effect and
#      json.dumps(sort_keys=True) reproduces the exact bytes.)
#   computeHash (hashchain.ts L19-22):
#     sha256(prevHash + canonicalPayloadJson(payload)) hex (plain concatenation).
# -----------------------------------------------------------------------------
def canonical_payload_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(prev_hash, obj):
    return hashlib.sha256((prev_hash + canonical_payload_json(obj)).encode("utf-8")).hexdigest()


def set_authenticated(uid):
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute(
        "SELECT set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": uid, "role": "authenticated"}),),
    )


def reset_role():
    cur.execute("RESET ROLE")
    cur.execute("SELECT set_config('request.jwt.claims', '', true)")


def expect_raise(fn):
    """Run fn() in a savepoint; return the exception message (or a sentinel if no
    exception); always roll the savepoint back so nothing persists."""
    cur.execute("savepoint sp")
    try:
        fn()
        msg = "<<NO EXCEPTION RAISED>>"
    except Exception as e:  # noqa: BLE001
        msg = str(e)
    finally:
        cur.execute("rollback to savepoint sp")
    return msg


def _fetch_jsonb_result():
    """Both RPCs `RETURN jsonb` (scalar), so `select * from fn(...)` yields a SINGLE
    column named after the function whose value is the jsonb result (psycopg2 auto-
    parses jsonb → dict). Unwrap it; fall back to a column-map if a future contract
    returns a composite row instead."""
    row = cur.fetchone()
    if len(cur.description) == 1:
        return row[0]
    return dict(zip([d.name for d in cur.description], row))


def call_enroll(org, rows, commit, batch):
    cur.execute(RPC_CONTRACT["enroll_sql"], build_enroll_params(org, rows, commit, batch))
    return _fetch_jsonb_result()


def call_revert(org, ref, commit):
    cur.execute(RPC_CONTRACT["revert_sql"], build_revert_params(org, ref, commit))
    return _fetch_jsonb_result()


# -----------------------------------------------------------------------------
# Classifier — a documented MIRROR of the app's name+class exact classifier.
# RECONCILE: sch-bulk-enroll(-shared).ts owns the authoritative classifier; this
# mirror encodes its contract (normalize = trim + collapse-whitespace + case-fold
# ONLY; no fuzzy) so the harness can route rows to the 3 buckets and prove what the
# RPC does with each. If the app's normalization changes, mirror it here.
# -----------------------------------------------------------------------------
def norm(name):
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def classify(file_rows, existing):
    """file_rows: [{name, class, fields, bc_tag, decision?}]
    existing: {norm_name: [(student_number, norm_class), ...]}
    Returns (auto_enrich, review, create).
      auto_enrich: name matches EXACTLY-ONE existing AND class matches → target set
      review:      class-differ | file-side name collision | non-match w/o decision
      create:      non-match WITH an explicit decision='create'
    """
    counts = {}
    for r in file_rows:
        counts[norm(r["name"])] = counts.get(norm(r["name"]), 0) + 1

    auto, review, create = [], [], []
    for r in file_rows:
        nn = norm(r["name"])
        if counts[nn] > 1:
            review.append(("file-collision", r))
            continue
        matches = existing.get(nn, [])
        if len(matches) == 1:
            sn, ncls = matches[0]
            if ncls == norm(r["class"]):
                rr = dict(r)
                rr["target_student_number"] = sn
                auto.append(rr)
            else:
                review.append(("class-differ", r))
        elif len(matches) == 0:
            if r.get("decision") == "create":
                create.append(r)
            else:
                review.append(("non-match", r))
        else:
            review.append(("ambiguous-existing", r))
    return auto, review, create


def enrich_prow(r):
    """Shape one auto-enrich file row into an enroll p_rows element WITH a resolved
    target_student_number → the RPC takes the enrich-by-target branch."""
    enc, h = fake_bc(r["bc_tag"])
    fields = dict(r["fields"])
    fields["display_name"] = r["name"]
    fields["class_name"] = r["class"]
    return {
        "target_student_number": r["target_student_number"],
        "nric_encrypted": enc,
        "nric_hash_v2": h,
        "fields": fields,
    }


def create_prow(r):
    """Shape one create file row into an enroll p_rows element WITHOUT a target →
    the RPC takes the BC-hash classify/create branch."""
    enc, h = fake_bc(r["bc_tag"])
    fields = dict(r["fields"])
    fields["display_name"] = r["name"]
    fields["class_name"] = r["class"]
    return {"nric_encrypted": enc, "nric_hash_v2": h, "fields": fields}


# =============================================================================
# Apply the migration(s) inside the rolled-back txn (self-contained).
# =============================================================================
def _load_migration(entry):
    disk = os.path.join(REPO_ROOT, "supabase", "migrations", entry["file"])
    if os.path.exists(disk):
        with open(disk, "r") as f:
            return f.read(), f"disk:{entry['file']}"
    if entry.get("git_ref"):
        out = subprocess.check_output(
            ["git", "-C", REPO_ROOT, "show", f"{entry['git_ref']}:{entry['git_path']}"],
            text=True,
        )
        return out, f"git:{entry['git_ref']}:{entry['git_path']}"
    raise SystemExit(f"migration {entry['file']} not found on disk and no git_ref given")


def apply_migrations():
    for entry in RPC_CONTRACT["migrations"]:
        sql, src = _load_migration(entry)
        # Strip the strippable BEGIN;/COMMIT; wrapper (anchored on standalone lines
        # so the literal "BEGIN;" inside header comments is not matched) — the outer
        # ROLLBACK owns the transaction boundary; we run the body in this txn.
        m = re.search(r"^\s*BEGIN;\s*$", sql, flags=re.MULTILINE)
        body = sql[m.start():] if m else sql
        body = re.sub(r"^\s*BEGIN;\s*$", "", body, flags=re.MULTILINE)
        body = re.sub(r"^\s*COMMIT;\s*$", "", body, flags=re.MULTILINE)
        cur.execute(body)
        print(f"  applied {src}")


# =============================================================================
# Small read helpers (service_role context).
# =============================================================================
def col(table, pk, cols):
    cur.execute(f"select {', '.join(cols)} from {table} where id = %s", (pk,))
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else None


def student_row_by_number(org, number, cols):
    cur.execute(
        f"select {', '.join(cols)} from sch_students "
        f"where org_id = %s and student_number = %s",
        (org, number),
    )
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else None


try:
    print(f"[{DSN_VAR}] applying enroll migrations inside the rolled-back txn:")
    apply_migrations()

    # Reuse real auth.users ids as synthetic identities (their profiles exist).
    cur.execute("select id from auth.users order by created_at limit 2")
    uids = [r[0] for r in cur.fetchall()]
    ck("[setup] found >=2 real auth.users ids to reuse as synthetic identities",
       len(uids) >= 2)
    if len(uids) < 2:
        raise SystemExit("need >=2 auth.users on this DB to impersonate; aborting")
    U_ADMIN, U_CASHIER = uids[0], uids[1]

    cur.execute("set local role service_role")

    ORG = u()
    cur.execute("insert into organizations(id,name,type) values (%s,%s,'madrasah')",
                (ORG, "WETPROVE enroll-merge org (ROLLED BACK)"))
    cur.execute("insert into org_members(id,org_id,user_id,role,accepted_at) "
                "values (%s,%s,%s,%s,now())", (u(), ORG, U_ADMIN, RPC_CONTRACT["required_role"]))
    cur.execute("insert into org_members(id,org_id,user_id,role,accepted_at) "
                "values (%s,%s,%s,'cashier',now())", (u(), ORG, U_CASHIER))

    CLS_1A, CLS_2B = u(), u()
    cur.execute("insert into sch_classes(id,org_id,name) values (%s,%s,'Class 1A')", (CLS_1A, ORG))
    cur.execute("insert into sch_classes(id,org_id,name) values (%s,%s,'Class 2B')", (CLS_2B, ORG))

    # ── NAME-ONLY existing students (mirror the real 889): display_name +
    #    student_number + class_id; NO nric/BC, NO DOB, NO Tier-A.
    def make_name_only(name, number, class_id, person_extra=None, stu_extra=None):
        pid, sid = u(), u()
        pcols = "id,org_id,display_name,is_active"
        pvals = [pid, ORG, name, True]
        for k, v in (person_extra or {}).items():
            pcols += "," + k
            pvals.append(v)
        cur.execute(f"insert into persons({pcols}) values ({','.join(['%s'] * len(pvals))})", pvals)
        scols = "id,org_id,person_id,student_number,class_id,status"
        svals = [sid, ORG, pid, number, class_id, "active"]
        for k, v in (stu_extra or {}).items():
            scols += "," + k
            svals.append(v)
        cur.execute(f"insert into sch_students({scols}) values ({','.join(['%s'] * len(svals))})", svals)
        cur.execute("insert into person_roles(id,org_id,person_id,role_type,is_active) "
                    "values (%s,%s,%s,'student',true)", (u(), ORG, pid))
        return pid, sid

    P1, S1 = make_name_only("Aiman Test", "STU-101", CLS_1A)          # enrich target
    P2, S2 = make_name_only("Balqis Test", "STU-102", CLS_1A)         # class-differ + batch2
    P3, S3 = make_name_only("Cahaya Test", "STU-103", CLS_1A,         # never-clobber target
                            person_extra={"gender": "male"},
                            stu_extra={"house": "Amin"})
    P4, S4 = make_name_only("Delima Test", "STU-104", CLS_1A)         # convergence target
    reset_role()

    cur.execute("set local role service_role")
    ck("[1 SETUP] S1 is name-only: nric_encrypted/nric_hash_v2/date_of_birth all NULL",
       col("persons", P1, ["nric_encrypted", "nric_hash_v2", "date_of_birth"])
       == {"nric_encrypted": None, "nric_hash_v2": None, "date_of_birth": None})
    ck("[1 SETUP] S1 has a class_id + student_number (name-only but classed, like the 889)",
       student_row_by_number(ORG, "STU-101", ["class_id", "student_number"])
       == {"class_id": CLS_1A, "student_number": "STU-101"})
    reset_role()

    print(f"[{DSN_VAR}] org={ORG} admin={str(U_ADMIN)[:8]} cashier={str(U_CASHIER)[:8]}")

    # ── The fabricated import file. Rows exercise all 3 buckets. ──────────────
    full_fields = {
        "date_of_birth": "2015-03-04", "gender": "female",
        "address": "1 Fabricated Lane #01-01",
        "enrollment_date": "2026-01-06", "status": "active",
        "house": "Siddiq", "citizenship": "SG", "nationality": "Singaporean",
        "country_of_birth": "Singapore", "race": "Malay", "fee_code": "FC-A",
        "admission_year": "2026", "sibling_count": "2", "withdrawal_date": "2026-06-30",
        "emergency_contact_name": "EC Name", "emergency_contact_number": "90000000",
        "emergency_contact_email": "ec@example.test",
        "emergency_contact_relationship": "parent",
        "emergency_contact_note": "note",
    }
    file_rows = [
        {"name": "Aiman Test", "class": "Class 1A", "bc_tag": "A", "fields": dict(full_fields)},
        {"name": "Balqis Test", "class": "Class 2B", "bc_tag": "B", "fields": dict(full_fields)},
        {"name": "Danish Test", "class": "Class 1A", "bc_tag": "C1", "fields": dict(full_fields)},
        {"name": "  danish   TEST ", "class": "Class 1A", "bc_tag": "C2", "fields": dict(full_fields)},
        {"name": "Ezra New", "class": "Class 1A", "bc_tag": "D", "fields": dict(full_fields), "decision": "create"},
        {"name": "Farah Undecided", "class": "Class 1A", "bc_tag": "E", "fields": dict(full_fields)},
        {"name": "Cahaya Test", "class": "Class 1A", "bc_tag": "F", "fields": dict(full_fields)},
    ]
    existing = {
        norm("Aiman Test"): [("STU-101", norm("Class 1A"))],
        norm("Balqis Test"): [("STU-102", norm("Class 1A"))],
        norm("Cahaya Test"): [("STU-103", norm("Class 1A"))],
    }
    auto, review, create = classify(file_rows, existing)

    # ── (3) 3-BUCKET routing assertions (app-side classifier contract). ──────
    auto_targets = sorted(r["target_student_number"] for r in auto)
    review_reasons = sorted(reason for reason, _ in review)
    ck("[3 BUCKETS] AUTO = exact name+class → S1 (STU-101) and S3 (STU-103) only",
       auto_targets == ["STU-101", "STU-103"])
    ck("[3 BUCKETS] REVIEW = class-differ + file-collision(x2) + non-match(undecided)",
       review_reasons == ["class-differ", "file-collision", "file-collision", "non-match"])
    ck("[3 BUCKETS] CREATE = non-match WITH explicit decision only (Ezra New)",
       [r["name"] for r in create] == ["Ezra New"])

    # ONE enroll call carries enrich rows (target set) + create rows (no target).
    prows = [enrich_prow(r) for r in auto] + [create_prow(r) for r in create]

    # Snapshot S1 (backfill target) BEFORE enrich for the exact-restore proof.
    ALL_P_COLS = RPC_CONTRACT["enrich_persons_cols"]
    ALL_S_COLS = RPC_CONTRACT["enrich_students_cols"]
    cur.execute("set local role service_role")
    S1_person_before = col("persons", P1, ALL_P_COLS)
    S1_student_before = student_row_by_number(ORG, "STU-101", ALL_S_COLS)
    cur.execute("select count(*) from persons where org_id=%s", (ORG,))
    (persons_before,) = cur.fetchone()
    reset_role()

    # ── COMMIT the enroll (create + enrich-by-target) as the org_admin. ──────
    REF = u()
    set_authenticated(U_ADMIN)
    res = call_enroll(ORG, prows, True, REF)
    reset_role()
    ck("[2/3] enroll commit reports created=1 (Ezra) and enriched>=1",
       int(res.get("created") or 0) == 1 and int(res.get("enriched") or 0) >= 1)

    # ── (2) ENRICH-BY-TARGET whitelist — S1 filled; never-clobber on pre-set. ─
    cur.execute("set local role service_role")
    S1_person_after = col("persons", P1, ALL_P_COLS)
    S1_student_after = student_row_by_number(ORG, "STU-101", ALL_S_COLS)
    for c in ALL_P_COLS:
        before = S1_person_before.get(c)
        after = S1_person_after.get(c)
        if before is None:
            ck(f"[2 ENRICH persons.{c}] previously-NULL → filled by enrich-by-target",
               after is not None)
        else:
            ck(f"[2 NEVER-CLOBBER persons.{c}] pre-existing value preserved", after == before)
    for c in ALL_S_COLS:
        before = S1_student_before.get(c)
        after = S1_student_after.get(c)
        if before is None:
            ck(f"[2 ENRICH sch_students.{c}] previously-NULL → filled by enrich-by-target",
               after is not None)
        else:
            # class_id + status were pre-set on the name-only student → never-clobber.
            ck(f"[2 NEVER-CLOBBER sch_students.{c}] pre-existing value preserved", after == before)
    # The filled-set was recorded (mig337 import_batch_enriched_fields).
    cur.execute("select import_batch_enriched_fields from persons where id=%s", (P1,))
    (p1_filled,) = cur.fetchone()
    ck("[2 ENRICH] persons.import_batch_enriched_fields recorded (value-aware restore basis)",
       p1_filled is not None and "nric_hash_v2" in (p1_filled or []))
    reset_role()

    # ── (2) NEVER-CLOBBER explicit — S3 gender + house untouched by file values. ─
    cur.execute("set local role service_role")
    s3p = col("persons", P3, ["gender"])
    s3s = student_row_by_number(ORG, "STU-103", ["house"])
    ck("[2 NEVER-CLOBBER] S3 pre-existing persons.gender='male' NOT overwritten by file 'female'",
       s3p["gender"] == "male")
    ck("[2 NEVER-CLOBBER] S3 pre-existing sch_students.house='Amin' NOT overwritten by file 'Siddiq'",
       s3s["house"] == "Amin")
    reset_role()

    # ── (3) REVIEW rows were NOT written. ────────────────────────────────────
    cur.execute("set local role service_role")
    ck("[3 REVIEW class-differ] S2 (Balqis) NOT enriched — still no nric_hash_v2",
       col("persons", P2, ["nric_hash_v2"])["nric_hash_v2"] is None)
    for name in ("Danish Test", "Farah Undecided"):
        cur.execute("select count(*) from persons where org_id=%s and display_name ilike %s",
                    (ORG, name))
        (n,) = cur.fetchone()
        ck(f"[3 REVIEW] no person created for review-bucket row '{name}'", n == 0)
    cur.execute("select count(*) from persons where org_id=%s", (ORG,))
    (persons_after_commit,) = cur.fetchone()
    ck("[3 CREATE] exactly ONE new person created (Ezra), review rows excluded",
       persons_after_commit == persons_before + 1)
    cur.execute("select count(*) from persons where org_id=%s and import_batch_id=%s "
                "and import_batch_role='created'", (ORG, REF))
    (created_tagged,) = cur.fetchone()
    ck("[3 CREATE] created row tagged import_batch_id + role='created'", created_tagged == 1)
    cur.execute("select count(*) from sch_students where org_id=%s and import_batch_id=%s "
                "and import_batch_role='backfilled'", (ORG, REF))
    (backfilled_tagged,) = cur.fetchone()
    ck("[4 tag] enriched targets tagged import_batch_role='backfilled' (revert discriminator)",
       backfilled_tagged >= 1)
    reset_role()

    # ── (7) CONVERGENCE — re-run the SAME file → no dup persons, no orphans. ──
    # Independent data (S4 target + a distinct create) so the re-run's latest-wins
    # re-stamp cannot touch REF's revert targets.
    conv_rows = [
        enrich_prow({"name": "Delima Test", "class": "Class 1A", "bc_tag": "CONV-S4",
                     "fields": dict(full_fields), "target_student_number": "STU-104"}),
        create_prow({"name": "Convergence Kid", "class": "Class 1A", "bc_tag": "CONV-NEW",
                     "fields": dict(full_fields)}),
    ]
    REF_C1, REF_C2 = u(), u()
    set_authenticated(U_ADMIN)
    call_enroll(ORG, conv_rows, True, REF_C1)
    reset_role()
    cur.execute("set local role service_role")
    cur.execute("select count(*) from persons where org_id=%s", (ORG,))
    (persons_after_conv1,) = cur.fetchone()
    reset_role()

    set_authenticated(U_ADMIN)
    call_enroll(ORG, conv_rows, True, REF_C2)  # same file again
    reset_role()
    cur.execute("set local role service_role")
    cur.execute("select count(*) from persons where org_id=%s", (ORG,))
    (persons_after_conv2,) = cur.fetchone()
    ck("[7 CONVERGENCE] re-running the same file created 0 new persons (BC-hash dedup)",
       persons_after_conv2 == persons_after_conv1)
    cur.execute("select count(*) from sch_students ss "
                "left join persons p on p.id = ss.person_id "
                "where ss.org_id=%s and ss.deleted_at is null and p.id is null", (ORG,))
    (orphans,) = cur.fetchone()
    ck("[7 CONVERGENCE] no orphan sch_students (every live student has a person)", orphans == 0)
    reset_role()

    # ── (5) proACL — EXECUTE denied anon/service_role/public, granted authenticated. ─
    for sig, label in [(RPC_CONTRACT["enroll_sig"], "enroll"),
                       (RPC_CONTRACT["revert_sig"], "revert")]:
        cur.execute(
            "select rolname, has_function_privilege(rolname, %s, 'EXECUTE') "
            "from (values ('anon'),('authenticated'),('service_role')) as t(rolname)",
            (sig,),
        )
        grants = dict(cur.fetchall())
        cur.execute("select has_function_privilege('public', %s, 'EXECUTE')", (sig,))
        (public_exec,) = cur.fetchone()
        ck(f"[5 proACL] {label}: EXECUTE denied anon+service_role+public, granted authenticated",
           grants == {"anon": False, "authenticated": True, "service_role": False}
           and public_exec is False)

    for role in RPC_CONTRACT["proacl_denied"]:
        cur.execute("savepoint sp5")
        cur.execute(f"set local role {role}")
        msg = expect_raise(lambda: call_enroll(ORG, [], False, u()))
        cur.execute("reset role")
        cur.execute("rollback to savepoint sp5")
        ck(f"[5 proACL call] role '{role}' EXECUTE denied at call time",
           "permission denied" in msg)

    # ── (6) CALLER-ROLE FAIL-CLOSED — non-authorized caller REJECTED. ────────
    cur.execute("set local role service_role")
    cur.execute("select count(*) from persons where org_id=%s", (ORG,))
    (persons_before_negatives,) = cur.fetchone()
    reset_role()
    # (a) member of the org with the WRONG role (cashier) → 42501.
    set_authenticated(U_CASHIER)
    msg = expect_raise(lambda: call_enroll(ORG, prows, True, u()))
    ck("[6 fail-closed] cashier (member, wrong role) enroll → not_permitted (42501)",
       "not_permitted" in msg or "insufficient_privilege" in msg)
    msg = expect_raise(lambda: call_revert(ORG, REF, True))
    ck("[6 fail-closed] cashier revert → not_permitted (42501)",
       "not_permitted" in msg or "insufficient_privilege" in msg)
    reset_role()
    # (b) org_admin of a DIFFERENT org calling into ORG → 42501.
    cur.execute("set local role service_role")
    ORG_OTHER = u()
    cur.execute("insert into organizations(id,name,type) values (%s,%s,'madrasah')",
                (ORG_OTHER, "WETPROVE other org (ROLLED BACK)"))
    cur.execute("insert into org_members(id,org_id,user_id,role,accepted_at) "
                "values (%s,%s,%s,%s,now())", (u(), ORG_OTHER, U_CASHIER, RPC_CONTRACT["required_role"]))
    reset_role()
    set_authenticated(U_CASHIER)  # now org_admin of ORG_OTHER, but NOT of ORG
    msg = expect_raise(lambda: call_enroll(ORG, prows, True, u()))
    ck("[6 fail-closed] org_admin of a DIFFERENT org into ORG → not_permitted (42501)",
       "not_permitted" in msg or "insufficient_privilege" in msg)
    reset_role()
    cur.execute("set local role service_role")
    cur.execute("select count(*) from persons where org_id=%s", (ORG,))
    (persons_after_negatives,) = cur.fetchone()
    ck("[6 fail-closed] rejected callers wrote NOTHING (person count unchanged)",
       persons_after_negatives == persons_before_negatives)
    reset_role()

    # ── (4) 🔴 DUAL REVERT SEMANTICS — the non-negotiable proof. ─────────────
    # Second INDEPENDENT batch (REF3) enriching S2 → batch-scoping proof.
    b2_rows = [enrich_prow({"name": "Balqis Test", "class": "Class 1A", "bc_tag": "B2",
                            "fields": dict(full_fields), "target_student_number": "STU-102"})]
    REF3 = u()
    set_authenticated(U_ADMIN)
    call_enroll(ORG, b2_rows, True, REF3)
    reset_role()
    cur.execute("set local role service_role")
    s2_after_batch2 = col("persons", P2, ["nric_hash_v2"])["nric_hash_v2"]
    ck("[4 batch-scope setup] S2 enriched under a SECOND batch (REF3), has nric_hash_v2",
       s2_after_batch2 is not None)
    cur.execute("select count(*) from audit_log where org_id=%s", (ORG,))
    (audit_before_revert,) = cur.fetchone()
    reset_role()

    # Dry-run revert of REF first (counts only, zero writes).
    set_authenticated(U_ADMIN)
    dry = call_revert(ORG, REF, False)
    reset_role()
    ck("[4 revert dry-run] committed=false, reports created+backfilled counts, writes nothing",
       (dry.get("committed") in (False, None))
       and int(dry.get("reverted_created_count") or 0) >= 1
       and int(dry.get("reverted_backfilled_count") or 0) >= 1)
    cur.execute("set local role service_role")
    ck("[4 revert dry-run] Ezra still live after dry-run (no write)",
       col("persons", P1, ["nric_hash_v2"])["nric_hash_v2"] is not None)
    reset_role()

    # Commit the revert of REF.
    set_authenticated(U_ADMIN)
    call_revert(ORG, REF, True)
    reset_role()

    cur.execute("set local role service_role")
    # 4a: role='created' (Ezra) → SOFT-DELETED.
    cur.execute("select count(*) from persons where org_id=%s and import_batch_id=%s "
                "and import_batch_role='created' and deleted_at is not null", (ORG, REF))
    (created_deleted,) = cur.fetchone()
    cur.execute("select count(*) from sch_students where org_id=%s and import_batch_id=%s "
                "and import_batch_role='created' and deleted_at is not null", (ORG, REF))
    (created_stu_deleted,) = cur.fetchone()
    ck("[4a DELETE] revert of role='created' → person soft-DELETED (deleted_at set)", created_deleted == 1)
    ck("[4a DELETE] revert of role='created' → sch_students soft-DELETED", created_stu_deleted == 1)

    # 4b: role='backfilled' (S1) → EXACT pre-enrich restore, student NOT deleted.
    cur.execute("select deleted_at from persons where id=%s", (P1,))
    (p1_deleted,) = cur.fetchone()
    ck("[4b RESTORE] revert of role='backfilled' NEVER deletes the pre-existing student (S1 alive)",
       p1_deleted is None)
    S1_person_restored = col("persons", P1, ALL_P_COLS)
    S1_student_restored = student_row_by_number(ORG, "STU-101", ALL_S_COLS)
    for c in ALL_P_COLS:
        ck(f"[4b RESTORE persons.{c}] restored to exact pre-enrich value",
           S1_person_restored.get(c) == S1_person_before.get(c))
    for c in ALL_S_COLS:
        ck(f"[4b RESTORE sch_students.{c}] restored to exact pre-enrich value",
           S1_student_restored.get(c) == S1_student_before.get(c))
    # Markers cleared → reads as fully pre-batch again.
    cur.execute("select import_batch_id, import_batch_role, import_batch_enriched_fields "
                "from persons where id=%s", (P1,))
    m_id, m_role, m_fields = cur.fetchone()
    ck("[4b RESTORE] batch markers cleared on the reverted backfilled row",
       m_id is None and m_role is None and m_fields is None)

    # 4c: batch-scoping — REF3 (S2) UNAFFECTED by reverting REF.
    cur.execute("select nric_hash_v2, deleted_at from persons where id=%s", (P2,))
    s2_hash, s2_del = cur.fetchone()
    ck("[4c BATCH-SCOPE] reverting REF did NOT touch the 2nd batch REF3 (S2 still enriched, alive)",
       s2_hash is not None and s2_del is None)
    reset_role()

    # 4d: empty / nonexistent batch = safe no-op.
    set_authenticated(U_ADMIN)
    noop = call_revert(ORG, u(), True)
    reset_role()
    ck("[4d NO-OP] reverting a nonexistent batch ref = safe no-op (0 created, 0 backfilled)",
       int(noop.get("reverted_created_count") or 0) == 0
       and int(noop.get("reverted_backfilled_count") or 0) == 0)

    # 4e: audit hash-chain intact + appended after revert. Verify the SAME way
    # hashchain.ts's verifier does (compute_hash / canonical_payload_json above):
    # re-canonicalise the STORED payload jsonb (keys sorted, spaceless) and
    # recompute sha256(prev_hash + canonical) — NOT from payload::text, which is
    # jsonb-spaced + jsonb-key-order and would never equal the hashed input.
    cur.execute("set local role service_role")
    # Select payload as jsonb → psycopg2 parses it to a Python object (the same
    # object the JS verifier reads before canonicalising).
    cur.execute("select prev_hash, hash, payload from audit_log where org_id=%s order by id asc",
                (ORG,))
    rows = cur.fetchall()
    cur.execute("select count(*) from audit_log where org_id=%s", (ORG,))
    (audit_after_revert,) = cur.fetchone()
    chain_ok = True
    prev = None
    for prev_hash, h, payload in rows:
        # linkage (hashchain.ts L185) then content recompute (L189).
        if (prev is not None and prev_hash != prev) or h != compute_hash(prev_hash, payload):
            chain_ok = False
            break
        prev = h
    ck("[4e AUDIT] hash-chain intact after revert (each hash == sha256(prev||canonicalPayloadJson), links unbroken)",
       chain_ok and len(rows) > 0)
    ck("[4e AUDIT] revert APPENDED an audit row (append-only; nothing rewritten)",
       audit_after_revert > audit_before_revert)
    reset_role()

    print(f"\n[{DSN_VAR}] all proofs executed.")

except Exception as e:  # noqa: BLE001
    print(f"\n[{DSN_VAR}] HARNESS ERROR (not an assertion): {type(e).__name__}: {e}")
    FAIL.append(f"HARNESS-ERROR:{type(e).__name__}")
finally:
    conn.rollback()
    print(f"[{DSN_VAR}] ROLLED BACK — nothing persisted (including the applied "
          f"migration DDL). conn.commit() was NEVER called.")

print(f"\n[{DSN_VAR}] {len(PASS)} PASS, {len(FAIL)} FAIL")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
sys.exit(0)
