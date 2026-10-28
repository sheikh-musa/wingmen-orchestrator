"""Guard: no test may write the LIVE priority_sla_watchdog log or state file
(tests/conftest.py::_sla_watchdog_log_and_state_off_live)."""
import os

from scripts import priority_sla_watchdog as s

LIVE = os.path.realpath(os.path.expanduser("~/wingmen/orchestrator/logs"))


def test_log_and_state_files_are_not_the_live_ones():
    for p in (s.LOG_FILE, s.STATE_FILE):
        assert not os.path.realpath(str(p)).startswith(LIVE + os.sep), p


def test_log_writes_land_in_the_tmp_file():
    s.log("isolation-probe")
    assert "isolation-probe" in s.LOG_FILE.read_text()
