"""Orphaned ephemeral test-Postgres reaper (orch-console #77831, 2026-10-27).

pg_dsn's cluster outlives a SIGKILLed/timed-out pytest (pg_ctl daemonizes it); three such
orphans sat on the Mini for up to 18 days. scripts/lib/pgtest_reaper.py sweeps them at the
start of every pg_dsn session. Unit tests inject every dependency; the wet test runs the
shipped reap() against a real PG17 cluster when the binaries exist.
"""
import os
import pathlib
import subprocess
import time

import pytest

from scripts.lib import pgtest_reaper as pr

NOW = 1_800_000_000.0
HOUR = 3600


def _cluster(root, name, *, idle_s, pid=4242, sock=None):
    d = root / f"wingmen-pgtest-{name}"
    d.mkdir(parents=True)
    log = d / "log"
    log.write_text("checkpoint complete\n")
    os.utime(log, (NOW - idle_s, NOW - idle_s))
    sock = sock or str(root / f"wingmen-pgsock-{name}")
    (d / "postmaster.pid").write_text(f"{pid}\n{d}\n123\n5555\n{sock}\n")
    return d


def _stale(root, **kw):
    kw.setdefault("now", NOW)
    kw.setdefault("max_idle_s", 6 * HOUR)
    return [p.name for p in pr.stale_clusters(root, **kw)]


def test_idle_cluster_with_zero_clients_is_stale(tmp_path):
    _cluster(tmp_path, "old", idle_s=7 * HOUR)
    assert _stale(tmp_path, clients=lambda s, p: 0) == ["wingmen-pgtest-old"]


def test_recently_active_cluster_is_spared(tmp_path):
    _cluster(tmp_path, "fresh", idle_s=10 * 60)
    assert _stale(tmp_path, clients=lambda s, p: 0) == []


def test_idle_cluster_with_a_client_is_spared(tmp_path):
    _cluster(tmp_path, "busy", idle_s=7 * HOUR)
    assert _stale(tmp_path, clients=lambda s, p: 1) == []


def test_unreachable_but_running_postmaster_is_spared(tmp_path):
    _cluster(tmp_path, "wedged", idle_s=7 * HOUR)
    assert _stale(tmp_path, clients=lambda s, p: None, alive=lambda pid: True) == []


def test_unreachable_and_dead_postmaster_is_stale(tmp_path):
    _cluster(tmp_path, "dead", idle_s=7 * HOUR)
    assert _stale(tmp_path, clients=lambda s, p: None,
                  alive=lambda pid: False) == ["wingmen-pgtest-dead"]


def test_leftover_datadir_without_a_server_is_stale(tmp_path):
    d = _cluster(tmp_path, "leftover", idle_s=7 * HOUR)
    (d / "postmaster.pid").unlink()
    assert _stale(tmp_path, clients=lambda s, p: pytest.fail("no server to ask")) == \
        ["wingmen-pgtest-leftover"]


def test_other_dirs_are_never_candidates(tmp_path):
    other = tmp_path / "some-other-pgdata"
    other.mkdir()
    (other / "log").write_text("x")
    os.utime(other / "log", (0, 0))
    assert _stale(tmp_path, clients=lambda s, p: 0) == []


def test_stop_and_remove_removes_datadir_and_socket_dir(tmp_path):
    d = _cluster(tmp_path, "old", idle_s=7 * HOUR)
    sock = tmp_path / "wingmen-pgsock-old"
    sock.mkdir()
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert pr.stop_and_remove(d, "/x/pg_ctl", alive=lambda pid: True, run=run)
    assert calls and calls[0][:3] == ["/x/pg_ctl", "-D", str(d)] and "stop" in calls[0]
    assert not d.exists() and not sock.exists()


def test_stop_failure_on_a_live_postmaster_leaves_everything(tmp_path):
    d = _cluster(tmp_path, "stuck", idle_s=7 * HOUR)

    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "could not stop")

    assert not pr.stop_and_remove(d, "/x/pg_ctl", alive=lambda pid: True, run=run)
    assert d.exists()


def test_reap_returns_only_what_it_removed(tmp_path):
    _cluster(tmp_path, "old", idle_s=7 * HOUR)
    _cluster(tmp_path, "fresh", idle_s=60)
    ok = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", "")
    reaped = pr.reap(tmp_path, "/x/pg_ctl", now=NOW, max_idle_s=6 * HOUR,
                     clients=lambda s, p: 0, alive=lambda pid: True, run=ok)
    assert reaped == ["wingmen-pgtest-old"]
    assert (tmp_path / "wingmen-pgtest-fresh").exists()


def test_pg_dsn_registers_exit_and_signal_teardown_and_sweeps_first():
    import inspect
    import tests.conftest as cf
    src = inspect.getsource(cf.pg_dsn)
    assert "_reap_orphaned_pg_clusters()" in src
    assert "atexit.register(_teardown)" in src
    assert "signal.SIGTERM" in src


# ---- wet: the shipped reap() against a real, orphaned-looking PG17 cluster -------------
_PG_BIN = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")


@pytest.mark.skipif(not os.path.exists(os.path.join(_PG_BIN, "pg_ctl")),
                    reason="PG17 binaries not present")
def test_wet_reap_stops_and_removes_a_real_idle_cluster(tmp_path):
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    d = tmp_path / "wingmen-pgtest-wet"
    import tempfile
    # Short socket path, as pg_dsn does: tmp_path overruns the ~104-char unix-socket limit.
    sock = pathlib.Path(tempfile.mkdtemp(prefix="wingmen-pgsock-"))
    subprocess.run([os.path.join(_PG_BIN, "initdb"), "-D", str(d), "-U", "postgres",
                    "--auth=trust", "--locale=C"], check=True, capture_output=True, env=env)
    subprocess.run([os.path.join(_PG_BIN, "pg_ctl"), "-D", str(d), "-l", str(d / "log"),
                    "-o", f"-p 54999 -k {sock} -c listen_addresses=''", "-w", "start"],
                   check=True, capture_output=True, env=env)
    pid = pr.postmaster_info(d)[0]
    try:
        old = time.time() - 7 * HOUR
        os.utime(d / "log", (old, old))
        reaped = pr.reap(tmp_path, os.path.join(_PG_BIN, "pg_ctl"), max_idle_s=6 * HOUR)
        assert reaped == ["wingmen-pgtest-wet"]
        assert not d.exists() and not sock.exists()
        for _ in range(50):
            if not pr.pid_alive(pid):
                break
            time.sleep(0.1)
        assert not pr.pid_alive(pid), "postmaster must actually be stopped"
    finally:
        if d.exists():
            subprocess.run([os.path.join(_PG_BIN, "pg_ctl"), "-D", str(d), "-w", "-m",
                            "immediate", "stop"], capture_output=True, env=env)
