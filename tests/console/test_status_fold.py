"""Console #90323 item 2 (op#37222 follow-up): /status must not dump the board.

600 open items painted flat measured 301,630 device px (~119 phone screens).
Each section now paints STATUS_MAX_RENDERED cards and folds the remainder behind
one toggle that carries the true count.

The assertion lives in a node test next to status_board.test.js; this wrapper is
what makes CI run it.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_status_fold_suite():
    r = subprocess.run(["node", str(ROOT / "tests" / "console" / "status_fold.test.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
