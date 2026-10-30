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


# bus #85217 (console): the real incident -- cc-quality's #85078 (a verdict
# on orchestrator PR#692/693/694) quoted a hypothetical PASS-shaped string
# while explaining PR#692's coverage gap, and it got misread as a fresh
# cosem-adcda PR#857 verdict, paging on a 9-day-stale citation instead of
# the real PR#857 verdict (#83927).
def test_skips_a_quoted_citation_of_a_pass_shaped_string():
    line = (
        "  refuse_if_pr_verdict_misrouted('cc-quality','cc-cosem-adcda-1',"
        "'cosem-adcda PR#857 PASS') -> no-op (did NOT fire)"
    )
    assert spw.extract_pass_pr_mentions(line) == []


def test_quoted_citation_guard_does_not_eat_a_real_verdict_elsewhere_in_the_same_message():
    text = (
        "  refuse_if_pr_verdict_misrouted('cc-quality','cc-cosem-adcda-1',"
        "'cosem-adcda PR#857 PASS') -> no-op (did NOT fire)\n"
        "PR#692 (misroute guard): code CORRECT, ran 171/171 -- PASS.\n"
    )
    assert spw.extract_pass_pr_mentions(text) == [("692", None)]


def test_fail_safe_hyphen_compound_does_not_suppress_a_real_pass_verdict():
    # found writing the quoted-citation test above: \bFAIL\b alone matches
    # "fail" inside "fail-safe" (hyphen is a non-word boundary), wrongly
    # treating a real PASS verdict as a FAIL verdict.
    assert spw.extract_pass_pr_mentions("PR#692: code CORRECT, fail-safe design -- PASS.") == \
        [("692", None)]
    assert spw.extract_pass_pr_mentions("PR#692: not on any block-list -- PASS.") == \
        [("692", None)]


def test_quoted_citation_guard_only_checks_right_after_the_pass_match():
    # an apostrophe EARLIER in the line (ordinary prose) must not suppress a
    # real verdict later in the same line -- only a quote immediately after
    # the matched PASS word disqualifies it.
    line = "cc-quality's review: cosem-adcda PR#857 -- PASS"
    assert spw.extract_pass_pr_mentions(line) == [("857", "cosem-adcda")]


# The exact real incident (bus #85078, the message console's #85217 was
# about): a verdict on orchestrator PR#692/693/694 that illustrates PR#692's
# own coverage gap by quoting a hypothetical call+return value verbatim.
# The quoted line ALSO happens to mention a different, unrelated PR (#855)
# right next to a hyphenated word ("Area-1"), which is the real-world case
# that defeated plain repo-hint matching as a fix: the hint extracted would
# have been syntactically valid (a real repo name) even though it was never
# meant to describe PR#857 at all.
REAL_85078_BODY = (
    "PR#857 op#35986 Doha-move Area 2 step 2a @ 07440aed — PASS (advisory; "
    "cosem-adcda, adult gov-ID PII lane). Same shape as the Area-1 PR#855 I "
    "already passed: a behavior-preserving server-only extraction of three "
    "read callables into functions-shared/lib + me-central1 twins, no "
    "client call-site switch. Review it AFTER #855 merges (it's stacked on "
    "it). ran on claude-opus-4-8.\n"
    "I isolated PR#857's OWN delta (diffed vs PR#855's head, not vs main, "
    "so #855's files don't muddy it) and verified the three Area-2 "
    "callables each way.\n"
)


def test_real_85078_body_does_not_misattribute_857_to_the_wrong_hint():
    # This message's own real PR#857 verdict line is itself genuine (not a
    # quoted citation) -- it correctly extracts, but with NO hint borrowed
    # from the unrelated "Area-1 PR#855" mention later in the same line.
    mentions = spw.extract_pass_pr_mentions(REAL_85078_BODY)
    assert ("857", None) in mentions
    assert not any(hint == "Area-1" for _, hint in mentions)


