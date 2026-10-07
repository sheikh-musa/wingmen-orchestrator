"""test_date_weekday_guard.py — outbound sends must never pair a weekday with the wrong date.

Incident (2026-10-07): operator/client messages said "Thursday 9 October", "Mon 13 Oct"
and "Friday 17 October" — in 2026 those dates are Fri / Tue / Sat. One reached a client.
scripts/lib/date_weekday_guard.py enforces it in code; the shell send scripts refuse on a
mismatch, bus_send.py only warns. PURE-LOGIC tests — no DB, nothing is sent.
"""
import io
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from scripts.lib import date_weekday_guard as g  # noqa: E402
import bus_send as bs  # noqa: E402

TODAY = date(2026, 10, 7)


# ---- today's real bad strings are refused ------------------------------------

@pytest.mark.parametrize("text,claimed,real,resolved", [
    ("Thursday 9 October", "Thursday", "Friday", date(2026, 10, 9)),
    ("Mon 13 Oct", "Monday", "Tuesday", date(2026, 10, 13)),
    ("Friday 17 October", "Friday", "Saturday", date(2026, 10, 17)),
])
def test_incident_strings_are_refused(text, claimed, real, resolved):
    found = g.find_mismatches(f"Heads up: the session is on {text}, see you then.", today=TODAY)
    assert len(found) == 1
    f = found[0]
    assert f.matched == text
    assert f.claimed_weekday == claimed
    assert f.real_weekday == real
    assert f.resolved_date == resolved


def test_multiple_mismatches_in_one_message():
    text = "Plan: Thursday 9 October kickoff, Mon 13 Oct review, Friday 17 October launch."
    assert [f.matched for f in g.find_mismatches(text, today=TODAY)] == [
        "Thursday 9 October", "Mon 13 Oct", "Friday 17 October"]


# ---- correct pairs pass ------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Friday 9 October",
    "Tue 13 Oct",
    "Saturday 17 October",
    "9 October (Friday)",
    "Friday, 9th October 2026",
    "FRIDAY 9 OCTOBER",
    "Fri. 9 Oct.",
    "Friday October 9",
    "Friday, October 9th, 2026",
    "Sunday 13 Sept 2026",
    "Friday the 9th of October",
])
def test_correct_pairs_pass(text):
    assert g.find_mismatches(text, today=TODAY) == []


@pytest.mark.parametrize("text", [
    "9 October (Thursday)",
    "Thursday October 9",
    "Thu, 9th Oct 2026",
    "Monday 13 Sept 2026",
])
def test_other_shapes_also_refused(text):
    assert len(g.find_mismatches(text, today=TODAY)) == 1


# ---- ignored inputs ----------------------------------------------------------

@pytest.mark.parametrize("text", [
    "The deadline is 9 October.",
    "Shipped on Oct 13, back on 17 October 2026.",
    "Thursday we meet; the date is 9 October.",
    "",
])
def test_dates_without_weekday_ignored(text):
    assert g.find_mismatches(text, today=TODAY) == []


def test_arabic_text_ignored():
    text = "الخميس ٩ أكتوبر — موعد الجلسة يوم الخميس 9 أكتوبر"
    assert g.find_mismatches(text, today=TODAY) == []


@pytest.mark.parametrize("text", [
    "Monday 31 February",
    "Friday 32 October",
    "Friday 0 October",
    "Thursday 29 February 2027",
])
def test_impossible_dates_ignored_not_crash(text):
    assert g.find_mismatches(text, today=TODAY) == []


# ---- year resolution ---------------------------------------------------------

def test_year_rollover_resolves_to_next_year():
    # 1 January 2027 is a Friday.
    assert g.find_mismatches("Friday 1 January", today=date(2026, 12, 30)) == []
    found = g.find_mismatches("Thursday 1 January", today=date(2026, 12, 30))
    assert len(found) == 1
    assert found[0].resolved_date == date(2027, 1, 1)
    assert found[0].real_weekday == "Friday"


def test_today_itself_resolves_to_this_year():
    assert g.find_mismatches("Wednesday 7 October", today=TODAY) == []


def test_explicit_year_honoured():
    # 9 October 2025 WAS a Thursday — correct with the explicit year.
    assert g.find_mismatches("Thursday 9 October 2025", today=TODAY) == []
    found = g.find_mismatches("Friday 9 October 2025", today=TODAY)
    assert len(found) == 1
    assert found[0].resolved_date == date(2025, 10, 9)
    assert found[0].real_weekday == "Thursday"


def test_recent_past_date_without_year_is_this_year_not_next():
    # A status update on 7 Oct saying "Monday 5 October" means the 5th just gone
    # (a Monday in 2026) — must not be resolved to 2027 (a Tuesday) and wrongly refused.
    assert g.find_mismatches("we shipped on Monday 5 October", today=TODAY) == []
    found = g.find_mismatches("we shipped on Sunday 5 October", today=TODAY)
    assert len(found) == 1 and found[0].resolved_date == date(2026, 10, 5)


def test_default_today_is_dubai_and_does_not_crash():
    assert isinstance(g.find_mismatches("Thursday 9 October"), list)


