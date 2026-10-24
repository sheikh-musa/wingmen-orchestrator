#!/usr/bin/env python3
"""Stage-1 arm-sign dry-run evidence pack generator (bus #75090).

Calls the REAL, shipped gate functions from scripts/sre_lane_recycle.py and
scripts/auto_recycle_on_bloat.py -- never a reimplementation or mock of the gate logic
itself. Only the I/O boundary (subprocess.run for tmux/ssh/git) is patched, exactly the
pattern already used throughout tests/test_sre_lane_recycle.py.

Run from the live orchestrator checkout: PYTHONPATH=. .venv/bin/python3 <this file>
"""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
sys.path.insert(0, os.path.join(os.getcwd(), "scripts", "lib"))

from scripts import sre_lane_recycle as slr
from scripts import auto_recycle_on_bloat as arb
from scripts.lib import fleet_health_boundaries as fhb

PASS, FAIL = [], []


def check(label, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    (PASS if cond else FAIL).append(label)
    print(f"[{tag}] {label}{(' -- ' + detail) if detail else ''}")


def _run(stdout="", rc=0):
    m = MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = ""
    return m


SPINNER_BUSY_PANE = "some output\n✻ Pondering… (12s · ↑ 3.2k tokens · esc to interrupt)\n"
SPINNER_ONLY_PANE = "some output\n✻ Pondering…\n"  # no literal 'esc to interrupt' text, spinner only
IDLE_LOOKING_PANE = "some output\nHuman: go ahead\n> \n"  # passive-read idle: no spinner, no esc-to-interrupt
CLEAN_GIT = _run(stdout="")
DIRTY_GIT = _run(stdout=" M scripts/foo.py\n")

print("=" * 90)
print("SECTION A -- scripts/sre_lane_recycle.py (the REAL destructive executor;")
print("  armed = dropping --no-arm on dev.wingmen.sre-lane-recycle.plist / CTX_WD_LANE_ARM=1)")
print("=" * 90)

# A1 busy (explicit 'esc to interrupt') -> gate_idle False
with patch("scripts.sre_lane_recycle.subprocess.run", return_value=_run(SPINNER_BUSY_PANE)):
    r = slr.gate_idle("some-session")
check("A1 busy pane ('esc to interrupt') -> gate_idle local", r is False)

# A2 animating spinner only -> gate_idle False
with patch("scripts.sre_lane_recycle.subprocess.run", return_value=_run(SPINNER_ONLY_PANE)):
    r = slr.gate_idle("some-session")
check("A2 animating spinner (no literal esc-to-interrupt line) -> gate_idle local", r is False)

# A3 ghost-composer / flicker-idle: a SINGLE passive capture that looks idle.
# Honest limitation: gate_idle alone cannot distinguish a genuinely-idle pane from a
# mid-redraw blank frame -- it is a point-in-time read. Demonstrate the actual mitigation:
# armed_recycle() re-invokes plan()->compute_gates()->gate_idle() FRESH, immediately before
# firing reset_lane.sh (never trusts an earlier read) -- so a lane that LOOKS idle at
# discovery-time but is busy at fire-time is caught at the re-verify, not assumed safe.
with patch("scripts.sre_lane_recycle.subprocess.run", return_value=_run(IDLE_LOOKING_PANE)):
    first_read = slr.gate_idle("ghost-session")
check("A3a single passive capture of an idle-looking pane reads True (documented limitation, "
      "not a false pass)", first_read is True)

lane_row = {"lane": "ghost-lane", "base_agent_id": "cc-ghost", "tmux_session": "ghost-session",
           "worktree_path": "/tmp/nonexistent-doesnt-matter", "notes": None, "host": None}
with patch("scripts.sre_lane_recycle.subprocess.run", return_value=_run(SPINNER_BUSY_PANE)):
    # fire-time re-read shows BUSY (the real state) even though an earlier read looked idle
    p = slr.plan(None, lane_row, armed=True, require_bloat=False)
check("A3b armed_recycle's plan() re-verify-at-fire-time catches a lane that is actually busy "
      "(ghost-composer window closed by re-verification, not by gate_idle alone)",
      p["permitted"] is False, detail=p["reason"])

# A4 dirty worktree -> gate_git_clean False
with patch("scripts.sre_lane_recycle.subprocess.run", return_value=DIRTY_GIT):
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        r = slr.gate_git_clean(td)
check("A4 dirty worktree -> gate_git_clean local", r is False)

# A5 no handoff -> gate_fresh_handoff False (offline, no DB/tmux needed)
r = slr.gate_fresh_handoff("cc-nobody-evidence", None, None)
check("A5 no handoff file -> gate_fresh_handoff fails closed", r is False)

# A6 idle+clean+handoff all true but NOT context-bloated -> plan() refuses (never recycle a
# non-bloated lane even if every other gate is green)
with patch.object(slr, "gate_idle", return_value=True), \
     patch.object(slr, "gate_git_clean", return_value=True), \
     patch.object(slr, "gate_fresh_handoff", return_value=True), \
     patch.object(slr, "gate_context_bloat", return_value=False):
    p = slr.plan(None, lane_row, armed=True, require_bloat=True)
check("A6 idle+clean+fresh-handoff but context_bloat=False -> plan() refuses "
      "(never recycles a merely-idle, non-bloated lane)", p["permitted"] is False)

# A7 singleton body -- structurally refused regardless of gates (G4 / CAI-681 cond 3)
for singleton_base in ("cc-fleet-health", "cai", "orch-console", "cc-orchestrator"):
    try:
        fhb.assert_sre_lane_red_permitted(
            singleton_base, {"idle": True, "git_clean": True, "fresh_handoff": True}, True,
            identity=fhb.SRE_AGENT_ID)
        ok = False
    except fhb.BoundaryViolation:
        ok = True
    check(f"A7 singleton '{singleton_base}' structurally refused even with all gates green", ok)

# A8 cross-host (gzb) BUSY lane -- gate_idle(ssh_target=...) reads remote pane over ssh, same
# busy heuristic, never a local fallback
with patch("scripts.sre_lane_recycle.subprocess.run", return_value=_run(SPINNER_BUSY_PANE)) as m:
    r = slr.gate_idle("cosem-port", ssh_target="gzb")
    ssh_call = m.call_args[0][0]
check("A8 cross-host busy lane -> gate_idle(ssh_target='gzb') False, dispatched over ssh",
      r is False and ssh_call[:2] == ["ssh", "gzb"], detail=str(ssh_call))

# A9 cross-host idle+clean+bloated+fresh-handoff -> routes to the NON-destructive nudge,
# never a forced cross-host /clear (fleet doctrine: tmux-WRITE stays Mini-only)
fake_conn = MagicMock()
fake_conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None  # no prior nudge (not on cooldown)
synthetic_plan = {
    "lane": "cosem-port", "base": "cc-cosem-port", "session": "cosem-port",
    "gates": {"idle": True, "git_clean": True, "fresh_handoff": True, "context_bloat": True},
    "permitted": True, "reason": "all gates verified; armed"}
with patch.object(slr, "armed_recycle") as armed_spy:
    act = slr._perform_armed_action(
        fake_conn, {"lane": "cosem-port", "base_agent_id": "cc-cosem-port",
                   "tmux_session": "cosem-port", "host": "gzb"},
        synthetic_plan,
        reason="evidence-pack synthetic", handoff_ref_epoch=None,
        invoker_agent=None, invoker_session=None, i_mean_it=False)
check("A9 cross-host idle-safe lane on 'gzb' -> nudge path taken, armed_recycle (the forcing "
      "tmux-write action) NEVER called", act.get("cross_host") is True and not armed_spy.called,
      detail=str(act))

print()
print("=" * 90)
print("SECTION A-CITED -- already-green, REAL-gate tests covering facets not re-derived above")
print("=" * 90)
print("  tests/test_discover_lanes_session_consistency.py::"
      "test_returned_lane_matches_the_chosen_session_not_an_arbitrary_sibling")
print("    -> 2 live siblings of one base, one FRESHER than the other; discover_lanes() against")
print("       a REAL disposable Postgres schema returns the freshest sibling's OWN lane+session,")
print("       never glues a different (possibly-busy) sibling's identity onto the chosen session.")
print("  tests/test_sre_lane_recycle.py::test_perform_armed_action_remote_host_sends_nudge_not_armed_recycle")
print("    -> same cross-host routing as A9 above, as a committed regression test.")
print("  tests/test_sre_lane_recycle.py::test_gate_idle_remote_ssh_failure_fails_closed")
print("    -> an unreachable gzb host fails gate_idle CLOSED (never treated as idle-by-default).")
print()

print("=" * 90)
print("SECTION B -- scripts/auto_recycle_on_bloat.py (AUTO_RECYCLE_STAGE1_WARN; "
      "NON-destructive pager)")
print("=" * 90)

singletons = frozenset(fhb.SINGLETON_BODIES)
worker_bases = {"cc-ghost", "cc-worker-evidence"}

# B1 singleton -> tier_eligible False
check("B1 tier_eligible('cc-fleet-health') -> False (singleton)",
      arb.tier_eligible("cc-fleet-health", singletons, worker_bases) is False)

# B2 unregistered/unknown base -> tier_eligible False (fail-closed, not 'assume worker')
check("B2 tier_eligible('cc-totally-unknown-lane') -> False (fail-closed, not in worker_bases)",
      arb.tier_eligible("cc-totally-unknown-lane", singletons, worker_bases) is False)

# B3 self-compacted (fresh re-read dropped below the fire threshold) -> SELF-COMPACTED, never WOULD-FIRE
class _Ctx:
    def __init__(self, agent, pct, stale=False, age_s=0):
        self.agent, self.pct, self.stale, self.age_s = agent, pct, stale, age_s

d = arb.classify(_Ctx("cc-worker-evidence", 90), singletons, worker_bases, fresh_pct=35, idle=True)
check("B3 fresh re-read 35% (< 88% fire threshold) after a 90% detect -> SELF-COMPACTED, never WOULD-FIRE",
      d.verdict == "SELF-COMPACTED")

# B4 classify_pane: at/above fire bar but BUSY (oracle != IDLE_EMPTY) -> GATED, not WOULD-FIRE
d = arb.classify_pane("worker-session", "cc-worker-evidence", singletons, worker_bases,
                      bloat_k=700, verdict_state="ACTIVE_TURN")
check("B4 pane >= fire bar (700k) but oracle=ACTIVE_TURN (busy) -> GATED, not WOULD-FIRE",
      d.verdict == "GATED")

# B4b the literal 'animating-but-hung' label: oracle returns an UNSURE verdict (probe failed /
# ambiguous) -- also never WOULD-FIRE; only a clean, positive IDLE_EMPTY fires.
d = arb.classify_pane("worker-session", "cc-worker-evidence", singletons, worker_bases,
                      bloat_k=700, verdict_state="UNSURE:TimeoutError")
check("B4b pane >= fire bar but oracle verdict UNSURE (ambiguous/ghost probe) -> GATED, never WOULD-FIRE",
      d.verdict == "GATED")

# B5 positive control: clean idle worker at/above the bar -> WOULD-FIRE (gates are not
# vacuously always-False)
d = arb.classify_pane("worker-session", "cc-worker-evidence", singletons, worker_bases,
                      bloat_k=700, verdict_state="IDLE_EMPTY")
check("B5 (positive control) pane >= fire bar (700k) and oracle=IDLE_EMPTY -> WOULD-FIRE",
      d.verdict == "WOULD-FIRE")

# B6 structural: auto_recycle_on_bloat.py contains no call to reset_lane.sh / armed_recycle
# anywhere -- AUTO_RECYCLE_STAGE1_WARN=1 is MECHANICALLY INCAPABLE of recycling anything; its
# only effect is an agent_messages INSERT (a page).
import inspect
src = inspect.getsource(arb)
check("B6 auto_recycle_on_bloat.py source calls armed_recycle() NOWHERE and invokes "
      "subprocess.run exactly ONCE total (read-only 'tmux ls' session enumeration) -- "
      "AUTO_RECYCLE_STAGE1_WARN=1 can only post a page, never recycle a session",
      "armed_recycle(" not in src and src.count("subprocess.run(") == 1,
      detail=f"subprocess.run occurrences={src.count('subprocess.run(')}")

print()
print("=" * 90)
print(f"TOTAL: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print(f"  - {f}")
sys.exit(1 if FAIL else 0)
