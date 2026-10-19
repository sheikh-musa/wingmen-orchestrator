"""Tests for scripts.adcda_port_watch — routing fix (2026-10-19): the new-commits
notification must go to PORT_OWNER (cc-cosem-platform-1 alone, #62694/#62695), never the
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

    calls = []

    def fake_post_bus(env, to_agent, subject, body, priority="P2"):
        calls.append(to_agent)
        return 999

    monkeypatch.setattr(watch, "_post_bus", fake_post_bus)

    # baseline run: establishes state, posts the BASELINE notice
    watch.main()
    assert calls == [watch.PORT_OWNER]
    assert watch.PORT_OWNER == "cc-cosem-platform-1"
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

    calls = []
    monkeypatch.setattr(watch, "_post_bus",
                         lambda env, to_agent, subject, body, priority="P2": calls.append(to_agent))

    watch.main()  # baseline
    calls.clear()
    watch.main()  # no new commits since baseline
    assert calls == []


def test_port_owner_is_the_sole_assigned_instance_not_bare_base():
    """Direct regression pin for the #71670/#71671 bug: the constant itself must name a
    specific instance, never the bare base string that fans out to every platform lane."""
    assert watch.PORT_OWNER == "cc-cosem-platform-1"
    assert watch.PORT_OWNER != "cc-cosem-platform"
