"""Test harness for the Execution-Reliability Layer.

The migration is AUTHORED-UNAPPLIED, so `public.exec_work_items` does not exist.
To exercise the real SQL semantics (SELECT FOR UPDATE SKIP LOCKED, UNIQUE dedupe,
RLS/role privileges) WITHOUT touching `public.exec_work_items`, these tests
materialise the layer's DDL into an ISOLATED, self-cleaning throwaway schema +
role on the substrate, and drop it all in teardown.

Guardrails:
  * Skips entirely unless EXEC_RL_SELFTEST=1 is set (a plain `pytest` run never
    touches the substrate).
  * Skips if no DATABASE_URL.
  * Schema/role names are uuid-suffixed and dropped in a finally block.
  * public.exec_work_items is NEVER created; only a `exec_rl_st_<uuid>` schema is.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

from nervous_system.exec_reliability import schema as _schema  # noqa: E402

_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
_SELFTEST = os.environ.get("EXEC_RL_SELFTEST") == "1"


def pytest_collection_modifyitems(config, items):
    """Skip EVERY exec_reliability test BEFORE any fixture runs unless explicitly
    opted in. A module-level pytestmark in a conftest does NOT propagate to sibling
    test modules, so a plain `pytest` would otherwise create/drop a throwaway
    schema on the live substrate. This gate runs at collection time — nothing
    (not even the module-scoped `substrate` fixture) executes when opted out.
    """
    if _DSN and _SELFTEST:
        return
    skip = pytest.mark.skip(
        reason="set EXEC_RL_SELFTEST=1 and DATABASE_URL to run substrate self-tests")
    for item in items:
        if "exec_reliability" in str(item.fspath):
            item.add_marker(skip)


@pytest.fixture(scope="module")
def substrate():
    """Create an isolated schema + runner role; drop everything in teardown."""
    suffix = uuid.uuid4().hex[:12]
    schema_name = f"exec_rl_st_{suffix}"
    role_name = f"exec_runner_st_{suffix}"

    with psycopg.connect(_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"create schema {schema_name}")
        cur.execute(_schema.full_migration_sql(schema_name, role_name))
        cur.execute(f"grant usage on schema {schema_name} to {role_name}")
    try:
        yield {"dsn": _DSN, "schema": schema_name, "role": role_name}
    finally:
        with psycopg.connect(_DSN, autocommit=True) as conn, conn.cursor() as cur:
            # Drop the schema (removes its tables + their grants to the role), then
            # revoke the only remaining cross-schema grant (on strategic_decisions)
            # so the role has no dependencies and drops cleanly. Avoids `drop owned
            # by` / role-membership admin ops the Supabase pooler rejects.
            cur.execute(f"drop schema if exists {schema_name} cascade")
            cur.execute(f"revoke all on public.strategic_decisions from {role_name}")
            cur.execute(f"drop role if exists {role_name}")


# Runner agent id used by the tests. agent_messages.from_agent is FK->agents(id),
# so the runner must be registered; we register/cleanup a dedicated test agent so
# tests don't depend on the production 'cc-exec-runner' registration.
RUNNER_AGENT = "cc-exec-runner-selftest"


@pytest.fixture(scope="module")
def runner_agent():
    with psycopg.connect(_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "insert into public.agents (id, display_name, status) "
            "values (%s, 'exec-rl selftest runner', 'offline') "
            "on conflict (id) do nothing",
            (RUNNER_AGENT,),
        )
    try:
        yield RUNNER_AGENT
    finally:
        with psycopg.connect(_DSN, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("delete from agent_messages where from_agent = %s", (RUNNER_AGENT,))
            cur.execute("delete from public.agents where id = %s", (RUNNER_AGENT,))


@pytest.fixture
def granted_decision(substrate):
    """Insert a synthetic GRANTED + challenge-closed decision; clean it up.

    is_test=true so it does not trip the auto-announce trigger. Yields a dict with
    the decision_ref and a helper to insert an exec_artifact into its constraints.
    """
    ref = f"EXEC-RL-TEST-{uuid.uuid4().hex[:8]}"
    created: list[str] = [ref]

    def _insert(execution_status="granted", challenge_status="accepted",
                status="active", repos_affected=None, exec_artifact=None):
        constraints = (
            [f"exec_artifact:{json.dumps(exec_artifact)}"] if exec_artifact else None
        )
        # A challenge_window decision requires a future challengeable_until
        # (enforce_challenge_window_requires_expiry trigger).
        challengeable_sql = (
            "now() + interval '1 day'"
            if challenge_status == "challenge_window" else "null"
        )
        with psycopg.connect(_DSN, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                insert into public.strategic_decisions
                    (decision_ref, title, decision, reasoning, domain, source,
                     challenge_status, execution_status, status, repos_affected,
                     constraints, challengeable_until, is_test)
                values (%s, 'exec-rl test', 'test decision', 'test reasoning',
                        'architecture', 'claude_ai_session', %s, %s, %s, %s, %s,
                        {challengeable_sql}, true)
                on conflict (decision_ref) do update set
                    execution_status = excluded.execution_status,
                    challenge_status = excluded.challenge_status,
                    status = excluded.status,
                    constraints = excluded.constraints,
                    challengeable_until = excluded.challengeable_until
                """,
                (ref, challenge_status, execution_status, status,
                 repos_affected, constraints),
            )
        return ref

    yield {"ref": ref, "insert": _insert}

    schema_name = substrate["schema"]
    with psycopg.connect(_DSN, autocommit=True) as conn, conn.cursor() as cur:
        # Remove referencing work-items first (grant_ref FK), then the decision.
        cur.execute(
            f"delete from {schema_name}.exec_work_items where grant_ref = any(%s)",
            (created,),
        )
        cur.execute(
            "delete from public.strategic_decisions where decision_ref = any(%s)",
            (created,),
        )


def relay_artifact(target="cc-orchestrator", requires_settled=None, cls="notification"):
    art = {
        "consumer_type": "relay",
        "class": cls,
        "channel": "bus",
        "target": target,
        "summary": "test authorized op",
    }
    if requires_settled is not None:
        art["requires_settled"] = requires_settled
    return art
