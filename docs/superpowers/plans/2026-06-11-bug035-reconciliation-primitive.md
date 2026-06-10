# BUG-035 Reconciliation Primitive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give cross-agent BLOCKING handoffs a checked reconciliation state so a read ruling can no longer leave a dependent task silently un-cleared (BUG-035 / CAI-RESP-205).

**Architecture:** A minimal `blocking_tasks` substrate table gives each cross-agent blocker an id; rulings (`strategic_decisions`) reference it via `unblocks_task_id`; reconciliation is an explicit owner close that stamps `reconciled_at`; an `open_blocking_tasks` view + a `boot_briefing` arm surface task-cleared state (not `read_at`). A thin Python helper wraps create/reconcile/list. All schema/view changes ship via psycopg-apply (decision-962 safe), never `supabase db push`.

**Tech Stack:** Python 3.9, psycopg, python-dotenv, pytest (real-DSN integration), Supabase Postgres.

---

## File Structure

- Create: `scripts/apply_blocking_tasks_schema.py` — table + FK column + view (psycopg-apply)
- Create: `scripts/apply_boot_briefing_blocking_tasks_arm.py` — boot_briefing arm (arm-surgery)
- Create: `nervous_system/blocking_tasks.py` — create / reconcile / list_open helper
- Create: `tests/test_blocking_tasks.py` — TDD for the helper
- Modify: `schema.sql` — reflect the new table, column, and view

---

### Task 1: Schema apply script (table + FK column + view)

**Files:**
- Create: `scripts/apply_blocking_tasks_schema.py`

- [ ] **Step 1: Write the apply script**

```python
"""BUG-035 / CAI-RESP-205: blocking_tasks reconciliation primitive (psycopg-apply).

Creates the minimal substrate so cross-agent BLOCKING handoffs have a checked
reconciliation state (read != reconciled). Idempotent DDL. CLAUDE.md forbids
`supabase db push` to prod (decision-962); use this direct apply.

Usage:
  python scripts/apply_blocking_tasks_schema.py            # dry-run (rolled back)
  python scripts/apply_blocking_tasks_schema.py --apply    # commit
"""
from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv

DDL = """
create table if not exists blocking_tasks (
    id bigint generated always as identity primary key,
    owner_agent text not null,
    created_by text not null,
    subject text not null,
    detail text,
    thread_id uuid,
    status text not null default 'open'
        check (status in ('open', 'reconciled', 'cancelled')),
    created_at timestamptz not null default now(),
    reconciled_at timestamptz,
    reconciled_by_decision_ref text,
    is_test boolean not null default false
);

alter table strategic_decisions
    add column if not exists unblocks_task_id bigint references blocking_tasks(id);

create or replace view open_blocking_tasks as
select bt.id, bt.owner_agent, bt.created_by, bt.subject, bt.detail,
       bt.thread_id, bt.created_at, bt.is_test,
       sd.decision_ref as unblocking_ruling_ref,
       (sd.decision_ref is not null) as ruling_issued,
       (now() - bt.created_at) as age
from blocking_tasks bt
left join strategic_decisions sd on sd.unblocks_task_id = bt.id
where bt.status = 'open' and bt.is_test is not true;
"""


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute("""
                select column_name from information_schema.columns
                where table_name = 'blocking_tasks' order by ordinal_position
            """)
            cols = [r[0] for r in cur.fetchall()]
            cur.execute("select count(*) from open_blocking_tasks")
            open_n = cur.fetchone()[0]
            cur.execute("""
                select 1 from information_schema.columns
                where table_name='strategic_decisions' and column_name='unblocks_task_id'
            """)
            link_ok = cur.fetchone() is not None
            print(f"blocking_tasks cols: {cols}")
            print(f"strategic_decisions.unblocks_task_id present: {link_ok}")
            print(f"open_blocking_tasks rows: {open_n}")

            ok = bool(cols) and link_ok
            if apply and ok:
                conn.commit()
                print("\nAPPLIED + committed.")
            elif apply:
                conn.rollback()
                print("\nABORTED: schema not in expected state; not committing.")
                return 1
            else:
                conn.rollback()
                print("\nDRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Dry-run**

Run: `.venv/bin/python scripts/apply_blocking_tasks_schema.py`
Expected: prints the 11 columns, `unblocks_task_id present: True`, `open_blocking_tasks rows: 0`, then DRY-RUN rolled back.

- [ ] **Step 3: Apply**

Run: `.venv/bin/python scripts/apply_blocking_tasks_schema.py --apply`
Expected: `APPLIED + committed.`

- [ ] **Step 4: Commit**

```bash
git add scripts/apply_blocking_tasks_schema.py
git commit -m "feat(bug-035): blocking_tasks schema + open_blocking_tasks view"
```

---

### Task 2: Helper module (TDD)

**Files:**
- Create: `nervous_system/blocking_tasks.py`
- Test: `tests/test_blocking_tasks.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Integration tests for the blocking_tasks reconciliation primitive (BUG-035)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")

