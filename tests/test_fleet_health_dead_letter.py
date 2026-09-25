"""fleet_health SURFACES (never reaps) unread rows whose to_agent has no live wake owner.

WHY (substrate audit #5B). Operator/non-agent addresses like 'musa' and 'substrate' are NOT
wake-eligible recipients (agent_wake never delivers to them), so messages sent there
dead-letter unread forever — and step-4's archive deliberately SPARES them, because reaping
would hide a real misroute. This detector makes them VISIBLE: one coalesced surface to
orch-console per (to_agent) per day, NEVER reaped. Deliverable identities (cc-* lanes, cai,
console, and the hub on its P0/P1 floor) are NOT flagged — a dead-but-once-live cc lane is
step-4's job, not this detector's.

Prod-clean: pure classification only, no DB (importing fleet_health must not load .env under
pytest — the module guards load_dotenv on PYTEST_CURRENT_TEST).
"""
import os
import pytest

from scripts import fleet_health as fh

DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def test_operator_and_non_address_are_undeliverable():
    assert fh._undeliverable("musa"), "the operator handle has no live wake owner"
    assert fh._undeliverable("substrate"), "'substrate' is a non-address, no wake owner"


def test_identities_with_a_wake_owner_are_not_flagged():
    assert not fh._undeliverable("cc-quality"), "a worker lane is a deliverable identity"
    assert not fh._undeliverable("cai")
    assert not fh._undeliverable("orch-console")
    # the hub is eligible on its narrow P0/P1 floor — the detector uses that floor, so it is
    # NEVER treated as an undeliverable dead-letter sink.
    assert not fh._undeliverable("cc-orchestrator")


def test_none_target_is_not_flagged():
    # a NULL to_agent is filtered by the query; the predicate must not crash on it either.
    assert not fh._undeliverable(None)


def test_nervous_system_resolves_under_script_invocation(tmp_path):
    """Regression guard for 99de7e3 (#5B): the launchd daemon runs `python3 scripts/fleet_health.py`,
    so sys.path[0] is scripts/ — NOT the repo root. _undeliverable()'s lazy
    `from nervous_system.agent_wake import is_wake_eligible_recipient` then raised
    ModuleNotFoundError and crash-looped the job every 10 min. The other tests here import via
    `from scripts import fleet_health`, which pytest silently masks (repo root already on path),
    so they never caught it. This reproduces the DAEMON's invocation faithfully: a fresh
    interpreter whose sys.path[0] is scripts/ (nothing else), then import fleet_health (which
    must bootstrap the repo root onto sys.path at module load) and resolve nervous_system.

    Prod-clean: PYTEST_CURRENT_TEST is set so fleet_health skips load_dotenv; we only exercise
    the import path, never the DB sweep."""
    import os, subprocess, sys
    orch = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    scripts_dir = os.path.join(orch, "scripts")
    probe = (
        "import sys; "
        f"sys.path.insert(0, {scripts_dir!r}); "      # emulate the daemon: sys.path[0] == scripts/
        "import fleet_health; "                          # module-top bootstrap must add the repo root
        "from nervous_system.agent_wake import is_wake_eligible_recipient; "
        "print('IMPORT_OK')"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}  # don't let PYTHONPATH mask it
    env["PYTEST_CURRENT_TEST"] = "1"                                  # keep load_dotenv off (prod-clean)
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                       cwd=str(tmp_path), env=env)  # cwd off-repo so the root isn't on path via cwd
    assert r.returncode == 0, f"nervous_system unresolved under script invocation (99de7e3): {r.stderr}"
    assert "IMPORT_OK" in r.stdout


# --- cursor-relay exemption (2026-09-10) ------------------------------------- #
# The operator ('musa') ⚠️ weekly-pace rows are CONSUMED by weekly_alert_relay (a durable-cursor
# daemon that pushes each to the operator's phone) — delivered, not dead — but a cursor never sets
# read_at, so the read_at/agent_wake detector false-flagged them as dead-letters. Fix exempts EXACTLY
# the relay's tuple; a genuine 'musa' misroute (different producer/subject) must still surface.

# --- human-opened liaison addresses (2026-09-25, Nazim #43120) --------------- #
# cto-desktop is a HUMAN-OPENED Claude Desktop liaison session (Musa's), not an agent and
# not a misroute. Its rows are superseded, not lost — must NOT be archived (would falsify
# receipt on the human's behalf) and must NOT be paged daily; a weekly one-line count is
# enough so rows don't rot unseen. Same never-touch class as 'musa'/'operator'.

def test_cto_desktop_is_classified_human_opened():
    assert fh._human_opened("cto-desktop")
    assert fh._human_opened("CTO-DESKTOP")  # case-insensitive
    assert not fh._human_opened("cc-quality")
    assert not fh._human_opened("musa")  # musa is operator, handled by its own never-archive entry
    assert not fh._human_opened(None)


def test_human_opened_addrs_are_in_the_never_archive_set():
    # step-4 archive must SPARE human-opened addresses (don't mark their rows read)
    assert "cto-desktop" in fh._NEVER_ARCHIVE_ADDRS
    for a in ("musa", "operator", "substrate"):
        assert a in fh._NEVER_ARCHIVE_ADDRS


def test_surface_uses_WEEKLY_dedup_for_human_opened():
    """A human-opened address is surfaced at most once per WEEK (not per day)."""
    class _Cur:
        def __init__(self): self.sqls = []
        def execute(self, sql, params=None): self.sqls.append(sql)
        def fetchall(self):
            return [("cto-desktop", 8, "2026-09-22", "2026-09-24", 1)]
        def fetchone(self): return None  # not surfaced this period yet
    cur = _Cur()
    fh.surface_dead_letters(cur, dry=True)
    dedup_sqls = [s for s in cur.sqls if "date_trunc" in s]
    assert any("date_trunc('week'" in s for s in dedup_sqls), \
        "human-opened cto-desktop must dedup on WEEK, not day"


