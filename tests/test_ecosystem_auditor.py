"""Tests for nervous_system.ecosystem_auditor — Gate 6 (BUG-012)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system import ecosystem_auditor
from tests.conftest import mock_supabase_chain


@pytest.fixture(autouse=True)
def _reset_gate6_cadence():
    """Clear the in-process cadence gate so each test actually runs Gate 6."""
    ecosystem_auditor._last_g6_run = None
    yield
    ecosystem_auditor._last_g6_run = None


async def test_gate_6_raises_when_anthropic_api_key_missing(monkeypatch, mock_supabase):
    """BUG-012: missing ANTHROPIC_API_KEY must raise, not silently return."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY missing"):
        await ecosystem_auditor.run_gate6_contradiction(mock_supabase)


async def test_gate_6_parses_valid_haiku_response(monkeypatch):
    """Gate 6 parses the Haiku JSON response and logs contradictions."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ECOSYSTEM_G6_DRY_RUN", "true")

    decisions = [
        {"decision_ref": f"DEC-{i}", "title": f"decision {i}", "domain": "d", "category": "c"}
        for i in range(4)
    ]
    supabase = mock_supabase_chain(final_data=decisions)

    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='{"contradictions": [{"refs": ["DEC-0", "DEC-1"], "description": "conflict"}]}')]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response
    fake_anthropic_module = MagicMock()
    fake_anthropic_module.Anthropic.return_value = fake_client

    with patch.dict(sys.modules, {"anthropic": fake_anthropic_module}):
        await ecosystem_auditor.run_gate6_contradiction(supabase)

    fake_anthropic_module.Anthropic.assert_called_once_with(api_key="test-key")
    assert fake_client.messages.create.called
    # _log_gate_run writes to ecosystem_audit_log with the contradictions action.
    logged_tables = [c.args[0] for c in supabase.table.call_args_list]
    assert "ecosystem_audit_log" in logged_tables
    inserted = supabase.insert.call_args.args[0]
    assert inserted["gate_name"] == "G6_contradiction"
    assert inserted["rows_affected"] == 1
    assert inserted["actions_taken"][0]["refs"] == ["DEC-0", "DEC-1"]
