"""Console #90041 item 5: the fleet page's "idle & fine" summary must count IDLE
lanes only. A registered lane with no live process is DOWN -- counting it there
said "all good" about lanes nobody had running (the glm-5.3 registrations).

The assertion lives in a node test next to the other fleet.js card tests; this
wrapper is what makes CI run it.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_lane_grouping_suite():
    r = subprocess.run(["node", str(ROOT / "tests" / "console" / "fleet_lanegroups.test.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
