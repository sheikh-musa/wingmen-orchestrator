import psycopg
import pytest

import scripts.apply_bug024_identity_enforcement as mig

# psycopg3 execute() runs one statement per call, so SCHEMA is a list of
# single statements (mirrors the real tables/triggers/roles the migration targets).
SCHEMA = [
    "create table identity_allowlist (posted_by text not null, allowed_from_agent text not null, note text)",
    "create table agent_messages (id serial primary key, from_agent text not null,"
    " to_agent text, posted_by_identity text, from_agent_verified boolean)",
    "create table strategic_decisions (id serial primary key, decided_by text not null,"
    " posted_by_identity text, decided_by_verified boolean)",
    "create trigger trg_agent_messages_provenance before insert on agent_messages"
    " for each row execute function populate_agent_messages_provenance()",
    "create trigger trg_strategic_decisions_provenance before insert or update on strategic_decisions"
    " for each row execute function populate_strategic_decisions_provenance()",
    "alter table agent_messages enable row level security",
    "alter table strategic_decisions enable row level security",
    'create role "cc-ihsanos" nologin',
    'create role "musa" nologin',
    'grant insert, select on agent_messages, strategic_decisions to "cc-ihsanos", "musa"',
    'grant select on identity_allowlist to "cc-ihsanos", "musa"',
    'grant usage, select on all sequences in schema public to "cc-ihsanos", "musa"',
]


@pytest.fixture
def db(fresh_db):
    conn = psycopg.connect(fresh_db, autocommit=True)
    cur = conn.cursor()
    # functions first (schema's triggers reference them), then schema, then policies
    cur.execute(mig.HARDEN_AGENT_MESSAGES_TRIGGER)
    cur.execute(mig.HARDEN_STRATEGIC_DECISIONS_TRIGGER)
    for stmt in SCHEMA:
        cur.execute(stmt)
    cur.execute(mig.SEED_ALLOWLIST)
    cur.execute(mig.CREATE_RLS_AGENT_MESSAGES)
    cur.execute(mig.CREATE_RLS_SELECT_AGENT_MESSAGES)
    cur.execute(mig.CREATE_RLS_STRATEGIC_DECISIONS)
    cur.execute(mig.CREATE_RLS_SELECT_STRATEGIC_DECISIONS)
    yield conn
    conn.close()


# The migration enforces INSERT identity only; it adds no SELECT policy, so the
# restricted roles cannot use INSERT ... RETURNING (RETURNING is checked against
# SELECT policies, which would filter every row). Tests therefore insert under
# the agent role, then RESET ROLE to read back the stamped values as the
# superuser owner (which bypasses RLS). Per-agent SELECT policies are a separate
# follow-up required before per-agent credentials go live — see plan doc.


def test_ac1_posted_by_identity_is_not_caller_overridable(db):
    cur = db.cursor()
    cur.execute('set role "cc-ihsanos"')
    cur.execute("insert into agent_messages (from_agent, posted_by_identity, from_agent_verified) "
                "values ('cc-ihsanos', 'SPOOFED', true)")
    cur.execute("reset role")
    cur.execute("select posted_by_identity from agent_messages")
    assert cur.fetchone()[0] == "cc-ihsanos"  # caller's 'SPOOFED' ignored


def test_ac2_verified_is_always_computed_true_on_self_post(db):
    cur = db.cursor()
    cur.execute('set role "cc-ihsanos"')
    cur.execute("insert into agent_messages (from_agent) values ('cc-ihsanos')")
    cur.execute("reset role")
    cur.execute("select from_agent_verified from agent_messages")
    assert cur.fetchone()[0] is True


def test_ac3_cross_identity_post_is_blocked_by_rls(db):
    cur = db.cursor()
    cur.execute('set role "cc-ihsanos"')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("insert into agent_messages (from_agent) values ('cai')")


def test_ac4_operator_allowlist_to_cai_is_permitted_and_verified(db):
    cur = db.cursor()
    cur.execute('set role "musa"')
    cur.execute("insert into agent_messages (from_agent) values ('cai')")
    cur.execute("reset role")
    cur.execute("select posted_by_identity, from_agent_verified from agent_messages")
    identity, verified = cur.fetchone()
    assert identity == "musa"
    assert verified is True


def test_ac5_strategic_decisions_decided_by_is_enforced(db):
    cur = db.cursor()
    cur.execute('set role "cc-ihsanos"')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("insert into strategic_decisions (decided_by) values ('cai')")
    # autocommit connection: the failed insert is its own aborted statement and
    # does not poison the next one, so we can continue as the same role.
    cur.execute("insert into strategic_decisions (decided_by, decided_by_verified) "
                "values ('cc-ihsanos', true)")
    cur.execute("reset role")
    cur.execute("select posted_by_identity, decided_by_verified from strategic_decisions")
    identity, verified = cur.fetchone()
    assert identity == "cc-ihsanos"
    assert verified is True


# --- SELECT-visibility policy (agent_messages): own inbox + own sent + broadcast.
# Rows are seeded as the superuser owner (bypasses RLS), then read back under the
# agent role to assert exactly which rows the SELECT policy exposes.

def _seed(cur, from_agent, to_agent):
    cur.execute("reset role")
    cur.execute("insert into agent_messages (from_agent, to_agent) values (%s, %s)",
                (from_agent, to_agent))


def test_ac6_agent_reads_its_own_inbox(db):
    cur = db.cursor()
    _seed(cur, "cai", "cc-ihsanos")          # addressed TO cc-ihsanos
    cur.execute('set role "cc-ihsanos"')
    cur.execute("select count(*) from agent_messages where to_agent = 'cc-ihsanos'")
    assert cur.fetchone()[0] == 1


def test_ac7_agent_reads_its_own_sent(db):
    cur = db.cursor()
    _seed(cur, "cc-ihsanos", "cai")          # sent BY cc-ihsanos
    cur.execute('set role "cc-ihsanos"')
    cur.execute("select count(*) from agent_messages where from_agent = 'cc-ihsanos'")
    assert cur.fetchone()[0] == 1


def test_ac8_agent_reads_broadcasts(db):
    cur = db.cursor()
    _seed(cur, "cai", "broadcast")
    cur.execute('set role "cc-ihsanos"')
    cur.execute("select count(*) from agent_messages where to_agent = 'broadcast'")
    assert cur.fetchone()[0] == 1


def test_ac9_agent_cannot_read_other_agents_messages(db):
    cur = db.cursor()
    _seed(cur, "cai", "cc-scholar")          # neither to nor from cc-ihsanos
    cur.execute('set role "cc-ihsanos"')
    cur.execute("select count(*) from agent_messages")
    assert cur.fetchone()[0] == 0            # filtered out entirely


# --- strategic_decisions SELECT: shared governance ledger (cai CAI-RESP-214 = (b)).
# Challenge-window polling and the ihsanos-drain grant-check are definitionally reads
# of decisions the agent did NOT author, so every agent role reads the full ledger.

def test_ac10_agent_roles_read_full_decision_ledger(db):
    cur = db.cursor()
    cur.execute("reset role")
    cur.execute("insert into strategic_decisions (decided_by) values ('cai')")         # not authored by cc-ihsanos
    cur.execute("insert into strategic_decisions (decided_by) values ('cc-ihsanos')")  # own
    cur.execute('set role "cc-ihsanos"')
    cur.execute("select count(*) from strategic_decisions")
    assert cur.fetchone()[0] == 2            # shared ledger: sees others' decisions + its own