from nervous_system.blocking_tasks import (
    create_blocking_task, reconcile_blocking_task, list_open_blocking_tasks,
)


@pytest.fixture
def cleanup_ids():
    ids: list[int] = []
    yield ids
    if not ids:
        return
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "update strategic_decisions set unblocks_task_id = null "
            "where unblocks_task_id = any(%s)", (ids,))
        cur.execute("delete from blocking_tasks where id = any(%s)", (ids,))


def test_create_returns_id_and_lists_as_open(cleanup_ids):
    tid = create_blocking_task(
        _DSN, owner_agent="cc-ihsanos", created_by="cc-orchestrator",
        subject="test blocker", is_test=True)
    cleanup_ids.append(tid)
    assert isinstance(tid, int) and tid > 0
    rows = list_open_blocking_tasks(_DSN, include_test=True)
    assert any(r["id"] == tid and r["status"] == "open" for r in rows)


def test_reconcile_sets_reconciled_state(cleanup_ids):
    tid = create_blocking_task(
        _DSN, owner_agent="cc-ihsanos", created_by="cc-orchestrator",
        subject="test blocker", is_test=True)
    cleanup_ids.append(tid)
    ok = reconcile_blocking_task(_DSN, tid, "CAI-TEST-001")
    assert ok is True
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "select status, reconciled_at is not null, reconciled_by_decision_ref "
            "from blocking_tasks where id = %s", (tid,))
        status, recon_set, ref = cur.fetchone()
    assert status == "reconciled" and recon_set is True and ref == "CAI-TEST-001"


def test_reconciled_task_drops_out_of_open_list(cleanup_ids):
    tid = create_blocking_task(
        _DSN, owner_agent="cc-ihsanos", created_by="cc-orchestrator",
        subject="test blocker", is_test=True)
    cleanup_ids.append(tid)
    reconcile_blocking_task(_DSN, tid, "CAI-TEST-001")
    rows = list_open_blocking_tasks(_DSN, include_test=True)
    assert all(r["id"] != tid for r in rows)


def test_reconcile_is_idempotent(cleanup_ids):
    tid = create_blocking_task(
        _DSN, owner_agent="cc-ihsanos", created_by="cc-orchestrator",
        subject="test blocker", is_test=True)
    cleanup_ids.append(tid)
    assert reconcile_blocking_task(_DSN, tid, "CAI-TEST-001") is True
    assert reconcile_blocking_task(_DSN, tid, "CAI-TEST-002") is True
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "select reconciled_by_decision_ref from blocking_tasks where id = %s",
            (tid,))
        # first reconcile wins; idempotent re-call does not overwrite
        assert cur.fetchone()[0] == "CAI-TEST-001"


