"""scripts/asks_reconcile_daily.py — Musa op#27030 (bus #57235): daily
operator_asks ledger reconciliation. The four fetch/apply functions run
against the real ephemeral operator_ledger_db schema (never mocked SQL —
these are genuine queries with real JOIN/EXISTS logic worth exercising for
real); the render_* functions and group_untriaged_by_owner are pure and
tested with no DB."""
import importlib
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

ard = importlib.import_module("scripts.asks_reconcile_daily")


# ── pure: CHANNEL_OWNER / group_untriaged_by_owner ───────────────────────────

def test_group_untriaged_by_owner_maps_known_tags():
    rows = [
        {"id": 1, "tag": "nazim-console", "ask": "x", "created_at": "t"},
        {"id": 2, "tag": "orch-channel", "ask": "y", "created_at": "t"},
        {"id": 3, "tag": "tmux-console", "ask": "z", "created_at": "t"},
    ]
    grouped = ard.group_untriaged_by_owner(rows)
    assert {r["id"] for r in grouped["orch-console"]} == {1, 3}
    assert {r["id"] for r in grouped["cc-orchestrator"]} == {2}
    assert None not in grouped


def test_group_untriaged_by_owner_unmapped_tag_lands_under_none():
    rows = [{"id": 9, "tag": "some-new-channel", "ask": "x", "created_at": "t"}]
    grouped = ard.group_untriaged_by_owner(rows)
    assert grouped[None] == rows


def test_group_untriaged_by_owner_missing_tag_lands_under_none():
    rows = [{"id": 10, "tag": None, "ask": "x", "created_at": "t"}]
    grouped = ard.group_untriaged_by_owner(rows)
    assert grouped[None] == rows


# ── pure: render functions ───────────────────────────────────────────────────

def test_render_untriaged_page_lists_ids_and_80_char_snippet():
    long_ask = "x" * 200
    page = ard.render_untriaged_page("orch-console", [
        {"id": 5, "ask": long_ask, "created_at": "2026-10-07T00:00:00Z"},
    ])
    assert "#5" in page
    assert long_ask[:80] in page
    assert long_ask[:81] not in page


def test_render_unmapped_channel_page_names_the_unmapped_tags():
    page = ard.render_unmapped_channel_page([
        {"id": 1, "tag": "weird-tag", "ask": "a", "created_at": "t"},
    ])
    assert "weird-tag" in page
    assert "#1" in page


def test_render_overdue_page_names_id_and_committed_date():
    page = ard.render_overdue_page({
        "id": 7, "committed_date": "2026-10-01", "triage_summary": "ship the thing",
        "delegated_to": "cc-scholar", "thread_id": None,
    })
    assert "#7" in page
    assert "ship the thing" in page
    assert "OVERDUE" in page


def test_render_summary_empty_run_says_zero_everywhere():
    summary = ard.render_summary(
        applied=False, close_candidates=[], closed=0,
        untriaged_by_owner={}, overdue=[], stale=[],
    )
    assert "close: 0" in summary
    assert "untriaged >24h: 0" in summary
    assert "overdue: 0" in summary
    assert "DRY-RUN" in summary


def test_render_summary_applied_mode_shows_applied_and_closed_count():
    summary = ard.render_summary(
        applied=True, close_candidates=[{"id": 1, "triage_state": "done"}], closed=1,
        untriaged_by_owner={}, overdue=[], stale=[],
    )
    assert "APPLIED" in summary
    assert "closed 1" in summary


def test_render_summary_flags_unmapped_owner_group():
    summary = ard.render_summary(
        applied=False, close_candidates=[], closed=0,
        untriaged_by_owner={None: [{"id": 1}], "orch-console": [{"id": 2}]},
        overdue=[], stale=[],
    )
    assert "UNMAPPED channel tag" in summary


