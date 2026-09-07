"""Ephemeral wet-prove for migration 060 — reject to_agent='substrate' (NOT VALID).

Exercises the SHIPPED artifact: the DDL is read FROM migrations/060_*.sql (not re-typed), so
this test breaks if the migration drifts from what it proves. Runs against a throwaway PG (the
fresh_db fixture), never the live substrate. Proves the exact scope orch-console gated (38080):

  * an INSERT to the retired alias 'substrate' RAISES (CheckViolation),
  * every KEEP target — musa, broadcast, operator (explicitly, since keeping it valid IS the
    scope decision), orch-console, cc-substrate (the real agent, not the pseudo) — PASSES,
  * NOT VALID leaves the 76-historical-rows case intact: a pre-existing 'substrate' row planted
    BEFORE the constraint survives the apply (a VALIDATED check would have failed the whole apply).
"""
import pathlib
import re

import psycopg
import pytest

MIG = (pathlib.Path(__file__).resolve().parent.parent.parent
       / "migrations" / "060_agent_messages_reject_pseudo_targets.sql")


def _executable_sql(path: pathlib.Path) -> str:
    """The migration body with `-- ...` comment lines stripped — the same lines apply_migration
    ignores. Dollar-quoted DO blocks are preserved verbatim."""
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


def _split_statements(sql: str):
    """Split on ';' at top level only, respecting `$$` dollar-quoting so the DO block stays whole."""
    out, buf, in_dollar = [], [], False
    i = 0
    while i < len(sql):
        if sql[i:i + 2] == "$$":
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        ch = sql[i]
        if ch == ";" and not in_dollar:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _make_table(cur):
    cur.execute(
        "CREATE TABLE agent_messages ("
        "  id serial PRIMARY KEY,"
        "  from_agent text,"
        "  to_agent text NOT NULL,"
        "  message_type text,"
        "  subject text,"
        "  body text)")


def _apply_migration(cur):
    for stmt in _split_statements(_executable_sql(MIG)):
        cur.execute(stmt)


def test_060_rejects_substrate_keeps_the_rest(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        _make_table(cur)
        # A pre-existing 'substrate' row (stands in for the 76 historical rows) — planted BEFORE
        # the constraint. NOT VALID must leave it untouched.
        cur.execute("INSERT INTO agent_messages (from_agent, to_agent, subject) "
                    "VALUES ('cai', 'substrate', 'historical FYI echo')")

        _apply_migration(cur)  # applies the ALTER ... NOT VALID + the in-txn DO self-check

        # history intact — the pre-existing substrate row survived the NOT VALID apply
        cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent='substrate'")
        assert cur.fetchone()[0] == 1

        # NEW substrate insert is refused
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("INSERT INTO agent_messages (from_agent, to_agent, subject) "
                        "VALUES ('cai', 'substrate', 'new echo — must be rejected')")

        # every KEEP target passes — operator included explicitly (the scope decision)
        for tgt in ("musa", "broadcast", "operator", "orch-console", "cc-substrate"):
            cur.execute("INSERT INTO agent_messages (from_agent, to_agent, subject) "
                        "VALUES ('cc-fleet-health', %s, 'keep')", [tgt])
        cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent = ANY(%s)",
                    [["musa", "broadcast", "operator", "orch-console", "cc-substrate"]])
        assert cur.fetchone()[0] == 5


def test_060_constraint_is_NOT_VALID(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        _make_table(cur)
        _apply_migration(cur)
        cur.execute("SELECT convalidated FROM pg_constraint "
                    "WHERE conname='agent_messages_reject_pseudo_targets'")
        row = cur.fetchone()
        assert row is not None and row[0] is False  # NOT VALID — the DO self-check also enforces this