def test_reconcile_unknown_id_returns_false():
    assert reconcile_blocking_task(_DSN, 999999999, "CAI-TEST-001") is False


def test_list_excludes_test_by_default(cleanup_ids):
    tid = create_blocking_task(
        _DSN, owner_agent="cc-ihsanos", created_by="cc-orchestrator",
        subject="test blocker", is_test=True)
    cleanup_ids.append(tid)
    rows = list_open_blocking_tasks(_DSN)  # include_test defaults False
    assert all(r["id"] != tid for r in rows)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_blocking_tasks.py -v`
Expected: ImportError / module not found for `nervous_system.blocking_tasks`.

- [ ] **Step 3: Write the helper module**

```python
"""blocking_tasks — BUG-035 / CAI-RESP-205 reconciliation primitive.

Cross-agent BLOCKING handoffs need a CHECKED reconciliation state, not a
convention: a ruling can be read yet leave the dependent task un-cleared
(read != reconciled). Each blocker gets a substrate row with an id; rulings
reference it via strategic_decisions.unblocks_task_id; the OWNER agent calls
reconcile_blocking_task when it has actually consumed the ruling. Reconciliation
is NEVER auto-stamped on ruling-existence — that reproduces the exact bug.

Scope (CAI-RESP-205): cross-agent blockers only. Not a general task manager;
local in-session tasks stay local.
"""
from __future__ import annotations

from typing import Any, Optional

import psycopg


def create_blocking_task(
    dsn: str,
    *,
    owner_agent: str,
    created_by: str,
    subject: str,
    detail: Optional[str] = None,
    thread_id: Optional[str] = None,
    is_test: bool = False,
) -> int:
    """Insert a cross-agent blocking task; return its substrate id."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "insert into blocking_tasks "
            "(owner_agent, created_by, subject, detail, thread_id, is_test) "
            "values (%s, %s, %s, %s, %s, %s) returning id",
            (owner_agent, created_by, subject, detail, thread_id, is_test),
        )
        return cur.fetchone()[0]


def reconcile_blocking_task(dsn: str, task_id: int, decision_ref: str) -> bool:
    """Explicit owner close: stamp reconciled_at. Idempotent; first ref wins.

    Returns True if the task is (now or already) reconciled, False for unknown
    or cancelled ids.
    """
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "update blocking_tasks set status = 'reconciled', "
            "reconciled_at = now(), reconciled_by_decision_ref = %s "
            "where id = %s and status = 'open'",
            (decision_ref, task_id),
        )
        if cur.rowcount == 1:
            return True
        # No open row updated: True only if it is already reconciled.
        cur.execute("select status from blocking_tasks where id = %s", (task_id,))
        row = cur.fetchone()
        return bool(row and row[0] == "reconciled")


def list_open_blocking_tasks(
    dsn: str, *, include_test: bool = False
) -> list[dict[str, Any]]:
    """Return open blocking tasks (the blocker view), newest first."""
    sql = (
        "select bt.id, bt.owner_agent, bt.created_by, bt.subject, bt.detail, "
        "bt.thread_id, bt.created_at, bt.is_test, bt.status, "
        "sd.decision_ref as unblocking_ruling_ref, "
        "(sd.decision_ref is not null) as ruling_issued "
        "from blocking_tasks bt "
        "left join strategic_decisions sd on sd.unblocks_task_id = bt.id "
        "where bt.status = 'open'"
    )
    if not include_test:
        sql += " and bt.is_test is not true"
    sql += " order by bt.created_at desc"
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_blocking_tasks.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add nervous_system/blocking_tasks.py tests/test_blocking_tasks.py
git commit -m "feat(bug-035): blocking_tasks helper (create/reconcile/list) + tests"
```

---

### Task 3: boot_briefing arm

**Files:**
- Create: `scripts/apply_boot_briefing_blocking_tasks_arm.py`

- [ ] **Step 1: Write the arm-surgery apply script**

```python
"""BUG-035 / CAI-RESP-205: boot_briefing open_blocking_tasks arm (psycopg-apply).