def test_real_85078_quoted_citation_variant_is_fully_suppressed():
    # The ACTUAL #85078 bug report (console's #85217): the real message body
    # also contains the quoted-citation line (reproduced verbatim) alongside
    # genuine PR#692/693/694 verdicts for a DIFFERENT repo. The citation's
    # PR#857 must not surface at all; the real PRs must still extract clean.
    text = (
        "PR#692 (misroute guard core): code CORRECT, 171/171 -- PASS.\n"
        "PR#693 (misroute guard tests): coverage CORRECT -- PASS.\n"
        "PR#694 (misroute guard docs): accurate -- PASS.\n"
        "Gap I noticed while reviewing #692: it only tests same-repo "
        "collisions. A cross-repo collision (hint resolves but to the wrong "
        "repo) is untested, e.g.:\n"
        "  refuse_if_pr_verdict_misrouted('cc-quality','cc-cosem-adcda-1',"
        "'cosem-adcda PR#857 PASS') -> no-op (did NOT fire)\n"
    )
    mentions = spw.extract_pass_pr_mentions(text)
    assert mentions == [("692", None), ("693", None), ("694", None)]


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


# bus #85202 (console): the page must name the PR's resolved live author
# instance as the one to act, not just the verdict's (possibly stale/
# misrouted) addressee -- #857 was exactly this case, pre-PR#692.
def test_alert_message_names_resolved_author_when_known():
    subj, body = spw.alert_message(
        "cosem-adcda", "857", 84217, "cc-cosem-adcda-1", 3700,
        resolved_author="cc-cosem-adcda-2",
    )
    assert "cc-cosem-adcda-2" in body
    # the WHAT TO DO line must point at the resolved author, not the stale addressee
    what_to_do = body.split("WHAT TO DO:", 1)[1]
    assert "cc-cosem-adcda-2" in what_to_do
    # the original addressee stays visible (audit trail) but isn't the actor
    assert "cc-cosem-adcda-1" in body


def test_alert_message_falls_back_to_to_agent_when_unresolved():
    subj, body = spw.alert_message("cosem-adcda", "857", 84217, "cc-cosem-adcda-1", 3700)
    assert "could not resolve a live pr author instance" in body.lower()
    what_to_do = body.split("WHAT TO DO:", 1)[1]
    assert "cc-cosem-adcda-1" in what_to_do


# ── _resolve_author ────────────────────────────────────────────────────────
def test_resolve_author_returns_none_without_head_ref():
    assert spw._resolve_author("cosem-adcda", {"state": "OPEN"}) is None


def test_resolve_author_delegates_to_bus_send_resolver(monkeypatch):
    calls = []

    def _stub(repo, branch, dsn=None):
        calls.append((repo, branch))
        return "cc-cosem-adcda-2"

    monkeypatch.setattr("scripts.bus_send._resolve_pr_author_instance", _stub)
    result = spw._resolve_author("cosem-adcda", {"headRefName": "fix/wedge"})
    assert result == "cc-cosem-adcda-2"
    assert calls == [("cosem-adcda", "fix/wedge")]


def test_resolve_author_swallows_errors(monkeypatch):
    def _boom(repo, branch, dsn=None):
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr("scripts.bus_send._resolve_pr_author_instance", _boom)
    assert spw._resolve_author("cosem-adcda", {"headRefName": "fix/wedge"}) is None


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


def test_run_wires_resolved_author_into_the_alert_body(monkeypatch):
    """bus #85202: run() must look up the resolved author and pass it all the
    way into the posted alert, not just leave the raw verdict addressee."""
    saved = {}
    now_epoch = 10_000
    verdicts = [(1, "cc-cosem-adcda-1", "cosem-adcda PR#857 -- PASS", now_epoch - THRESHOLD - 10)]
    resolved = {"857": ("sheikh-musa", "cosem-adcda",
                         {"state": "OPEN", "mergedAt": None, "commits": [],
                          "headRefName": "fix/wedge"})}
    cur, conn = _wire(monkeypatch, verdicts, resolved_by_pr=resolved, saved=saved)
    monkeypatch.setattr(spw.time, "time", lambda: now_epoch)
    monkeypatch.setattr(spw, "_resolve_author", lambda repo, pr_json: "cc-cosem-adcda-2")
    spw.run(conn, dry=False)
    assert len(cur.inserts) == 1
    body = cur.inserts[0][1]
    assert "cc-cosem-adcda-2" in body


