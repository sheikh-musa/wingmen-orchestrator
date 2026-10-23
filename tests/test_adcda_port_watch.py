"""Tests for scripts.adcda_port_watch — routing fix (2026-10-19): the new-commits
notification must go to PORT_OWNER (the single assigned owner -- orch-console since
2026-10-23's op#32789-hold reassignment, #74719/#74721/#74724), never the
bare base 'cc-cosem-platform' which fans out to every platform instance.
No DB, no network: _post_bus and _git are monkeypatched; a tiny real git repo backs _git's
own command construction so the fetch/rev-parse/log plumbing is exercised for real."""
import subprocess

from scripts import adcda_port_watch as watch


def _run_git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _make_adcda_repo(tmp_path):
    """A real local git repo standing in for the ADCDA checkout, with an 'origin' remote
    that is just another local bare-ish clone we can push new commits into."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _run_git(origin, "init", "-q", "-b", "main")
    _run_git(origin, "config", "user.email", "t@example.com")
    _run_git(origin, "config", "user.name", "t")
    (origin / "f.txt").write_text("1")
    _run_git(origin, "add", ".")
    _run_git(origin, "commit", "-q", "-m", "initial")

    clone = tmp_path / "adcda"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    _run_git(clone, "config", "user.email", "t@example.com")
    _run_git(clone, "config", "user.name", "t")
    return origin, clone


def test_posts_new_commits_to_port_owner_not_bare_base(tmp_path, monkeypatch):
    origin, clone = _make_adcda_repo(tmp_path)
    state = tmp_path / "logs" / "adcda_port_watch.state"

    monkeypatch.setattr(watch, "ADCDA", clone)
    monkeypatch.setattr(watch, "STATE", state)
    monkeypatch.setattr(watch, "_env", lambda: {"DATABASE_URL": "unused"})
    monkeypatch.setattr(watch, "_owner_has_any_registration", lambda env, agent_id: True)

    calls = []

    def fake_post_bus(env, to_agent, subject, body, priority="P2"):
        calls.append(to_agent)
        return 999

    monkeypatch.setattr(watch, "_post_bus", fake_post_bus)

    # baseline run: establishes state, posts the BASELINE notice
    watch.main()
    assert calls == [watch.PORT_OWNER]
    assert watch.PORT_OWNER == "orch-console"
    assert "cc-cosem-platform" not in [c for c in calls if c != watch.PORT_OWNER]

    # new commit lands on origin/main
    (origin / "f.txt").write_text("2")
    _run_git(origin, "commit", "-aq", "-m", "feature: something port-worthy")

    calls.clear()
    watch.main()
    assert calls == [watch.PORT_OWNER]


def test_no_new_commits_posts_nothing(tmp_path, monkeypatch, capsys):
    origin, clone = _make_adcda_repo(tmp_path)
    state = tmp_path / "logs" / "adcda_port_watch.state"
    monkeypatch.setattr(watch, "ADCDA", clone)
    monkeypatch.setattr(watch, "STATE", state)
    monkeypatch.setattr(watch, "_env", lambda: {"DATABASE_URL": "unused"})
    monkeypatch.setattr(watch, "_owner_has_any_registration", lambda env, agent_id: True)

    calls = []
    monkeypatch.setattr(watch, "_post_bus",
                         lambda env, to_agent, subject, body, priority="P2": calls.append(to_agent))

    watch.main()  # baseline
    calls.clear()
    watch.main()  # no new commits since baseline
    assert calls == []


def test_port_owner_is_a_single_assigned_identity_not_bare_base():
    """Direct regression pin for the #71670/#71671 bug: the constant itself must name a
    single specific identity, never the bare base string that fans out to every platform
    lane. As of 2026-10-23 (#74719/#74721/#74724) that identity is orch-console, standing
    in for the held op#32789 porting mandate -- re-pin this when the hold lifts."""
    assert watch.PORT_OWNER == "orch-console"
    assert watch.PORT_OWNER != "cc-cosem-platform"


# ── de-registered PORT_OWNER DEGRADE (bus #74354, 2026-10-23) ──────────────────────────────
# PORT_OWNER migrated/wound down (op#32789/#74287) and its agent_status row was fully reaped
# -- this script kept blindly posting "assess new commits" to a fully vanished identity every
# ~3h, which also fed my own idle-unread watchdog into a re-nudge loop against a dead target
# with no agent_status row to veto on. Fix: before posting to PORT_OWNER, check it still has
# ANY agent_status registration at all; if not, DEGRADE to orch-console instead (same shape as
# the existing repo-missing/fetch-failed DEGRADE paths) -- detected commits aren't lost, state
# still advances, and the escalation is attributable + loud, not a silent stranded post.
#
# NOTE (2026-10-23, #74719/#74721/#74724): PORT_OWNER is now itself "orch-console" (the
# op#32789-hold reassignment), so the DEGRADE fallback target and PORT_OWNER coincide here --
# there is no more-privileged identity to fall back to below the fleet's own console. This is
# accepted, not a new gap: orch-console going fully dark is a top-tier fleet alarm owned by
# other dead-man's-switches, not something this script needs its own second fallback for.

def test_degrades_to_orch_console_when_port_owner_deregistered(tmp_path, monkeypatch):
    origin, clone = _make_adcda_repo(tmp_path)
    state = tmp_path / "logs" / "adcda_port_watch.state"
    monkeypatch.setattr(watch, "ADCDA", clone)
    monkeypatch.setattr(watch, "STATE", state)
    monkeypatch.setattr(watch, "_env", lambda: {"DATABASE_URL": "unused"})
    monkeypatch.setattr(watch, "_owner_has_any_registration", lambda env, agent_id: True)

    calls = []
    monkeypatch.setattr(watch, "_post_bus",
                         lambda env, to_agent, subject, body, priority="P2": (calls.append((to_agent, subject, priority)), 999)[1])
    watch.main()  # baseline, owner still "live"

    # new commit lands, but NOW the owner is fully de-registered
    (origin / "f.txt").write_text("2")
    _run_git(origin, "commit", "-aq", "-m", "feature: something port-worthy")
    monkeypatch.setattr(watch, "_owner_has_any_registration", lambda env, agent_id: False)

    calls.clear()
    watch.main()

    assert len(calls) == 1
    to_agent, subject, priority = calls[0]
    assert to_agent == "orch-console"
    # PORT_OWNER is itself "orch-console" right now (op#32789 hold) -- the degrade fallback and
    # the owner coincide by design, see NOTE above. Pin the fact rather than assert inequality.
    assert to_agent == watch.PORT_OWNER
    assert "DEGRADED" in subject and "reassign" in subject.lower()


def test_owner_registration_check_db_error_fails_safe_to_true(monkeypatch):
    """A transient DB-read failure checking registration must NEVER silently suppress a real
    notification -- fail-safe defaults to True (assume live, post as normal)."""
    class _BoomConn:
        def __enter__(self): raise RuntimeError("connection refused")
        def __exit__(self, *a): return False

    monkeypatch.setattr(watch.psycopg, "connect", lambda dsn: _BoomConn())
    assert watch._owner_has_any_registration({"DATABASE_URL": "unused"}, "cc-cosem-platform-1") is True
