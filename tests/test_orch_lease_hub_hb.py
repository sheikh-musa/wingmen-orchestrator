"""op#11774 #4b (operator-P1) — the hub's lease-renew ALSO stamps agent_status
heartbeat + auth_fp, so the console SHOWS the hub's real token (it was auth_fp=NULL
+ hb ~20h stale — the blind spot that hid the token-exhaustion incident).

auth_fp source = /proc (ground truth = what the process actually runs), FAIL-SOFT:
a fp read hiccup must NEVER fail the lease renew, and must RETAIN the last-known fp
(never NULL it). This file locks the pure fp-from-/proc-environ extraction + the
fail-soft contract; the DB write uses COALESCE(new_fp, auth_fp) for the retain.
"""
import hashlib
import importlib

ol = importlib.import_module("scripts.lib.orch_lease")


def test_hub_auth_fp_from_environ_extracts_and_sha256_12():
    tok = "sk-ant-oat01-EXAMPLE-TOKEN-VALUE"
    environ = f"PATH=/usr/bin\x00CLAUDE_CODE_OAUTH_TOKEN={tok}\x00HOME=/root\x00"
    expect = hashlib.sha256(tok.encode()).hexdigest()[:12]  # = the console badge format
    assert ol._hub_auth_fp_from_environ(environ) == expect


def test_hub_auth_fp_none_when_token_absent():
    # fail-soft: no token in environ -> None (caller retains last-known, skips field)
    assert ol._hub_auth_fp_from_environ("PATH=/usr/bin\x00HOME=/root\x00") is None


def test_hub_auth_fp_none_on_empty_or_garbage():
    assert ol._hub_auth_fp_from_environ("") is None
    assert ol._hub_auth_fp_from_environ(None) is None


def test_hub_auth_fp_handles_token_with_equals_in_value():
    tok = "sk-ant-oat01-aa==bb"  # value may contain '='; split once
    environ = f"CLAUDE_CODE_OAUTH_TOKEN={tok}\x00"
    assert ol._hub_auth_fp_from_environ(environ) == hashlib.sha256(tok.encode()).hexdigest()[:12]


def test_read_hub_auth_fp_failsoft_returns_none_not_raises():
    # injected seams: no pid found -> None, never an exception (fail-soft contract).
    assert ol._read_hub_auth_fp(find_pid=lambda: None, read_environ=lambda p: "") is None
    # environ read raises -> None (never propagate; the renew must not fail)
    def boom(p):
        raise OSError("permission denied")
    assert ol._read_hub_auth_fp(find_pid=lambda: 123, read_environ=boom) is None


def test_read_hub_auth_fp_happy_path():
    tok = "sk-ant-oat01-LIVE"
    fp = ol._read_hub_auth_fp(find_pid=lambda: 999,
                             read_environ=lambda p: f"CLAUDE_CODE_OAUTH_TOKEN={tok}\x00")
    assert fp == hashlib.sha256(tok.encode()).hexdigest()[:12]


def test_hub_hb_write_sql_uses_coalesce_retain():
    # the retain guarantee is SQL-level: auth_fp = COALESCE(new, auth_fp) so a NULL
    # new fp keeps the existing value (never blanks the hub key).
    sql = ol._HUB_HB_SQL
    assert "COALESCE" in sql and "auth_fp" in sql and "last_heartbeat=now()" in sql
    assert "cc-orchestrator" in sql or "%s" in sql  # parameterized target
