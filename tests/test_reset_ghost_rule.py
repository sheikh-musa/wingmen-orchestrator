"""Every reset that wipes a composer must read the wipe's own answer.

WHY THIS EXISTS. An idle Claude Code pane paints its most recent history entry as a dim
autosuggestion GHOST into an EMPTY input buffer. The verify-empty guard in each reset reads
that ghost as residue and refuses — so on 2026-08-15 the operator could not clear his own
console at 82% ("cant clear you cos of the ghost text in your terminal"), and reset_lane.sh
refused on a string that was still byte-identical ninety minutes after it was logged.

The wipe IS the probe, and its answer was being thrown away. $WIPE backspaces (>= staged
length + 80, min 200) cannot leave real staged text byte-identical — ten characters of real
input die in ten. So text that survives the wipe UNCHANGED was never in the composer.

The rule does NOT weaken the guard, and the distinction is the whole point: residue that
CHANGED but is still non-empty is a real PARTIAL wipe — /clear would stage behind it and
never run, which is the failure the check exists for — and must still refuse.

reset_lane.sh (@37c6efb) and reset_nazim.sh (@1f3f2f2) carry it, 5-for-5 on wild ghosts.
reset_cai.sh, reset_fleet_health.sh and reset_orch.sh did not, so the same ghost could veto
THEIR recycles — and cai's is the next one due. A promise to port it does not survive a
context reset; this test does.
"""
import re
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

# Only the resets that actually WIPE a composer can apply the rule. reset_hub_remote.sh is a
# pure ssh wrapper — it never touches a pane itself.
_WIPING_RESETS = sorted(p for p in _SCRIPTS.glob("reset_*.sh") if "BSpace" in p.read_text())


def test_the_wiping_resets_are_discovered():
    """Guards the guard: an empty list would make every assertion below vacuous."""
    assert len(_WIPING_RESETS) >= 5


@pytest.mark.parametrize("path", _WIPING_RESETS, ids=lambda p: p.name)
def test_reset_captures_the_composer_before_wiping_it(path):
    body = path.read_text()
    assert re.search(r"CC_BEFORE_WIPE=", body), (
        f"{path.name} wipes the composer without saving what was there first, so it cannot "
        f"tell a dim ghost (byte-identical after the wipe) from a real partial wipe. Capture "
        f"CC_FLAT into CC_BEFORE_WIPE immediately before the BSpace run."
    )


@pytest.mark.parametrize("path", _WIPING_RESETS, ids=lambda p: p.name)
def test_reset_proceeds_when_the_residue_is_byte_identical_after_the_wipe(path):
    body = path.read_text()
    assert re.search(r'"\$CC_FLAT"\s*=\s*"\$CC_BEFORE_WIPE"', body), (
        f"{path.name} never compares the post-wipe composer with the pre-wipe capture, so a "
        f"dim autosuggestion ghost can veto this body's recycle — the exact failure that "
        f"stopped the operator clearing his console on 2026-08-15. Proceed when the two are "
        f"byte-identical (real text cannot survive the wipe unchanged); keep refusing when "
        f"the residue CHANGED, which is a genuine partial wipe."
    )
