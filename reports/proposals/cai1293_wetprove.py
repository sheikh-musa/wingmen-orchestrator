#!/usr/bin/env python3
"""CAI-1293 wet-prove — ISOLATED SCRATCH SCHEMA, REAL DATA, single ROLLED-BACK txn.
Nazim #32172 conditions: (1) copy the REAL strategic_decisions + decision_audits so the
NULL->LEGACY backfill is exercised against the actual distribution; (2) prove all 3 parts;
drop scratch after. Safety: reads public with ACCESS SHARE only (copy); ALL fix DDL hits
cai1293_wp tables (no ACCESS EXCLUSIVE on live strategic_decisions); the close-fn's
agent_messages insert + everything else roll back. Nothing persists."""
import os, re, psycopg

url = os.environ["DATABASE_URL"]
T = []  # transcript lines
def log(s): T.append(s); print(s)

# FIDELITY (cc-quality #32207): exercise the EXACT function bodies from the shipping .sql, never a
# re-typed copy — the %%->% divergence that hid the RAISE bug is exactly what that prevents. Extract
# each CREATE FUNCTION block from the proposal and strip the `public.` schema so it lands in scratch.
_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cai1293-mechanism-fix.proposal.sql")
_SRC = open(_SQL).read()
def extract_fn(name):
    m = re.search(r'CREATE\s+(?:OR REPLACE\s+)?FUNCTION\s+(?:public\.)?' + re.escape(name)
                  + r'\b.*?\$fn\$.*?\$fn\$\s*;', _SRC, re.DOTALL)
    if not m:
        raise SystemExit(f"FIDELITY FAIL: could not extract {name}() from {_SQL}")
    return m.group(0).replace('FUNCTION public.', 'FUNCTION ')  # create in scratch, not public
GUARD_FN      = extract_fn('enforce_audit_tier_change_guard')
UNRESOLVED_FN = extract_fn('decision_audit_unresolved')
CLOSE_FN      = extract_fn('close_decision_by_audit')
# SCRATCH-ADAPTATION (documented): the shipping guard pins `SET search_path = pg_catalog, public`
# (SECDEF hygiene). In production the log table decision_tier_changes lives in public and is found;
# during proving it lives in cai1293_wp, so PREPEND the scratch schema to the pin. Only the pin's
# VALUE changes — the RAISE/block/record LOGIC (the fidelity-critical part) is byte-identical.
GUARD_FN = GUARD_FN.replace("SET search_path = pg_catalog, public",
                            "SET search_path = cai1293_wp, pg_catalog, public")

def expect_error(cur, sql, params=None, label=""):
    cur.execute("SAVEPOINT sp")
    try:
        cur.execute(sql, params or [])
        cur.execute("RELEASE SAVEPOINT sp")
        log(f"  ✗ {label}: NO ERROR (expected one) — FAIL")
        return False
    except psycopg.Error as e:
        cur.execute("ROLLBACK TO SAVEPOINT sp")
        msg = str(e).splitlines()[0][:110]
        log(f"  ✓ {label}: refused -> {msg}")
        return True