def test_render_summary_flags_overdue_rows_with_no_delegate():
    summary = ard.render_summary(
        applied=False, close_candidates=[], closed=0, untriaged_by_owner={},
        overdue=[{"id": 11, "delegated_to": None}, {"id": 12, "delegated_to": "cc-scholar"}],
        stale=[],
    )
    assert "no delegated_to" in summary
    assert "#11" in summary
    assert "#12" not in summary.split("no delegated_to")[1]  # only the unowned one listed


def test_render_summary_lists_stale_rows_for_owner_decision():
    summary = ard.render_summary(
        applied=False, close_candidates=[], closed=0, untriaged_by_owner={},
        overdue=[], stale=[{"id": 3, "delegated_to": "cc-scholar", "triage_summary": "old one"}],
    )
    assert "STALE — decide:" in summary
    assert "#3" in summary
    assert "old one" in summary


# ── CLI parser ────────────────────────────────────────────────────────────────

def test_parser_defaults_to_dry_run():
    args = ard.build_parser().parse_args([])
    assert args.apply is False
    assert args.untriaged_hours == 24
    assert args.stale_days == 14
    assert args.stale_quiet_days == 7


def test_parser_apply_flag():
    args = ard.build_parser().parse_args(["--apply"])
    assert args.apply is True


# ── DB fixtures: real schema, real queries (tests/conftest.py's ephemeral PG) ─

def _insert_ask(conn, **kw):
    cols = {
        "ask": "[__test__] row", "triage_state": "captured", "closed_at": None,
        "closed_reason": None, "committed_date": None, "delegated_to": None,
        "source_msg_id": None, "triage_summary": None, "thread_id": None,
        "created_at": None,
    }
    cols.update(kw)
    created_at = cols.pop("created_at")
    fields = list(cols.keys())
    placeholders = ", ".join(["%s"] * len(fields))
    sql = f"INSERT INTO operator_asks ({', '.join(fields)}"
    params = [cols[f] for f in fields]
    if created_at is not None:
        sql += ", created_at) VALUES (" + placeholders + ", %s)"
        params.append(created_at)
    else:
        sql += ") VALUES (" + placeholders + ")"
    sql += " RETURNING id"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rid = cur.fetchone()[0]
    conn.commit()
    return rid


def _insert_message(conn, **kw):
    cols = {"direction": "inbound", "channel": "telegram", "tag": None,
            "text": "[__test__] msg", "created_at": None}
    cols.update(kw)
    created_at = cols.pop("created_at")
    fields = list(cols.keys())
    placeholders = ", ".join(["%s"] * len(fields))
    sql = f"INSERT INTO operator_messages ({', '.join(fields)}"
    params = [cols[f] for f in fields]
    if created_at is not None:
        sql += ", created_at) VALUES (" + placeholders + ", %s)"
        params.append(created_at)
    else:
        sql += ") VALUES (" + placeholders + ")"
    sql += " RETURNING id"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rid = cur.fetchone()[0]
    conn.commit()
    return rid


def _ago(days=0, hours=0):
    return (datetime.now(timezone.utc) - timedelta(days=days, hours=hours)).isoformat()


def _insert_outbound(conn, tag="nazim-console"):
    """A committed_date on operator_asks requires outbound_msg_id (migration
    085's CHECK (committed_date IS NULL OR outbound_msg_id IS NOT NULL)) --
    every OVERDUE test fixture needs one of these to satisfy it."""
    return _insert_message(conn, direction="outbound", tag=tag, text="[__test__] committed")


# -- 1. CLOSE ------------------------------------------------------------------

