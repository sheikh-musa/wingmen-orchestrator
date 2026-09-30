"""scripts/lane_operator_reconcile.py: explicit-id stamping only (Fable audit 2026-09-30
B-2 / op#23531 PR B). `handle --through N` was a tag-scoped HIGH-WATER stamp: a coord that
answered only the newest message and stamped 'through' its id silently marked every older
unanswered message handled. Now: `handle --tag T --ids a,b` stamps exactly those ids;
`--through` is refused (exit 2) and lists the ids it would have eaten so the caller can
re-run with --ids. Ephemeral PG only."""
import os
import subprocess
import sys

import psycopg
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from scripts import lane_operator_reconcile as lor  # noqa: E402

SCRIPT = os.path.join(ROOT, "scripts", "lane_operator_reconcile.py")


@pytest.fixture
def ledger_db(operator_ledger_db):
    with psycopg.connect(operator_ledger_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE operator_messages ADD COLUMN IF NOT EXISTS handled_at timestamptz")
    return operator_ledger_db


def _seed(dsn, rows):
    ids = []
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        for tag, text in rows:
            cur.execute("INSERT INTO operator_messages (direction, channel, tag, text) "
                        "VALUES ('inbound','telegram',%s,%s) RETURNING id", (tag, text))
            ids.append(cur.fetchone()[0])
    return ids


def _handled(dsn):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM operator_messages WHERE handled_at IS NOT NULL ORDER BY id")
        return [r[0] for r in cur.fetchall()]


def test_mark_handled_stamps_only_given_ids_in_tag(ledger_db, monkeypatch):
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-coord")
    ids = _seed(ledger_db, [("gazzabyte-irsyad", "old unanswered"), ("gazzabyte-irsyad", "newest"),
                            ("oeh", "other tag")])
    n = lor.mark_handled([ids[1], ids[2]], "gazzabyte-irsyad")
    assert n == 1                                   # other tag's id is not stamped
    assert _handled(ledger_db) == [ids[1]]          # the old unanswered row is UNTOUCHED
    assert [r[0] for r in lor.unprocessed("gazzabyte-irsyad")] == [ids[0]]


def test_mark_handled_requires_tag(ledger_db):
    with pytest.raises(SystemExit):
        lor.mark_handled([1], "")


def test_mark_handled_through_is_refused_and_stamps_nothing(ledger_db):
    ids = _seed(ledger_db, [("gazzabyte-irsyad", "a"), ("gazzabyte-irsyad", "b")])
    with pytest.raises(SystemExit) as exc:
        lor.mark_handled_through(ids[-1], "gazzabyte-irsyad")
    assert exc.value.code == 2
    assert "--ids" in exc.value.why and str(ids[0]) in exc.value.why
    assert _handled(ledger_db) == []


def _cli(args, env):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True,
                          env=env, timeout=30)


def test_cli_handle_ids_stamps_exactly(ledger_db):
    ids = _seed(ledger_db, [("gazzabyte-irsyad", "a"), ("gazzabyte-irsyad", "b")])
    env = {**os.environ, "DATABASE_URL": ledger_db, "CC_BASE_AGENT_ID": "cc-irsyad-coord"}
    env.pop("SUPABASE_DB_URL", None)
    r = _cli(["handle", "--tag", "gazzabyte-irsyad", "--ids", str(ids[1])], env)
    assert r.returncode == 0, r.stderr
    assert _handled(ledger_db) == [ids[1]]


def test_cli_handle_through_refuses_with_listing(ledger_db):
    ids = _seed(ledger_db, [("gazzabyte-irsyad", "a"), ("gazzabyte-irsyad", "b")])
    env = {**os.environ, "DATABASE_URL": ledger_db, "CC_BASE_AGENT_ID": "cc-irsyad-coord"}
    env.pop("SUPABASE_DB_URL", None)
    r = _cli(["handle", "--tag", "gazzabyte-irsyad", "--through", str(ids[1])], env)
    assert r.returncode == 2
    out = r.stdout + r.stderr
    assert str(ids[0]) in out and str(ids[1]) in out and "--ids" in out
    assert _handled(ledger_db) == []


def test_cli_read_hint_names_ids_not_through(ledger_db):
    ids = _seed(ledger_db, [("gazzabyte-irsyad", "a")])
    env = {**os.environ, "DATABASE_URL": ledger_db}
    env.pop("SUPABASE_DB_URL", None)
    r = _cli(["read", "--tag", "gazzabyte-irsyad"], env)
    assert r.returncode == 0, r.stderr
    assert "--ids" in r.stdout and "--through" not in r.stdout
