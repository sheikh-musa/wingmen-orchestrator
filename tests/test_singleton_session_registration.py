"""Singleton tmux_session registration must be DETERMINISTIC + SELF-HEALING.

Context (2026-09-10, bus 38667-38673): cai's wakes misrouted into orch-console's
pane because cai's agent_status.tmux_session was stale='nazim'. Root cause: the
singleton boots captured the session via `tmux display-message -p '#S'` ONLY at
boot, so a boot that ran inside a FOREIGN pane stamped the wrong session, and the
heartbeat never re-asserted tmux_session, so the drift persisted until reboot.

The CLASS (same vulnerable pattern): boot_cai.sh, boot_fleet_health.sh,
boot_quality.sh. The console path (boot_nazim_bus_notify.sh) already does it the
safe way: ORCH_TMUX_SESSION="${ORCH_TMUX_SESSION:-nazim}" — a deterministic,
env-overridable canonical default, NOT display-message. These tests pin that fix
across the class:

  (A) each boot resolves its session as `${VAR:-<canonical>}`, never via
      `tmux display-message` (a foreign-pane boot can no longer mis-stamp);
  (B) each boot's HEARTBEAT loop re-asserts tmux_session every beat (drift
      self-heals within <=5min instead of persisting to reboot);
  (C) behavioral proof (DB-executing, rolled back): the ACTUAL heartbeat UPDATE
      extracted from boot_fleet_health.sh corrects a drifted tmux_session on the
      live cc-fleet-health row (identity-permitted: SRE writes its own row).
"""
import os
import re
import pathlib

import psycopg2
import pytest

ORCH = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ORCH / "scripts"

# (boot script, shell var, canonical session name)
SINGLETONS = [
    ("boot_cai.sh", "CAI_TMUX_SESSION", "cai", "cai"),
    ("boot_fleet_health.sh", "FH_TMUX_SESSION", "fleet-health", "cc-fleet-health"),
    ("boot_quality.sh", "Q_TMUX_SESSION", "quality", "cc-quality"),
]


def _text(script: str) -> str:
    return (SCRIPTS / script).read_text()


def _heartbeat_block(text: str) -> str:
    """The body of the background heartbeat loop (between its `()` def and the
    `&` that backgrounds it). The per-beat re-assert lives HERE, not in the
    one-shot boot INSERT or the _handle_exit offline path."""
    m = re.search(r"_(?:quality_)?heartbeat_loop\(\)\s*\{(.*?)\n_(?:quality_)?heartbeat_loop &",
                  text, re.S)
    assert m, "could not locate the background heartbeat loop block"
    return m.group(1)


@pytest.mark.parametrize("script,var,canonical,agent_id", SINGLETONS)
def test_boot_registers_canonical_session_not_via_display_message(script, var, canonical, agent_id):
    """(A) Deterministic, env-overridable canonical default — no display-message.

    Fails on the pre-fix code, which captured the session from whatever pane the
    boot ran in (`VAR="$(tmux display-message -p '#S' ...)"`)."""
    text = _text(script)
    # The capture must be the env-overridable canonical default.
    assert re.search(rf'{var}="\$\{{{var}:-{re.escape(canonical)}\}}"', text), (
        f"{script}: {var} must be \"${{{var}:-{canonical}}}\" (deterministic default)")
    # And must NOT fall back to display-message for the session capture.
    assert "display-message -p '#S'" not in text, (
        f"{script}: session must not be captured via `tmux display-message` "
        "(a foreign-pane boot would mis-stamp tmux_session)")


@pytest.mark.parametrize("script,var,canonical,agent_id", SINGLETONS)
def test_heartbeat_reasserts_tmux_session(script, var, canonical, agent_id):
    """(B) The heartbeat UPDATE re-asserts tmux_session so drift self-heals.

    Fails on the pre-fix heartbeat, which bumped only last_heartbeat/status."""
    block = _heartbeat_block(_text(script))
    update = re.search(r'UPDATE agent_status SET [^"]*?WHERE agent_id=', block)
    assert update, f"{script}: no agent_status UPDATE in the heartbeat loop"
    assert "tmux_session" in update.group(0), (
        f"{script}: heartbeat UPDATE must re-assert tmux_session (self-heal drift)")


def test_fleet_health_heartbeat_self_heals_drifted_session_in_db():
    """(C) Behavioral: run the ACTUAL extracted heartbeat UPDATE from
    boot_fleet_health.sh against a deliberately-drifted cc-fleet-health row and
    confirm it self-heals to 'fleet-health'. Rolled back — no live effect.

    Identity-permitted: enforce_agent_status_identity() allows writing the SRE's
    OWN row (GUC == agent_id). Exercises the shipped artifact, not a replica."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        pytest.skip("no DATABASE_URL")

    block = _heartbeat_block(_text("boot_fleet_health.sh"))
    m = re.search(r'cur\.execute\(\s*"(UPDATE agent_status SET [^"]*?'
                  r"WHERE agent_id='cc-fleet-health')\"", block)
    assert m, "could not extract the heartbeat UPDATE statement"
    sql = m.group(1)
    nparams = sql.count("%s")
    params = tuple(["fleet-health"] * nparams)

    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
        # Drift it.
        cur.execute("UPDATE agent_status SET tmux_session='__drift_test__' "
                    "WHERE agent_id='cc-fleet-health'")
        # Run the REAL heartbeat UPDATE.
        cur.execute(sql, params)
        cur.execute("SELECT tmux_session FROM agent_status WHERE agent_id='cc-fleet-health'")
        got = cur.fetchone()[0]
        assert got == "fleet-health", (
            f"heartbeat did not self-heal the drift: tmux_session={got!r}")
    finally:
        conn.rollback()  # never persist the drift or the heal
        conn.close()
