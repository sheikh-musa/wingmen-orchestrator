"""scripts/asks_daily_digest.py — op#22669 piece 4 (second half): a simple
once-daily roll-up of open operator_asks. render_digest() is pure formatting;
_already_sent_today/_mark_sent are a trivial date-stamp dedup. Both are tested
with no DB/network."""
import importlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

add = importlib.import_module("scripts.asks_daily_digest")


def row(id=1, ask="do the thing", delegated_to="cc-scholar", waiting_on_operator=False,
        chase_by=None, created_at="2026-09-27T10:00:00Z"):
    # migration 082: fetch_open_asks() now selects triage_summary (never the
    # raw `ask` column) — the `ask` kwarg name is kept for existing callers'
    # readability but populates triage_summary, matching the real query shape.
    return {"id": id, "triage_summary": ask, "delegated_to": delegated_to,
            "waiting_on_operator": waiting_on_operator, "chase_by": chase_by,
            "created_at": created_at}


# ── render_digest: pure formatting ────────────────────────────────────────────
def test_empty_rows_says_ledger_is_clear():
    assert "nothing open" in add.render_digest([])


def test_header_counts_total_and_waiting():
    rows = [row(id=1, waiting_on_operator=True), row(id=2), row(id=3)]
    digest = add.render_digest(rows)
    assert "3 open" in digest
    assert "1 waiting on you" in digest


def test_waiting_rows_appear_under_waiting_on_you_section():
    rows = [row(id=1, ask="approve the migration", waiting_on_operator=True)]
    digest = add.render_digest(rows)
    assert "WAITING ON YOU:" in digest
    assert "#1" in digest and "approve the migration" in digest


def test_non_waiting_rows_appear_under_open_in_progress():
    rows = [row(id=2, ask="ship the thing", waiting_on_operator=False)]
    digest = add.render_digest(rows)
    assert "Open / in progress:" in digest
    assert "#2" in digest and "ship the thing" in digest


def test_waiting_section_omitted_when_none_waiting():
    rows = [row(id=2, waiting_on_operator=False)]
    digest = add.render_digest(rows)
    assert "WAITING ON YOU:" not in digest


def test_others_section_omitted_when_all_waiting():
    rows = [row(id=1, waiting_on_operator=True)]
    digest = add.render_digest(rows)
    assert "Open / in progress:" not in digest


def test_unassigned_delegated_to_falls_back_to_label():
    rows = [row(id=9, delegated_to=None, waiting_on_operator=False)]
    digest = add.render_digest(rows)
    assert "unassigned" in digest


# ── migration 082: captured-bucket nudge line, never enumerated ──────────────
def test_captured_count_line_appears_with_age():
    digest = add.render_digest([], {"count": 5, "oldest": datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)})
    assert "5 messages not yet sorted" in digest
    assert "oldest" in digest


def test_no_captured_line_when_count_zero():
    rows = [row(id=1)]
    digest = add.render_digest(rows, {"count": 0, "oldest": None})
    assert "not yet sorted" not in digest


def test_empty_with_zero_captured_still_says_ledger_clear():
    assert "nothing open" in add.render_digest([], {"count": 0, "oldest": None})


def test_not_ledger_clear_when_only_captured_rows_exist():
    """A digest must not claim 'nothing open' when there ARE untriaged
    captures sitting in the ledger — only when both buckets are truly empty."""
    digest = add.render_digest([], {"count": 3, "oldest": datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)})
    assert "nothing open" not in digest
    assert "3 messages not yet sorted" in digest


def test_fetch_open_asks_scopes_to_operator_surface_only():
    """Migration 085 regression guard: a client-channel row triaged 'ask'
    must NEVER leak into Musa's own board mixed in as if he'd asked it
    himself — fetch_open_asks()'s SQL must filter ask_surface='operator'."""
    import inspect
    src = inspect.getsource(add.fetch_open_asks)
    assert "ask_surface = 'operator'" in src


def test_fetch_captured_summary_scopes_to_operator_surface_only():
    import inspect
    src = inspect.getsource(add.fetch_captured_summary)
    assert "ask_surface = 'operator'" in src


