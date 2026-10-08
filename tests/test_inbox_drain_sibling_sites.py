"""DB-integration tests proving the sibling COUNT sites now see an instance-addressed
row (bus #51166 / #58271 / orch-console #59675), with a singleton guard that a
base==instance lane is unchanged.

Sites covered:
  - scripts/idle_with_work_watchdog.gather_owned_work  (lane owned-work bus read)
  - nervous_system/lane_wedge_watchdog.read_bus_signal (wedge unread pileup)
  - scripts/lib/lane_winddown.live_unread_count        (wind-down gate)
  - scripts/scheduled_cc_sweep.sh                      (family spawn prefilter — the
        actual embedded python, extracted + run against the test DB)

Skipped unless a LOCAL Postgres DSN is set (scripts/pytest_local.sh Option A); the
prod-ref guard refuses a prod DSN regardless.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


def _dsn():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set — DB-integration test (scripts/pytest_local.sh Option A)")
    return dsn


def _conn():
    import psycopg
    return psycopg.connect(_dsn(), autocommit=True)


def _seed_lane(cur, base, instance, sess):
    """A live lane: fleet_lanes row + agent_status instance row on this session."""
    cur.execute("INSERT INTO fleet_lanes (lane, base_agent_id) VALUES (%s,%s)", (sess, base))
    cur.execute(
        "INSERT INTO agent_status (agent_id, base_agent_id, tmux_session, status, "
        "last_heartbeat, updated_at) VALUES (%s,%s,%s,'working', now(), now())",
        (instance, base, sess))


def _bus(cur, to_agent, *, rr=True, prio="P1", age_min=40):
    cur.execute(
        "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
        "requires_response, priority, created_at) "
        "VALUES ('orch-console',%s,'task','op row','x',%s,%s, now() - make_interval(mins => %s))",
        (to_agent, rr, prio, age_min))


# ── idle_with_work_watchdog.gather_owned_work ────────────────────────────────
def test_idle_with_work_sees_instance_bus_row():
    from scripts import idle_with_work_watchdog as iww
    tag = uuid.uuid4().hex[:8]
    base, inst, sess = f"cc-zz-{tag}", f"cc-zz-{tag}-2", f"zz-lane-{tag}"
    with _conn() as conn, conn.cursor() as cur:
        _seed_lane(cur, base, inst, sess)
        _bus(cur, inst)  # addressed to the INSTANCE only
        try:
            items = iww.gather_owned_work(cur, base, sess)
            bus_items = [i for i in items if i["source"] == "agent_messages"]
            assert bus_items, "instance-addressed bus row missed by gather_owned_work"
        finally:
            cur.execute("DELETE FROM agent_messages WHERE to_agent=%s", (inst,))
            cur.execute("DELETE FROM agent_status WHERE agent_id=%s", (inst,))
            cur.execute("DELETE FROM fleet_lanes WHERE lane=%s", (sess,))


def test_idle_with_work_singleton_base_only_unchanged():
    from scripts import idle_with_work_watchdog as iww
    tag = uuid.uuid4().hex[:8]
    base, sess = f"cc-zzs-{tag}", f"zz-s-{tag}"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO fleet_lanes (lane, base_agent_id) VALUES (%s,%s)", (sess, base))
        _bus(cur, base)  # base-addressed, no instance row at all
        try:
            items = iww.gather_owned_work(cur, base, sess)
            assert [i for i in items if i["source"] == "agent_messages"], \
                "base-addressed bus row must still be seen for a singleton"
        finally:
            cur.execute("DELETE FROM agent_messages WHERE to_agent=%s", (base,))
            cur.execute("DELETE FROM fleet_lanes WHERE lane=%s", (sess,))


# ── lane_wedge_watchdog.read_bus_signal ──────────────────────────────────────
def test_lane_wedge_counts_instance_addressed_row():
    import nervous_system.lane_wedge_watchdog as lw
    tag = uuid.uuid4().hex[:8]
    base, inst, sess = f"cc-zzw-{tag}", f"cc-zzw-{tag}-2", f"zz-w-{tag}"
    with _conn() as conn, conn.cursor() as cur:
        _seed_lane(cur, base, inst, sess)
        _bus(cur, inst, age_min=40)  # inside the [20m,6h] wedge window
        try:
            sig = lw.read_bus_signal(base, conn, session=sess)
            assert sig.unread >= 1, f"instance-addressed row not counted in wedge signal — {sig.unread}"
            # regression guard: base-only (no session) misses it
            sig_base = lw.read_bus_signal(base, conn, session=None)
            assert sig_base.unread == 0, "base-only read should miss the instance row"
        finally:
            cur.execute("DELETE FROM agent_messages WHERE to_agent=%s", (inst,))
            cur.execute("DELETE FROM agent_status WHERE agent_id=%s", (inst,))
            cur.execute("DELETE FROM fleet_lanes WHERE lane=%s", (sess,))


def test_lane_wedge_singleton_unaffected():
    import nervous_system.lane_wedge_watchdog as lw
    tag = uuid.uuid4().hex[:8]
    base, sess = f"cc-zzws-{tag}", f"zz-ws-{tag}"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_status (agent_id, base_agent_id, tmux_session, status, "
            "last_heartbeat, updated_at) VALUES (%s,%s,%s,'working', now(), now())",
            (base, base, sess))
        _bus(cur, base, age_min=40)
        try:
            sig = lw.read_bus_signal(base, conn, session=sess)
            assert sig.unread >= 1, "base-addressed row must still count for a singleton"
        finally:
            cur.execute("DELETE FROM agent_messages WHERE to_agent=%s", (base,))
            cur.execute("DELETE FROM agent_status WHERE agent_id=%s", (base,))


# ── lane_winddown.live_unread_count ──────────────────────────────────────────
def test_lane_winddown_counts_both_base_and_instance():
    from scripts.lib import lane_winddown as lwd
    tag = uuid.uuid4().hex[:8]
    base, inst, sess = f"cc-zzd-{tag}", f"cc-zzd-{tag}-2", f"zz-d-{tag}"
    with _conn() as conn, conn.cursor() as cur:
        _seed_lane(cur, base, inst, sess)
        _bus(cur, inst)   # instance-addressed
        _bus(cur, base)   # base-addressed
        try:
            os.environ["DATABASE_URL"] = _dsn()
            n = lwd.live_unread_count(sess)
            assert n == 2, f"wind-down gate must count BOTH base and instance mail — got {n}"
        finally:
            cur.execute("DELETE FROM agent_messages WHERE to_agent = ANY(%s)", ([base, inst],))
            cur.execute("DELETE FROM agent_status WHERE agent_id=%s", (inst,))
            cur.execute("DELETE FROM fleet_lanes WHERE lane=%s", (sess,))


# ── scheduled_cc_sweep.sh — the embedded python prefilter, extracted + executed ──
def _extract_sweep_prefilter() -> str:
    """The exact python heredoc from scheduled_cc_sweep.sh (wet-prove the shipped
    bytes, not a paraphrase). Sliced between the PYEOF markers."""
    text = (_ROOT / "scripts" / "scheduled_cc_sweep.sh").read_text()
    m = re.search(r"<<'PYEOF'[^\n]*\n(.*?)\nPYEOF", text, re.S)
    assert m, "could not locate the PYEOF prefilter block in scheduled_cc_sweep.sh"
    return m.group(1)


def test_scheduled_sweep_family_count_includes_instance():
    dsn = _dsn()
    tag = uuid.uuid4().hex[:8]
    family, inst = f"cc-zzf-{tag}", f"cc-zzf-{tag}-2"
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_status (agent_id, base_agent_id, tmux_session, status, "
            "last_heartbeat, updated_at) VALUES (%s,%s,%s,'working', now(), now())",
            (inst, family, f"zz-f-{tag}"))
        _bus(cur, inst)  # family work addressed to the instance only
    try:
        src = _extract_sweep_prefilter()
        script = _ROOT / "tests" / f"_sweep_prefilter_{tag}.py"
        script.write_text(src)
        env = dict(os.environ, SCHEDULED_FAMILY=family, DATABASE_URL=dsn)
        env.pop("SUPABASE_DB_URL", None)
        r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=30)
        out = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
        assert re.match(r"^\d+,\d+,\d+$", out), f"prefilter did not emit counts: {out!r} / {r.stderr[-400:]}"
        unread = int(out.split(",")[0])
        assert unread >= 1, f"family sweep must count the instance-addressed row — got {out}"
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM agent_messages WHERE to_agent=%s", (inst,))
            cur.execute("DELETE FROM agent_status WHERE agent_id=%s", (inst,))
        try:
            script.unlink()
        except Exception:
            pass
