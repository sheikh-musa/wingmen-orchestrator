"""operator_log failure_reason -> cos_triage (Nazim #40837/#40866). Per the constraint-lock
lesson (#40859): mock the network, but exercise the REAL operator_messages write so a
schema/column mismatch fails the suite, not production. Inserts a clearly-test row
(chat 123456, tag __test__, delivered=False), reads cos_triage back, then deletes it."""
import importlib
import os

import pytest

ol = importlib.import_module("nervous_system.operator_log")


def _dsn():
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


@pytest.mark.skipif(not _dsn(), reason="no DSN (offline CI) — the mocked helper tests cover logic")
def test_log_writes_failure_reason_into_cos_triage_for_real():
    import psycopg
    reason = {"status": 429, "description": "Too Many Requests", "retry_after": 9, "chunk": "1/1"}
    rid = ol.log("outbound", "[__test__] tg-send resilience cos_triage write",
                 chat_id="123456", tag="__test__", delivered=False, failure_reason=reason)
    try:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("SELECT delivered, cos_triage FROM operator_messages WHERE id=%s", (rid,))
            delivered, cos = cur.fetchone()
        assert delivered is False
        assert cos is not None, "cos_triage must be written when failure_reason is given"
        sf = cos.get("send_failure") or {}
        assert sf.get("status") == 429 and sf.get("retry_after") == 9
        assert "Too Many Requests" in (sf.get("description") or "")
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_messages WHERE id=%s", (rid,))
            c.commit()


@pytest.mark.skipif(not _dsn(), reason="no DSN")
def test_log_without_failure_reason_leaves_cos_triage_null():
    import psycopg
    rid = ol.log("outbound", "[__test__] no-reason row", chat_id="123456", tag="__test__",
                 delivered=True)
    try:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("SELECT cos_triage FROM operator_messages WHERE id=%s", (rid,))
            (cos,) = cur.fetchone()
        assert cos is None
    finally:
        with psycopg.connect(_dsn()) as c, c.cursor() as cur:
            cur.execute("DELETE FROM operator_messages WHERE id=%s", (rid,))
            c.commit()
