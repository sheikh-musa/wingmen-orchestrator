"""Wake-path hygiene follow-ups from Nazim #43143.

(1) A refused wake is a DELIVERY FAILURE, never a "benign ghost" (agreed #43083): the
    DIM-stable revert-fail notice must say so and carry the undelivered row id.
(2) The singleton boot scripts historically skipped ensure_lane_deny (only
    launch_dangerous_cc.sh had it), so a fresh checkout of cai/quality/fleet-health/nazim
    got neither the AskUserQuestion deny nor promptSuggestionEnabled:false. Each must now
    call it. boot_orch is the hub's (gzb) — the HUB wires its own ensure_lane_deny (Nazim
    #43217); the tracked hub boot now carries it, so the guard below asserts PRESENCE
    (flipped from asserted-absent per #43962 once the hub did its part).

Source-level guards (the changes are wording + a launcher line; asserting the source is the
robust, prod-clean check)."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NUDGE = (REPO / "scripts" / "lane_nudge.sh").read_text()


def test_dim_stable_revertfail_is_labeled_delivery_failure_with_row_id():
    # the DIM-stable note + log line must call it a DELIVERY FAILURE and name the row id
    assert "DELIVERY FAILURE" in NUDGE
    assert "undelivered row=" in NUDGE
    assert "LANE_NUDGE_ROW_ID" in NUDGE  # the row id is threaded into the note


def test_dim_stable_revertfail_drops_the_benign_ghost_wording():
    # the retired "likely benign ghost" framing (#43083) must be gone from the DIM-stable path
    assert "likely benign ghost repaint" not in NUDGE


def test_singleton_boots_enforce_ensure_lane_deny():
    for boot in ("boot_cai.sh", "boot_quality.sh", "boot_fleet_health.sh", "boot_nazim.sh"):
        src = (REPO / "scripts" / boot).read_text()
        assert "ensure_lane_deny.py" in src, f"{boot} must call ensure_lane_deny (Nazim #43143)"


def test_boot_orch_carries_hub_ensure_lane_deny():
    # boot_orch.sh is OWNED by the hub (gzb): only the hub identity edits it — NOT
    # cc-substrate or other bodies. That ownership was #148's real intent; the original
    # content-ABSENT assert was a proxy for it and went stale the moment the hub did its
    # own part: Nazim #43217 told the hub to wire ensure_lane_deny into boot_orch.sh, the
    # hub did (PR #153), and #43962 ratified flipping this to assert PRESENCE. Robust
    # "only-the-hub-edits" enforcement belongs in CODEOWNERS, not a content assert.
    src = (REPO / "scripts" / "boot_orch.sh").read_text()
    assert "ensure_lane_deny.py" in src, (
        "the hub's boot_orch.sh MUST carry its own ensure_lane_deny wedge-fix "
        "(Nazim #43217); if absent, the hub's cold boot lost the launch-time "
        "AskUserQuestion deny + promptSuggestionEnabled:false"
    )
