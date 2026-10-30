"""Tests for scripts/stale_pass_verdict_watchdog.py — pages orch-console once for
an open PR that cc-quality PASSed but that has had no merge/commit for over
THRESHOLD_S (bus #85014, half 2). No real DB, no real git repo, no real gh.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import stale_pass_verdict_watchdog as spw  # noqa: E402

THRESHOLD = spw.THRESHOLD_S


# ── extract_pass_pr_mentions ─────────────────────────────────────────────────
def test_extracts_single_pr_with_repo_hint():
    text = "cosem-adcda PR#857 -- PASS"
    assert spw.extract_pass_pr_mentions(text) == [("857", "cosem-adcda")]


def test_extracts_multiple_prs_bundled_in_one_message():
    text = (
        "PR#653 @ 58fa81a (docs, reset-arming proposal) — PASS.\n"
        "PR#655 @ e712c99 (per-lane burn alarm) — PASS, RAN 30/30.\n"
        "PR#656 @ bcc92a6 (zero-waste audit) — PASS, RAN 6/6.\n"
    )
    mentions = spw.extract_pass_pr_mentions(text)
    assert [m[0] for m in mentions] == ["653", "655", "656"]
    assert all(hint is None for _, hint in mentions)  # no repo-name token on these lines


def test_skips_a_line_with_no_pass():
    assert spw.extract_pass_pr_mentions("PR#42 still in review") == []


def test_skips_a_fail_or_block_verdict_even_if_pass_appears_elsewhere():
    assert spw.extract_pass_pr_mentions("PR#42 -- FAIL (did not PASS lint)") == []
    assert spw.extract_pass_pr_mentions("PR#42 -- BLOCK, needs rework") == []


def test_handles_no_pr_mention_at_all():
    assert spw.extract_pass_pr_mentions("all green, nothing to report") == []


def test_handles_empty_text():
    assert spw.extract_pass_pr_mentions("") == []
    assert spw.extract_pass_pr_mentions(None) == []


# ── is_stale ──────────────────────────────────────────────────────────────────
def test_merged_pr_is_never_stale():
    assert spw.is_stale("MERGED", "2026-10-30T18:00:00Z", None, 1000, 1000 + THRESHOLD + 10) is False


def test_closed_unmerged_pr_is_never_stale():
    assert spw.is_stale("CLOSED", None, None, 1000, 1000 + THRESHOLD + 10) is False


def test_open_pr_under_threshold_is_not_yet_stale():
    assert spw.is_stale("OPEN", None, None, 1000, 1000 + THRESHOLD - 1) is False


def test_open_pr_past_threshold_with_no_commit_info_is_stale():
    assert spw.is_stale("OPEN", None, None, 1000, 1000 + THRESHOLD + 1) is True


def test_open_pr_with_a_commit_before_the_verdict_is_still_stale():
    # the commit predates the verdict -- nothing NEW happened after the PASS
    assert spw.is_stale("OPEN", None, 500, 1000, 1000 + THRESHOLD + 1) is True


def test_open_pr_with_a_new_commit_after_the_verdict_is_not_stale():
    # author responded after the verdict -- an active PR, not a stall
    assert spw.is_stale("OPEN", None, 1500, 1000, 1000 + THRESHOLD + 1) is False


def test_boundary_at_exactly_threshold_is_stale():
    assert spw.is_stale("OPEN", None, None, 1000, 1000 + THRESHOLD) is True


# ── alert_message ────────────────────────────────────────────────────────────
def test_alert_message_shape():
    subj, body = spw.alert_message("cosem-adcda", "857", 84217, "cc-cosem-adcda-1", 3700)
    assert subj.startswith(spw.SUBJECT_PREFIX)
    assert "cosem-adcda" in subj and "857" in subj
    assert "84217" in body and "cc-cosem-adcda-1" in body
    assert "did not touch" in body.lower()  # detect-only framing


# ── run() wiring ──────────────────────────────────────────────────────────────
class _Cur:
    def __init__(self, verdicts, pr_lookup):
        self.inserts = []
        self._verdicts = verdicts
        self._pr_lookup = pr_lookup  # unused by the cursor itself; kept for clarity

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if "FROM agent_messages" in sql:
            self._last = ("verdicts", self._verdicts)
        elif "FROM fleet_lanes" in sql:
            self._last = ("lanes", [])
        elif "post_agent_message" in sql:
            self.inserts.append(params)
            self._last = ("insert", None)
        else:
            self._last = (None, None)

    def fetchall(self):
        kind, rows = self._last
        return rows or []


class _Conn:
    def __init__(self, cur):
        self._cur = cur
        self.commits = 0

    def cursor(self):
        return self._cur

    def commit(self):
        self.commits += 1


def _wire(monkeypatch, verdicts, *, resolved_by_pr=None, state=None, saved=None):
    """verdicts: list of (id, to_agent, body, verdict_epoch).
    resolved_by_pr: {pr_number: (owner, repo, pr_json)} -- stubs _resolve_pr."""
    cur = _Cur(verdicts, resolved_by_pr)
    conn = _Conn(cur)
    monkeypatch.setattr(spw, "_load_state", lambda: dict(state or {}))
    monkeypatch.setattr(spw, "_save_state", lambda st: (saved.update(st) if saved is not None else None))
    monkeypatch.setattr(spw, "_known_repos", lambda cur, st, now: [("sheikh-musa", "cosem-adcda")])

    def _resolve(known, hint, pr_number):
        if resolved_by_pr and pr_number in resolved_by_pr:
            return resolved_by_pr[pr_number]
        return None

    monkeypatch.setattr(spw, "_resolve_pr", _resolve)
    return cur, conn


def test_run_no_verdicts_alerts_nothing(monkeypatch):
    saved = {}
    cur, conn = _wire(monkeypatch, [], saved=saved)
    spw.run(conn, dry=False)
    assert cur.inserts == []


def test_run_fresh_pass_under_threshold_alerts_nothing(monkeypatch):
    saved = {}
    now_epoch = 10_000
    verdicts = [(1, "cc-cosem-adcda-1", "cosem-adcda PR#857 -- PASS", now_epoch - THRESHOLD + 10)]
    resolved = {"857": ("sheikh-musa", "cosem-adcda", {"state": "OPEN", "mergedAt": None, "commits": []})}
    cur, conn = _wire(monkeypatch, verdicts, resolved_by_pr=resolved, saved=saved)
    monkeypatch.setattr(spw.time, "time", lambda: now_epoch)
    spw.run(conn, dry=False)
    assert cur.inserts == []


def test_run_stale_open_pr_alerts_once(monkeypatch):
    saved = {}
    now_epoch = 10_000
    verdicts = [(1, "cc-cosem-adcda-1", "cosem-adcda PR#857 -- PASS", now_epoch - THRESHOLD - 10)]
    resolved = {"857": ("sheikh-musa", "cosem-adcda", {"state": "OPEN", "mergedAt": None, "commits": []})}
    cur, conn = _wire(monkeypatch, verdicts, resolved_by_pr=resolved, saved=saved)
    monkeypatch.setattr(spw.time, "time", lambda: now_epoch)
    spw.run(conn, dry=False)
    assert len(cur.inserts) == 1
    subject = cur.inserts[0][0]
    assert subject.startswith(spw.SUBJECT_PREFIX)
    assert saved.get("alerted") == ["1:857"]


def test_run_does_not_repage_an_already_alerted_pair(monkeypatch):
    saved = {}
    now_epoch = 10_000
    verdicts = [(1, "cc-cosem-adcda-1", "cosem-adcda PR#857 -- PASS", now_epoch - THRESHOLD - 10)]
    resolved = {"857": ("sheikh-musa", "cosem-adcda", {"state": "OPEN", "mergedAt": None, "commits": []})}
    cur, conn = _wire(monkeypatch, verdicts, resolved_by_pr=resolved,
                       state={"alerted": ["1:857"]}, saved=saved)
    monkeypatch.setattr(spw.time, "time", lambda: now_epoch)
    spw.run(conn, dry=False)
    assert cur.inserts == []  # already paged once, per the "once" contract -- never again


def test_run_merged_pr_alerts_nothing(monkeypatch):
    saved = {}
    now_epoch = 10_000
    verdicts = [(1, "cc-cosem-adcda-1", "cosem-adcda PR#857 -- PASS", now_epoch - THRESHOLD - 10)]
    resolved = {"857": ("sheikh-musa", "cosem-adcda",
                         {"state": "MERGED", "mergedAt": "2026-10-30T18:00:00Z", "commits": []})}
    cur, conn = _wire(monkeypatch, verdicts, resolved_by_pr=resolved, saved=saved)
    monkeypatch.setattr(spw.time, "time", lambda: now_epoch)
    spw.run(conn, dry=False)
    assert cur.inserts == []


def test_run_unresolvable_pr_never_guesses_alerts_nothing(monkeypatch):
    saved = {}
    now_epoch = 10_000
    verdicts = [(1, "cc-cosem-adcda-1", "cosem-adcda PR#857 -- PASS", now_epoch - THRESHOLD - 10)]
    cur, conn = _wire(monkeypatch, verdicts, resolved_by_pr=None, saved=saved)
    monkeypatch.setattr(spw.time, "time", lambda: now_epoch)
    spw.run(conn, dry=False)
    assert cur.inserts == []


def test_run_multiple_prs_in_one_message_each_alert_independently(monkeypatch):
    saved = {}
    now_epoch = 10_000
    old = now_epoch - THRESHOLD - 10
    verdicts = [(1, "cc-fleet-health",
                 "PR#653 -- PASS.\nPR#655 -- PASS.\n", old)]
    resolved = {
        "653": ("sheikh-musa", "wingmen-orchestrator", {"state": "OPEN", "mergedAt": None, "commits": []}),
        "655": ("sheikh-musa", "wingmen-orchestrator", {"state": "OPEN", "mergedAt": None, "commits": []}),
    }
    cur, conn = _wire(monkeypatch, verdicts, resolved_by_pr=resolved, saved=saved)
    monkeypatch.setattr(spw.time, "time", lambda: now_epoch)
    spw.run(conn, dry=False)
    assert len(cur.inserts) == 2
    assert sorted(saved.get("alerted")) == ["1:653", "1:655"]


def test_run_dry_run_never_inserts_or_saves(monkeypatch):
    saved = {}
    now_epoch = 10_000
    verdicts = [(1, "cc-cosem-adcda-1", "cosem-adcda PR#857 -- PASS", now_epoch - THRESHOLD - 10)]
    resolved = {"857": ("sheikh-musa", "cosem-adcda", {"state": "OPEN", "mergedAt": None, "commits": []})}
    cur, conn = _wire(monkeypatch, verdicts, resolved_by_pr=resolved, saved=saved)
    monkeypatch.setattr(spw.time, "time", lambda: now_epoch)
    spw.run(conn, dry=True)
    assert cur.inserts == []
    assert saved == {}


# ── _latest_commit_epoch ──────────────────────────────────────────────────────
def test_latest_commit_epoch_picks_the_newest_date():
    pr = {"commits": [{"committedDate": "2026-10-30T10:00:00Z"},
                       {"committedDate": "2026-10-30T18:30:00Z"}]}
    import datetime
    expected = int(datetime.datetime(2026, 10, 30, 18, 30, tzinfo=datetime.timezone.utc).timestamp())
    assert spw._latest_commit_epoch(pr) == expected


def test_latest_commit_epoch_none_when_no_commits():
    assert spw._latest_commit_epoch({"commits": []}) is None
    assert spw._latest_commit_epoch({}) is None


# ── _repo_from_remote_url ──────────────────────────────────────────────────────
# ── regression: a %s bind param INSIDE a quoted interval literal is a silent
# no-op under psycopg3 (verified live against the real substrate: returned 0
# rows instead of ~160, because `interval '%s minutes'` becomes the literal
# text `interval '$1 minutes'`, which postgres parses leniently as ~0, not an
# error). The fix multiplies a bare numeric param against a fixed-unit
# interval, with the param OUTSIDE any quotes. Guard against reintroducing
# the broken shape via source inspection -- a real DB isn't available in CI.
def test_recent_quality_verdicts_sql_has_no_quoted_placeholder_interval():
    import inspect

    src = inspect.getsource(spw._recent_quality_verdicts)
    assert "interval '%s" not in src
    assert "* interval '1 minute')" in src


def test_repo_from_remote_url_handles_https_and_ssh():
    assert spw._repo_from_remote_url("https://github.com/sheikh-musa/cosem-adcda.git") == \
        ("sheikh-musa", "cosem-adcda")
    assert spw._repo_from_remote_url("git@github.com:sheikh-musa/wingmen-orchestrator.git") == \
        ("sheikh-musa", "wingmen-orchestrator")
    assert spw._repo_from_remote_url("not a url") is None