def test_run_skips_an_ambiguous_resolution_without_alerting(monkeypatch):
    # bus #85217/#85259: a bare "PR#636 -- PASS" (no repo hint) that collides across 2+ known
    # repos must never be cited -- run() just skips it, same as an unresolved (None) PR.
    saved = {}
    now_epoch = 10_000
    verdicts = [(1, "cc-orchestrator", "PR#636 -- PASS", now_epoch - THRESHOLD - 10)]
    resolved = {"636": spw.Ambiguous(["cosem-platform", "wingmen-orchestrator"])}
    cur, conn = _wire(monkeypatch, verdicts, resolved_by_pr=resolved, saved=saved)
    monkeypatch.setattr(spw.time, "time", lambda: now_epoch)
    spw.run(conn, dry=False)
    assert cur.inserts == []
    assert saved.get("alerted", []) == []


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


# ── _resolve_pr ──────────────────────────────────────────────────────────────
# bus #85217 (console): a repo_hint that doesn't resolve must mean
# "unresolved", never license to go looking in a DIFFERENT known repo and
# cite whatever PR happens to share that number there -- the same cross-repo
# collision class bus_send.py's refuse_if_pr_verdict_misrouted (PR#697) fixed.
def test_resolve_pr_with_a_hint_never_falls_through_to_another_repo(monkeypatch):
    calls = []

    def _fake_pr_view(owner, repo, pr_number):
        calls.append((owner, repo))
        return {"state": "OPEN"} if repo == "cosem-adcda" else None

    monkeypatch.setattr(spw, "_pr_view", _fake_pr_view)
    known = [("sheikh-musa", "cosem-adcda"), ("sheikh-musa", "wingmen-orchestrator")]
    # repo_hint="wingmen-orchestrator" has no PR#857 (fake _pr_view returns None for it);
    # cosem-adcda DOES have a real PR#857 -- but it must NOT be tried, because the hint
    # named a different repo.
    result = spw._resolve_pr(known, "wingmen-orchestrator", "857")
    assert result is None
    assert calls == [("sheikh-musa", "wingmen-orchestrator")]  # cosem-adcda never even tried


def test_resolve_pr_with_a_matching_hint_resolves_normally(monkeypatch):
    known = [("sheikh-musa", "cosem-adcda"), ("sheikh-musa", "wingmen-orchestrator")]
    monkeypatch.setattr(
        spw, "_pr_view",
        lambda owner, repo, n: {"state": "OPEN"} if repo == "cosem-adcda" else None,
    )
    assert spw._resolve_pr(known, "cosem-adcda", "857") == ("sheikh-musa", "cosem-adcda", {"state": "OPEN"})


def test_resolve_pr_without_a_hint_still_tries_every_known_repo(monkeypatch):
    known = [("sheikh-musa", "cosem-adcda"), ("sheikh-musa", "wingmen-orchestrator")]
    monkeypatch.setattr(
        spw, "_pr_view",
        lambda owner, repo, n: {"state": "OPEN"} if repo == "wingmen-orchestrator" else None,
    )
    assert spw._resolve_pr(known, None, "692") == ("sheikh-musa", "wingmen-orchestrator", {"state": "OPEN"})


# bus #85217/#85259 (console ruling, following up on cc-quality's #85256 divergence note): a
# bare "PR#636 -- PASS" with no repo hint, where PR#636 genuinely exists in 2+ known repos at
# once, must report the collision and cite NOTHING -- never silently pick whichever repo
# happened to be checked first.
def test_resolve_pr_without_a_hint_reports_ambiguous_on_a_real_collision(monkeypatch):
    known = [("sheikh-musa", "cosem-adcda"), ("sheikh-musa", "wingmen-orchestrator")]
    monkeypatch.setattr(spw, "_pr_view", lambda owner, repo, n: {"state": "OPEN"})  # both match
    result = spw._resolve_pr(known, None, "636")
    assert isinstance(result, spw.Ambiguous)
    assert sorted(result.repos) == ["cosem-adcda", "wingmen-orchestrator"]


def test_resolve_pr_without_a_hint_still_returns_none_when_no_repo_matches(monkeypatch):
    known = [("sheikh-musa", "cosem-adcda"), ("sheikh-musa", "wingmen-orchestrator")]
    monkeypatch.setattr(spw, "_pr_view", lambda owner, repo, n: None)
    assert spw._resolve_pr(known, None, "999") is None


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
