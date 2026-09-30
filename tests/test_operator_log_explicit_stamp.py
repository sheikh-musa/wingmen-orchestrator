"""operator_log: explicit-id stamping + no unscoped body role (Fable audit 2026-09-30
items B-2 / S-? — 'stamp-past-unanswered', op#23531 helper PR B).

The two silent-loss mechanisms this locks out:
  1. An UNSET ORCH_BODY_ROLE ("") used to be a SANCTIONED role with NO channel scope, so a
     lane (launch_dangerous_cc.sh unsets the role for every lane) calling
     mark_handled_through(N) stamped EVERY channel's inbound <= N handled in one call,
     attributed to cc-orchestrator. Now: unset == 'lane', and a lane MUST pass tag=.
  2. unprocessed(limit=20) returns at most 20 rows but mark_handled_through(max_id) was a
     high-water stamp over ALL rows <= max_id — the 21st+ row (never shown to the caller)
     was stamped handled without ever being read. Now: mark_handled(ids) stamps ONLY the
     ids given, and the deprecated mark_handled_through() is bounded to the ids the caller's
     own last unprocessed() read actually returned.

DB tests run on the ephemeral PG17 fixture (operator_ledger_db) — never the live substrate.
"""
import os
import sys

import psycopg
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from nervous_system import operator_log as ol  # noqa: E402


def _seed(dsn, rows):
    """rows: list of (tag, text). Returns ids in insertion order."""
    ids = []
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        for tag, text in rows:
            cur.execute(
                "INSERT INTO operator_messages (direction, channel, tag, text) "
                "VALUES ('inbound','telegram',%s,%s) RETURNING id", (tag, text))
            ids.append(cur.fetchone()[0])
    return ids


def _handled(dsn):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM operator_messages WHERE handled_at IS NOT NULL ORDER BY id")
        return [r[0] for r in cur.fetchall()]


@pytest.fixture
def ledger_db(operator_ledger_db):
    """operator_ledger_db + the handled/deferred columns the real table carries
    (migrations 021/072; the shared fixture predates them). ALTER on the EPHEMERAL
    cluster only — never the substrate."""
    with psycopg.connect(operator_ledger_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE operator_messages "
                    "ADD COLUMN IF NOT EXISTS handled_at timestamptz, "
                    "ADD COLUMN IF NOT EXISTS deferred_at timestamptz, "
                    "ADD COLUMN IF NOT EXISTS deferred_by text, "
                    "ADD COLUMN IF NOT EXISTS deferred_reason text")
    return operator_ledger_db


@pytest.fixture
def lane_role(monkeypatch):
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    monkeypatch.delenv("ORCH_AGENT_ID", raising=False)
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-oeh")
    ol._reset_read_cursor()


@pytest.fixture
def hub_role(monkeypatch):
    monkeypatch.setenv("ORCH_BODY_ROLE", "hub")
    monkeypatch.setenv("ORCH_AGENT_ID", "cc-orchestrator")
    ol._reset_read_cursor()


# ---- pure: role contract ----

def test_b_empty_role_is_not_an_unscoped_role(monkeypatch):
    """(b) '' is rejected as a role of its own: it means 'lane' and a lane needs tag=."""
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    assert ol._body_role() == "lane"
    assert "" not in ol._RECOGNIZED_BODY_ROLES
    assert ol._RECOGNIZED_BODY_ROLES == frozenset({"console", "hub", "lane"})


def test_lane_scope_without_tag_raises(monkeypatch):
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    with pytest.raises(ValueError) as exc:
        ol._channel_scope()
    assert "tag" in str(exc.value)


def test_lane_scope_with_tag_is_parameterized(monkeypatch):
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    sql, params = ol._channel_scope(tag="oeh")
    assert "tag=%s" in sql or "tag = %s" in sql
    assert params == ("oeh",)
    # never string-interpolated (tag comes from a caller)
    assert "'oeh'" not in sql


def test_lane_scope_rejects_bad_tag_shape(monkeypatch):
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    with pytest.raises(ValueError):
        ol._channel_scope(tag="'; DROP TABLE operator_messages; --")


def test_lane_agent_id_is_never_the_hub(monkeypatch):
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    monkeypatch.delenv("ORCH_AGENT_ID", raising=False)
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-oeh")
    assert ol._agent_id() == "cc-oeh"
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    monkeypatch.setenv("AGENT_ID", "some-daemon")
    assert ol._agent_id() == "some-daemon"
    monkeypatch.delenv("AGENT_ID", raising=False)
    assert ol._agent_id() != "cc-orchestrator"   # unknown lane is NOT the hub


def test_mark_handled_empty_ids_is_a_noop_without_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setenv("ORCH_BODY_ROLE", "hub")
    assert ol.mark_handled([]) == 0


# ---- DB: lane role ----

def test_lane_unprocessed_requires_tag(ledger_db, lane_role):
    with pytest.raises(ValueError):
        ol.unprocessed()


