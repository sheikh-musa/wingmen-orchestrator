"""Integration tests for Batch 2 BUG-030 bridge trigger fix.

Covers:
  - parent_msg_id FK column + constraint
  - announce_to_agent + announce_thread_id override columns
  - trigger_cai_decision_announce 3-tier routing (explicit > inferred > legacy)
  - UPDATE-path firing on challenge_status change (BUG-020 precedent)

Uses psycopg against the live orchestrator Supabase. Each test manages its
own SAVEPOINT/ROLLBACK or DELETE cleanup to avoid leaking test rows.
"""
import os
import uuid

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()
DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")

pytestmark = pytest.mark.skipif(
    not DSN, reason="DATABASE_URL not set — integration tests skipped"
)


def _conn():
    return psycopg.connect(DSN, autocommit=False)


def _cleanup(ref_prefix):
    """Delete any test rows matching a decision_ref prefix. Crash-safe."""
    with psycopg.connect(DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM agent_messages WHERE subject LIKE %s",
                (f"{ref_prefix}%",),
            )
            cur.execute(
                "DELETE FROM strategic_decisions WHERE decision_ref LIKE %s",
                (f"{ref_prefix}%",),
            )


@pytest.fixture(autouse=True)
def _clean_test_bug030_rows():
    _cleanup("TEST-BUG030-")
    yield
    _cleanup("TEST-BUG030-")


# NOTE: strategic_decisions.domain has a CHECK constraint restricting values to
# {pricing, architecture, islamic, sales, product, operations, renovation}.
# Tests use 'operations' as the closest fit for process/governance test rows.


def test_strategic_decisions_has_bug030_columns():
    """AC-BUG030-1/2/3: parent_msg_id + announce_to_agent + announce_thread_id."""
    expected = {
        "parent_msg_id": ("bigint", "YES"),
        "announce_to_agent": ("text", "YES"),
        "announce_thread_id": ("uuid", "YES"),
    }
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_name = 'strategic_decisions'
                   AND column_name = ANY(%s)
                """,
                (list(expected.keys()),),
            )
            actual = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    assert actual == expected, f"columns mismatch: expected {expected}, got {actual}"


def test_parent_msg_id_fk_rejects_nonexistent_id():
    """AC-BUG030-1: FK REFERENCES agent_messages(id) must reject bad values."""
    with _conn() as c:
        with c.cursor() as cur:
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                cur.execute(
                    """
                    INSERT INTO strategic_decisions
                      (decision_ref, title, decision, reasoning, domain, status,
                       source, challenge_status, decided_by, parent_msg_id)
                    VALUES ('TEST-BUG030-FK', 't', 'd', 'r', 'operations', 'active',
                            'claude_ai_session', 'accepted', 'cai', 9999999999999)
                    """
                )
            c.rollback()


def test_tier2_recipient_inferred_from_parent_msg_sender():
    """AC-BUG030-4 Tier 2: parent_msg_id populated, announce_to_agent NULL →
    bridge infers to_agent = parent.from_agent."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_messages
                  (thread_id, from_agent, to_agent, message_type, subject, body)
                VALUES (gen_random_uuid(), 'cc-cosem', 'cai', 'question', 'p', 'p')
                RETURNING id, thread_id
                """
            )
            parent_id, parent_thread = cur.fetchone()

            cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
            cur.execute(
                """
                INSERT INTO strategic_decisions
                  (decision_ref, title, decision, reasoning, domain, status,
                   source, challenge_status, decided_by, parent_msg_id,
                   challengeable_until)
                VALUES ('TEST-BUG030-T2', 't', 'd', 'r', 'operations', 'active',
                        'claude_ai_session', 'challenge_window', 'cai', %s,
                        now() + interval '1 day')
                RETURNING announced_by_msg_id
                """,
                (parent_id,),
            )
            announced_msg_id = cur.fetchone()[0]
            assert announced_msg_id is not None, "trigger did not populate announced_by_msg_id"

            cur.execute(
                "SELECT to_agent, thread_id FROM agent_messages WHERE id = %s",
                (announced_msg_id,),
            )
            to_agent, thread_id = cur.fetchone()
            assert to_agent == "cc-cosem", f"expected cc-cosem (parent.from_agent), got {to_agent}"
            assert thread_id == parent_thread, \
                f"expected inherited thread {parent_thread}, got {thread_id}"

        c.rollback()


def test_tier1_explicit_announce_to_agent_overrides_inference():
    """AC-BUG030-4 Tier 1: announce_to_agent populated → overrides parent inference."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_messages
                  (thread_id, from_agent, to_agent, message_type, subject, body)
                VALUES (gen_random_uuid(), 'cc-cosem', 'cai', 'question', 'p', 'p')
                RETURNING id
                """
            )
            parent_id = cur.fetchone()[0]

            cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
            cur.execute(
                """
                INSERT INTO strategic_decisions
                  (decision_ref, title, decision, reasoning, domain, status,
                   source, challenge_status, decided_by, parent_msg_id,
                   announce_to_agent, challengeable_until)
                VALUES ('TEST-BUG030-T1A', 't', 'd', 'r', 'operations', 'active',
                        'claude_ai_session', 'challenge_window', 'cai', %s,
                        'cc-scholar', now() + interval '1 day')
                RETURNING announced_by_msg_id
                """,
                (parent_id,),
            )
            msg_id = cur.fetchone()[0]
            cur.execute("SELECT to_agent FROM agent_messages WHERE id = %s", (msg_id,))
            assert cur.fetchone()[0] == "cc-scholar", "explicit override ignored"
        c.rollback()


def test_tier1_explicit_announce_thread_id_overrides_inheritance():
    """AC-BUG030-4 Tier 1: announce_thread_id populated → overrides parent.thread_id."""
    override_thread = str(uuid.uuid4())
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_messages
                  (thread_id, from_agent, to_agent, message_type, subject, body)
                VALUES (gen_random_uuid(), 'cc-cosem', 'cai', 'question', 'p', 'p')
                RETURNING id, thread_id
                """
            )
            parent_id, parent_thread = cur.fetchone()
            assert str(parent_thread) != override_thread  # sanity

            cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
            cur.execute(
                """
                INSERT INTO strategic_decisions
                  (decision_ref, title, decision, reasoning, domain, status,
                   source, challenge_status, decided_by, parent_msg_id,
                   announce_thread_id, challengeable_until)
                VALUES ('TEST-BUG030-T1T', 't', 'd', 'r', 'operations', 'active',
                        'claude_ai_session', 'challenge_window', 'cai', %s, %s,
                        now() + interval '1 day')
                RETURNING announced_by_msg_id
                """,
                (parent_id, override_thread),
            )
            msg_id = cur.fetchone()[0]
            cur.execute("SELECT thread_id FROM agent_messages WHERE id = %s", (msg_id,))
            assert str(cur.fetchone()[0]) == override_thread, "thread_id override ignored"
        c.rollback()


