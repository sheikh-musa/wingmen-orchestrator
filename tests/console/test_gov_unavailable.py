"""Console #90041 item 3 (op#37222): when /api/governance cannot be read, the
Governance section must hide the controls that arm a registry it never loaded
(armed-key box, confirm strip, audit log) -- hidden, never removed, and restored
by the next successful load. The lane sheet's own armed-key field keeps working.

The assertion lives in a node test next to the other fleet.js card tests; this
wrapper is what makes CI run it.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_gov_unavailable_suite():
    r = subprocess.run(["node", str(ROOT / "tests" / "console" / "fleet_gov_unavailable.test.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
