"""reset_auditor.sh / reset_cai.sh / reset_fleet_health.sh / reset_nazim.sh LAYER-2
dead-man-verify escalation must go through scripts/bus_send.py, never a raw psycopg
INSERT INTO agent_messages (Musa op#34845, bus #78757/#78763, PR-B step 2).

3 of these 4 scripts (reset_cai.sh, reset_fleet_health.sh, reset_nazim.sh) have no
other test file at all, and reset_auditor.sh's own 27-test suite never exercises this
LAYER-2 branch (it needs a composer_capture CC_GHOST+history-match state the static
tests/fixtures/stub_tmux.sh single-value stub cannot produce — reconstructing that
dynamically is out of proportion to this migration). This is a static, source-level
discriminator instead: same technique scripts/agent_messages_writer_gate.py already
uses to detect a raw writer, inverted to assert the NEW path is actually present.
"""
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

SCRIPTS = ["reset_auditor.sh", "reset_cai.sh", "reset_fleet_health.sh", "reset_nazim.sh"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_raw_insert_or_psycopg_remains(name):
    text = (REPO / "scripts" / name).read_text()
    assert "INSERT INTO agent_messages" not in text, name
    assert "import psycopg" not in text, name


@pytest.mark.parametrize("name", SCRIPTS)
def test_escalation_goes_through_bus_send_py_to_orch_console(name):
    text = (REPO / "scripts" / name).read_text()
    assert "scripts/bus_send.py" in text, name
    assert "--to orch-console" in text, name
    assert "--type blocker" in text, name
    assert "--priority P1" in text, name
    assert "--req" in text, name
