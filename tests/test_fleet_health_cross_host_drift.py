"""fleet_health cross-host drift (cc-fleet-health, 2026-10-31, bus #87832).

WHY. agent_status has carried a `host` column all along and the DRIFT check never read it.
On 2026-10-31 the sweep reported six agents as "'working' but tmux '<s>' is GONE (crashed?)".
Every measurement behind that line was locally correct — `tmux has-session` was false for all
six on this machine and no claude process had those worktrees as cwd — and the conclusion was
still wrong: cc-orchestrator had moved all six lanes to gzb (op#32811 for cosem-convergence,
Mini copy confirmed down on #73475), where they were running normally. The rows even said
host='gzbai'.

The failure is not cosmetic. A lane reported as crashed invites a boot, and booting a second
dangerous-CC onto a live worktree is the one failure mode that can lose work — this file's
sibling guard (lanes.sh) was being hardened against it the same evening.

THE FIX IS A HOST GATE, NOT A SILENCE. A row on another host is reported as "NOT verified
from here", separately from the ! crash list, so:
  * a real crash on THIS host still raises the same ! it always did;
  * another host's liveness is never asserted by a machine that cannot see it;
  * NULL host is NOT treated as remote — absence of the column must not disarm the check
    for legacy rows (that would be the same fail-open, just quieter).
"""
from scripts.fleet_health import drift_for_row, row_is_remote

LOCAL = "Sheikhs-Mini"
REMOTE = "gzbai"


def test_other_host_row_is_not_reported_as_crashed():
    # The exact 2026-10-31 false positive: 'working', no local session, host=gzbai.
    kind, line = drift_for_row("cc-scholar-1", "working", REMOTE, "scholar", set(), set(), LOCAL)
    assert kind == "remote", line
    assert "NOT verified" in line
    assert "GONE" not in line and "crashed" not in line


def test_same_host_row_is_still_a_crash_signal():
    # The gate must not blunt the real thing. Same inputs, host = THIS machine.
    kind, line = drift_for_row("cc-bariq-1", "working", LOCAL, "bariq", set(), set(), LOCAL)
    assert kind == "gone"
    assert "GONE" in line


def test_null_host_is_treated_as_local_never_as_remote():
    # Legacy rows predate the column. Reading NULL as "elsewhere" would silently disarm the
    # crash check for every one of them — a fail-open dressed as caution.
    assert row_is_remote(None, LOCAL) is False
    assert row_is_remote("", LOCAL) is False
    kind, _ = drift_for_row("cc-old-1", "working", None, "old", set(), set(), LOCAL)
    assert kind == "gone"


def test_host_matching_is_case_and_domain_insensitive():
    assert row_is_remote("sheikhs-mini", LOCAL) is False
    assert row_is_remote("Sheikhs-Mini.local", LOCAL) is False
    assert row_is_remote("gzbai", LOCAL) is True


def test_present_local_session_is_not_drift_on_either_host():
    # A session this machine can see means the lane is running: no verdict at all.
    assert drift_for_row("cc-bariq-1", "working", LOCAL, "bariq", {"bariq"}, set(), LOCAL) is None
    assert drift_for_row("cc-scholar-1", "working", REMOTE, "scholar", {"scholar"}, set(), LOCAL) is None


def test_protected_singleton_is_still_skipped():
    # Unchanged behaviour: singleton liveness is lease-tracked, never inferred from tmux.
    assert drift_for_row("orch-console", "working", LOCAL, "nazim", set(),
                         {"orch-console"}, LOCAL) is None


def test_offline_rows_are_not_drift():
    assert drift_for_row("cc-x-1", "offline", LOCAL, "x", set(), set(), LOCAL) is None


def test_remote_row_whose_registered_path_is_local_is_flagged():
    # irsyad-coord / irsyad-tabung-jumaat as of #87832: host says gzbai, but fleet_lanes
    # records a worktree under this machine's home AND that directory exists. Neither the
    # host nor the path can be trusted until a human looks, so say so in the line.
    import os
    path = os.path.join(os.path.expanduser("~"), "wingmen")   # a real local dir under home
    kind, line = drift_for_row("cc-irsyad-coord-1", "working", REMOTE, "irsyad-coord",
                               set(), set(), LOCAL, worktree_path=path)
    assert kind == "remote"
    assert "host/path disagree" in line


def test_remote_row_with_a_foreign_path_has_no_disagreement_note():
    kind, line = drift_for_row("cc-scholar-1", "working", REMOTE, "scholar", set(), set(), LOCAL,
                               worktree_path="/home/gazzai/wingmen/projects/ai-scholar")
    assert kind == "remote"
    assert "host/path disagree" not in line
