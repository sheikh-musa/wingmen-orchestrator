"""Drift-detector (CAI-RESP-420 #50) — deterministic off-live tests.

diff_silo() is pure (two introspection snapshots in, findings out) — no DB. These
encode cai's rules: money/PII drift = CRITICAL; intentional module PRESENCE diffs
are allowlistable (with a reason); within-object drift (column/index/policy/grant/
SECDEF) is the 092 class and is NEVER allowlistable; the pos_orders regression
(flag pre-fix, clear post-fix) is the operator's self-test in fixture form.
"""
import pytest

pytest.importorskip("psycopg")

import sys, pathlib  # noqa: E402
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous_system"))
sys.path.insert(0, str(ROOT / "scripts" / "gates"))
import drift_detector as dd  # noqa: E402


def snap(tables=(), columns=(), indexes=(), policies=(), grants=(), routines=()):
    return {
        "tables": [{"table_name": t} for t in tables],
        "columns": [{"table_name": t, "column_name": c, "data_type": d,
                     "is_nullable": n, "column_default": None} for (t, c, d, n) in columns],
        "indexes": [{"tablename": t, "indexname": i, "indexdef": df} for (t, i, df) in indexes],
        "policies": [{"tablename": t, "policyname": p, "cmd": "ALL", "permissive": "PERMISSIVE",
                      "roles": r, "qual": None, "with_check": None} for (t, p, r) in policies],
        "table_grants": [{"table_name": t, "grantee": g, "privilege_type": pr} for (t, g, pr) in grants],
        "col_grants": [],
        "routines": [{"routine_name": n, "secdef": s, "args": ""} for (n, s) in routines],
    }


def diff(ref, silo):
    return dd.diff_silo(ref, silo, "goumlyne", "goumlynecruxrlmzlntp")


# ── money/PII drift = CRITICAL ────────────────────────────────────────────────

def test_money_column_missing_is_critical_non_expected():
    ref = snap(tables=["pos_orders"], columns=[("pos_orders", "currency", "text", "NO")])
    silo = snap(tables=["pos_orders"])                      # currency absent on silo
    fs = diff(ref, silo)
    m = [f for f in fs if f["object"] == "pos_orders.currency"]
    assert len(m) == 1
    assert m[0]["severity"] == "CRITICAL" and m[0]["is_money"] and not m[0]["expected"]


def test_open_anon_write_grant_on_money_table_is_critical():
    ref = snap(tables=["pos_orders"])                       # ceayj: no anon grant (revoked)
    silo = snap(tables=["pos_orders"], grants=[("pos_orders", "anon", "INSERT")])
    fs = diff(ref, silo)
    g = [f for f in fs if f["kind"] == "grant_extra" and f["object"] == "pos_orders"]
    assert len(g) == 1 and g[0]["severity"] == "CRITICAL"   # the D2 open-write hole class


def test_secdef_flip_on_shared_fn_alerts():
    ref = snap(routines=[("place_storefront_order", True)])
    silo = snap(routines=[("place_storefront_order", False)])
    fs = diff(ref, silo)
    s = [f for f in fs if f["kind"] == "fn_secdef_diff"]
    assert len(s) == 1 and not s[0]["expected"]


# ── allowlist = PRESENCE only, with a reason ──────────────────────────────────

def test_intentional_module_table_is_allowlisted_info():
    ref = snap(tables=["organizations"])                    # ceayj has no gl_accounts
    silo = snap(tables=["organizations", "gl_accounts"])    # goumlyne-only GL module
    fs = diff(ref, silo)
    gl = [f for f in fs if f["object"] == "gl_accounts"]
    assert len(gl) == 1 and gl[0]["expected"] and gl[0]["severity"] == "INFO"
    assert "GL module" in gl[0]["reason"]


def test_ceayj_only_module_missing_on_silo_is_allowlisted():
    ref = snap(tables=["telegram_users"])
    silo = snap(tables=[])
    fs = diff(ref, silo)
    tu = [f for f in fs if f["object"] == "telegram_users"]
    assert len(tu) == 1 and tu[0]["expected"] and tu[0]["kind"] == "table_missing"


def test_within_table_drift_is_NEVER_allowlisted_hard_rule():
    # A shared table whose NAME matches nothing special, but even if a table were
    # allowlisted for presence, a COLUMN diff inside it must still alert. Here the
    # table exists in both silos, so no presence entry applies; the column drift
    # must be a non-expected finding regardless.
    ref = snap(tables=["gl_accounts"], columns=[("gl_accounts", "balance", "numeric", "NO")])
    silo = snap(tables=["gl_accounts"])                     # shared table, missing a column
    fs = diff(ref, silo)
    col = [f for f in fs if f["kind"] == "column_missing"]
    assert len(col) == 1 and not col[0]["expected"]         # never suppressed by the allowlist
    # and NO finding of any within-object kind is ever marked expected
    assert all(not f["expected"] for f in fs
               if f["kind"] not in ("table_missing", "table_extra", "fn_missing", "fn_extra"))


# ── clean run + pos_orders regression (the operator's self-test, fixture form) ─

def test_identical_snapshots_yield_no_findings():
    s = snap(tables=["pos_orders"], columns=[("pos_orders", "currency", "text", "NO")])
    assert diff(s, s) == []


POS_COLS_FULL = [("pos_orders", c, "text", "YES") for c in
                 ("payment_idempotency_key", "payment_external_ref", "payment_confirmed_at",
                  "currency", "session_id", "surface_key")]


def test_pos_orders_regression_flag_prefix_clear_postfix():
    ref = snap(tables=["pos_orders"], columns=POS_COLS_FULL)
    pre = snap(tables=["pos_orders"])                        # pre-092: all 6 payment cols missing
    pre_f = [f for f in diff(ref, pre) if f["object"].startswith("pos_orders.")]
    assert len(pre_f) == 6 and all(f["severity"] == "CRITICAL" for f in pre_f)   # flags the class
    post = snap(tables=["pos_orders"], columns=POS_COLS_FULL)  # post-fix: parity
    post_f = [f for f in diff(ref, post) if f["object"].startswith("pos_orders.")]
    assert post_f == []                                     # clean once remediated
