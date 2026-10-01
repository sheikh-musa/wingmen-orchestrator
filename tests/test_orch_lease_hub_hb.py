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


# --- 2026-10-01: the hub's renew also stamps its LIVE model (operator: "I still
# don't see the hub's model"). The console on the Mini cannot read the gzb hub's
# process, so the hub reports the model from its own /proc argv as a `model=<id>`
# token in current_task. Unreadable -> None -> current_task untouched (never a guess).

def _argv(*a):
    return "\x00".join(a) + "\x00"


def test_hub_model_from_cmdline_reads_model_flag():
    cmd = _argv("/usr/bin/claude", "--dangerously-skip-permissions", "--continue",
                "--model", "claude-opus-4-8")
    assert ol._hub_model_from_proc(cmd) == "claude-opus-4-8"


def test_hub_model_last_model_flag_wins_and_equals_form():
    cmd = _argv("claude", "--model", "claude-opus-4-8", "--model=claude-sonnet-5")
    assert ol._hub_model_from_proc(cmd) == "claude-sonnet-5"


def test_hub_model_falls_back_to_anthropic_model_environ():
    cmd = _argv("claude", "--dangerously-skip-permissions", "--continue")
    env = "PATH=/usr/bin\x00ANTHROPIC_MODEL=claude-opus-5-5\x00"
    assert ol._hub_model_from_proc(cmd, env) == "claude-opus-5-5"


def test_hub_model_none_when_unreadable_never_a_default():
    assert ol._hub_model_from_proc(_argv("claude", "--continue"), "PATH=/x\x00") is None
    assert ol._hub_model_from_proc("", None) is None
    assert ol._hub_model_from_proc(_argv("claude", "--model", "; drop table x")) is None


def test_read_hub_model_failsoft():
    assert ol._read_hub_model(find_pid=lambda: None) is None

    def boom(p):
        raise OSError("gone")
    assert ol._read_hub_model(find_pid=lambda: 7, read_cmdline=boom, read_environ=boom) is None
    assert ol._read_hub_model(
        find_pid=lambda: 7, read_cmdline=lambda p: _argv("claude", "--model", "claude-opus-4-8"),
        read_environ=boom) == "claude-opus-4-8"


class _Cur:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))


def test_write_hub_heartbeat_passes_model_and_null_model_leaves_task():
    cur = _Cur()
    ol._write_hub_heartbeat(cur, "abc123def456", "claude-opus-4-8")
    sql, params = cur.calls[0]
    assert params == ("abc123def456", "claude-opus-4-8", "claude-opus-4-8", "cc-orchestrator")
    # NULL model -> CASE keeps current_task as-is (no blanking, no guess)
    assert "WHEN %s::text IS NULL THEN current_task" in sql
    cur2 = _Cur()
    ol._write_hub_heartbeat(cur2, None)
    assert cur2.calls[0][1] == (None, None, None, "cc-orchestrator")


def test_hub_model_token_is_the_form_the_console_parses():
    """The SQL appends ' model=<id>'; the console's parser must read it back."""
    from nervous_system.console import app as console_app
    task = "hub — always-on orchestrator (VPS) model=claude-opus-4-8"
    assert console_app._resolve_model("orch", task, None, {}) == ("claude-opus-4-8", "hb")
