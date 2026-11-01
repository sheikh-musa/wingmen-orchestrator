"""op#37222 item 6 (orch-console #90041): /builds folds finished programmes and
repos with no live-verified ship in 7+ days behind one toggle per section.

The assertion lives in a node test next to builds_view.test.js; this wrapper is
what makes CI run it.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_builds_quiet_fold_suite():
    r = subprocess.run(["node", str(ROOT / "tests" / "console" / "builds_quiet_fold.test.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
