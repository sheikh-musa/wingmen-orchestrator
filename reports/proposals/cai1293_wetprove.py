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

    # ---- recreate decision_audit_state view over SCRATCH (from live viewdef; unqualified names bind to scratch) ----
    cur.execute("SELECT pg_get_viewdef('public.decision_audit_state'::regclass, true)")
    viewbody = cur.fetchone()[0]
    cur.execute(UNRESOLVED_FN)  # fixed unresolved (B.2) — the view calls it
    cur.execute("CREATE VIEW cai1293_wp.decision_audit_state AS " + viewbody)

    # ========================= PART A =========================
    log("== PART A — mandatory tier + drop-guard + record ==")
    cur.execute("""CREATE TABLE cai1293_wp.decision_tier_changes(
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, decision_ref text NOT NULL,
        changed_at timestamptz NOT NULL DEFAULT now(), actor text, old_tier text, new_tier text NOT NULL,
        reason text, direction text NOT NULL)""")
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

    log("== ALL ARMS EXERCISED — rolling back (nothing persists) ==")
finally:
    conn.rollback()
    conn.close()

# confirm the scratch schema did not persist
c2 = psycopg.connect(url); cur2=c2.cursor()
cur2.execute("SELECT count(*) FROM information_schema.schemata WHERE schema_name='cai1293_wp'")
print(f"\npost-rollback: cai1293_wp schema exists? {cur2.fetchone()[0]} (expect 0)")
c2.close()