def test_lane_unprocessed_is_tag_scoped(ledger_db, lane_role):
    ids = _seed(ledger_db, [("oeh", "a"), ("angullia", "b"), ("oeh", "c")])
    rows = ol.unprocessed(tag="oeh")
    assert [r[0] for r in rows] == [ids[0], ids[2]]


def test_lane_mark_handled_stamps_only_given_ids_in_own_tag(ledger_db, lane_role):
    ids = _seed(ledger_db, [("oeh", "a"), ("angullia", "b"), ("oeh", "c")])
    # try to stamp one own row AND another tag's row: only the own-tag id lands
    n = ol.mark_handled([ids[0], ids[1]], tag="oeh")
    assert n == 1
    assert _handled(ledger_db) == [ids[0]]
    # the unread own-tag row was NOT touched (no high-water)
    assert [r[0] for r in ol.unprocessed(tag="oeh")] == [ids[2]]


def test_a_lane_answers_only_newest_of_three_older_two_stay_unhandled(ledger_db, lane_role):
    """(a) the stamp-past-unanswered incident, inverted: answering + stamping only the
    newest of 3 leaves the 2 older rows handled_at NULL."""
    ids = _seed(ledger_db, [("oeh", "old-1"), ("oeh", "old-2"), ("oeh", "newest")])
    ol.unprocessed(tag="oeh")
    assert ol.mark_handled([ids[2]], tag="oeh") == 1
    assert _handled(ledger_db) == [ids[2]]
    assert [r[0] for r in ol.unprocessed(tag="oeh")] == ids[:2]


def test_c_lane_mark_handled_through_without_tag_is_refused(ledger_db, lane_role):
    ids = _seed(ledger_db, [("oeh", "a")])
    ol.unprocessed(tag="oeh")
    with pytest.raises(ValueError):
        ol.mark_handled_through(ids[0])
    assert _handled(ledger_db) == []


def test_lane_mark_handled_through_is_refused(ledger_db, lane_role):
    ids = _seed(ledger_db, [("oeh", "a"), ("oeh", "b")])
    ol.unprocessed(tag="oeh")
    with pytest.raises(ValueError) as exc:
        ol.mark_handled_through(ids[-1], tag="oeh")
    assert "mark_handled" in str(exc.value)
    assert _handled(ledger_db) == []


# ---- DB: hub role (the 21st-row class) ----

def test_e_hub_reading_limit_does_not_lose_the_unread_older_row(ledger_db, hub_role):
    """(e) unprocessed(limit=2) with 3 unread: stamping through the newest READ id leaves
    the never-shown 3rd row unprocessed — no high-water advance past unread ids."""
    ids = _seed(ledger_db, [("orch-channel", "1"), ("orch-channel", "2"),
                            ("orch-channel", "3")])
    rows = ol.unprocessed(limit=2)            # the caller only ever SAW the first two
    assert [r[0] for r in rows] == ids[:2]
    n = ol.mark_handled_through(ids[1])       # legacy call with the newest id it READ
    assert n == 2
    assert _handled(ledger_db) == ids[:2]
    assert [r[0] for r in ol.unprocessed()] == [ids[2]]


def test_d_hub_mark_handled_through_refuses_an_id_not_in_last_read(ledger_db, hub_role):
    """(d) max_id the caller never read (the 21st row) -> refused, nothing stamped."""
    ids = _seed(ledger_db, [("orch-channel", "1"), ("orch-channel", "2"),
                            ("orch-channel", "3")])
    ol.unprocessed(limit=2)
    with pytest.raises(ValueError) as exc:
        ol.mark_handled_through(ids[2])
    assert str(ids[2]) in str(exc.value)
    assert _handled(ledger_db) == []


def test_hub_mark_handled_through_without_prior_read_raises(ledger_db, hub_role):
    ids = _seed(ledger_db, [("orch-channel", "1")])
    with pytest.raises(ValueError):
        ol.mark_handled_through(ids[0])
    assert _handled(ledger_db) == []


def test_hub_mark_handled_explicit_ids_respects_scope(ledger_db, hub_role):
    ids = _seed(ledger_db, [("orch-channel", "1"), ("nazim-console", "console-dm"),
                                     ("cai-channel", "cai")])
    ol.unprocessed()
    # nazim-console is the console body's; cai-channel is cai's — the hub can't stamp either
    n = ol.mark_handled(ids)
    assert n == 1
    assert _handled(ledger_db) == [ids[0]]


def test_hub_mark_handled_through_only_stamps_read_ids_at_or_below_max(ledger_db, hub_role):
    ids = _seed(ledger_db, [("orch-channel", "1"), ("orch-channel", "2")])
    ol.unprocessed()
    n = ol.mark_handled_through(ids[0])       # answered only the first
    assert n == 1
    assert _handled(ledger_db) == [ids[0]]
