"""test_bus_send.py — the ONE sanctioned bus-post CLI enforces priority (bus #43651).

orch-console's ask after op#22517's aftermath (a hand-written INSERT that
omitted `priority` silently landed at the table default 'P2', below the hub's
wake floor — reference_hub_wake_floor_p1_rr): make it structural, never a
promise. These are PURE-LOGIC / argparse tests — no live DB, no psycopg
connection (matches the style of test_operator_log_scope.py).
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
import bus_send as bs  # noqa: E402


# ---- --priority is REQUIRED, no default ------------------------------------

def test_priority_is_required():
    with pytest.raises(SystemExit):
        bs.build_parser().parse_args([
            "--to", "cc-orchestrator", "--type", "update",
            "--subject", "s", "--req",
        ])


def test_priority_rejects_unknown_value():
    with pytest.raises(SystemExit):
        bs.build_parser().parse_args([
            "--to", "cc-orchestrator", "--type", "update",
            "--subject", "s", "--priority", "URGENT",
        ])


@pytest.mark.parametrize("p", ["P0", "P1", "P2", "P3"])
def test_priority_accepts_each_valid_value(p):
    args = bs.build_parser().parse_args([
        "--to", "cc-orchestrator", "--type", "update",
        "--subject", "s", "--priority", p,
    ])
    assert args.priority == p


def test_message_type_rejects_unknown_value():
    with pytest.raises(SystemExit):
        bs.build_parser().parse_args([
            "--to", "cc-orchestrator", "--type", "nonsense",
            "--subject", "s", "--priority", "P1",
        ])


# ---- identity resolution: fail closed, never guess --------------------------

def test_identity_prefers_cc_base_agent_id():
    env = {"CC_BASE_AGENT_ID": "cc-substrate", "AGENT_ID": "should-not-win",
           "ORCH_BODY_ROLE": "console", "ORCH_AGENT_ID": "orch-console"}
    assert bs.resolve_from_agent(env) == "cc-substrate"


def test_identity_falls_back_to_agent_id():
    env = {"AGENT_ID": "cc-fleet-health"}
    assert bs.resolve_from_agent(env) == "cc-fleet-health"


def test_identity_uses_orch_agent_id_only_when_console_role():
    env = {"ORCH_BODY_ROLE": "console", "ORCH_AGENT_ID": "orch-console"}
    assert bs.resolve_from_agent(env) == "orch-console"


def test_identity_ignores_orch_agent_id_when_role_is_not_console():
    # ORCH_AGENT_ID is fleet-wide .env noise (every launcher sources .env) —
    # must never be trusted outside the one documented console-fallback case.
    env = {"ORCH_AGENT_ID": "orch-console"}
    with pytest.raises(bs.IdentityError):
        bs.resolve_from_agent(env)


def test_identity_refuses_rather_than_guesses_when_nothing_resolves():
    with pytest.raises(bs.IdentityError):
        bs.resolve_from_agent({})


# ---- empty-body guard (Nazim shipped 3 blank rows on 2026-09-05) -----------

def test_read_body_refuses_short_body():
    import io
    with pytest.raises(SystemExit):
        bs.read_body(io.StringIO("too short"))


def test_read_body_accepts_body_at_or_above_floor():
    import io
    body = "x" * bs._MIN_BODY_BYTES
    assert bs.read_body(io.StringIO(body)) == body


# ---- end-to-end CLI, --dry-run so it never touches the DB -------------------

def test_dry_run_end_to_end(monkeypatch, capsys):
    import io
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    rc = bs.main([
        "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
        "--priority", "P1", "--req", "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "priority=P1" in out
    assert "from=cc-substrate" in out


def test_dry_run_still_enforces_empty_body_guard(monkeypatch):
    import io
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setattr(sys, "stdin", io.StringIO("too short"))
    with pytest.raises(SystemExit):
        bs.main([
            "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
            "--priority", "P1", "--dry-run",
        ])


def test_main_refuses_when_identity_cannot_resolve(monkeypatch, capsys):
    import io
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * bs._MIN_BODY_BYTES))
    rc = bs.main([
        "--to", "cc-orchestrator", "--type", "update", "--subject", "s",
        "--priority", "P1", "--dry-run",
    ])
    assert rc == 2
    assert "cannot resolve" in capsys.readouterr().err


# ---- send() — the one INSERT, reused by _bus_tmp.py / scratchpad shims -----
# (bus #43673). priority validation happens before any DB connection, so
# these are safe to test directly with no live DB.

def test_send_rejects_invalid_priority():
    with pytest.raises(ValueError):
        bs.send("cc-substrate", "cc-orchestrator", "update", "s",
                 "x" * bs._MIN_BODY_BYTES, "URGENT")


def test_send_rejects_missing_priority():
    with pytest.raises(ValueError):
        bs.send("cc-substrate", "cc-orchestrator", "update", "s",
                 "x" * bs._MIN_BODY_BYTES, None)


@pytest.mark.parametrize("p", ["P0", "P1", "P2", "P3"])
def test_send_accepts_valid_priority_then_reaches_the_db_call(monkeypatch, p):
    # Confirms priority validation doesn't reject a valid value — push past
    # it into psycopg2.connect (monkeypatched to fail fast) so no real DB
    # connection is attempted.
    import psycopg2

    def _boom(*a, **k):
        raise RuntimeError("no live DB in tests")

    monkeypatch.setattr(psycopg2, "connect", _boom)
    with pytest.raises(RuntimeError, match="no live DB in tests"):
        bs.send("cc-substrate", "cc-orchestrator", "update", "s",
                 "x" * bs._MIN_BODY_BYTES, p)
