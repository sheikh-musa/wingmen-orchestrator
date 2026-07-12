#!/usr/bin/env python3
"""drift_detector.py — cross-silo schema drift-detector (CAI-RESP-420, task #50).

Gate for invariants SCHEMA-1 (schema >= code-contract in every silo) and the
schema half of MIGRATION-1. Would have caught goumlyne weeks ago: it introspects
each tenant silo and diffs it against the canonical reference (ceayj, the pooled
prod, most-hardened), across 7 dimensions — tables, columns, indexes, RLS
policies, table grants, column grants, functions (+SECDEF) — and alerts on ANY
divergence that isn't an allowlisted intentional module-scoping difference.

HARD RULE (cai, CAI-RESP-420): the allowlist may suppress ONLY whole-table /
whole-function PRESENCE differences (a module a silo intentionally doesn't run),
each with a reason. Column / index / policy / grant / SECDEF drift INSIDE a shared
object is the 092 class and ALWAYS alerts — it is structurally un-allowlistable
here (enforced in _finding: allowlist is consulted for presence kinds only).

Read-only on every silo. Findings persist to schema_drift_findings; CRITICAL
non-expected drift pages the war-room (health_check.sh tg_send path) + the bus.
On a clean run (no non-expected drift) it stamps the registry via mark_asserted.

    python3 -m nervous_system.drift_detector --once   [--reason daily|on-apply|pre-live]
    python3 -m nervous_system.drift_detector --print  # dry: introspect+diff, no writes/alerts
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ORCH = Path(__file__).resolve().parent.parent
load_dotenv(ORCH / ".env")

sys.path.insert(0, str(ORCH / "scripts" / "gates"))
import registry  # noqa: E402  (scripts/gates/registry.py — mark_asserted)

WAR_ROOM_CHAT = "-5383530504"
TG_SEND = str(ORCH / "scripts" / "tg_send.sh")

# ── Config (shadow-tunable; aligned to docs/data-store-registry.md) ───────────
REFERENCE = {"alias": "ceayj", "dsn_env": "IHSANOS_PROD_DATABASE_URL",
             "ref": "ceayjeamtmcyzzvqflus"}
TARGETS = [
    {"alias": "goumlyne", "dsn_env": "GOUMLYNE_DATABASE_URL", "ref": "goumlynecruxrlmzlntp"},
]

# Money/PII markers → CRITICAL severity for any drift on the object.
MONEY_MARKERS = ("pos_order", "donation", "donor", "tabung", "organization",
                 "org_role_permission", "payment", "wallet", "ledger",
                 "transaction", "fee", "hr_claim", "person", "fundraiser", "campaign")

# ALLOWLIST — PRESENCE-ONLY (cai-blessed intentional module scoping). Each entry:
# (silo, kind, glob, reason). kind: 'missing' = in ceayj-ref, absent on silo
# (module the silo doesn't run); 'extra' = on silo, absent on ceayj-ref
# (silo-only module). Applies to table_missing/table_extra/fn_missing/fn_extra ONLY.
ALLOWLIST = [
    # goumlyne-only modules (extra vs ceayj) — irsyad-only GL + WooCommerce-ingest.
    ("goumlyne", "extra", "gl_*", "irsyad-only GL module (enforce_balanced_journal/post_journal_atomic)"),
    ("goumlyne", "extra", "wc_*", "irsyad-only WooCommerce-ingest module"),
    ("goumlyne", "extra", "organizations_fiscal_config", "irsyad-only fiscal config (GL module)"),
    # ceayj-only modules (missing on goumlyne) — telegram / consent / platform.
    ("goumlyne", "missing", "telegram_users", "ceayj-only telegram storefront module; irsyad has none"),
    ("goumlyne", "missing", "tg_*", "ceayj-only telegram module"),
    ("goumlyne", "missing", "donor_consent", "ceayj-only consent module"),
    ("goumlyne", "missing", "donor_invites", "ceayj-only platform module"),
    ("goumlyne", "missing", "parent_consents", "ceayj-only consent module"),
    ("goumlyne", "missing", "newsletter_subscriptions", "ceayj-only platform module"),
    ("goumlyne", "missing", "organization_fee_overrides", "ceayj-only platform-fee module"),
    ("goumlyne", "missing", "client_contracts", "ceayj-only platform module"),
    ("goumlyne", "missing", "academic_terms", "ceayj-only platform module"),
    ("goumlyne", "missing", "pos_order_counters", "ceayj-only storefront-counter module"),
]

# The 7 introspection dimensions — proven SQL lifted from tonight's sweep
# (scripts/drift/_sweep_diff_starting_point.py).
SQL = {
"tables": "SELECT table_name FROM information_schema.tables "
          "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name",
"columns": "SELECT table_name, column_name, data_type, is_nullable, column_default "
           "FROM information_schema.columns WHERE table_schema='public' "
           "ORDER BY table_name, ordinal_position",
"indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes "
           "WHERE schemaname='public' ORDER BY tablename, indexname",
"policies": "SELECT tablename, policyname, cmd, permissive, roles, qual, with_check "
            "FROM pg_policies WHERE schemaname='public' ORDER BY tablename, policyname",
"table_grants": "SELECT table_name, grantee, privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema='public' AND grantee IN ('anon','authenticated') "
                "ORDER BY table_name, grantee, privilege_type",
"col_grants": "SELECT table_name, column_name, grantee, privilege_type "
              "FROM information_schema.role_column_grants "
              "WHERE table_schema='public' AND grantee IN ('anon','authenticated') "
              "ORDER BY table_name, column_name, grantee, privilege_type",
"routines": "SELECT r.routine_name, p.prosecdef AS secdef, "
            "       pg_get_function_identity_arguments(p.oid) AS args "
            "FROM information_schema.routines r JOIN pg_proc p ON p.proname=r.routine_name "
            "JOIN pg_namespace n ON n.oid=p.pronamespace AND n.nspname='public' "
            "WHERE r.routine_schema='public' AND r.routine_type='FUNCTION' ORDER BY r.routine_name",
}

WRITE_PRIVS = {"INSERT", "UPDATE", "DELETE", "TRUNCATE"}


def is_money(name: str) -> bool:
    n = (name or "").lower()
    return any(m in n for m in MONEY_MARKERS)


def _log(msg: str) -> None:
    print(f"[drift] {datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}", flush=True)


# ── Introspect ────────────────────────────────────────────────────────────────

def introspect(dsn: str) -> dict:
    out = {}
    with psycopg.connect(dsn, connect_timeout=20) as conn:
        for name, sql in SQL.items():
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d.name for d in cur.description]
                out[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    return out


# ── Allowlist (presence-only) ─────────────────────────────────────────────────

def _allowlisted(silo: str, kind: str, name: str) -> str | None:
    """Reason string if this PRESENCE difference is blessed, else None. Consulted
    ONLY for table/function presence kinds — never for within-object drift."""
    for a_silo, a_kind, glob, reason in ALLOWLIST:
        if a_silo == silo and a_kind == kind and fnmatch.fnmatch(name, glob):
            return reason
    return None


def _finding(silo, silo_ref, dimension, kind, obj, detail, *, presence=False,
             presence_kind=None, presence_name=None) -> dict:
    money = is_money(obj)
    expected, reason = False, None
    if presence:
        reason = _allowlisted(silo, presence_kind, presence_name)
        expected = reason is not None
    if expected:
        severity = "INFO"
    elif money:
        severity = "CRITICAL"
    elif presence:
        severity = "NOTABLE"          # unexpected whole-object presence diff
    else:
        severity = "NOTABLE"          # within-object drift on a non-money shared table
    return {"silo": silo, "silo_ref": silo_ref, "dimension": dimension, "kind": kind,
            "object": obj, "severity": severity, "expected": expected,
            "is_money": money, "detail": detail, "reason": reason}


# ── Diff one silo vs the reference ────────────────────────────────────────────

def diff_silo(ref: dict, silo: dict, silo_alias: str, silo_ref: str) -> list:
    f = []
    add = f.append

    # tables (presence — allowlist-eligible)
    rt = {r["table_name"] for r in ref["tables"]}
    st = {r["table_name"] for r in silo["tables"]}
    shared = rt & st
    for t in sorted(rt - st):
        add(_finding(silo_alias, silo_ref, "tables", "table_missing", t,
                     {"note": "in ceayj-ref, absent on silo"},
                     presence=True, presence_kind="missing", presence_name=t))
    for t in sorted(st - rt):
        add(_finding(silo_alias, silo_ref, "tables", "table_extra", t,
                     {"note": "on silo, absent in ceayj-ref"},
                     presence=True, presence_kind="extra", presence_name=t))

    # columns (within shared tables — ALWAYS alerts)
    def colmap(rows):
        m = {}
        for r in rows:
            m.setdefault(r["table_name"], {})[r["column_name"]] = (
                r["data_type"], r["is_nullable"], r["column_default"])
        return m
    rc, sc = colmap(ref["columns"]), colmap(silo["columns"])
    for t in sorted(shared):
        a, b = rc.get(t, {}), sc.get(t, {})
        for col in sorted(set(a) - set(b)):
            add(_finding(silo_alias, silo_ref, "columns", "column_missing", f"{t}.{col}",
                         {"ceayj": a[col]}))
        for col in sorted(set(b) - set(a)):
            add(_finding(silo_alias, silo_ref, "columns", "column_extra", f"{t}.{col}",
                         {"silo": b[col]}))
        for col in sorted(set(a) & set(b)):
            if a[col][:2] != b[col][:2]:   # type/nullability differ (ignore default text)
                add(_finding(silo_alias, silo_ref, "columns", "column_type_diff", f"{t}.{col}",
                             {"ceayj": a[col], "silo": b[col]}))

    # indexes (within shared tables — ALWAYS alerts)
    def idxmap(rows):
        m = {}
        for r in rows:
            m.setdefault(r["tablename"], {})[r["indexname"]] = r["indexdef"]
        return m
    ri, si = idxmap(ref["indexes"]), idxmap(silo["indexes"])
    for t in sorted(shared):
        a, b = ri.get(t, {}), si.get(t, {})
        for n in sorted(set(a) - set(b)):
            add(_finding(silo_alias, silo_ref, "indexes", "index_missing", f"{t}:{n}", {"def": a[n]}))
        for n in sorted(set(b) - set(a)):
            add(_finding(silo_alias, silo_ref, "indexes", "index_extra", f"{t}:{n}", {"def": b[n]}))
        for n in sorted(set(a) & set(b)):
            if a[n] != b[n]:
                add(_finding(silo_alias, silo_ref, "indexes", "index_def_diff", f"{t}:{n}",
                             {"ceayj": a[n], "silo": b[n]}))

    # policies (within shared tables — ALWAYS alerts; the anon-write/RLS class)
    def polmap(rows):
        m = {}
        for r in rows:
            m.setdefault(r["tablename"], {})[r["policyname"]] = {
                "cmd": r["cmd"], "roles": r["roles"], "qual": r["qual"],
                "with_check": r["with_check"], "permissive": r["permissive"]}
        return m
    rp, sp = polmap(ref["policies"]), polmap(silo["policies"])
    for t in sorted(shared):
        a, b = rp.get(t, {}), sp.get(t, {})
        for n in sorted(set(a) - set(b)):
            add(_finding(silo_alias, silo_ref, "policies", "policy_missing", f"{t}:{n}", {"ceayj": a[n]}))
        for n in sorted(set(b) - set(a)):
            add(_finding(silo_alias, silo_ref, "policies", "policy_extra", f"{t}:{n}", {"silo": b[n]}))
        for n in sorted(set(a) & set(b)):
            if a[n] != b[n]:
                add(_finding(silo_alias, silo_ref, "policies", "policy_diff", f"{t}:{n}",
                             {"ceayj": a[n], "silo": b[n]}))

    # table grants to anon/authenticated (within shared — ALWAYS alerts; extra
    # WRITE grant on a silo = the open-write hole class → forced CRITICAL on money)
    def tgmap(rows):
        m = {}
        for r in rows:
            m.setdefault(r["table_name"], set()).add((r["grantee"], r["privilege_type"]))
        return m
    rg, sg = tgmap(ref["table_grants"]), tgmap(silo["table_grants"])
    for t in sorted(shared):
        a, b = rg.get(t, set()), sg.get(t, set())
        for grantee, priv in sorted(b - a):
            fi = _finding(silo_alias, silo_ref, "grants", "grant_extra", t,
                          {"grantee": grantee, "privilege": priv})
            if priv in WRITE_PRIVS and is_money(t):
                fi["severity"] = "CRITICAL"   # open anon/auth write on a money table (D2 hole)
            f.append(fi)
        for grantee, priv in sorted(a - b):
            add(_finding(silo_alias, silo_ref, "grants", "grant_missing", t,
                         {"grantee": grantee, "privilege": priv}))

    # functions: presence (allowlist-eligible) + SECDEF flip on shared (ALWAYS alerts)
    def fnmap(rows):
        m = {}
        for r in rows:
            m.setdefault(r["routine_name"], []).append(r.get("secdef"))
        return m
    rf, sf = fnmap(ref["routines"]), fnmap(silo["routines"])
    for n in sorted(set(rf) - set(sf)):
        add(_finding(silo_alias, silo_ref, "functions", "fn_missing", n,
                     {"secdef": any(rf[n])}, presence=True, presence_kind="missing", presence_name=n))
    for n in sorted(set(sf) - set(rf)):
        add(_finding(silo_alias, silo_ref, "functions", "fn_extra", n,
                     {"secdef": any(sf[n])}, presence=True, presence_kind="extra", presence_name=n))
    for n in sorted(set(rf) & set(sf)):
        if any(rf[n]) != any(sf[n]):
            add(_finding(silo_alias, silo_ref, "functions", "fn_secdef_diff", n,
                         {"ceayj_secdef": any(rf[n]), "silo_secdef": any(sf[n])}))
    return f


# ── Persist + alert ───────────────────────────────────────────────────────────

def persist(conn, run_id: str, reason_run: str, findings: list) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-infra',true)")
        for fi in findings:
            cur.execute(
                "INSERT INTO schema_drift_findings "
                "(run_id, reason_run, silo, silo_ref, dimension, kind, object, severity, "
                " expected, is_money, detail, reason) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, reason_run, fi["silo"], fi["silo_ref"], fi["dimension"], fi["kind"],
                 fi["object"], fi["severity"], fi["expected"], fi["is_money"],
                 json.dumps(fi["detail"], default=str), fi["reason"]))
        conn.commit()


def _tg_war_room(text: str) -> bool:
    try:
        subprocess.run([TG_SEND, text], check=True, capture_output=True, timeout=30,
                       env={**os.environ, "TG_CHAT_OVERRIDE": WAR_ROOM_CHAT})
        return True
    except Exception as e:
        _log(f"war-room alert failed: {e}")
        return False


def _bus(conn, subject: str, body: str, priority: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-infra',true)")
        cur.execute("INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
                    "requires_response,priority) VALUES ('cc-infra','cc-orchestrator','blocker',%s,%s,true,%s)",
                    (subject, body, priority))
        conn.commit()


def alert(conn, run_id: str, findings: list) -> None:
    crit = [f for f in findings if f["severity"] == "CRITICAL" and not f["expected"]]
    notable = [f for f in findings if f["severity"] == "NOTABLE" and not f["expected"]]
    if crit:
        lines = [f"- [{f['silo']}] {f['kind']} {f['object']}" for f in crit[:15]]
        more = f"\n(+{len(crit)-15} more)" if len(crit) > 15 else ""
        _tg_war_room(f"\U0001F6A8 SCHEMA DRIFT — {len(crit)} CRITICAL on a tenant silo "
                     f"(money/PII, the 092 class):\n" + "\n".join(lines) + more +
                     f"\nrun={run_id}. Details: schema_drift_findings.")
        _bus(conn, f"[drift-detector] {len(crit)} CRITICAL schema drift ({run_id})",
             "CRITICAL cross-silo drift (money/PII / 092 class):\n" +
             "\n".join(f"- [{f['silo']}] {f['dimension']}/{f['kind']} {f['object']}: {f['detail']}"
                       for f in crit), "P1")
    if notable:
        _bus(conn, f"[drift-detector] {len(notable)} NOTABLE schema drift ({run_id})",
             "NOTABLE cross-silo drift (non-money shared-object or unexpected presence):\n" +
             "\n".join(f"- [{f['silo']}] {f['dimension']}/{f['kind']} {f['object']}"
                       for f in notable[:60]), "P2")


# ── Orchestrate ───────────────────────────────────────────────────────────────

def run(reason_run: str = "daily", write: bool = True, alert_on: bool = True) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"drift-{ts}-{reason_run}"
    _log(f"run {run_id}: reference={REFERENCE['alias']}")
    ref_snap = introspect(os.environ[REFERENCE["dsn_env"]])
    all_findings = []
    for tgt in TARGETS:
        dsn = os.environ.get(tgt["dsn_env"])
        if not dsn:
            _log(f"{tgt['alias']}: dsn env {tgt['dsn_env']} empty — skipped")
            continue
        snap = introspect(dsn)
        fs = diff_silo(ref_snap, snap, tgt["alias"], tgt["ref"])
        all_findings.extend(fs)
        crit = sum(1 for f in fs if f["severity"] == "CRITICAL" and not f["expected"])
        _log(f"{tgt['alias']}: {len(fs)} findings ({crit} CRITICAL non-expected)")

    non_expected = [f for f in all_findings if not f["expected"]]
    summary = {"run_id": run_id, "total": len(all_findings),
               "non_expected": len(non_expected),
               "critical": sum(1 for f in non_expected if f["severity"] == "CRITICAL"),
               "notable": sum(1 for f in non_expected if f["severity"] == "NOTABLE"),
               "findings": all_findings}
    if write:
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        with psycopg.connect(dsn) as conn:
            persist(conn, run_id, reason_run, all_findings)
            if alert_on:
                alert(conn, run_id, all_findings)
        # SCHEMA-1 + schema-half of MIGRATION-1 hold only when there is NO
        # non-expected drift. A dirty run leaves them non-COVERED (honest).
        if not non_expected:
            registry.mark_asserted("SCHEMA-1", f"drift_detector:{run_id}")
            registry.mark_asserted("MIGRATION-1", f"drift_detector(schema):{run_id}")
            _log("clean run — SCHEMA-1 + MIGRATION-1(schema) asserted COVERED")
        else:
            registry.mark_failing("SCHEMA-1")
            _log(f"{len(non_expected)} non-expected drift — SCHEMA-1 left non-COVERED")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run, persist, alert")
    ap.add_argument("--print", dest="printonly", action="store_true",
                    help="dry: introspect+diff, print, NO writes/alerts")
    ap.add_argument("--reason", default="daily", choices=["daily", "on-apply", "pre-live", "manual"])
    a = ap.parse_args()
    if a.printonly:
        s = run(reason_run=a.reason, write=False, alert_on=False)
        by = {}
        for f in s["findings"]:
            by.setdefault((f["severity"], f["expected"]), 0)
            by[(f["severity"], f["expected"])] += 1
        print(f"\nrun={s['run_id']}  total={s['total']}  non_expected={s['non_expected']} "
              f"(CRITICAL={s['critical']} NOTABLE={s['notable']})")
        for (sev, exp), n in sorted(by.items()):
            print(f"  {sev:8} expected={exp}: {n}")
        for f in s["findings"]:
            if not f["expected"] and f["severity"] == "CRITICAL":
                print(f"  CRIT [{f['silo']}] {f['dimension']}/{f['kind']} {f['object']}")
        return 0
    if a.once:
        s = run(reason_run=a.reason, write=True, alert_on=True)
        print(f"run={s['run_id']} total={s['total']} non_expected={s['non_expected']} "
              f"CRITICAL={s['critical']} NOTABLE={s['notable']}")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
