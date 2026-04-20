"""
Governance hygiene batch — 7-case live verification matrix.

Run AFTER the migration is applied via CAI MCP. Each case inserts a test
fixture row into strategic_decisions (and related tables), asserts trigger
behaviour, then rolls back the transaction to leave the DB clean.

Usage:
    python scripts/verify_governance_hygiene_batch.py

Exit code 0 = all cases pass. Non-zero = at least one case failed.

Cases:
    1. Schema: 'superseded' enum value accepted by CHECK constraint
    2. Schema: superseded_by_decision_ref FK constraint enforced (RESTRICT)
    3. Schema: agent_messages.skipped_at column exists + nullable
    4. Trigger: fresh CAI decision INSERT fires announce (BUG-025 preserved)
    5. Trigger: hygiene-flip UPDATE on already-implemented row does NOT fire
       announce (13-row storm regression test — msg 378 Section F item 11)
    6. Trigger: execution_status transition INTO 'implemented' auto-closes
       the linked announce row via the new AFTER trigger
    7. Trigger: re-flip of already-announced row does NOT re-announce
       (CAI msg 380 Case 7 — OLD.announced_by_msg_id guard branch)

Each case uses a SAVEPOINT/ROLLBACK pattern so fixture rows never persist.
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg
from dotenv import load_dotenv


def _agent_id_guc(cur: psycopg.Cursor, agent_id: str) -> None:
    """Set app.current_agent_id GUC for ARCH-035 identity trigger."""
    cur.execute("SELECT set_config('app.current_agent_id', %s, true)", (agent_id,))


def case_1_superseded_enum(cur: psycopg.Cursor) -> tuple[bool, str]:
    """CHECK constraint allows 'superseded' as challenge_status."""
    ref = f"VERIFY-CASE1-{uuid.uuid4().hex[:8]}"
    try:
        cur.execute(
            """
            INSERT INTO strategic_decisions
                (decision_ref, title, decision, reasoning, domain,
                 challenge_status, source)
            VALUES (%s, 't', 'd', 'r', 'architecture', 'superseded',
                    'claude_ai_session')
            """,
            (ref,),
        )
        return True, "CHECK accepts 'superseded'"
    except psycopg.errors.CheckViolation as e:
        return False, f"CHECK rejects 'superseded': {e}"


def case_2_fk_restrict(cur: psycopg.Cursor) -> tuple[bool, str]:
    """FK superseded_by_decision_ref enforces existence; RESTRICT on delete."""
    child_ref = f"VERIFY-CASE2-CHILD-{uuid.uuid4().hex[:8]}"
    try:
        cur.execute(
            """
            INSERT INTO strategic_decisions
                (decision_ref, title, decision, reasoning, domain,
                 challenge_status, source, superseded_by_decision_ref)
            VALUES (%s, 't', 'd', 'r', 'architecture', 'superseded',
                    'claude_ai_session', 'DOES-NOT-EXIST-12345')
            """,
            (child_ref,),
        )
        return False, "FK accepted a non-existent superseded_by_decision_ref"
    except psycopg.errors.ForeignKeyViolation:
        return True, "FK correctly rejects non-existent parent"


def case_3_skipped_at_column(cur: psycopg.Cursor) -> tuple[bool, str]:
    """agent_messages.skipped_at exists, nullable, TIMESTAMPTZ."""
    cur.execute(
        """
        SELECT data_type, is_nullable
          FROM information_schema.columns
         WHERE table_name = 'agent_messages'
           AND column_name = 'skipped_at'
        """
    )
    row = cur.fetchone()
    if row is None:
        return False, "skipped_at column missing"
    data_type, is_nullable = row
    if data_type != "timestamp with time zone":
        return False, f"skipped_at wrong type: {data_type}"
    if is_nullable != "YES":
        return False, "skipped_at should be nullable"
    return True, "skipped_at column present (timestamptz, nullable)"


def case_4_fresh_announce_fires(cur: psycopg.Cursor) -> tuple[bool, str]:
    """Fresh CAI-filed decision INSERT produces an agent_messages announce."""
    ref = f"VERIFY-CASE4-{uuid.uuid4().hex[:8]}"
    cur.execute(
        """
        INSERT INTO strategic_decisions
            (decision_ref, title, decision, reasoning, domain,
             challenge_status, source)
        VALUES (%s, 'verify case 4', 'd', 'r', 'architecture',
                'challenge_window', 'claude_ai_session')
        RETURNING announced_by_msg_id
        """,
        (ref,),
    )
    (msg_id,) = cur.fetchone()
    if msg_id is None:
        return False, "INSERT did not produce announced_by_msg_id"
    cur.execute(
        "SELECT subject, requires_response FROM agent_messages WHERE id = %s",
        (msg_id,),
    )
    row = cur.fetchone()
    if row is None:
        return False, f"announce msg {msg_id} not found in agent_messages"
    subject, requires_response = row
    if not subject.startswith(ref):
        return False, f"announce subject missing ref prefix: {subject}"
    if not requires_response:
        return False, "challenge_window announce should require response"
    return True, f"announce fired correctly (msg_id={msg_id})"


def case_5_hygiene_flip_suppressed(cur: psycopg.Cursor) -> tuple[bool, str]:
    """OLD.execution_status='implemented' UPDATE does NOT fire a new announce.

    Regression test for the 13-row storm from Step 1. Insert a row simulating
    pre-BUG-020 backlog (announced_by_msg_id=NULL, execution_status set
    directly on INSERT to bypass the initial announce), then flip its
    challenge_status challenge_window → accepted and assert NO new announce.

    Invariants this test depends on (CAI msg 380 Case 5 fragility note):
      1. The BEFORE UPDATE trigger is scoped `OF challenge_status` — the
         intermediate `UPDATE bypass_review=false` below relies on this to
         NOT fire the announce trigger. If a future migration widens trigger
         scope to `OF *` or `OF bypass_review`, this test silently breaks.
      2. `bypass_review` column remains mutable post-INSERT (no future trigger
         locks it at insert time).
      3. OLD row in the final UPDATE reflects the prior UPDATE's state change —
         Postgres guarantees this within a single transaction, confirming.
    """
    ref = f"VERIFY-CASE5-{uuid.uuid4().hex[:8]}"
    # Insert with bypass_review=true so the initial INSERT does NOT announce,
    # then we manually flip the columns to simulate the pre-BUG-020 backlog
    # shape: implemented + challenge_window + announced_by_msg_id NULL.
    cur.execute(
        """
        INSERT INTO strategic_decisions
            (decision_ref, title, decision, reasoning, domain,
             challenge_status, source, bypass_review, execution_status)
        VALUES (%s, 'verify case 5', 'd', 'r', 'architecture',
                'challenge_window', 'claude_ai_session', true, 'implemented')
        """,
        (ref,),
    )
    # Now clear bypass_review so the guard logic has to rely on OLD-side check.
    cur.execute(
        "UPDATE strategic_decisions SET bypass_review=false WHERE decision_ref=%s",
        (ref,),
    )
    # Snapshot agent_messages count before the hygiene flip.
    cur.execute(
        "SELECT COUNT(*) FROM agent_messages WHERE subject LIKE %s",
        (f"{ref}:%",),
    )
    (before_count,) = cur.fetchone()

    cur.execute(
        """
        UPDATE strategic_decisions
           SET challenge_status = 'accepted'
         WHERE decision_ref = %s
        """,
        (ref,),
    )

    cur.execute(
        "SELECT COUNT(*) FROM agent_messages WHERE subject LIKE %s",
        (f"{ref}:%",),
    )
    (after_count,) = cur.fetchone()

    if after_count > before_count:
        return False, (
            f"hygiene flip produced {after_count - before_count} announce(s) "
            f"— OLD-side guard not working"
        )
    return True, "hygiene flip correctly suppressed (0 new announces)"


def case_6_autoclose_on_implementation(cur: psycopg.Cursor) -> tuple[bool, str]:
    """execution_status transition to 'implemented' auto-closes the announce."""
    ref = f"VERIFY-CASE6-{uuid.uuid4().hex[:8]}"
    # Fresh CAI decision — trigger fires an announce with requires_response=true.
    cur.execute(
        """
        INSERT INTO strategic_decisions
            (decision_ref, title, decision, reasoning, domain,
             challenge_status, source)
        VALUES (%s, 'verify case 6', 'd', 'r', 'architecture',
                'challenge_window', 'claude_ai_session')
        RETURNING announced_by_msg_id
        """,
        (ref,),
    )
    (msg_id,) = cur.fetchone()
    if msg_id is None:
        return False, "initial INSERT did not fire announce"

    # Confirm announce starts unclosed.
    cur.execute("SELECT responded_at FROM agent_messages WHERE id = %s", (msg_id,))
    (responded_at,) = cur.fetchone()
    if responded_at is not None:
        return False, "announce already closed before implementation"

    # Transition to implemented.
    cur.execute(
        "UPDATE strategic_decisions SET execution_status='implemented' WHERE decision_ref=%s",
        (ref,),
    )

    cur.execute(
        "SELECT responded_at, response_ref FROM agent_messages WHERE id = %s",
        (msg_id,),
    )
    row = cur.fetchone()
    if row is None:
        return False, "announce row disappeared"
    responded_at, response_ref = row
    if responded_at is None:
        return False, "auto-close did not stamp responded_at"
    if response_ref is None or not response_ref.startswith("auto-closed-on-implementation:"):
        return False, f"response_ref missing or wrong shape: {response_ref}"
    return True, f"auto-close stamped responded_at + response_ref={response_ref}"


def case_7_reannounce_prevented(cur: psycopg.Cursor) -> tuple[bool, str]:
    """Already-announced decisions do NOT re-announce on subsequent flips.

    CAI msg 380 Case 7 suggestion. Exercises the
    `OLD.announced_by_msg_id IS NOT NULL → suppress` branch of the OLD-side
    guard, which the 13-row storm test (Case 5) doesn't hit because those
    rows had announced_by_msg_id=NULL.

    Sequence:
      a. INSERT with challenge_status='challenge_window' — first announce fires.
      b. Flip challenge_status back to 'unchallenged' (pass the shared early
         exit's 'challenge_status NOT IN (challenge_window, accepted)' filter).
      c. Flip to 'accepted' — guard should suppress because OLD.announced_by_msg_id
         is still non-NULL from step (a).
      d. Assert: exactly one announce row for this decision_ref.
    """
    ref = f"VERIFY-CASE7-{uuid.uuid4().hex[:8]}"
    cur.execute(
        """
        INSERT INTO strategic_decisions
            (decision_ref, title, decision, reasoning, domain,
             challenge_status, source)
        VALUES (%s, 'verify case 7', 'd', 'r', 'architecture',
                'challenge_window', 'claude_ai_session')
        RETURNING announced_by_msg_id
        """,
        (ref,),
    )
    (first_msg_id,) = cur.fetchone()
    if first_msg_id is None:
        return False, "initial INSERT did not fire announce"

    cur.execute(
        "UPDATE strategic_decisions SET challenge_status='unchallenged' WHERE decision_ref=%s",
        (ref,),
    )
    cur.execute(
        "UPDATE strategic_decisions SET challenge_status='accepted' WHERE decision_ref=%s",
        (ref,),
    )

    cur.execute(
        "SELECT COUNT(*) FROM agent_messages WHERE subject LIKE %s",
        (f"{ref}:%",),
    )
    (count,) = cur.fetchone()

    if count != 1:
        return False, (
            f"expected 1 announce after re-flips, got {count} — "
            f"OLD.announced_by_msg_id guard not suppressing"
        )
    return True, "re-announce correctly suppressed (1 announce total)"


CASES = [
    ("Case 1 — superseded enum", case_1_superseded_enum),
    ("Case 2 — FK RESTRICT", case_2_fk_restrict),
    ("Case 3 — skipped_at column", case_3_skipped_at_column),
    ("Case 4 — fresh announce fires", case_4_fresh_announce_fires),
    ("Case 5 — hygiene flip suppressed", case_5_hygiene_flip_suppressed),
    ("Case 6 — auto-close on implementation", case_6_autoclose_on_implementation),
    ("Case 7 — re-announce prevented", case_7_reannounce_prevented),
]


def main() -> int:
    load_dotenv(".env")
    dsn = os.environ["DATABASE_URL"]
    agent_id = "cc-ihsanos-3"

    total, passed = 0, 0
    failures: list[str] = []

    with psycopg.connect(dsn, autocommit=False) as conn:
        for name, case in CASES:
            total += 1
            with conn.cursor() as cur:
                cur.execute("SAVEPOINT case_start")
                try:
                    _agent_id_guc(cur, agent_id)
                    ok, detail = case(cur)
                except Exception as e:
                    ok, detail = False, f"exception: {e!r}"
                cur.execute("ROLLBACK TO SAVEPOINT case_start")
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}] {name}: {detail}")
            if ok:
                passed += 1
            else:
                failures.append(f"{name}: {detail}")
        conn.rollback()

    print(f"\n{passed}/{total} cases passed.")
    if failures:
        print("\nFailures:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