# ---- CLI ---------------------------------------------------------------------

def _cli(args, stdin, cwd):
    env = dict(os.environ, PYTHONPATH=str(_ROOT), DATE_WEEKDAY_GUARD_TODAY="2026-10-07")
    return subprocess.run(
        [sys.executable, "-m", "scripts.lib.date_weekday_guard", *args],
        input=stdin, capture_output=True, text=True, cwd=cwd, env=env)


def test_cli_refuses_with_clear_message_from_any_cwd(tmp_path):
    r = _cli([], "See you Thursday 9 October!", cwd=tmp_path)
    assert r.returncode == 1
    assert "REFUSED: 'Thursday 9 October'" in r.stderr
    assert "9 October 2026 is a Friday" in r.stderr


def test_cli_clean_exits_zero(tmp_path):
    r = _cli([], "See you Friday 9 October!", cwd=tmp_path)
    assert r.returncode == 0, r.stderr


def test_cli_text_arg(tmp_path):
    r = _cli(["--text", "Mon 13 Oct"], "", cwd=tmp_path)
    assert r.returncode == 1 and "is a Tuesday" in r.stderr


def test_cli_warn_only_exits_zero_but_warns(tmp_path):
    r = _cli(["--warn-only"], "Friday 17 October", cwd=tmp_path)
    assert r.returncode == 0
    assert "17 October 2026 is a Saturday" in r.stderr


# ---- shell helper: refuse ONLY a confirmed mismatch; a guard crash FAILS OPEN + pages ----
# orch-console #58089: a broken safety check must never become an outage of every send.

def _helper(text, orch_dir, tmp_path):
    """Run a stub SENDER: guard, then 'send' (append to sent.txt). Returns (proc, sent, state)."""
    sent = tmp_path / "sent.txt"
    state = tmp_path / "state"
    script = (f'source "{_ROOT}/scripts/lib/date_weekday_guard.sh"\n'
              '_date_weekday_guard "$1" || { echo "rc=$?"; exit 0; }\n'
              f'printf "%s\\n" "$1" >> "{sent}"; echo "rc=0"')
    env = dict(os.environ, ORCH_DIR=str(orch_dir), DATE_WEEKDAY_GUARD_TODAY="2026-10-07",
               WEEKDAY_GUARD_STATE_DIR=str(state),
               WEEKDAY_GUARD_PAGE_DRYRUN=str(tmp_path / "pages.jsonl"))
    r = subprocess.run(["/bin/bash", "-c", script, "bash", text],
                       capture_output=True, text=True, env=env)
    return r, sent, state


def _wait_for(path, secs=10.0):
    import time
    end = time.time() + secs
    while time.time() < end:
        if path.exists() and path.read_text().strip():
            return path.read_text()
        time.sleep(0.1)
    return ""


@pytest.fixture
def fake_orch(tmp_path):
    """A stand-in ORCH_DIR whose .venv/bin/python3 is this interpreter and whose
    scripts/ is the worktree under test."""
    orch = tmp_path / "orch"
    (orch / ".venv" / "bin").mkdir(parents=True)
    (orch / ".venv" / "bin" / "python3").symlink_to(sys.executable)
    (orch / "scripts").symlink_to(_ROOT / "scripts")
    return orch


@pytest.fixture
def crashing_orch(tmp_path):
    """ORCH_DIR whose guard module CRASHES on import, with the real pager next to it."""
    orch = tmp_path / "orch"
    lib = orch / "scripts" / "lib"
    lib.mkdir(parents=True)
    (orch / ".venv" / "bin").mkdir(parents=True)
    (orch / ".venv" / "bin" / "python3").symlink_to(sys.executable)
    (lib / "date_weekday_guard.py").write_text("raise RuntimeError('guard exploded on import')\n")
    (lib / "weekday_guard_pager.py").symlink_to(_ROOT / "scripts" / "lib" / "weekday_guard_pager.py")
    return orch


def test_shell_helper_passes_clean(fake_orch, tmp_path):
    r, sent, _ = _helper("Friday 9 October", fake_orch, tmp_path)
    assert "rc=0" in r.stdout, r.stderr
    assert sent.read_text() == "Friday 9 October\n"


def test_shell_helper_refuses_mismatch_and_does_not_send(fake_orch, tmp_path):
    r, sent, _ = _helper("Thursday 9 October", fake_orch, tmp_path)
    assert "rc=6" in r.stdout
    assert "REFUSED" in r.stderr
    assert not sent.exists()


def test_shell_helper_guard_crash_sends_and_pages(crashing_orch, tmp_path):
    r, sent, _ = _helper("Friday 9 October", crashing_orch, tmp_path)
    assert "rc=0" in r.stdout, r.stderr
    assert sent.read_text() == "Friday 9 October\n"          # the message WAS sent
    assert "UNGUARDED" in r.stderr
    page = _wait_for(tmp_path / "pages.jsonl")                # page attempted (background)
    assert "weekday guard CRASHED — sent unguarded:" in page
    assert "guard exploded on import" in page


