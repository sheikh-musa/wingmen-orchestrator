"""lanes.sh `up` must pin the booted lane's identity via CC_BASE_OVERRIDE.

op#22448 / bus#43342: `lanes.sh up cosem-tdu-coord` booted with
CC_BASE_AGENT_ID=cc-cosem-tdu instead of the row's own base_agent_id
'cc-cosem-tdu-coord', because boot_one/cmd_up handed launch_dangerous_cc.sh
no identity hint at all -- the launcher fell back to its own pwd-based
auto-resolution against agents.repo_scope, which picked the WRONG agent
because cc-cosem-tdu (builder) and cc-cosem-tdu-coord (coord) both plausibly
match a cosem-tdu-ish worktree path. orch-console caught it live, killed the
mis-booted process, and relaunched by hand with an explicit
CC_BASE_OVERRIDE. This test pins the fix: cmd_up must always read the row's
own fleet_lanes.base_agent_id and pass it through to boot_one, which must
pass it to tmux as a session env var (`-e CC_BASE_OVERRIDE=...`) rather than
relying on pwd guessing -- the same trap can bite any future lane whose
repo_scope/worktree naming overlaps another agent's (the message itself
flagged cc-angullia as a candidate).
"""
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
LANES_SH = _REPO_ROOT / "scripts" / "lanes.sh"


def _src() -> str:
    return LANES_SH.read_text()


def test_up_lane_row_selects_base_agent_id():
    src = _src()
    m = re.search(r"up_lane_row\(\)\s*\{.*?\n\}", src, re.S)
    assert m, "could not locate up_lane_row() in lanes.sh"
    body = m.group(0)
    assert "base_agent_id" in body, (
        "up_lane_row must SELECT base_agent_id alongside worktree_path so "
        "cmd_up/boot_one can pin identity explicitly"
    )
    # Must be selected from fleet_lanes, not some other table.
    select_m = re.search(r"SELECT\s+([^\n]+?)\s+FROM\s+fleet_lanes", body)
    assert select_m, "expected a SELECT ... FROM fleet_lanes in up_lane_row"
    assert "base_agent_id" in select_m.group(1)


def test_boot_one_accepts_base_agent_id_and_pins_via_tmux_dash_e():
    src = _src()
    m = re.search(r"boot_one\(\)\s*\{.*?\n\}", src, re.S)
    assert m, "could not locate boot_one() in lanes.sh"
    body = m.group(0)
    assert re.search(r"local\s+name=\"\$1\"\s+dir=\"\$2\"\s+base_agent_id=", body), (
        "boot_one must take base_agent_id as a 3rd positional param"
    )
    # It must actually thread base_agent_id into the tmux invocation as
    # CC_BASE_OVERRIDE via tmux's own -e (session env var), NOT rely on the
    # launcher's pwd-based auto-resolution.
    assert re.search(r'-e\s+"CC_BASE_OVERRIDE=\$base_agent_id"', body), (
        "boot_one must pass CC_BASE_OVERRIDE=$base_agent_id via tmux -e when "
        "base_agent_id is set"
    )
    # The guarded/unguarded fallback must both still launch $LAUNCHER.
    assert body.count("$LAUNCHER") >= 2, (
        "expected both the override and the fallback tmux new-session calls "
        "to invoke $LAUNCHER"
    )


def test_cmd_up_extracts_and_forwards_base_agent_id():
    src = _src()
    m = re.search(r"cmd_up\(\)\s*\{.*?\n\}", src, re.S)
    assert m, "could not locate cmd_up() in lanes.sh"
    body = m.group(0)
    assert "base_agent_id" in body, (
        "cmd_up must read base_agent_id out of up_lane_row's result"
    )
    assert re.search(r'boot_one\s+"\$want"\s+"\$dir"\s+"\$base_agent_id"', body), (
        "cmd_up must forward base_agent_id as boot_one's 3rd argument"
    )


def test_boot_one_still_refuses_a_running_or_existing_session_before_booting():
    """Regression guard: the identity fix must not disturb the pre-existing
    idempotency/safety checks (dir missing, claude already running, tmux
    session already exists) that boot_one performs before it ever reaches
    the tmux new-session call."""
    src = _src()
    m = re.search(r"boot_one\(\)\s*\{.*?\n\}", src, re.S)
    body = m.group(0)
    assert 'echo "SKIP $name — dir missing: $dir"' in body
    assert 'dir_has_claude "$dir"' in body
    assert 'tmux has-session -t "$name"' in body