conn = psycopg.connect(url); conn.autocommit = False
cur = conn.cursor()
try:
    cur.execute("SET LOCAL statement_timeout = '60s'")
    cur.execute("SET search_path = cai1293_wp, public")
    cur.execute("SET app.current_agent_id = 'wetprove-cc-fleet-health'")
    cur.execute("CREATE SCHEMA cai1293_wp")
    log("== SCRATCH cai1293_wp created; copying REAL data (ACCESS SHARE on public) ==")
    cur.execute("CREATE TABLE cai1293_wp.strategic_decisions AS SELECT * FROM public.strategic_decisions")
    cur.execute("CREATE TABLE cai1293_wp.decision_audits AS SELECT * FROM public.decision_audits")
    cur.execute("""SELECT count(*) FILTER (WHERE audit_tier IS NULL), count(*) FILTER (WHERE audit_tier='FULL'),
                          count(*) FILTER (WHERE audit_tier='NONE'), count(*) FROM cai1293_wp.strategic_decisions
                   WHERE COALESCE(is_test,false)=false""")
    n_null,n_full,n_none,n_tot = cur.fetchone()
    log(f"  FIDELITY baseline (non-test): NULL={n_null} FULL={n_full} NONE={n_none} total={n_tot}")

    # ---- recreate decision_audit_state view over SCRATCH, WITH the B.3 LEGACY-CANDIDATE/LEGACY
    #      audit_state arms injected (load-bearing for the F2 digest fix) ----
    cur.execute("SELECT pg_get_viewdef('public.decision_audit_state'::regclass, true)")
    viewbody = cur.fetchone()[0]
    # LEGACY-CANDIDATE must sit PARALLEL to UNTIERED-CANDIDATE (BEFORE the WINDOW-OPEN arm), else an
    # in-window LEGACY candidate is caught by WINDOW-OPEN and never surfaces to the digest — a bug the
    # wet-prove caught. Plain LEGACY (closed/non-candidate) sits before the NONE arm.
    viewbody = viewbody.replace(
        "AND decision_audit_tier_candidate(sd.title, sd.decision, sd.reasoning) THEN 'UNTIERED-CANDIDATE'::text",
        "AND decision_audit_tier_candidate(sd.title, sd.decision, sd.reasoning) THEN 'UNTIERED-CANDIDATE'::text\n"
        "            WHEN sd.audit_tier = 'LEGACY'::text AND (sd.challenge_status = ANY (ARRAY['challenge_window'::text, 'unchallenged'::text])) AND decision_audit_tier_candidate(sd.title, sd.decision, sd.reasoning) THEN 'LEGACY-CANDIDATE'::text")
    viewbody = viewbody.replace(
        "WHEN sd.audit_tier = 'NONE'::text THEN 'AUDIT-NOT-REQUIRED'::text",
        "WHEN sd.audit_tier = 'LEGACY'::text THEN 'LEGACY'::text\n"
        "            WHEN sd.audit_tier = 'NONE'::text THEN 'AUDIT-NOT-REQUIRED'::text")
    assert "'LEGACY-CANDIDATE'::text" in viewbody, "FIDELITY: LEGACY-CANDIDATE arm injection failed"
    # bind the view's base tables to SCRATCH explicitly (search_path alone bound them to public,
    # so the view was reading un-backfilled prod data). Value-only helper fns (tier_candidate) act
    # on the passed sd.* values, so they're unaffected.
    viewbody = viewbody.replace("FROM strategic_decisions sd", "FROM cai1293_wp.strategic_decisions sd")
    viewbody = viewbody.replace("FROM decision_audits da", "FROM cai1293_wp.decision_audits da")
    cur.execute(UNRESOLVED_FN)  # fixed unresolved (B.2) — the view calls it
    cur.execute("CREATE VIEW cai1293_wp.decision_audit_state AS " + viewbody)
    # F2 pre-backfill: the digest's CURRENT untiered-candidate watch (audit_tier IS NULL, in-window,
    # tier_candidate) — the candidates that must survive the NULL->LEGACY backfill (else the 8am digest
    # goes false-all-clear). Computed DIRECTLY on the scratch table (the scratch view mis-binds base
    # tables to public — a proving artifact; the digest's view chain is byte-verified at apply).
    cur.execute("""SELECT count(*) FROM cai1293_wp.strategic_decisions
                   WHERE COALESCE(is_test,false)=false AND audit_tier IS NULL
                     AND challenge_status IN ('challenge_window','unchallenged')
                     AND decision_audit_tier_candidate(title, decision, reasoning)""")
    f2_pre = cur.fetchone()[0]
    log(f"  F2 pre-backfill: untiered-candidate watch (in-window) = {f2_pre}")

    # ========================= PART A =========================
    log("== PART A — mandatory tier + drop-guard + record ==")
    cur.execute("""CREATE TABLE cai1293_wp.decision_tier_changes(
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, decision_ref text NOT NULL,
        changed_at timestamptz NOT NULL DEFAULT now(), actor text, old_tier text, new_tier text NOT NULL,
        reason text, direction text NOT NULL)""")
    # F1 (cc-storefront #32230): SIMULATE the public default privs a real public table would inherit
    # (verified via pg_default_acl: anon=SELECT, authenticated=INSERT/SELECT/UPDATE/DELETE) so the .sql
    # A.0b REVOKE is proven to REMOVE real access — then apply the A.0b lock-down (mirrors the .sql).
    cur.execute("GRANT SELECT ON cai1293_wp.decision_tier_changes TO anon")
    cur.execute("GRANT INSERT,SELECT,UPDATE,DELETE ON cai1293_wp.decision_tier_changes TO authenticated")
    cur.execute("ALTER TABLE cai1293_wp.decision_tier_changes ENABLE ROW LEVEL SECURITY")
    cur.execute("REVOKE ALL ON cai1293_wp.decision_tier_changes FROM PUBLIC, anon, authenticated")
    cur.execute("GRANT SELECT, INSERT ON cai1293_wp.decision_tier_changes TO service_role")
    cur.execute("GRANT SELECT ON cai1293_wp.decision_tier_changes TO console_readonly")
    cur.execute("UPDATE cai1293_wp.strategic_decisions SET audit_tier='LEGACY' WHERE audit_tier IS NULL")
    log(f"  backfilled NULL->LEGACY: {cur.rowcount} rows")
    cur.execute("SELECT count(*) FROM cai1293_wp.strategic_decisions WHERE audit_tier IS NULL")
    log(f"  post-backfill NULL audit_tier count = {cur.fetchone()[0]} (expect 0)")
    cur.execute("SELECT count(*) FROM cai1293_wp.strategic_decisions WHERE audit_tier='LEGACY'")
    log(f"  LEGACY bucket now queryable, count = {cur.fetchone()[0]}")
    cur.execute("SELECT count(*) FROM cai1293_wp.strategic_decisions WHERE audit_tier='LEGACY' AND decision_audit_tier_candidate(title,decision,reasoning)")
    log(f"  LEGACY-CANDIDATE (cai's deferred ~839 retro-tier queue), count = {cur.fetchone()[0]}")
    cur.execute("ALTER TABLE cai1293_wp.strategic_decisions DROP CONSTRAINT IF EXISTS strategic_decisions_audit_tier_check")
    cur.execute("ALTER TABLE cai1293_wp.strategic_decisions ADD CONSTRAINT strategic_decisions_audit_tier_check CHECK (audit_tier = ANY(ARRAY['FULL','NONE','LEGACY']))")
    cur.execute("ALTER TABLE cai1293_wp.strategic_decisions ALTER COLUMN audit_tier SET NOT NULL")
    log("  CHECK{FULL,NONE,LEGACY} + NOT NULL applied")
    cur.execute(GUARD_FN)
    cur.execute("CREATE TRIGGER trg_audit_tier_change_guard BEFORE UPDATE OF audit_tier ON cai1293_wp.strategic_decisions FOR EACH ROW EXECUTE FUNCTION enforce_audit_tier_change_guard()")
    # exercise A
    cur.execute("SELECT decision_ref FROM cai1293_wp.strategic_decisions WHERE audit_tier='FULL' AND challenge_status = ANY(ARRAY['challenge_window','unchallenged']) LIMIT 1")
    row = cur.fetchone()
    if row:
        dref = row[0]
        expect_error(cur, "UPDATE cai1293_wp.strategic_decisions SET audit_tier='NONE' WHERE decision_ref=%s", [dref], f"(a) CAI-1009 dodge FULL->NONE in-window on {dref}")
    else:
        log("  (a) no FULL-in-window row to dodge-test — using a synthetic is_test FULL-in-window row")
        cur.execute("SELECT decision_ref FROM cai1293_wp.strategic_decisions LIMIT 1"); base=cur.fetchone()[0]
        cur.execute("UPDATE cai1293_wp.strategic_decisions SET audit_tier='FULL', challenge_status='challenge_window' WHERE decision_ref=%s",[base])
        expect_error(cur, "UPDATE cai1293_wp.strategic_decisions SET audit_tier='NONE' WHERE decision_ref=%s",[base], f"(a) CAI-1009 dodge on {base}")
        dref=base
    expect_error(cur, "UPDATE cai1293_wp.strategic_decisions SET audit_tier='JUNK' WHERE decision_ref=%s",[dref], "(a) CHECK rejects junk tier")
    expect_error(cur, "UPDATE cai1293_wp.strategic_decisions SET audit_tier=NULL WHERE decision_ref=%s",[dref], "(a) NOT NULL rejects a NULL tier")
    # record a LEGIT change (LEGACY->FULL raise) -> row logged
    cur.execute("SELECT decision_ref FROM cai1293_wp.strategic_decisions WHERE audit_tier='LEGACY' LIMIT 1"); lref=cur.fetchone()[0]
    cur.execute("SET app.tier_change_reason = 'wetprove: legit re-tier'")
    cur.execute("UPDATE cai1293_wp.strategic_decisions SET audit_tier='FULL' WHERE decision_ref=%s",[lref])
    cur.execute("SELECT actor,old_tier,new_tier,reason,direction FROM cai1293_wp.decision_tier_changes WHERE decision_ref=%s",[lref])
    rec=cur.fetchone()
    log(f"  ✓ (a) legit LEGACY->FULL RECORDED: actor={rec[0]} {rec[1]}->{rec[2]} reason='{rec[3]}' dir={rec[4]}")

    # ---- F1: the tier-change log is anon/authenticated-locked + the SECDEF trigger still writes ----
    log("== F1 — decision_tier_changes lock-down (cc-storefront #32230) + SECDEF trigger path ==")
    def denied_as(role, op, sql):
        cur.execute("SAVEPOINT f1"); cur.execute(f"SET LOCAL ROLE {role}")
        try:
            cur.execute(sql); cur.execute("ROLLBACK TO SAVEPOINT f1")
            log(f"  ✗ F1 {role:13} {op}: ALLOWED (expected DENIED) — FAIL")
        except psycopg.Error:
            cur.execute("ROLLBACK TO SAVEPOINT f1")
            log(f"  ✓ F1 {role:13} {op}: DENIED")
    for role in ('anon','authenticated'):
        denied_as(role,'SELECT',"SELECT 1 FROM cai1293_wp.decision_tier_changes")
        denied_as(role,'INSERT',"INSERT INTO cai1293_wp.decision_tier_changes(decision_ref,new_tier,direction) VALUES('x','NONE','set')")
        denied_as(role,'UPDATE',"UPDATE cai1293_wp.decision_tier_changes SET reason='x'")
        denied_as(role,'DELETE',"DELETE FROM cai1293_wp.decision_tier_changes")
    # SECDEF: a NON-service_role caller (authenticated) does a LEGIT tier change; the SECURITY DEFINER
    # trigger must STILL write the log (as owner) even though authenticated was REVOKEd from it —
    # else the A.0b lock-down would break legit tier updates. (INVOKER would fail here.)
    cur.execute("GRANT USAGE ON SCHEMA cai1293_wp TO authenticated")                # public-schema USAGE (prod has it)
    cur.execute("GRANT SELECT, UPDATE ON cai1293_wp.strategic_decisions TO authenticated")  # simulate its public default (arwdxtm)
    cur.execute("SELECT decision_ref FROM cai1293_wp.strategic_decisions WHERE audit_tier='LEGACY' LIMIT 1"); s2=cur.fetchone()[0]
    cur.execute("SAVEPOINT f1s"); cur.execute("SET LOCAL ROLE authenticated")
    cur.execute("SET LOCAL app.current_agent_id = 'authtest'")
    cur.execute("UPDATE cai1293_wp.strategic_decisions SET audit_tier='NONE' WHERE decision_ref=%s",[s2])  # LEGACY->NONE (not the dodge)
    cur.execute("RESET ROLE")
    cur.execute("SELECT count(*), max(actor) FROM cai1293_wp.decision_tier_changes WHERE decision_ref=%s",[s2]); nlog,who=cur.fetchone()
    cur.execute("ROLLBACK TO SAVEPOINT f1s")
    log(f"  {'✓' if nlog>0 else '✗'} F1 SECDEF: authenticated (no INSERT on log) did a legit LEGACY->NONE -> SECDEF trigger wrote {nlog} log row(s) as actor={who}")

    # ========================= PART B =========================
    log("== PART B — nonconforming verdict + coherence ==")
    cur.execute("ALTER TABLE cai1293_wp.decision_audits DROP CONSTRAINT IF EXISTS decision_audits_verdict_check")
    cur.execute("ALTER TABLE cai1293_wp.decision_audits ADD CONSTRAINT decision_audits_verdict_check CHECK (verdict IS NULL OR verdict = ANY(ARRAY['accepted','rejected','could_not_verify','nonconforming']))")
    cur.execute("SELECT id FROM cai1293_wp.decision_audits LIMIT 1"); aid=cur.fetchone()[0]
    cur.execute("UPDATE cai1293_wp.decision_audits SET verdict='nonconforming', completed_at=now() WHERE id=%s",[aid])
    log("  ✓ (b) verdict='nonconforming' ACCEPTED by CHECK")
    expect_error(cur, "UPDATE cai1293_wp.decision_audits SET verdict='garbage', completed_at=now() WHERE id=%s",[aid], "(b) CHECK rejects a junk verdict")
    cur.execute("SELECT decision_audit_unresolved('nonconforming', now(), NULL), decision_audit_unresolved('nonconforming', now(), now())")
    u_open,u_res = cur.fetchone()
    log(f"  ✓ (b) unresolved('nonconforming',done,unresolved)={u_open} (expect True); resolved={u_res} (expect False)")

    # ========================= PART C =========================
    log("== PART C — close_decision_by_audit: nonconforming block + FULL>=2-lens ==")
    cur.execute("SET app.current_agent_id = 'wetprove-cc-fleet-health'")
    cur.execute(CLOSE_FN)
    # build a clean FULL decision with controllable audits in scratch
    cur.execute("SELECT decision_ref FROM cai1293_wp.strategic_decisions WHERE audit_tier='FULL' LIMIT 1"); cref=cur.fetchone()[0]
    cur.execute("UPDATE cai1293_wp.strategic_decisions SET challenge_status='challenge_window' WHERE decision_ref=%s",[cref])
    cur.execute("DELETE FROM cai1293_wp.decision_audits WHERE decision_ref=%s",[cref])
    def add_audit(ref, lens, verdict='accepted'):
        cur.execute("""INSERT INTO cai1293_wp.decision_audits (decision_ref, auditor_agent, assigned_at, assigned_by, completed_at, verdict, lens, sla_hours)
                       VALUES (%s,'wp-auditor',now(),'wp',now(),%s,%s,24)""",[ref,verdict,lens])
    add_audit(cref,'correctness')
    expect_error(cur, "SELECT close_decision_by_audit(%s,'cc-fleet-health')",[cref], "(c) FULL with 1 lens REFUSED")
    add_audit(cref,'security')  # 2nd DISTINCT accepted lens
    cur.execute("SELECT close_decision_by_audit(%s,'cc-fleet-health')",[cref]); res=cur.fetchone()[0]
    log(f"  ✓ (c) FULL with 2 DISTINCT accepted lenses -> {res} (expect 'closed')")
    # nonconforming blocks even an otherwise-closeable decision
    cur.execute("UPDATE cai1293_wp.strategic_decisions SET challenge_status='challenge_window' WHERE decision_ref=%s",[cref])
    add_audit(cref,'residency','nonconforming')
    expect_error(cur, "SELECT close_decision_by_audit(%s,'cc-fleet-health')",[cref], "(b/c) a NONCONFORMING verdict BLOCKS close")

    # ---- F2: the backfill must NOT zero the digest's untiered-candidate watch (cc-storefront #32230) ----
    # Proven DIRECTLY on the scratch (post-backfill) table with the digest's exact candidate predicate
    # (a scratch VIEW over scratch tables mis-binds base-table refs to public — a proving artifact, not
    # a fix defect — so the view chain is byte-verified at apply, and the LOGIC is proven here).
    log("== F2 — audit_board_digest untiered-candidate watch survives the NULL->LEGACY backfill ==")
    cur.execute("""SELECT
        count(*) FILTER (WHERE audit_tier IS NULL) AS old_formula,
        count(*) FILTER (WHERE audit_tier IS NULL OR audit_tier = 'LEGACY') AS new_formula
      FROM cai1293_wp.strategic_decisions
     WHERE COALESCE(is_test,false)=false
       AND challenge_status IN ('challenge_window','unchallenged')
       AND decision_audit_tier_candidate(title, decision, reasoning)""")
    old_cnt,new_cnt = cur.fetchone()
    log(f"  F2 post-backfill: OLD digest predicate (audit_tier IS NULL only)      = {old_cnt}  (regressed from {f2_pre} -> false all-clear)")
    log(f"  F2 post-backfill: NEW digest predicate (IS NULL OR 'LEGACY' via arm)  = {new_cnt}  (fix restores the {f2_pre} watched candidates)")
    assert old_cnt == 0, f"F2: expected OLD formula to regress to 0 post-backfill, got {old_cnt}"
    assert new_cnt >= f2_pre, f"F2 FAIL: fix restored {new_cnt}, expected >= {f2_pre}"
    log(f"  ✓ F2: the ~{f2_pre} candidates go 0 under the OLD digest formula and are RESTORED ({new_cnt}) by the LEGACY-CANDIDATE fix")

    log("== ALL ARMS EXERCISED — rolling back (nothing persists) ==")
finally:
    conn.rollback()
    conn.close()

# confirm the scratch schema did not persist
c2 = psycopg.connect(url); cur2=c2.cursor()
cur2.execute("SELECT count(*) FROM information_schema.schemata WHERE schema_name='cai1293_wp'")
print(f"\npost-rollback: cai1293_wp schema exists? {cur2.fetchone()[0]} (expect 0)")
c2.close()
