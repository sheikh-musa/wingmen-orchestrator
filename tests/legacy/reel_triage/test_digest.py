from legacy.reel_triage import digest
import pytest

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



def _triaged(reel_db, code, prio):
    reel_db.cursor().execute(
        "insert into reel_inbox (shortcode, url, source, status, action, priority) "
        "values (%s,%s,'share_link','triaged', 'do '||%s, %s)",
        (code, code, code, prio))


def test_top5_orders_by_priority_desc(reel_db):
    for i in range(7):
        _triaged(reel_db, f"R{i}", float(i))
    top = digest.top_actions(reel_db)
    assert len(top) == 5
    assert [r["shortcode"] for r in top] == ["R6", "R5", "R4", "R3", "R2"]


def test_apply_respects_wip_cap(reel_db):
    for i in range(3):
        reel_db.cursor().execute(
            "insert into reel_inbox (shortcode,url,source,status) values (%s,%s,'share_link','applying')",
            (f"W{i}", f"W{i}"))
    _triaged(reel_db, "NEW", 9.0)
    ok, in_progress = digest.apply(reel_db, "NEW")
    assert ok is False                 # at cap -> rejected
    assert len(in_progress) == 3       # returns current 3 for Done/Discard


def test_apply_under_cap_moves_to_applying(reel_db):
    _triaged(reel_db, "GO", 9.0)
    ok, _ = digest.apply(reel_db, "GO")
    assert ok is True
    row = reel_db.cursor().execute("select status from reel_inbox where shortcode='GO'").fetchone()
    assert row["status"] == "applying"


def test_auto_discard_after_two_digests(reel_db):
    _triaged(reel_db, "STALE", 1.0)
    digest.mark_shown(reel_db, ["STALE"])      # digest 1
    digest.mark_shown(reel_db, ["STALE"])      # digest 2 -> auto-discard
    row = reel_db.cursor().execute("select status from reel_inbox where shortcode='STALE'").fetchone()
    assert row["status"] == "discarded"