def test_fetch_close_candidates_finds_drift_rows(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        drifted = _insert_ask(c, triage_state="done", closed_at=None)
        _insert_ask(c, triage_state="not_an_ask", closed_at=None)
        # a properly-closed row (via the normal path) must NOT show up
        _insert_ask(c, triage_state="done")
        with c.cursor() as cur:
            cur.execute("UPDATE operator_asks SET closed_at = now() WHERE triage_state = 'done' "
                        "AND id != %s", (drifted,))
        c.commit()
    with psycopg.connect(operator_ledger_db) as conn:
        candidates = ard.fetch_close_candidates(conn)
    ids = {r["id"] for r in candidates}
    assert drifted in ids
    assert len(candidates) == 2


def test_apply_close_sets_closed_at_and_distinct_reasons(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        done_id = _insert_ask(c, triage_state="done", closed_at=None)
        not_id = _insert_ask(c, triage_state="not_an_ask", closed_at=None)
    with psycopg.connect(operator_ledger_db) as conn:
        candidates = ard.fetch_close_candidates(conn)
        n = ard.apply_close(conn, candidates)
        conn.commit()
    assert n == 2
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT closed_at, closed_reason FROM operator_asks WHERE id = %s", (done_id,))
        closed_at, reason = cur.fetchone()
        assert closed_at is not None
        assert "triaged done" in reason
        cur.execute("SELECT closed_at, closed_reason FROM operator_asks WHERE id = %s", (not_id,))
        closed_at, reason = cur.fetchone()
        assert closed_at is not None
        assert reason == "auto: not an ask"


def test_apply_close_is_idempotent_on_rerun(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        _insert_ask(c, triage_state="done", closed_at=None)
    with psycopg.connect(operator_ledger_db) as conn:
        candidates = ard.fetch_close_candidates(conn)
        first = ard.apply_close(conn, candidates)
        conn.commit()
    assert first == 1
    with psycopg.connect(operator_ledger_db) as conn:
        # re-run: fetch again (the row is no longer a candidate) AND re-apply
        # the stale `candidates` list from before -- the WHERE closed_at IS
        # NULL guard must make the second apply a true no-op either way.
        still_candidates = ard.fetch_close_candidates(conn)
        assert still_candidates == []
        second = ard.apply_close(conn, candidates)
        conn.commit()
    assert second == 0


def test_apply_close_empty_list_is_a_noop(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as conn:
        assert ard.apply_close(conn, []) == 0


# -- 2. UNTRIAGED --------------------------------------------------------------

def test_fetch_untriaged_respects_age_threshold(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        old_msg = _insert_message(c, tag="nazim-console")
        _insert_ask(c, triage_state="captured", source_msg_id=old_msg, created_at=_ago(hours=30))
        new_msg = _insert_message(c, tag="nazim-console")
        _insert_ask(c, triage_state="captured", source_msg_id=new_msg, created_at=_ago(hours=2))
    with psycopg.connect(operator_ledger_db) as conn:
        untriaged = ard.fetch_untriaged(conn, stale_hours=24)
    assert len(untriaged) == 1
    assert untriaged[0]["tag"] == "nazim-console"


def test_fetch_untriaged_excludes_closed_and_non_captured(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        msg = _insert_message(c, tag="nazim-console")
        _insert_ask(c, ask="[__test__] already an ask", triage_state="ask",
                    source_msg_id=msg, created_at=_ago(hours=30))
        _insert_ask(c, ask="[__test__] closed captured", triage_state="captured",
                    source_msg_id=msg, created_at=_ago(hours=30),
                    closed_at=datetime.now(timezone.utc).isoformat())
    with psycopg.connect(operator_ledger_db) as conn:
        untriaged = ard.fetch_untriaged(conn, stale_hours=24)
    assert untriaged == []


def test_fetch_untriaged_missing_source_message_still_surfaces_with_null_tag(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        _insert_ask(c, triage_state="captured", source_msg_id=None, created_at=_ago(hours=30))
    with psycopg.connect(operator_ledger_db) as conn:
        untriaged = ard.fetch_untriaged(conn, stale_hours=24)
    assert len(untriaged) == 1
    assert untriaged[0]["tag"] is None


# -- 3. OVERDUE -----------------------------------------------------------------

def test_fetch_overdue_finds_past_committed_open_ask(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        ob = _insert_outbound(c)
        overdue_id = _insert_ask(c, triage_state="ask", triage_summary="late one",
                                  committed_date=_ago(days=2), delegated_to="cc-scholar",
                                  outbound_msg_id=ob)
        _insert_ask(c, ask="[__test__] future", triage_state="ask", triage_summary="future",
                    committed_date=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                    delegated_to="cc-scholar", outbound_msg_id=ob)
        _insert_ask(c, ask="[__test__] no date", triage_state="ask", triage_summary="no date",
                    committed_date=None)
    with psycopg.connect(operator_ledger_db) as conn:
        overdue = ard.fetch_overdue(conn)
    assert [r["id"] for r in overdue] == [overdue_id]


def test_fetch_overdue_excludes_closed_rows(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        ob = _insert_outbound(c)
        _insert_ask(c, triage_state="ask", committed_date=_ago(days=2), outbound_msg_id=ob,
                    closed_at=datetime.now(timezone.utc).isoformat())
    with psycopg.connect(operator_ledger_db) as conn:
        assert ard.fetch_overdue(conn) == []


def test_fetch_overdue_excludes_non_ask_triage_state(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        ob = _insert_outbound(c)
        _insert_ask(c, triage_state="captured", committed_date=_ago(days=2), outbound_msg_id=ob)
    with psycopg.connect(operator_ledger_db) as conn:
        assert ard.fetch_overdue(conn) == []


# -- 4. STALE -------------------------------------------------------------------

def test_fetch_stale_flags_old_ask_with_no_recent_outbound(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        msg = _insert_message(c, tag="nazim-console")
        stale_id = _insert_ask(c, triage_state="ask", triage_summary="forgotten",
                                source_msg_id=msg, created_at=_ago(days=20))
    with psycopg.connect(operator_ledger_db) as conn:
        stale = ard.fetch_stale(conn, age_days=14, quiet_days=7)
    assert [r["id"] for r in stale] == [stale_id]


def test_fetch_stale_excludes_when_recent_outbound_on_same_tag(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        msg = _insert_message(c, tag="nazim-console")
        _insert_ask(c, triage_state="ask", source_msg_id=msg, created_at=_ago(days=20))
        _insert_message(c, tag="nazim-console", direction="outbound", created_at=_ago(days=1))
    with psycopg.connect(operator_ledger_db) as conn:
        stale = ard.fetch_stale(conn, age_days=14, quiet_days=7)
    assert stale == []


def test_fetch_stale_excludes_too_recent_rows(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        msg = _insert_message(c, tag="nazim-console")
        _insert_ask(c, triage_state="ask", source_msg_id=msg, created_at=_ago(days=3))
    with psycopg.connect(operator_ledger_db) as conn:
        assert ard.fetch_stale(conn, age_days=14, quiet_days=7) == []


def test_fetch_stale_never_touches_closed_at(operator_ledger_db):
    """STALE only ever flags for a human decision — confirms fetch_stale is
    read-only (no apply/close function exists for this check at all)."""
    with psycopg.connect(operator_ledger_db) as c:
        msg = _insert_message(c, tag="nazim-console")
        stale_id = _insert_ask(c, triage_state="ask", source_msg_id=msg, created_at=_ago(days=20))
    with psycopg.connect(operator_ledger_db) as conn:
        ard.fetch_stale(conn, age_days=14, quiet_days=7)
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT closed_at FROM operator_asks WHERE id = %s", (stale_id,))
        assert cur.fetchone()[0] is None


# -- main(): dry-run vs --apply -----------------------------------------------

def test_main_dry_run_does_not_close_anything(operator_ledger_db, capsys):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_ask(c, triage_state="done", closed_at=None)
    rc = ard.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT closed_at FROM operator_asks WHERE id = %s", (rid,))
        assert cur.fetchone()[0] is None


def test_main_apply_closes_drift_and_sends_summary(operator_ledger_db, monkeypatch, capsys):
    sent = []

    def _fake_send(from_agent, to, mtype, subject, body, priority, **kw):
        sent.append({"to": to, "mtype": mtype, "subject": subject, "priority": priority, **kw})
        return 1, "thread-uuid"

    import scripts.bus_send as bs
    monkeypatch.setattr(bs, "send", _fake_send)
    monkeypatch.setattr(bs, "refuse_if_base_has_live_instances", lambda *a, **k: None)

    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_ask(c, triage_state="done", closed_at=None)

    rc = ard.main(["--apply"])
    assert rc == 0

    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT closed_at FROM operator_asks WHERE id = %s", (rid,))
        assert cur.fetchone()[0] is not None

    summary_sends = [s for s in sent if s["mtype"] == "update"]
    assert len(summary_sends) == 1
    assert summary_sends[0]["to"] == "orch-console"


def test_main_apply_pages_untriaged_owner(operator_ledger_db, monkeypatch):
    sent = []

    def _fake_send(from_agent, to, mtype, subject, body, priority, **kw):
        sent.append({"to": to, "mtype": mtype, "subject": subject})
        return 1, "thread-uuid"

    import scripts.bus_send as bs
    monkeypatch.setattr(bs, "send", _fake_send)
    monkeypatch.setattr(bs, "refuse_if_base_has_live_instances", lambda *a, **k: None)

    with psycopg.connect(operator_ledger_db) as c:
        msg = _insert_message(c, tag="nazim-console")
        _insert_ask(c, triage_state="captured", source_msg_id=msg, created_at=_ago(hours=30))

    rc = ard.main(["--apply"])
    assert rc == 0

    pages = [s for s in sent if s["to"] == "orch-console" and s["mtype"] == "blocker"]
    assert len(pages) == 1


def test_main_apply_pages_unmapped_channel_to_console_and_flags_in_subject(operator_ledger_db, monkeypatch):
    sent = []

    def _fake_send(from_agent, to, mtype, subject, body, priority, **kw):
        sent.append({"to": to, "mtype": mtype, "subject": subject})
        return 1, "thread-uuid"

    import scripts.bus_send as bs
    monkeypatch.setattr(bs, "send", _fake_send)
    monkeypatch.setattr(bs, "refuse_if_base_has_live_instances", lambda *a, **k: None)

    with psycopg.connect(operator_ledger_db) as c:
        msg = _insert_message(c, tag="some-unmapped-tag")
        _insert_ask(c, triage_state="captured", source_msg_id=msg, created_at=_ago(hours=30))

    rc = ard.main(["--apply"])
    assert rc == 0
    pages = [s for s in sent if s["mtype"] == "blocker" and "unmapped" in s["subject"].lower()]
    assert len(pages) == 1
    assert pages[0]["to"] == "orch-console"


def test_main_apply_one_send_failure_does_not_block_the_others(operator_ledger_db, monkeypatch, capsys):
    calls = []

    def _flaky_send(from_agent, to, mtype, subject, body, priority, **kw):
        calls.append(to)
        if to == "orch-console" and mtype == "blocker":
            raise SystemExit("bus_send: REFUSED — simulated undeliverable")
        return 1, "thread-uuid"

    import scripts.bus_send as bs
    monkeypatch.setattr(bs, "send", _flaky_send)
    monkeypatch.setattr(bs, "refuse_if_base_has_live_instances", lambda *a, **k: None)

    with psycopg.connect(operator_ledger_db) as c:
        msg = _insert_message(c, tag="some-unmapped-tag")  # routes the blocker to orch-console
        _insert_ask(c, triage_state="captured", source_msg_id=msg, created_at=_ago(hours=30))
        _insert_ask(c, triage_state="done", closed_at=None)  # close still proceeds

    rc = ard.main(["--apply"])
    assert rc == 1  # non-zero because a send failed
    err = capsys.readouterr().err
    assert "FAILURES" in err
    # the summary 'update' send still ran despite the earlier 'blocker' failure
    assert "orch-console" in calls


def test_main_apply_overdue_page_checked_against_base_instance_refusal(operator_ledger_db, monkeypatch):
    """OVERDUE pages go through refuse_if_base_has_live_instances explicitly
    (send() called directly, bypassing the CLI path that normally runs this
    check) — confirms it's actually invoked, not skipped."""
    probed = []

    def _fake_refuse(to, to_base, dsn=None):
        probed.append(to)

    sent = []

    def _fake_send(from_agent, to, mtype, subject, body, priority, **kw):
        sent.append(to)
        return 1, "thread-uuid"

    import scripts.bus_send as bs
    monkeypatch.setattr(bs, "refuse_if_base_has_live_instances", _fake_refuse)
    monkeypatch.setattr(bs, "send", _fake_send)

    with psycopg.connect(operator_ledger_db) as c:
        ob = _insert_outbound(c)
        _insert_ask(c, triage_state="ask", triage_summary="late", delegated_to="cc-scholar",
                    committed_date=_ago(days=2), outbound_msg_id=ob)

    ard.main(["--apply"])
    assert "cc-scholar" in probed
    assert "cc-scholar" in sent


def test_main_apply_overdue_page_skips_cleanly_when_base_has_live_instances(operator_ledger_db, monkeypatch, capsys):
    import scripts.bus_send as bs

    def _refuse(to, to_base, dsn=None):
        raise SystemExit(f"bus_send: REFUSED — '{to}' is a BASE id with live instance(s)")

    monkeypatch.setattr(bs, "refuse_if_base_has_live_instances", _refuse)
    monkeypatch.setattr(bs, "send", lambda *a, **k: (1, "t"))

    with psycopg.connect(operator_ledger_db) as c:
        ob = _insert_outbound(c)
        _insert_ask(c, triage_state="ask", triage_summary="late", delegated_to="cc-cosem-platform",
                    committed_date=_ago(days=2), outbound_msg_id=ob)

    rc = ard.main(["--apply"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "overdue page for" in err
    assert "BASE id" in err


def test_main_returns_2_when_no_dsn(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setattr(ard, "_dsn", lambda: None)
    rc = ard.main([])
    assert rc == 2
    assert "no DATABASE_URL" in capsys.readouterr().err


# ── launchd plist Hour/Minute matches 04:30 UTC in this host's local TZ ──────

def test_asks_reconcile_plist_fires_at_0430_utc_in_host_tz():
    import re
    from pathlib import Path
    from zoneinfo import ZoneInfo

    host_tz = ZoneInfo("Asia/Singapore")  # this Mac Mini's clock (see test_launchd_uae_calendar_hours.py)
    target_utc = datetime(2026, 1, 5, 4, 30, tzinfo=timezone.utc)
    expected = target_utc.astimezone(host_tz)

    plist = Path(__file__).resolve().parent.parent / "launchd" / "dev.wingmen.asks-reconcile.plist"
    text = plist.read_text()
    block = re.search(r"<key>StartCalendarInterval</key>\s*<dict>(.*?)</dict>", text, re.DOTALL)
    assert block, "no StartCalendarInterval dict found"
    values = {k: int(v) for k, v in re.findall(r"<key>(\w+)</key>\s*<integer>(-?\d+)</integer>", block.group(1))}
    assert values["Hour"] == expected.hour
    assert values["Minute"] == expected.minute


def test_asks_reconcile_plist_runs_apply_and_before_digest_hour():
    """--apply is baked into the plist (there's no separate dry-run cron to
    maintain), and this job's Hour/Minute must sort strictly before the
    digest's Hour=13/Minute=0 so the close/flag pass always lands first."""
    import re
    from pathlib import Path

    plist = Path(__file__).resolve().parent.parent / "launchd" / "dev.wingmen.asks-reconcile.plist"
    text = plist.read_text()
    assert "--apply" in text
    block = re.search(r"<key>StartCalendarInterval</key>\s*<dict>(.*?)</dict>", text, re.DOTALL)
    values = {k: int(v) for k, v in re.findall(r"<key>(\w+)</key>\s*<integer>(-?\d+)</integer>", block.group(1))}
    assert (values["Hour"], values["Minute"]) < (13, 0)