def test_digest_never_shows_raw_ask_text_only_triage_summary():
    """render_digest must render triage_summary, never a raw `ask` field —
    even if a caller's row dict happens to still carry one (defence in depth:
    fetch_open_asks() no longer SELECTs `ask` at all)."""
    r = row(id=1, ask="clean summary")
    r["ask"] = "RAW UNTRIAGED TEXT SHOULD NEVER APPEAR"
    digest = add.render_digest([r])
    assert "RAW UNTRIAGED TEXT SHOULD NEVER APPEAR" not in digest
    assert "clean summary" in digest


# ── dedup: at most one send per UTC calendar day ──────────────────────────────
def test_already_sent_today_false_when_state_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(add, "STATE_FILE", tmp_path / "nope.json")
    assert add._already_sent_today("2026-09-27") is False


def test_mark_sent_then_already_sent_today_true(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(add, "STATE_FILE", state_file)
    add._mark_sent("2026-09-27")
    assert add._already_sent_today("2026-09-27") is True
    assert json.loads(state_file.read_text()) == {"last_sent_date": "2026-09-27"}


def test_already_sent_today_false_for_a_different_date(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(add, "STATE_FILE", state_file)
    add._mark_sent("2026-09-26")
    assert add._already_sent_today("2026-09-27") is False


def test_already_sent_today_false_on_corrupt_state_file(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text("not json")
    monkeypatch.setattr(add, "STATE_FILE", state_file)
    assert add._already_sent_today("2026-09-27") is False


# ── main(): dry-run / force / already-sent skip ───────────────────────────────
class _FakeCur:
    def execute(self, *a, **k): pass
    def fetchall(self): return []
    def fetchone(self): return (0, None)
    @property
    def description(self): return []
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def cursor(self): return _FakeCur()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_main_dry_run_prints_digest_and_does_not_send(monkeypatch, capsys):
    monkeypatch.setattr(add, "_dsn", lambda: "postgresql://unused")
    monkeypatch.setattr(add.psycopg, "connect", lambda *a, **k: _FakeConn())
    monkeypatch.setattr(add.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry-run must not send")))
    rc = add.main(["--dry-run"])
    assert rc == 0
    assert "nothing open" in capsys.readouterr().out


def test_main_skips_when_already_sent_today_and_not_forced(monkeypatch, tmp_path):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(add, "STATE_FILE", state_file)
    import datetime
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    add._mark_sent(today)
    monkeypatch.setattr(add.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not send")))
    rc = add.main([])
    assert rc == 0


def test_main_no_dsn_returns_2(monkeypatch, tmp_path):
    monkeypatch.setattr(add, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(add, "_dsn", lambda: None)
    rc = add.main(["--force"])
    assert rc == 2


def test_main_sends_via_nazim_send_not_tg_send(monkeypatch, tmp_path):
    """op#22669 / orch-console bus #43972: this launchd job is Mini/console
    hosted and the asks ledger is Nazim's own thread, not the hub's — sending
    via tg_send.sh fail-closes for the console body (ORCH-TOPOLOGY-001) and
    would silently never reach the operator."""
    monkeypatch.setattr(add, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(add, "_dsn", lambda: "postgresql://unused")
    monkeypatch.setattr(add.psycopg, "connect", lambda *a, **k: _FakeConn())
    captured = {}

    def fake_run(cmd, *a, **k):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(add.subprocess, "run", fake_run)
    rc = add.main(["--force"])
    assert rc == 0
    assert captured["cmd"][:2] == ["bash", str(add.ORCH / "scripts" / "nazim_send.sh")]
    assert "tg_send.sh" not in " ".join(captured["cmd"])


# ── _log_uae_send_time: bus #44286 robustness (log the actual UAE send hour) ──
def test_log_uae_send_time_at_9am_uae_is_silent_no_warning(capsys):
    nine_am_uae_as_utc = datetime(2026, 9, 28, 5, 0, tzinfo=timezone.utc)  # 09:00 Asia/Dubai
    add._log_uae_send_time(nine_am_uae_as_utc)
    out = capsys.readouterr().out
    assert "09:00" in out or "09:0" in out
    assert "WARNING" not in out


def test_log_uae_send_time_warns_when_far_from_9am_uae(capsys):
    one_am_uae_as_utc = datetime(2026, 9, 27, 21, 0, tzinfo=timezone.utc)  # 01:00 Asia/Dubai -- the bus #44286 incident
    add._log_uae_send_time(one_am_uae_as_utc)
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "44286" in out


def test_log_uae_send_time_defaults_to_now_when_no_arg_given(monkeypatch, capsys):
    fixed = datetime(2026, 9, 28, 5, 0, tzinfo=timezone.utc)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(add, "datetime", _FixedDatetime)
    add._log_uae_send_time()
    assert "WARNING" not in capsys.readouterr().out


def test_main_logs_uae_send_time_before_sending(monkeypatch, tmp_path):
    monkeypatch.setattr(add, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(add, "_dsn", lambda: "postgresql://unused")
    monkeypatch.setattr(add.psycopg, "connect", lambda *a, **k: _FakeConn())
    order = []
    monkeypatch.setattr(add, "_log_uae_send_time", lambda *a, **k: order.append("log"))
    monkeypatch.setattr(add.subprocess, "run",
                         lambda *a, **k: order.append("send") or SimpleNamespace(returncode=0))
    rc = add.main(["--force"])
    assert rc == 0
    assert order == ["log", "send"]


def test_main_does_not_log_uae_send_time_on_dry_run(monkeypatch, capsys):
    monkeypatch.setattr(add, "_dsn", lambda: "postgresql://unused")
    monkeypatch.setattr(add.psycopg, "connect", lambda *a, **k: _FakeConn())
    called = []
    monkeypatch.setattr(add, "_log_uae_send_time", lambda *a, **k: called.append(True))
    rc = add.main(["--dry-run"])
    assert rc == 0
    assert called == []


# ── migration 085: client asks section (Musa op#23944, bus #47110/#47114) ────
def client_row(id=1, ask="add feature X", delegated_to="cc-irsyad-coord",
                committed_date=None, created_at=None):
    created_at = created_at or datetime(2026, 9, 29, 1, 47, tzinfo=timezone.utc)
    return {"id": id, "triage_summary": ask, "delegated_to": delegated_to,
            "committed_date": committed_date, "created_at": created_at}


def test_client_section_omitted_when_no_client_rows_or_captured():
    digest = add.render_digest([row(id=1)])
    assert "CLIENT ASKS" not in digest


def test_client_section_appears_with_count_and_age():
    now = datetime(2026, 10, 1, 1, 47, tzinfo=timezone.utc)  # 72h after the client_row default
    digest = add.render_digest([], {"count": 0, "oldest": None},
                                [client_row(id=7)], {"count": 0, "oldest": None})
    assert "CLIENT ASKS" in digest
    assert "1 open" in digest
    assert "#7" in digest
    assert "add feature X" in digest


def test_client_row_shows_no_date_yet_when_uncommitted():
    digest = add.render_digest([], None, [client_row(id=7, committed_date=None)], None)
    assert "no date yet" in digest


def test_client_row_shows_committed_date_when_set():
    committed = datetime(2026, 10, 2, tzinfo=timezone.utc)
    digest = add.render_digest([], None, [client_row(id=7, committed_date=committed)], None)
    assert "committed 2026-10-02" in digest
    assert "FAR-OUT" not in digest


def test_client_row_flags_far_out_committed_date():
    """Musa op#23946: 'no far-out dates' — a commitment given more than 3
    days after the client's own ask must be flagged (bus #47110 item 3b)."""
    created = datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc)
    committed = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)  # 6 days out
    digest = add.render_digest(
        [], None, [client_row(id=7, created_at=created, committed_date=committed)], None)
    assert "FAR-OUT DATE" in digest
    assert "op#23946" in digest


def test_client_row_within_3_days_is_not_flagged():
    created = datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc)
    committed = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)  # 2 days out
    digest = add.render_digest(
        [], None, [client_row(id=7, created_at=created, committed_date=committed)], None)
    assert "FAR-OUT" not in digest


def test_client_captured_count_line_never_enumerates_raw_text():
    digest = add.render_digest(
        [], None, [], {"count": 4, "oldest": datetime(2026, 9, 29, tzinfo=timezone.utc)})
    assert "4 client messages not yet sorted" in digest


def test_ledger_not_clear_when_only_client_asks_open():
    """The digest must not claim 'nothing open' when there ARE open client
    asks, even with zero operator-surface asks (a plausible day: Musa has no
    personal asks open, but a client one is sitting there)."""
    digest = add.render_digest([], None, [client_row(id=9)], None)
    assert "nothing open" not in digest


def test_client_section_never_shows_captured_raw_ask_text():
    """Only triage_state='ask' rows (a human-reviewed triage_summary) ever
    reach fetch_client_open_asks() — captured rows are summarized as a count
    only via fetch_client_captured_summary(), never enumerated (same no-PII
    rule as the operator side, bus #47114: 'summaries, not raw text')."""
    digest = add.render_digest(
        [], None, [], {"count": 1, "oldest": datetime(2026, 9, 29, tzinfo=timezone.utc)})
    assert "1 client messages not yet sorted" in digest


# ── bus #47267 item 2: DIGEST_CLIENT_ASKS_ENABLED default-off gate ───────────
def test_client_asks_digest_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv(add.DIGEST_CLIENT_ASKS_ENABLED_ENV, raising=False)
    assert add.client_asks_digest_enabled() is False


def test_client_asks_digest_enabled_off_for_non_exact_values(monkeypatch):
    for v in ("true", "TRUE", "yes", "0", ""):
        monkeypatch.setenv(add.DIGEST_CLIENT_ASKS_ENABLED_ENV, v)
        assert add.client_asks_digest_enabled() is False


def test_client_asks_digest_enabled_true_only_for_exact_1(monkeypatch):
    monkeypatch.setenv(add.DIGEST_CLIENT_ASKS_ENABLED_ENV, "1")
    assert add.client_asks_digest_enabled() is True


def test_main_does_not_fetch_client_rows_when_flag_off(monkeypatch, tmp_path):
    """orch-console bus #47267 decision 2: until the ~118-row irsyad backlog is
    triaged, the client section must stay OFF Musa's morning roll-up by
    default — main() must not even query the client tables."""
    monkeypatch.delenv(add.DIGEST_CLIENT_ASKS_ENABLED_ENV, raising=False)
    monkeypatch.setattr(add, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(add, "_dsn", lambda: "postgresql://unused")
    monkeypatch.setattr(add.psycopg, "connect", lambda *a, **k: _FakeConn())
    monkeypatch.setattr(
        add, "fetch_client_open_asks",
        lambda conn: (_ for _ in ()).throw(AssertionError("must not fetch when flag is off")))
    monkeypatch.setattr(
        add, "fetch_client_captured_summary",
        lambda conn: (_ for _ in ()).throw(AssertionError("must not fetch when flag is off")))
    rc = add.main(["--dry-run"])
    assert rc == 0


def test_main_fetches_client_rows_when_flag_on(monkeypatch, tmp_path):
    monkeypatch.setenv(add.DIGEST_CLIENT_ASKS_ENABLED_ENV, "1")
    monkeypatch.setattr(add, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(add, "_dsn", lambda: "postgresql://unused")
    monkeypatch.setattr(add.psycopg, "connect", lambda *a, **k: _FakeConn())
    called = {"open": False, "captured": False}

    def fake_open(conn):
        called["open"] = True
        return []

    def fake_captured(conn):
        called["captured"] = True
        return {"count": 0, "oldest": None}

    monkeypatch.setattr(add, "fetch_client_open_asks", fake_open)
    monkeypatch.setattr(add, "fetch_client_captured_summary", fake_captured)
    rc = add.main(["--dry-run"])
    assert rc == 0
    assert called["open"] and called["captured"]


# ── bus #47267 item 4: behavioral row-level surface-scoping (not just SQL text) ─
def test_fetch_open_asks_and_fetch_client_open_asks_are_row_level_disjoint(operator_ledger_db):
    """Insert one operator-surface row and one client-channel row and prove
    each fetcher returns ONLY its own surface — a stronger guard than the
    existing inspect.getsource string checks, which would pass even if the
    SQL text and the live query diverged."""
    import psycopg
    with psycopg.connect(operator_ledger_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO operator_asks (ask, triage_state, triage_summary, ask_surface) "
                "VALUES ('op ask text', 'ask', 'operator summary', 'operator') RETURNING id")
            operator_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO operator_asks (ask, triage_state, triage_summary, ask_surface) "
                "VALUES ('client ask text', 'ask', 'client summary', 'client-channel') RETURNING id")
            client_id = cur.fetchone()[0]
            conn.commit()

        operator_rows = add.fetch_open_asks(conn)
        client_rows = add.fetch_client_open_asks(conn)

    operator_ids = {r["id"] for r in operator_rows}
    client_ids = {r["id"] for r in client_rows}
    assert operator_id in operator_ids
    assert operator_id not in client_ids
    assert client_id in client_ids
    assert client_id not in operator_ids