def test_shell_helper_python_missing_sends_and_warns(tmp_path):
    orch = tmp_path / "orch"
    orch.mkdir()                                              # no .venv/bin/python3 at all
    r, sent, state = _helper("Thursday 9 October", orch, tmp_path)
    assert "rc=0" in r.stdout, r.stderr
    assert sent.exists()                                      # sent anyway (fail OPEN)
    assert "UNGUARDED" in r.stderr
    assert "rc=127" in (state / "weekday_guard_crash.log").read_text()


def test_shell_helper_module_missing_sends_and_warns(tmp_path):
    orch = tmp_path / "orch"
    (orch / ".venv" / "bin").mkdir(parents=True)
    (orch / ".venv" / "bin" / "python3").symlink_to(sys.executable)
    r, sent, _ = _helper("Friday 9 October", orch, tmp_path)
    assert "rc=0" in r.stdout and sent.exists()
    assert "UNGUARDED" in r.stderr


def test_cli_crash_exits_3_not_1(monkeypatch, capsys):
    monkeypatch.setattr(g, "find_mismatches", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    assert g.main(["--text", "Thursday 9 October"]) == 3
    assert "CRASHED" in capsys.readouterr().err


# ---- pager: best-effort, bounded, deduped, never raises ----------------------

from scripts.lib import weekday_guard_pager as pager  # noqa: E402


def test_pager_sends_the_specified_bus_row(tmp_path):
    calls = []
    st = pager.page_guard_crash("tg_send.sh", "RuntimeError: boom", "Traceback...\n",
                                state_dir=tmp_path, sender=lambda **kw: calls.append(kw) or (1, "t"))
    assert st == "paged"
    kw = calls[0]
    assert kw["from_agent"] == "cc-fleet-health" and kw["to"] == "orch-console"
    assert kw["priority"] == "P1" and kw["req"] is True
    assert kw["subject"].startswith("weekday guard CRASHED — sent unguarded:")
    assert "tg_send.sh" in kw["subject"] and "Traceback" in kw["body"]


def test_pager_dedups_15_minutes_per_source(tmp_path):
    calls = []
    send = lambda **kw: calls.append(kw) or (1, "t")  # noqa: E731
    assert pager.page_guard_crash("a.sh", "E", "", state_dir=tmp_path, sender=send) == "paged"
    assert pager.page_guard_crash("a.sh", "E", "", state_dir=tmp_path, sender=send) == "deduped"
    assert pager.page_guard_crash("b.sh", "E", "", state_dir=tmp_path, sender=send) == "paged"
    assert len(calls) == 2


def test_pager_failure_never_raises_and_is_logged(tmp_path):
    def boom(**kw):
        raise SystemExit("no DATABASE_URL")
    st = pager.page_guard_crash("a.sh", "E", "", state_dir=tmp_path, sender=boom)
    assert st.startswith("failed")
    assert "no DATABASE_URL" in (tmp_path / "weekday_guard_crash.log").read_text()
    # a failed page does not stamp the dedup window -> the next crash retries
    calls = []
    assert pager.page_guard_crash("a.sh", "E", "", state_dir=tmp_path,
                                  sender=lambda **kw: calls.append(kw) or (1, "t")) == "paged"


def test_pager_is_time_bounded(tmp_path):
    import time
    t0 = time.time()
    st = pager.page_guard_crash("a.sh", "E", "", state_dir=tmp_path, timeout=0.5,
                                sender=lambda **kw: time.sleep(5))
    assert st == "timeout"
    assert time.time() - t0 < 2.0


def test_pager_truncates_traceback(tmp_path):
    calls = []
    pager.page_guard_crash("a.sh", "E", "x" * 50000, state_dir=tmp_path,
                           sender=lambda **kw: calls.append(kw) or (1, "t"))
    assert len(calls[0]["body"]) < 4000


# ---- bus_send: WARN only, never raises / blocks ------------------------------

def test_bus_send_warns_on_mismatch():
    buf = io.StringIO()
    bs.warn_if_weekday_date_mismatch("sync Thursday 9 October", "body text", stream=buf)
    assert "9 October" in buf.getvalue() and "Friday" in buf.getvalue()


def test_bus_send_silent_when_clean():
    buf = io.StringIO()
    bs.warn_if_weekday_date_mismatch("sync", "no dates here at all", stream=buf)
    assert buf.getvalue() == ""


def test_bus_send_warn_never_raises_even_if_guard_crashes(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("guard exploded")
    monkeypatch.setattr(g, "find_mismatches", boom)
    buf = io.StringIO()
    bs.warn_if_weekday_date_mismatch("s", "Thursday 9 October", stream=buf)  # must not raise
    assert "could not run" in buf.getvalue()


def test_bus_send_main_dry_run_not_blocked_by_mismatch(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        "Kickoff is Thursday 9 October, review Mon 13 Oct — padding to pass the min body."))
    rc = bs.main(["--to", "cc-fleet-health", "--type", "update", "--subject", "s",
                  "--priority", "P3", "--from", "cc-fleet-health", "--dry-run"])
    out = capsys.readouterr()
    assert rc == 0
    assert "DRY RUN" in out.out
    assert "WARNING" in out.err and "is a Friday" in out.err
