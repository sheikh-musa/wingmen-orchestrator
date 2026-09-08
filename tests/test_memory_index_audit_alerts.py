"""memory_index_audit alert dedup + owner-routing (Nazim 38292/38293).

A stable advisory (NEAR-LIMIT) re-paging the SRE every daily run is noise that erodes the channel's
signal (same anti-pattern as a perpetually-red gate). Fix: dedup the ADVISORY tier and route it to
the dir's OWNER, while NEVER suppressing a worsening or a hard/structural flag. These lock Nazim's
gate conditions — the load-bearing one is #1: dedup must never hide a WORSENING or a hard flag.

Split of responsibility:
  * HARD / structural (NO-INDEX, orphans, dangling, broken-edges, tail-unreachable, OVER-READ-LIMIT)
    = active or imminent silent-loss -> SRE, page EVERY run until cleared (never deduped).
  * ADVISORY (NEAR-LIMIT) = approaching, not-yet loss -> the dir OWNER, deduped (page once; re-arm
    when it clears or worsens). Owner unresolvable -> SRE fallback (never silently dropped).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts import memory_index_audit as mia  # noqa: E402

HOME_SLUG = str(pathlib.Path.home()).strip("/").replace("/", "-")
IHSANOS = f"-{HOME_SLUG}-wingmen-projects-ihsanos"
SELF = f"-{HOME_SLUG}-wingmen-fleet-health"
SRE = "cc-fleet-health"
AGENTS = {"cc-ihsanos", "cc-fleet-health", "cc-quality"}

NEAR = "NEAR-LIMIT(19781B, 4619B headroom)"
OVER = "OVER-READ-LIMIT(25000B)"


def _posts_to(posts, to):
    return [p for p in posts if p["to"] == to]


# ── classify_flags: NEAR-LIMIT is advisory; everything else is hard ──────────────────────────
def _r(**kw):
    base = dict(missing_index=False, files=1, orphans=[], dangling=[], size=1000, tail_unreachable=False)
    base.update(kw)
    return base


def test_near_limit_classifies_advisory_over_and_structural_hard():
    hard, adv = mia.classify_flags(_r(size=19781), {"repairable": {}})
    assert hard == [] and any("NEAR-LIMIT" in f for f in adv)
    hard, adv = mia.classify_flags(_r(size=25000), {"repairable": {}})
    assert adv == [] and any("OVER-READ-LIMIT" in f for f in hard)
    hard, adv = mia.classify_flags(_r(missing_index=True, files=2, orphans=["a.md", "b.md"]), {"repairable": {}})
    assert adv == [] and any("NO-INDEX" in f for f in hard) and any("ORPHANED" in f for f in hard)


# ── resolve_owner: slug -> cc-<project>, only if a real agent; else None (SRE fallback) ───────
def test_resolve_owner_maps_project_slug_to_agent():
    assert mia.resolve_owner(IHSANOS, AGENTS) == "cc-ihsanos"
    assert mia.resolve_owner(SELF, AGENTS) == "cc-fleet-health"      # multi-word project handled


def test_resolve_owner_unknown_is_none():
    assert mia.resolve_owner(f"-{HOME_SLUG}-wingmen-projects-nonesuch", AGENTS) is None
    assert mia.resolve_owner("-totally-foreign-slug", AGENTS) is None


# ── plan_alerts: the load-bearing dedup + routing ────────────────────────────────────────────
def test_c_fresh_near_limit_pages_owner_once():
    posts, state = mia.plan_alerts([(IHSANOS, [], [NEAR])], {}, AGENTS, sre_agent=SRE)
    owner_posts = _posts_to(posts, "cc-ihsanos")
    assert len(owner_posts) == 1 and owner_posts[0]["tier"] == "advisory"
    assert state.get(IHSANOS, {}).get("near_limit") is True   # remembered so the next run dedups


def test_stable_near_limit_is_deduped_second_run():
    _, state1 = mia.plan_alerts([(IHSANOS, [], [NEAR])], {}, AGENTS, sre_agent=SRE)
    posts2, _ = mia.plan_alerts([(IHSANOS, [], [NEAR])], state1, AGENTS, sre_agent=SRE)
    assert _posts_to(posts2, "cc-ihsanos") == []   # same dir + same condition + not worse -> suppressed


def test_a_near_to_over_repages_as_hard_and_rearms():
    # was NEAR (in state); now crossed to OVER (hard). Must RE-PAGE (as OVER, to SRE), and the
    # NEAR memory must clear so a future NEAR re-alerts.
    _, state1 = mia.plan_alerts([(IHSANOS, [], [NEAR])], {}, AGENTS, sre_agent=SRE)
    posts, state2 = mia.plan_alerts([(IHSANOS, [OVER], [])], state1, AGENTS, sre_agent=SRE)
    sre_posts = _posts_to(posts, SRE)
    assert len(sre_posts) == 1 and sre_posts[0]["tier"] == "hard" and any("OVER" in f for f in sre_posts[0]["flags"])
    assert IHSANOS not in state2   # NEAR memory re-armed (not stuck)


def test_b_hard_flags_page_every_run_never_deduped():
    entry = [(IHSANOS, ["NO-INDEX", "ORPHANED=3"], [])]
    posts1, s1 = mia.plan_alerts(entry, {}, AGENTS, sre_agent=SRE)
    posts2, _ = mia.plan_alerts(entry, s1, AGENTS, sre_agent=SRE)   # carry state forward
    assert len(_posts_to(posts1, SRE)) == 1 and len(_posts_to(posts2, SRE)) == 1   # BOTH runs page


def test_owner_unresolvable_advisory_falls_back_to_sre():
    posts, _ = mia.plan_alerts([("-totally-foreign-slug", [], [NEAR])], {}, AGENTS, sre_agent=SRE)
    fb = _posts_to(posts, SRE)
    assert len(fb) == 1 and fb[0]["tier"] == "advisory"   # never silently dropped


def test_state_loss_realerts_advisory_once():
    # empty state stands in for a lost/reset state file -> the advisory re-alerts ONCE (not
    # silently suppressed forever).
    posts, _ = mia.plan_alerts([(IHSANOS, [], [NEAR])], {}, AGENTS, sre_agent=SRE)
    assert len(_posts_to(posts, "cc-ihsanos")) == 1


def test_dir_with_both_hard_and_advisory_routes_each_correctly():
    posts, _ = mia.plan_alerts([(IHSANOS, ["ORPHANED=1"], [NEAR])], {}, AGENTS, sre_agent=SRE)
    assert len(_posts_to(posts, SRE)) == 1 and _posts_to(posts, SRE)[0]["tier"] == "hard"
    assert len(_posts_to(posts, "cc-ihsanos")) == 1 and _posts_to(posts, "cc-ihsanos")[0]["tier"] == "advisory"


# ── _dispatch_alerts shell: state persistence + owner-routing + dedup across runs, no real DB ──
class _FakeCur:
    def __init__(self, agents, inserts):
        self._agents, self._inserts, self._rows = agents, inserts, []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        if "FROM agents" in sql:
            self._rows = [(a,) for a in self._agents]
        elif sql.strip().upper().startswith("INSERT"):
            self._inserts.append((sql, params))
    def fetchall(self): return self._rows


class _FakeConn:
    def __init__(self, agents, inserts): self._agents, self._inserts = agents, inserts
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return _FakeCur(self._agents, self._inserts)


def _r_full(**kw):
    return _r(**kw)


def test_dispatch_persists_state_dedups_advisory_keeps_hard(tmp_path, monkeypatch):
    monkeypatch.setattr(mia, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(mia, "ALERT_STATE_PATH", tmp_path / "alerts.json")
    inserts = []
    def fake_connect(dsn, autocommit=True):
        return _FakeConn(["cc-ihsanos", "cc-fleet-health"], inserts)

    # ihsanos NEAR-LIMIT (advisory) + a hard-flag dir (self, NO-INDEX+orphan)
    problems = [
        (IHSANOS, [], [NEAR], _r_full(size=19781)),
        (SELF, ["NO-INDEX", "ORPHANED=1"], [], _r_full(missing_index=True, files=1, orphans=["x.md"])),
    ]

    mia._dispatch_alerts(problems, connect=fake_connect, dsn="fake")
    run1 = [(p[1][0], "advisory" if "'update'" in p[0] else "hard") for p in inserts]
    inserts.clear()
    mia._dispatch_alerts(problems, connect=fake_connect, dsn="fake")
    run2 = [(p[1][0], "advisory" if "'update'" in p[0] else "hard") for p in inserts]

    # run 1: advisory routed to the OWNER cc-ihsanos + hard page to the SRE
    assert ("cc-ihsanos", "advisory") in run1
    assert ("cc-fleet-health", "hard") in run1
    # run 2: advisory DEDUPED (state persisted), hard STILL pages every run
    assert ("cc-ihsanos", "advisory") not in run2
    assert ("cc-fleet-health", "hard") in run2