def test_tier3_legacy_fallback_when_parent_msg_id_null():
    """AC-BUG030-4 Tier 3: parent_msg_id NULL + announce_* NULL → cc-ihsanos default +
    fresh thread_id. Backward-compatible behavior for all 300+ pre-migration rows."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
            cur.execute(
                """
                INSERT INTO strategic_decisions
                  (decision_ref, title, decision, reasoning, domain, status,
                   source, challenge_status, decided_by, challengeable_until)
                VALUES ('TEST-BUG030-T3', 't', 'd', 'r', 'operations', 'active',
                        'claude_ai_session', 'challenge_window', 'cai',
                        now() + interval '1 day')
                RETURNING announced_by_msg_id
                """
            )
            msg_id = cur.fetchone()[0]
            cur.execute(
                "SELECT to_agent, thread_id FROM agent_messages WHERE id = %s",
                (msg_id,),
            )
            to_agent, thread_id = cur.fetchone()
            assert to_agent == "cc-ihsanos"
            assert thread_id is not None
        c.rollback()


def test_update_path_fires_trigger_on_challenge_status_change():
    """AC-BUG030-5: UPDATE of challenge_status into ('challenge_window', 'accepted')
    must fire the bridge trigger (BUG-020 precedent preserved).

    Isolation: seeds with challenge_status='unchallenged' (not in the announcing
    whitelist {'challenge_window','accepted'}) so the INSERT path early-returns
    without announcing. A single UPDATE to 'challenge_window' then exercises ONLY
    the UPDATE branch of the trigger.
    """
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT set_config('app.current_agent_id', 'cai', true)")
            cur.execute(
                """
                INSERT INTO strategic_decisions
                  (decision_ref, title, decision, reasoning, domain, status,
                   source, challenge_status, decided_by)
                VALUES ('TEST-BUG030-UPD', 't', 'd', 'r', 'operations', 'active',
                        'claude_ai_session', 'unchallenged', 'cai')
                """
            )
            # INSERT: challenge_status='unchallenged' falls outside the trigger's
            # whitelist → early return, announced_by_msg_id stays NULL. Verify.
            cur.execute(
                "SELECT announced_by_msg_id FROM strategic_decisions "
                "WHERE decision_ref = 'TEST-BUG030-UPD'"
            )
            assert cur.fetchone()[0] is None, "INSERT should not have announced (status='proposed')"

            # Now the UPDATE-path test: flip challenge_status into the whitelist +
            # set challengeable_until (required by challenge_window constraint).
            cur.execute(
                """
                UPDATE strategic_decisions
                   SET challenge_status = 'challenge_window',
                       challengeable_until = now() + interval '1 day'
                 WHERE decision_ref = 'TEST-BUG030-UPD'
                 RETURNING announced_by_msg_id
                """
            )
            msg_id = cur.fetchone()[0]
            assert msg_id is not None, "UPDATE path did not fire trigger"
        c.rollback()


def test_trigger_body_contains_tiered_coalesce_pattern():
    """Section 4 guard: trigger body must reference all three tier inputs.
    A future refactor that drops announce_to_agent from the COALESCE silently
    regresses BUG-030; this test catches it at commit time."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT pg_get_functiondef(oid)
                  FROM pg_proc
                 WHERE proname = 'trigger_cai_decision_announce'
                """
            )
            body = cur.fetchone()[0]
    for token in ("NEW.announce_to_agent", "NEW.announce_thread_id",
                  "NEW.parent_msg_id", "cc-ihsanos"):
        assert token in body, f"trigger body missing {token}"
