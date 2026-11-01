"""Console #90323 item 1 (op#37222 follow-up): /builds must fit a phone.

The first quiet fold barely shrank the page because the fillers were one open
repo group (15 rows) and every active programme card. An open group now paints
MAX_ROWS_PER_GROUP rows with the remainder behind one counted toggle, and ALL
programmes fold behind one toggle. Nothing is dropped.

The assertion lives in a node test next to builds_view.test.js; this wrapper is
what makes CI run it.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_builds_row_cap_suite():
    r = subprocess.run(["node", str(ROOT / "tests" / "console" / "builds_row_cap.test.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
