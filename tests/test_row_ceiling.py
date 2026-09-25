"""Per-ROW delivery ceiling (Nazim #43063/#43073, changes-requested #43114) — the
reliability floor UNDER the per-agent wake cap. lane_nudge is the common typing choke for
EVERY waker (wake_agent->_verified_submit->lane_nudge, the backstop via wake_agent, AND
the SLA watchdog's direct call), but nothing bounded re-delivery of the SAME bus row — so
one stale row was typed into a lane ~12x/13min. This lib caps deliveries PER ROW.

DESIGN (post-review): a LIFETIME ceiling (ROW_CAP total deliveries per row_id, no windowed
reset), a FIXED host-level default dir so all callers on the host share one count, and
FAIL-CLOSED on any state error (a wake that doesn't land is recoverable; an unbounded loop
is the bug). A separate GC deletes long-dead row files so the dir doesn't grow forever."""
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
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 3}
    assert _rc('row_ceiling_ok r1', env) == 0


def test_capped_after_ROW_CAP_deliveries(tmp_path):
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 3}
    r = _run('for i in 1 2 3; do row_ceiling_record r1; done; row_ceiling_ok r1', env)
    assert r.returncode == 1, f"expected CAPPED (rc1) after 3 records, got {r.returncode}: {r.stderr}"


def test_under_cap_still_ok(tmp_path):
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 3}
    assert _run('row_ceiling_record r1; row_ceiling_record r1; row_ceiling_ok r1', env).returncode == 0


def test_ceiling_is_LIFETIME_not_windowed(tmp_path):
    # deliveries from long ago (any past time) STILL count — a lifetime budget, not a
    # rolling window that would let ~240 deliveries/day of one stuck row (Nazim #43114).
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 3}
    stamp = tmp_path / "r1"
    old = int(time.time()) - 30 * 86400  # 30 days ago
    stamp.write_text(f"{old}\n{old}\n{old}\n")  # 3 ancient deliveries
    assert _run('row_ceiling_ok r1', env).returncode == 1, "ancient deliveries must still count (lifetime)"


def test_different_rows_have_independent_counts(tmp_path):
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 2}
    assert _run('row_ceiling_record r1; row_ceiling_record r1; row_ceiling_ok r2', env).returncode == 0
    assert _run('row_ceiling_record r1; row_ceiling_record r1; row_ceiling_ok r1', env).returncode == 1


def test_shipped_default_dir_is_host_level_and_ceiling_trips(tmp_path):
    # THE regression this fixes: with NO ROW_CEILING_DIR and NO LANE_NUDGE_LOG_DIR set, the
    # default must resolve to a WRITABLE host-level dir (under $HOME) and the ceiling must
    # actually fire. On the old head the default was "/.rownudge" (unwritable) -> fail-open.
    env = {"HOME": str(tmp_path), "ROW_CAP": 2}   # no ROW_CEILING_DIR, no LANE_NUDGE_LOG_DIR
    for k in ("ROW_CEILING_DIR", "LANE_NUDGE_LOG_DIR"):
        env.pop(k, None)
    r = _run('unset ROW_CEILING_DIR LANE_NUDGE_LOG_DIR; . "%s"; '
             'row_ceiling_record d1; row_ceiling_record d1; row_ceiling_ok d1' % LIB, env)
    assert r.returncode == 1, f"shipped default must be writable + trip the ceiling; got {r.returncode}: {r.stderr}"
    assert (tmp_path / ".wingmen_state" / "rownudge" / "d1").exists(), "default state should live under $HOME"


def test_fail_CLOSED_on_unwritable_state_dir(tmp_path):
    # If the state dir cannot be created/read, REFUSE (rc 2), never proceed uncapped.
    bad = tmp_path / "afile"
    bad.write_text("x")  # a FILE where the dir should be -> mkdir must fail
    env = {"ROW_CEILING_DIR": str(bad / "sub"), "ROW_CAP": 3}
    assert _run('row_ceiling_ok r1', env).returncode == 2, "unwritable state dir must FAIL CLOSED (rc 2)"


def test_gc_deletes_old_row_files_keeps_recent(tmp_path):
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_GC_AGE_S": 3600}  # 1h
    old = tmp_path / "oldrow"; old.write_text("1\n")
    recent = tmp_path / "recentrow"; recent.write_text("1\n")
    # age the old file 2h back
    two_h_ago = time.time() - 7200
    os.utime(old, (two_h_ago, two_h_ago))
    _run('row_ceiling_gc', env)
    assert not old.exists(), "GC must delete a row file older than ROW_GC_AGE_S"
    assert recent.exists(), "GC must keep a recent row file (lifetime budget preserved)"


def test_maybe_gc_is_throttled_but_runs_when_stale(tmp_path):
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_GC_AGE_S": 3600, "ROW_GC_INTERVAL_S": 3600}
    old = tmp_path / "oldrow"; old.write_text("1\n")
    two_h_ago = time.time() - 7200
    os.utime(old, (two_h_ago, two_h_ago))
    # first maybe_gc runs GC (no marker yet) -> deletes the stale row + writes the marker
    _run('row_ceiling_maybe_gc', env)
    assert not old.exists(), "first maybe_gc should GC the stale row"
    assert (tmp_path / ".last_gc").exists(), "maybe_gc should stamp its throttle marker"
    # a fresh stale row + an immediate 2nd maybe_gc must be THROTTLED (marker still fresh)
    old2 = tmp_path / "oldrow2"; old2.write_text("1\n")
    os.utime(old2, (two_h_ago, two_h_ago))
    _run('row_ceiling_maybe_gc', env)
    assert old2.exists(), "2nd maybe_gc within the interval must be throttled (no GC)"


def test_record_then_count_reflects_records(tmp_path):
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 5}
    out = _run('row_ceiling_record rX; row_ceiling_record rX; row_ceiling_count rX', env)
    assert out.stdout.strip().splitlines()[-1] == "2", f"count should be 2, got: {out.stdout!r}"


def test_row_id_is_sanitized_for_filename_safety(tmp_path):
    env = {"ROW_CEILING_DIR": str(tmp_path), "ROW_CAP": 2}
    r = _run('row_ceiling_record "../evil"; row_ceiling_count "../evil"', env)
    assert not (tmp_path.parent / "evil").exists(), "row_id must be sanitized — no path traversal"
    assert r.returncode == 0
