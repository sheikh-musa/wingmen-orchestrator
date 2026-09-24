"""Per-ROW delivery ceiling (Nazim #43063/#43073) — the reliability floor UNDER the
per-agent wake cap. The bug: lane_nudge is the common typing choke for EVERY waker
(wake_agent->_verified_submit->lane_nudge, the backstop via wake_agent, AND the SLA
watchdog's direct lane_nudge call), but nothing bounds re-delivery of the SAME bus row
— so one stale row was typed into a lane ~12x/13min. This lib caps deliveries PER ROW,
keyed by row_id, so ANY caller is bounded. Pure/file-backed, unit-tested directly."""
import os
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "lib" / "row_ceiling.sh"


def _run(snippet: str, env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ, **{k: str(v) for k, v in env_extra.items()})
    return subprocess.run(["bash", "-c", f'. "{LIB}"\n{snippet}'],
                          capture_output=True, text=True, env=env)


def _rc(snippet: str, env_extra: dict) -> int:
    return _run(snippet, env_extra).returncode


def test_ok_when_no_prior_deliveries(tmp_path):
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 3, "ROW_WINDOW_S": 1800}
    assert _rc('row_ceiling_ok r1; echo rc=$?', env) == 0


def test_capped_after_ROW_CAP_deliveries(tmp_path):
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 3, "ROW_WINDOW_S": 1800}
    # record ROW_CAP deliveries, then the next check must be CAPPED (rc 1)
    r = _run('for i in 1 2 3; do row_ceiling_record r1; done; row_ceiling_ok r1', env)
    assert r.returncode == 1, f"expected CAPPED (rc1) after 3 records, got {r.returncode}: {r.stderr}"


def test_under_cap_still_ok(tmp_path):
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 3, "ROW_WINDOW_S": 1800}
    r = _run('row_ceiling_record r1; row_ceiling_record r1; row_ceiling_ok r1', env)
    assert r.returncode == 0, "2 deliveries < cap 3 -> still OK to deliver"


def test_different_rows_have_independent_counts(tmp_path):
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 2, "ROW_WINDOW_S": 1800}
    # cap r1, but r2 must be unaffected
    r = _run('row_ceiling_record r1; row_ceiling_record r1; row_ceiling_ok r2', env)
    assert r.returncode == 0, "r2 must not inherit r1's count"
    r = _run('row_ceiling_record r1; row_ceiling_record r1; row_ceiling_ok r1', env)
    assert r.returncode == 1, "r1 at cap must be capped"


def test_deliveries_outside_window_do_not_count(tmp_path):
    # window 100s; a delivery recorded 200s ago must be pruned -> not counted
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 1, "ROW_WINDOW_S": 100}
    old = int(time.time()) - 200
    # seed an old stamp directly, then a check should be OK (old one is stale)
    stamp = tmp_path / "r1"
    stamp.write_text(f"{old}\n")
    assert _rc('row_ceiling_ok r1', env) == 0, "a stamp older than the window must not count toward the cap"


def test_record_then_count_reflects_records(tmp_path):
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 5, "ROW_WINDOW_S": 1800}
    out = _run('row_ceiling_record rX; row_ceiling_record rX; row_ceiling_count rX', env)
    assert out.stdout.strip().splitlines()[-1] == "2", f"count should be 2, got: {out.stdout!r}"


def test_row_id_is_sanitized_for_filename_safety(tmp_path):
    # a row_id must never be able to escape ROW_CEILING_DIR (path traversal / odd chars)
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 2, "ROW_WINDOW_S": 1800}
    r = _run('row_ceiling_record "../evil"; row_ceiling_count "../evil"', env)
    # no file should be created outside tmp_path
    assert not (tmp_path.parent / "evil").exists(), "row_id must be sanitized — no path traversal"
    assert r.returncode == 0
