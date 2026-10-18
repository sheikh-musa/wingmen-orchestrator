"""hc#95 exec-broker systemd unit draft — static-text check only, no live
systemd involved. The unit is NOT installed by this PR (orch-console's
provisioning step, handoff pack §5)."""
from __future__ import annotations

from pathlib import Path

import pytest

UNIT_PATH = Path(__file__).resolve().parent.parent / "deploy" / "irsyad-exec-broker.service"
UNIT = UNIT_PATH.read_text()


@pytest.mark.parametrize("directive", [
    "User=irsyad-exec",
    "Group=irsyad-exec",
    "RuntimeDirectory=irsyad-exec-broker",
    "RuntimeDirectoryMode=0750",
    "ExecStart=",
    "Restart=",
])
def test_unit_file_has_required_directive(directive):
    assert directive in UNIT


def test_unit_file_never_commits_a_raw_dsn_or_uid_list():
    # DATABASE_URL / EXEC_BROKER_ALLOWED_UIDS must come from an
    # EnvironmentFile, never be inlined into this tracked unit.
    assert "DATABASE_URL=" not in UNIT
    assert "EXEC_BROKER_ALLOWED_UIDS=" not in UNIT
    assert "EnvironmentFile=" in UNIT
