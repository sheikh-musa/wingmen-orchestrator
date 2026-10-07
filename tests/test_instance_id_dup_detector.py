"""Pure-core tests for scripts/instance_id_dup_detector.py (orch-console bus #57914).

Replays the 2026-10-07 incident shape: tmux session cosem-platform-author holds a
FRESH cc-cosem-platform-5 row, while its OLD id cc-cosem-platform-6 (offline, same
session) is still having its bus rows read -> the lane is reading mail for an id it
no longer owns. No DB.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import instance_id_dup_detector as d  # noqa: E402

NOW = dt.datetime(2026, 10, 7, 11, 0, tzinfo=dt.timezone.utc)
B = "cc-cosem-platform"


def _row(n, sess, host="Sheikhs-Mini", status="working", age_min=1, base=B):
    return {
        "agent_id": f"{base}-{n}", "base_agent_id": base, "tmux_session": sess,
        "host": host, "status": status,
        "last_heartbeat": NOW - dt.timedelta(minutes=age_min),
    }


CLEAN = [
    _row(1, "cosem-port"), _row(2, "cosem-platform-calendar"),
    _row(3, "cosem-platform-adhoc"), _row(5, "cosem-platform-author"),
    _row(6, "cosem-platform-author", status="offline", age_min=160),
]


# ── (a) one session, 2+ fresh live rows ──────────────────────────────────────
def test_dup_live_clean_fleet_has_no_findings():
    assert d.find_duplicate_live_claims(CLEAN, NOW) == []


def test_dup_live_two_fresh_rows_one_session():
    rows = CLEAN + [_row(7, "cosem-platform-adhoc", age_min=3)]
    f = d.find_duplicate_live_claims(rows, NOW)
    assert len(f) == 1
    assert f[0]["kind"] == "dup_live"
    assert f[0]["tmux_session"] == "cosem-platform-adhoc"
    assert f[0]["agent_ids"] == [f"{B}-3", f"{B}-7"]
    assert f[0]["key"] == f"dup_live:Sheikhs-Mini:cosem-platform-adhoc:{B}-3,{B}-7"


def test_dup_live_ignores_stale_offline_other_host_and_null_session():
    rows = [
        _row(1, "s"), _row(2, "s", age_min=11),            # stale (>10 min)
        _row(3, "s", status="offline"),                    # offline
        _row(4, "s", host="gzb"),                          # same name, other host
        _row(5, None), _row(6, None),                      # session-less
    ]
    assert d.find_duplicate_live_claims(rows, NOW) == []


# ── (b) a session reading mail for its OLD (stale/offline) id ────────────────
def test_stale_read_attributed_to_session_now_running_other_n():
    reads = [{"to_agent": f"{B}-6", "n_read": 4,
              "last_read_at": NOW - dt.timedelta(minutes=37)}]
    f = d.find_stale_id_reads(CLEAN, reads, NOW)
    assert len(f) == 1
    x = f[0]
    assert x["kind"] == "stale_read"
    assert x["attributed"] is True
    assert x["stale_id"] == f"{B}-6"
    assert x["tmux_session"] == "cosem-platform-author"
    assert x["live_ids"] == [f"{B}-5"]
    assert x["key"] == f"stale_read:{B}-6:Sheikhs-Mini:cosem-platform-author"


def test_stale_read_unattributed_when_no_live_row_in_that_session():
    rows = [_row(1, "x"), _row(6, "gone-session", status="offline", age_min=200)]
    reads = [{"to_agent": f"{B}-6", "n_read": 1, "last_read_at": NOW}]
    f = d.find_stale_id_reads(rows, reads, NOW)
    assert len(f) == 1 and f[0]["attributed"] is False
    assert f[0]["live_ids"] == []


def test_stale_read_clean_when_target_is_live():
    # reads to a FRESH id are normal inbox traffic
    reads = [{"to_agent": f"{B}-5", "n_read": 9, "last_read_at": NOW}]
    assert d.find_stale_id_reads(CLEAN, reads, NOW) == []


def test_stale_read_requires_same_base_and_host():
    rows = [
        _row(6, "author", status="offline", age_min=200),
        _row(1, "author", base="cc-other"),        # different base, same session
        _row(2, "author", host="gzb"),             # same base, other host
    ]
    reads = [{"to_agent": f"{B}-6", "n_read": 1, "last_read_at": NOW}]
    f = d.find_stale_id_reads(rows, reads, NOW)
    assert len(f) == 1 and f[0]["attributed"] is False


def test_stale_read_unknown_target_row_skipped():
    reads = [{"to_agent": f"{B}-99", "n_read": 1, "last_read_at": NOW}]
    assert d.find_stale_id_reads(CLEAN, reads, NOW) == []


# ── dedup / cooldown + paging selection ─────────────────────────────────────
def test_cooldown_filters_recently_paged_keys():
    findings = [{"key": "k1"}, {"key": "k2"}]
    state = {"k1": (NOW - dt.timedelta(minutes=59)).isoformat(),
             "k2": (NOW - dt.timedelta(minutes=61)).isoformat()}
    assert [f["key"] for f in d.due_for_page(findings, state, NOW)] == ["k2"]
    assert [f["key"] for f in d.due_for_page(findings, {}, NOW)] == ["k1", "k2"]


def test_only_attributed_or_dup_findings_page():
    fs = [{"key": "a", "kind": "dup_live"},
          {"key": "b", "kind": "stale_read", "attributed": True},
          {"key": "c", "kind": "stale_read", "attributed": False}]
    assert [f["key"] for f in d.pageable(fs)] == ["a", "b"]


def test_page_subject_is_one_line_tldr():
    f = d.find_stale_id_reads(CLEAN, [{"to_agent": f"{B}-6", "n_read": 4,
                                        "last_read_at": NOW}], NOW)[0]
    subj, body = d.render_page(f)
    assert "\n" not in subj and len(subj) <= 200
    assert f"{B}-6" in subj and "cosem-platform-author" in subj
    assert len(body) >= 40 and body.splitlines()[0].startswith("TL;DR")
