"""The reset family's LOUD "STUCK (relay to operator)" row must never be posted from a test
(orch-console #80174). It leaked twice (#78935 on 10-27, #80158 on 10-28 14:11Z): a test faked
tmux to drive the STUCK branch, and a scratch clone with the live env file symlinked in gave
bus_send the live DSN. scripts/lib/reset_escalation_guard.sh now refuses the post in any test
context, failing CLOSED. These tests cover each trigger, the real-run pass-through (the post's
argv is captured by a recording stub, so nothing is ever posted), and the missing-library case.
"""
import os
import pathlib
import re
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "lib" / "reset_escalation_guard.sh"
REAL_BINARY = "/bin/ls"   # a compiled binary standing in for the real tmux
SCRIPTS = {   # script -> (snippet start, end regex, preamble, extra env, expected --from)
    "reset_cai": ("_escalated=0", r"\nfi\n", "", {"SESS": "cai"}, "cc-fleet-health"),
    "reset_nazim": ("_escalated=0", r"\nfi\n", "", {"SESS": "nazim"}, "cc-fleet-health"),
    "reset_fleet_health": ("_escalated=0", r"\nfi\n", "", {}, "orch-console"),
    "reset_auditor": ('if [ "$_cleared" != 1 ]; then', r"\n  exit 8\nfi\n",
                      '_cleared=0\nPY="$ORCH_DIR/.venv/bin/python3"\n',
                      {"RESET_BY": "cc-fleet-health", "BASE": "cai"}, "cc-fleet-health"),
}


def _real_run_env(**extra):
    """An env that looks like a REAL run: no test markers, no DSN at all."""
    drop = {"WINGMEN_TEST_SANDBOX", "PYTEST_CURRENT_TEST", "DATABASE_URL", "SUPABASE_DB_URL"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(extra)
    return env


def _guard(env):
    r = subprocess.run(["bash", "-c", f'. "{LIB}"; reset_escalation_test_context; '
                                      'echo "rc=$? reason=$RESET_ESC_TEST_REASON"'],
                       capture_output=True, text=True, timeout=10, env=env)
    return r.stdout.strip()


def test_real_run_with_the_real_tmux_binary_is_not_a_test_context():
    assert _guard(_real_run_env(TM=REAL_BINARY)) == "rc=1 reason="


@pytest.mark.parametrize("marker,reason", [
    ({"WINGMEN_TEST_SANDBOX": "1"}, "WINGMEN_TEST_SANDBOX is set"),
    ({"PYTEST_CURRENT_TEST": "tests/x.py::t (call)"}, "PYTEST_CURRENT_TEST is set"),
])
def test_each_env_marker_is_a_test_context(marker, reason):
    assert _guard(_real_run_env(TM=REAL_BINARY, **marker)) == f"rc=0 reason={reason}"


def test_a_script_stub_tmux_is_a_test_context(tmp_path):
    stub = tmp_path / "tmux"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    out = _guard(_real_run_env(TM=str(stub)))
    assert out.startswith("rc=0 reason=tmux is a script stub")


def test_a_missing_tmux_is_a_test_context(tmp_path):
    out = _guard(_real_run_env(TM=str(tmp_path / "nope")))
    assert out.startswith("rc=0 reason=tmux binary not found")


def _fake_repo(tmp_path, with_lib=True):
    """ORCH_DIR whose python is a RECORDER: it writes its argv and posts nothing."""
    repo = tmp_path / "repo"
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / "scripts" / "lib").mkdir(parents=True)
    rec = tmp_path / "argv.txt"
    py = repo / ".venv" / "bin" / "python3"
    py.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{rec}"\nexit 0\n')
    py.chmod(0o755)
    if with_lib:
        (repo / "scripts" / "lib" / "reset_escalation_guard.sh").symlink_to(LIB)
    return repo, rec


def _run(name, repo, env_extra, tmp_path):
    start, end_re, preamble, extra, _ = SCRIPTS[name]
    text = (REPO / "scripts" / f"{name}.sh").read_text()
    i = text.index(start)
    snippet = text[i: i + re.search(end_re, text[i:]).end()]
    env = _real_run_env(ORCH_DIR=str(repo), HOME=str(tmp_path), LOGDIR=str(tmp_path / "logs"),
                        TM=REAL_BINARY, **extra, **env_extra)
    return subprocess.run(["bash", "-c", preamble + snippet],
                          capture_output=True, text=True, timeout=30, env=env)


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_real_run_reaches_the_post_with_the_right_argv(name, tmp_path):
    repo, rec = _fake_repo(tmp_path)
    r = _run(name, repo, {}, tmp_path)
    assert r.returncode == 8, r.stderr
    argv = rec.read_text().split("\n")
    assert argv[0].endswith("/scripts/bus_send.py")
    flags = dict(zip(argv, argv[1:]))
    assert flags["--to"] == "orch-console" and flags["--from"] == SCRIPTS[name][4]
    assert flags["--type"] == "blocker" and flags["--priority"] == "P1" and "--req" in argv
    assert "escalated LOUD failure" in r.stderr and "REFUSED" not in r.stderr


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_a_test_marker_refuses_and_never_calls_bus_send(name, tmp_path):
    repo, rec = _fake_repo(tmp_path)
    r = _run(name, repo, {"PYTEST_CURRENT_TEST": "tests/x.py::t (call)"}, tmp_path)
    assert r.returncode == 8, r.stderr
    assert not rec.exists(), "bus_send was invoked from a test context"
    assert "REFUSED to post the operator-relay row: PYTEST_CURRENT_TEST is set" in r.stderr
    assert f"{name} escalation REFUSED (test context)" in (tmp_path / "logs" / "reset_escalation_refused.log").read_text()


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_a_missing_guard_library_fails_closed(name, tmp_path):
    repo, rec = _fake_repo(tmp_path, with_lib=False)
    r = _run(name, repo, {}, tmp_path)
    assert r.returncode == 8, r.stderr
    assert not rec.exists(), "bus_send was invoked although the guard could not load"
    assert "REFUSED to post the operator-relay row: guard library missing" in r.stderr


def test_the_suite_forces_the_sandbox_flag_for_every_test():
    # tests/conftest.py set_test_env (autouse) -- bus_send's sandbox refusal and this guard
    # both key on it, so it must stay on for the whole suite
    assert os.environ.get("WINGMEN_TEST_SANDBOX") == "1"