Surfaces open cross-agent blockers in the boot index so digests / chases key on
task-cleared, not read_at. Arm-level surgery (append one arm; preserve all
others verbatim) to avoid the decision-962 arm-stripping hazard. Idempotent:
skips if the arm already exists.

Usage:
  python scripts/apply_boot_briefing_blocking_tasks_arm.py            # dry-run
  python scripts/apply_boot_briefing_blocking_tasks_arm.py --apply    # commit
"""
from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv

NEW_ARM = """ SELECT 'open_blocking_task'::text AS source,
    obt.owner_agent AS key,
    json_build_object('task_id', obt.id, 'owner', obt.owner_agent, 'created_by', obt.created_by, 'subject', obt.subject, 'ruling_issued', obt.ruling_issued, 'unblocking_ruling_ref', obt.unblocking_ruling_ref, 'age_minutes', (EXTRACT(epoch FROM obt.age) / 60)::integer) AS context
   FROM open_blocking_tasks obt"""


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("select pg_get_viewdef('boot_briefing'::regclass, true)")
            viewdef = cur.fetchone()[0]
            if "'open_blocking_task'::text AS source" in viewdef:
                print("arm already present; nothing to do.")
                conn.rollback()
                return 0
            new_def = viewdef + "\nUNION ALL\n" + NEW_ARM
            cur.execute(f"CREATE OR REPLACE VIEW boot_briefing AS {new_def}")
            cur.execute("select count(*) from boot_briefing where source = 'open_blocking_task'")
            n = cur.fetchone()[0]
            cur.execute("select count(*) from boot_briefing")
            total = cur.fetchone()[0]
            print(f"boot_briefing total rows: {total}  open_blocking_task rows: {n}")

            if apply:
                conn.commit()
                print("\nAPPLIED + committed.")
            else:
                conn.rollback()
                print("\nDRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Dry-run**

Run: `.venv/bin/python scripts/apply_boot_briefing_blocking_tasks_arm.py`
Expected: prints total rows + `open_blocking_task rows: 0` (no open non-test tasks yet), DRY-RUN rolled back.

- [ ] **Step 3: Apply**

Run: `.venv/bin/python scripts/apply_boot_briefing_blocking_tasks_arm.py --apply`
Expected: `APPLIED + committed.`

- [ ] **Step 4: Commit**

```bash
git add scripts/apply_boot_briefing_blocking_tasks_arm.py
git commit -m "feat(bug-035): boot_briefing open_blocking_task arm"
```

---

### Task 4: Reflect in schema.sql

**Files:**
- Modify: `schema.sql`

- [ ] **Step 1: Add the table, column note, and view to schema.sql**

Append a `blocking_tasks` table definition, the `strategic_decisions.unblocks_task_id` column, and the `open_blocking_tasks` view matching the DDL applied in Task 1 (so `/schema` drift-check stays clean).

- [ ] **Step 2: Verify schema gate**

Run: `.venv/bin/python -c "import nervous_system.schema_gate"` (or the repo's schema-compare entrypoint) and confirm no unexpected drift on the new objects.

- [ ] **Step 3: Commit**

```bash
git add schema.sql
git commit -m "docs(bug-035): reflect blocking_tasks in schema.sql"
```

---

### Task 5: Handoff + STATUS

**Files:**
- Modify: `STATUS.md`

- [ ] **Step 1:** Post a bus message to cc-ihsanos describing the primitive and how to adopt it for task #55-class blockers (create on raise, reconcile on ruling-consumed).
- [ ] **Step 2:** Post an update to cai: primitive shipped per CAI-RESP-205; adoption handed to cc-ihsanos; auto-reconcile explicitly not built.
- [ ] **Step 3:** Update `STATUS.md` with the new primitive.
- [ ] **Step 4: Commit** `STATUS.md`.