def test_relay_consumed_is_exactly_the_relay_tuple():
    assert fh._relay_consumed("musa", "cc-fleet-health", "⚠️ Pace warning — Musa pool may run out")
    # any leg of the tuple differing -> NOT exempt (a real misroute must stay flaggable)
    assert not fh._relay_consumed("musa", "cc-fleet-health", "a normal subject with no warning glyph")
    assert not fh._relay_consumed("musa", "cc-quality", "⚠️ from a different producer")
    assert not fh._relay_consumed("operator", "cc-fleet-health", "⚠️ different address")
    assert not fh._relay_consumed(None, None, None)


def test_exemption_sql_is_scoped_not_blanket():
    """Nazim's gate condition: scoped to the EXACT relay tuple, never a blanket 'musa' or
    blanket-⚠️ that could hide a real future dead-letter to a different producer/address."""
    sql = fh._RELAY_CONSUMED_SQL
    assert "'musa'" in sql and "cc-fleet-health" in sql and "⚠️" in sql
    assert sql.count(" AND ") >= 2, "all three legs must be ANDed (a blanket suppression drops one)"


def test_surface_query_carries_the_exemption():
    """The dead-letter aggregation query must EXCLUDE the relay-consumed set."""
    class _Cur:
        def __init__(self):
            self.sqls = []

        def execute(self, sql, params=None):
            self.sqls.append(sql)

        def fetchall(self):
            return []

        def fetchone(self):
            return None

    cur = _Cur()
    assert fh.surface_dead_letters(cur, dry=True) == []
    agg = cur.sqls[0]
    assert "NOT (" in agg and fh._RELAY_CONSUMED_SQL in agg, \
        "surface_dead_letters must exclude the relay-consumed set from its count"


@pytest.mark.skipif(not DSN, reason="DATABASE_URL not set (DB-executing test)")
def test_exemption_sql_null_subject_semantics_EXECUTED():
    """EXECUTED (rolled back): guards the SQL's three-valued NULL semantics the pure twin + string
    tests cannot. Fed a synthetic NULL subject via VALUES (a real INSERT can't — agent_messages.
    subject is NOT NULL, so the fail-open is unreachable in PRACTICE; this is defense-in-depth +
    twin-parity). With the coalesce() fix, NOT(exemption) for a NULL-subject cc-fleet-health->musa
    row is TRUE (the dead-letter WHERE KEEPS it = surfaces); without coalesce it would be NULL
    (dropped = a fail-open hide). Also checks a ⚠️ row is exempted and a plain row is not."""
    import psycopg
    with psycopg.connect(DSN, autocommit=False) as conn, conn.cursor() as cur:
        try:
            cur.execute(
                f"SELECT subject, NOT ({fh._RELAY_CONSUMED_SQL}) AS surfaces "
                f"FROM (VALUES ('musa','cc-fleet-health', NULL::text), "
                f"             ('musa','cc-fleet-health', '⚠️ pace'::text), "
                f"             ('musa','cc-fleet-health', 'plain misroute'::text)) "
                f"     t(to_agent, from_agent, subject) "
                f"ORDER BY 1 NULLS FIRST")
            rows = {r[0]: r[1] for r in cur.fetchall()}
            assert rows[None] is True, "NULL subject must NOT be exempted (coalesce null-safety)"
            assert rows["⚠️ pace"] is False, "a ⚠️ row IS relay-consumed -> exempted (not surfaced)"
            assert rows["plain misroute"] is True, "a non-⚠️ musa row still surfaces"
        finally:
            conn.rollback()


@pytest.mark.skipif(not DSN, reason="DATABASE_URL not set (DB-executing test)")
def test_surface_dead_letters_end_to_end_EXECUTED():
    """EXECUTED (rolled back, zero residue): run the actual surface_dead_letters against real rows.
    An empty-subject cc-fleet-health->musa misroute SURFACES; a ⚠️ relay row is EXEMPTED. (Empty '' is
    the insertable stand-in for the NULL case, which the NOT NULL constraint forbids.)"""
    import psycopg
    with psycopg.connect(DSN, autocommit=False) as conn, conn.cursor() as cur:
        try:
            # isolate the 'musa' group + clear today's dedup so the surface decision turns ONLY on
            # the exemption, not on live rows / a prior advisory. All rolled back.
            cur.execute("DELETE FROM agent_messages WHERE to_agent='musa' AND read_at IS NULL")
            cur.execute("DELETE FROM agent_messages WHERE from_agent='cc-fleet-health' "
                        "AND to_agent='orch-console' AND subject LIKE 'dead-letter[musa]:%' "
                        "AND created_at >= date_trunc('day', now())")
            cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
            cur.execute("INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
                        "priority,requires_response) VALUES "
                        "('cc-fleet-health','musa','update','','(test body)','P2',false)")   # non-⚠️ misroute
            assert "musa" in fh.surface_dead_letters(cur, dry=True), \
                "a non-⚠️ cc-fleet-health->musa row must SURFACE"
            cur.execute("DELETE FROM agent_messages WHERE to_agent='musa' AND read_at IS NULL")
            cur.execute("INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
                        "priority,requires_response) VALUES "
                        "('cc-fleet-health','musa','update','⚠️ Pace warning — test row','(test body)','P2',false)")
            assert "musa" not in fh.surface_dead_letters(cur, dry=True), \
                "a ⚠️ relay-consumed row must be exempted (delivered by the cursor relay, not dead)"
        finally:
            conn.rollback()
