"""TG2.0 s6, section 6 — the paths unit fakes could not prove.

cc-quality's PR#726 review (bus #86348) proved, on a throwaway PostgreSQL
cluster, that two failures were invisible to the shape-matched fakes:

  * Finding 1: `record_unmatched` committed inside the caller's
    `with conn.transaction():`, which on a real psycopg3 connection raises
    `ProgrammingError: Explicit commit() forbidden within a Transaction
    context` — every FIRST alert was swallowed. A FakeConn whose commit() just
    counts cannot see it.
  * Finding 2: an expired row is still `state='pending'`, so a re-alert INSERT
    hit the partial unique index. A fake that does not model the index cannot
    see it.

So these tests start a real, throwaway PostgreSQL cluster (initdb in a temp
dir, no network listener beyond loopback, no production connection string, no
environment DSN), apply migration 136 as its own `psql` run, and drive the
real code against the real constraints. Skipped where the postgres binaries
are absent — the same shape the reviewer used to prove the defects.

Nothing here reads or writes any fleet database: the conninfo is assembled
from the temp directory and a free local port.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

psycopg = pytest.importorskip("psycopg")

MIGRATION = Path(__file__).parent.parent / "migrations" / "136_tg_pending_contacts.sql"
BOT = "wingmendevbot"
REVIEWER = "999"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(b) for b in ("initdb", "pg_ctl", "psql")),
    reason="needs local postgres binaries (initdb/pg_ctl/psql) for a throwaway cluster",
)


def _server_bindir():
    """The directory holding a REAL `initdb` — one with `postgres` beside it.

    Homebrew's libpq formula ships a client-only initdb that fails with
    'program "postgres" is needed by initdb but was not found'; that is not a
    server, so it must skip, not error.
    """
    candidates = []
    found = shutil.which("initdb")
    if found:
        candidates.append(Path(found).parent)
    pg_config = shutil.which("pg_config")
    if pg_config:
        r = subprocess.run([pg_config, "--bindir"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            candidates.append(Path(r.stdout.strip()))
    # Homebrew installs the SERVER in a versioned keg that PATH may not lead
    # to (the unversioned `initdb` above is libpq's client-only one).
    for prefix in ("/opt/homebrew/opt", "/usr/local/opt"):
        candidates.extend(sorted(Path(prefix).glob("postgresql*/bin"), reverse=True))
    for d in candidates:
        if (d / "postgres").exists() and (d / "initdb").exists():
            return d
    return None



def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def cluster():
    bindir = _server_bindir()
    if bindir is None:
        pytest.skip("no postgres SERVER binaries on this host (client-only initdb)")
    tmp = tempfile.mkdtemp(prefix="tg136-")
    data, sock = Path(tmp) / "data", Path(tmp) / "sock"
    sock.mkdir()
    port = _free_port()
    initdb, pg_ctl = str(bindir / "initdb"), str(bindir / "pg_ctl")
    psql = shutil.which("psql") or str(bindir / "psql")
    # macOS: without a plain locale the postmaster goes multithreaded during
    # startup and refuses ("postmaster became multithreaded during startup").
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    try:
        subprocess.run(
            [initdb, "-D", str(data), "-U", "postgres", "-A", "trust",
             "--no-sync", "-E", "UTF8"],
            check=True, capture_output=True, text=True, env=env)
        subprocess.run(
            [pg_ctl, "-D", str(data), "-l", str(Path(tmp) / "log"),
             "-o", f"-k {sock} -p {port} -c listen_addresses=127.0.0.1",
             "-w", "start"],
            check=True, capture_output=True, text=True, env=env)
        conninfo = f"host={sock} port={port} dbname=postgres user=postgres"

        with psycopg.connect(conninfo, autocommit=True) as conn:
            # The roles a Supabase/PostgREST substrate has by default. Created
            # here so migration 136's REVOKE/GRANT floor is applied verbatim.
            for role in ("anon", "authenticated", "service_role", "console_readonly"):
                conn.execute(f"CREATE ROLE {role} NOLOGIN")

        # Apply the migration exactly as the gate would: its own file, its own
        # transaction, ON_ERROR_STOP.
        subprocess.run(
            [psql, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", str(sock),
             "-p", str(port), "-U", "postgres", "-d", "postgres",
             "-f", str(MIGRATION)],
            check=True, capture_output=True, text=True, env=env)
        yield conninfo
    finally:
        subprocess.run([pg_ctl, "-D", str(data), "-m", "immediate", "-w", "stop"],
                       check=False, capture_output=True, text=True)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture()
def conn(cluster):
    c = psycopg.connect(cluster)
    c.execute("TRUNCATE public.tg_pending_contacts RESTART IDENTITY")
    c.commit()
    yield c
    c.close()


def _ingest(monkeypatch):
    ingest = pytest.importorskip("nervous_system.ingest")
    sent = []

    def fake_tg_call(token, method, payload):
        sent.append((method, payload))
        return {"ok": True}

    monkeypatch.setattr(ingest, "tg_call", fake_tg_call)
    monkeypatch.setenv("MUSA_TELEGRAM_ID", REVIEWER)
    ch = SimpleNamespace(key="coffeemedia", channel_tag="coffeemedia",
                         token="tok", token_env_key=BOT)
    return ingest, ch, sent


def _update(chat_id=777, first_name="Afiq", username="afiq"):
    return {"message": {"chat": {"id": chat_id},
                        "from": {"first_name": first_name, "username": username}}}


# ── Finding 1: the alert is actually delivered ─────────────────────────────

def test_first_alert_is_delivered_through_the_real_transaction_wrapper(conn, monkeypatch):
    ingest, ch, sent = _ingest(monkeypatch)
    ingest._maybe_page_new_contact(conn, ch, _update(), None)
    conn.commit()

    assert sent, "no alert was sent — the record_unmatched commit() was swallowed"
    method, payload = sent[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == REVIEWER
    assert "Afiq" in payload["text"]
    assert any(b["callback_data"].startswith("pc:approve:")
               for row in payload["reply_markup"]["inline_keyboard"] for b in row)

    row = conn.execute("SELECT state, pending_id FROM public.tg_pending_contacts").fetchone()
    assert row[0] == "pending"


def test_a_second_message_from_the_same_chat_does_not_alert_again(conn, monkeypatch):
    ingest, ch, sent = _ingest(monkeypatch)
    ingest._maybe_page_new_contact(conn, ch, _update(), None)
    ingest._maybe_page_new_contact(conn, ch, _update(), None)
    conn.commit()
    assert len(sent) == 1
    assert conn.execute("SELECT count(*) FROM public.tg_pending_contacts").fetchone()[0] == 1


# ── Finding 2: expiry re-alerts against the real partial unique index ──────

def test_expired_pending_row_is_refreshed_not_duplicated(conn, monkeypatch):
    ingest, ch, sent = _ingest(monkeypatch)
    conn.execute(
        "INSERT INTO public.tg_pending_contacts "
        "(bot_username, chat_id, channel_tag, first_name, username, pending_id, "
        " notified_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, now() - interval '4 days', "
        "        now() - interval '1 day')",
        (BOT, "777", "coffeemedia", "Old", None, "pcid-old"))
    conn.commit()

    # Before the fix this raised UniqueViolation inside the savepoint and the
    # alert was silently dropped for the contact, forever.
    ingest._maybe_page_new_contact(conn, ch, _update(), None)
    conn.commit()

    assert sent, "an expired contact was never re-alerted"
    rows = conn.execute(
        "SELECT state, pending_id FROM public.tg_pending_contacts").fetchall()
    assert len(rows) == 1, "the stale row must be refreshed, not duplicated"
    assert rows[0][0] == "pending" and rows[0][1] != "pcid-old"


# ── the button path on the real table ───────────────────────────────────────

def test_approve_files_the_gate_and_resolves_the_row(conn):
    from nervous_system import pending_contacts as pcm
    conn.execute(
        "INSERT INTO public.tg_pending_contacts "
        "(bot_username, chat_id, channel_tag, first_name, username, pending_id, "
        " notified_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, now(), now() + interval '3 days')",
        (BOT, "777", "coffeemedia", "Afiq", "afiq", "pcid-1"))
    conn.commit()

    filed = []
    res = pcm.handle_button(conn, data="pc:approve:pcid-1", presser=REVIEWER,
                            reviewer_chat=REVIEWER, file_gate_request=filed.append)
    assert res == {"ok": True, "action": "approve"}
    assert len(filed) == 1
    row = conn.execute(
        "SELECT state, resolved_by FROM public.tg_pending_contacts").fetchone()
    assert row == ("approved", REVIEWER)


def test_a_failed_gate_filing_leaves_the_contact_actionable(conn):
    from nervous_system import pending_contacts as pcm
    conn.execute(
        "INSERT INTO public.tg_pending_contacts "
        "(bot_username, chat_id, channel_tag, first_name, username, pending_id, "
        " notified_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, now(), now() + interval '3 days')",
        (BOT, "777", "coffeemedia", "Afiq", "afiq", "pcid-1"))
    conn.commit()

    def failing(_req):
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()

    res = pcm.handle_button(conn, data="pc:approve:pcid-1", presser=REVIEWER,
                            reviewer_chat=REVIEWER, file_gate_request=failing)
    assert res["ok"] is False and res["reason"] == "gate_not_filed"
    row = conn.execute(
        "SELECT state, resolved_at FROM public.tg_pending_contacts").fetchone()
    assert row == ("pending", None), "the tap must be retryable"

    # Retrying with a working filer succeeds.
    res2 = pcm.handle_button(conn, data="pc:approve:pcid-1", presser=REVIEWER,
                             reviewer_chat=REVIEWER, file_gate_request=lambda r: None)
    assert res2["ok"] is True


# ── Finding 3: the migration actually leaves web roles without privilege ───

def test_migration_leaves_web_roles_with_no_privilege_and_rls_on(conn):
    for role in ("anon", "authenticated", "service_role"):
        for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert conn.execute(
                "SELECT has_table_privilege(%s, 'public.tg_pending_contacts', %s)",
                (role, priv)).fetchone()[0] is False, (role, priv)
        for priv in ("USAGE", "SELECT", "UPDATE"):
            assert conn.execute(
                "SELECT has_sequence_privilege(%s, 'public.tg_pending_contacts_id_seq', %s)",
                (role, priv)).fetchone()[0] is False, (role, priv)

    assert conn.execute(
        "SELECT relrowsecurity FROM pg_class "
        "WHERE oid = 'public.tg_pending_contacts'::regclass").fetchone()[0] is True
