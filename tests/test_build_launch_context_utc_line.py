"""The launch-context dump is the "doorbell" text a lane reads at boot (bus #58614
item 2): two lanes called on-time work "late" after 16:00Z because the dump's
timestamp wasn't explicitly labeled UTC and nothing warned that the harness date
can be local SGT. This locks both the explicit UTC label and the warning line in.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.build_launch_context import build


def _fake_client():
    chain = MagicMock()
    for name in ("table", "select", "eq", "in_", "is_", "or_", "order", "limit", "update"):
        getattr(chain, name).return_value = chain
    chain.execute.return_value = MagicMock(data=[])
    client = MagicMock()
    client.table.return_value = chain
    return client


def test_build_labels_session_start_as_utc():
    with patch("scripts.build_launch_context._client", return_value=_fake_client()):
        block = build("cc-test", dry_run=True)
    assert "Session start (UTC):" in block


def test_build_includes_utc_deadline_warning():
    with patch("scripts.build_launch_context._client", return_value=_fake_client()):
        block = build("cc-test", dry_run=True)
    assert "Deadlines and bus times are UTC" in block
    assert "date -u" in block


def test_lane_template_includes_utc_deadline_warning():
    template = (Path(__file__).parent.parent / "templates" / "lane_claude_template.md").read_text()
    assert "Deadlines and bus times are UTC" in template
    assert "date -u" in template
