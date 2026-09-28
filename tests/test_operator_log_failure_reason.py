"""operator_log failure_reason -> cos_triage (Nazim #40837/#40866). Per the constraint-lock
lesson (#40859): mock the network, but exercise the REAL operator_messages write so a
schema/column mismatch fails the suite, not production. Inserts a clearly-test row
(chat 123456, tag __test__, delivered=False), reads cos_triage back, then deletes it.

bus #44531: ol.log() opens and commits its OWN internal connection (see
nervous_system/operator_log.py), so — unlike the other live-DB integration
tests fixed for #44531 — this write cannot be made safe by wrapping the
test's own connection in a transaction that's always rolled back; the row is
already committed, via a connection the test never controlled, before the
test gets control back. Wrapping it would mean monkeypatching ol.log()'s
internals, defeating this test's whole point (exercising the REAL write path
against the REAL schema). So this file takes the OTHER option orch-console
offered: gated behind an explicit opt-in env var, never plain DATABASE_URL —
SUBSTRATE_LIVE_TESTS=1 must also be set, so this real (if synthetic and
self-cleaning) write only happens when someone deliberately asks for it, not
on every PR's CI run by default."""
import importlib
import os

import pytest

ol = importlib.import_module("nervous_system.operator_log")


def _dsn():
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _live_tests_enabled():
    return bool(_dsn()) and os.environ.get("SUBSTRATE_LIVE_TESTS") == "1"


@pytest.mark.skipif(
    not _live_tests_enabled(),
    reason="requires DATABASE_URL and SUBSTRATE_LIVE_TESTS=1 (opt-in; bus #44531) "
    "— the mocked helper tests cover the logic otherwise",
)
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


@pytest.mark.skipif(
    not _live_tests_enabled(),
    reason="requires DATABASE_URL and SUBSTRATE_LIVE_TESTS=1 (opt-in; bus #44531)",
)
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
