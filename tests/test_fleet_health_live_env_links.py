"""fleet_health step 10 -- live-env-file symlink watch (orch-console #80192).

A scratch clone or worktree whose .env is a symlink into the LIVE orchestrator checkout gives
any test or script run there the live DSN (the #80158 investigation found one; three more sat on
the Mini on 2026-10-28). This step scans each host, pages orch-console P2 once per path, and
re-pages a path only if it disappears and comes back. Pure-logic tests plus a real-filesystem
scan of a tmp tree; no live DB, no ssh.
"""
import importlib
import os
import subprocess

fh = importlib.import_module("scripts.fleet_health")
LIVE = "/home/u/wingmen/orchestrator"


def test_only_links_resolving_into_the_live_checkout_from_outside_it_are_kept():
    rows = [
        (LIVE, "/home/u/wingmen/clone/.env", f"{LIVE}/.env"),          # flagged
        (LIVE, "/home/u/wingmen/clone2/.env", f"{LIVE}/sub/.env"),     # flagged (inside live)
        (LIVE, "/home/u/wingmen/other/.env", "/home/u/secrets/.env"),  # elsewhere: ignored
        (LIVE, f"{LIVE}/wt/.env", f"{LIVE}/.env"),                     # inside live itself: ignored
        (LIVE, "/home/u/wingmen/dangling/.env", ""),                   # dangling: ignored
        (LIVE, "/home/u/wingmen/orchestrator2/.env", "/home/u/wingmen/orchestrator2x/.env"),
    ]
    assert fh.live_env_links(rows) == ["/home/u/wingmen/clone/.env", "/home/u/wingmen/clone2/.env"]


def test_prefix_lookalike_dir_is_not_the_live_checkout():
    rows = [(LIVE, "/home/u/x/.env", "/home/u/wingmen/orchestrator.wt-a/.env")]
    assert fh.live_env_links(rows) == []


def test_plan_pages_new_paths_once_and_forgets_removed_ones():
    page, new = fh.env_link_plan("mini", ["/a/.env", "/b/.env"], {})
    assert page == ["/a/.env", "/b/.env"] and new == {"mini": ["/a/.env", "/b/.env"]}
    page2, new2 = fh.env_link_plan("mini", ["/a/.env", "/b/.env"], new)
    assert page2 == [] and new2 == new
    _, gone = fh.env_link_plan("mini", ["/a/.env"], new2)
    page3, _ = fh.env_link_plan("mini", ["/a/.env", "/b/.env"], gone)
    assert page3 == ["/b/.env"]


def test_plan_is_per_host():
    _, st = fh.env_link_plan("mini", ["/a/.env"], {})
    page, st2 = fh.env_link_plan("gzb", ["/a/.env"], st)
    assert page == ["/a/.env"] and st2 == {"mini": ["/a/.env"], "gzb": ["/a/.env"]}


def test_the_scan_script_finds_a_real_live_link(tmp_path):
    home = tmp_path / "home"
    live = home / "wingmen" / "orchestrator"
    live.mkdir(parents=True)
    (live / ".env").write_text("X=1\n")
    clone = home / "wingmen" / "clone"
    clone.mkdir()
    (clone / ".env").symlink_to(live / ".env")
    other = home / "wingmen" / "plain"
    other.mkdir()
    (other / ".env").write_text("Y=1\n")
    out = subprocess.run(["sh", "-s"], input=fh.ENV_LINK_SCAN_SH, capture_output=True, text=True,
                         timeout=30, env={"HOME": str(home), "PATH": os.environ["PATH"]}).stdout
    rows = fh.parse_env_link_scan(out)
    assert fh.live_env_links(rows) == [os.path.realpath(clone) + "/.env"] or \
        fh.live_env_links(rows) == [str(clone / ".env")]


def test_watch_pages_once_then_stays_quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(fh, "ENV_LINK_STATE", tmp_path / "s.json")
    scans = {"mini": [(LIVE, "/home/u/wingmen/clone/.env", f"{LIVE}/.env")]}
    sent = []
    send = lambda *a, **k: sent.append(a)
    assert fh.live_env_link_watch(scan=lambda host: scans[host], hosts=["mini"], send=send) == ["mini"]
    assert fh.live_env_link_watch(scan=lambda host: scans[host], hosts=["mini"], send=send) == []
    assert len(sent) == 1 and "/home/u/wingmen/clone/.env" in sent[0][4] and sent[0][5] == "P2"


def test_an_unreachable_host_is_loud_and_not_fatal(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(fh, "ENV_LINK_STATE", tmp_path / "s.json")

    def scan(host):
        if host == "gzb":
            raise RuntimeError("ssh timeout")
        return [(LIVE, "/home/u/wingmen/clone/.env", f"{LIVE}/.env")]
    sent = []
    paged = fh.live_env_link_watch(scan=scan, hosts=["gzb", "mini"], send=lambda *a, **k: sent.append(a))
    assert paged == ["mini"] and len(sent) == 1
    assert "LIVE-ENV LINK WATCH could not scan gzb" in capsys.readouterr().out


def test_an_unreachable_host_keeps_its_previous_state(monkeypatch, tmp_path):
    monkeypatch.setattr(fh, "ENV_LINK_STATE", tmp_path / "s.json")
    ok = lambda host: [(LIVE, "/g/.env", f"{LIVE}/.env")]
    fh.live_env_link_watch(scan=ok, hosts=["gzb"], send=lambda *a, **k: None)

    def down(host):
        raise RuntimeError("down")
    fh.live_env_link_watch(scan=down, hosts=["gzb"], send=lambda *a, **k: None)
    sent = []
    assert fh.live_env_link_watch(scan=ok, hosts=["gzb"], send=lambda *a, **k: sent.append(a)) == []


def test_a_failed_page_is_retried(monkeypatch, tmp_path):
    monkeypatch.setattr(fh, "ENV_LINK_STATE", tmp_path / "s.json")
    scan = lambda host: [(LIVE, "/c/.env", f"{LIVE}/.env")]

    def fail(*a, **k):
        raise RuntimeError("bus down")
    assert fh.live_env_link_watch(scan=scan, hosts=["mini"], send=fail) == []
    assert fh.live_env_link_watch(scan=scan, hosts=["mini"], send=lambda *a, **k: None) == ["mini"]


def test_the_sweep_step_contains_any_escape(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(fh, "live_env_link_watch", boom)
    assert fh.run_live_env_link_step() == []
    assert "LIVE-ENV LINK WATCH FAILED (sweep continues)" in capsys.readouterr().out
    import inspect
    assert "run_live_env_link_step()" in inspect.getsource(fh.main)
