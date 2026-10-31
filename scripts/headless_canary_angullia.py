"""headless_canary_angullia.py — op#36531/36535 headless-agent canary entrypoint.

Moved from scratchpad/headless-spike-op36531/run_canary_supervisor.py into the
tracked live checkout (bus #87654/#87739) -- the canary was running out of a
lane worktree that can be pruned out from under a live launchd job. Driven by
dev.wingmen.headless-canary-angullia.plist. TEMPORARY, scoped to this canary;
remove with the plist when the window ends (bus #86360 condition 4/5).
"""
import os
import sys
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))
from scripts.lib.headless_supervisor import Supervisor  # noqa: E402
from scripts.lib.substrate_dsn import dsn_from_env_file  # noqa: E402

_STATE_DIR = _ORCH_DIR / "state"
# Moving this canary again? Carry state/canary_angullia_session_id.txt (and
# the log file) to the new location BEFORE the first restart from there, or
# the supervisor starts a fresh session instead of resuming (bus #88136/#88144).

dsn = os.environ.get("DATABASE_URL") or dsn_from_env_file()

sup = Supervisor(
    agent_id="cc-angullia",
    lane_name="angullia",
    cwd="/Users/sheikhmusa/wingmen/projects/angullia",
    db_dsn=dsn,
    log_path=str(_STATE_DIR / "canary_angullia_log.jsonl"),
    session_state_path=str(_STATE_DIR / "canary_angullia_session_id.txt"),
    model="deepseek-flash",
)
sup.run_forever(poll_interval_s=30)
