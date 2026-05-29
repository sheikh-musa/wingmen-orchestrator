"""Tests for nervous_system.ecosystem_auditor — Gate 6 (BUG-012 + MAX-FIRST migration)."""

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


async def test_gate_6_no_anthropic_api_key_required(monkeypatch):
    """Post-MAX-FIRST migration: GATE 6 routes through ai_provider.call_ai
    (CLI/Max), so ANTHROPIC_API_KEY is no longer required to run the gate.

    Supersedes the pre-migration test that expected RuntimeError on missing key.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ECOSYSTEM_G6_DRY_RUN", "true")

    decisions = [
        {"decision_ref": f"DEC-{i}", "title": f"decision {i}", "domain": "d", "category": "c"}
        for i in range(4)
    ]
    supabase = mock_supabase_chain(final_data=decisions)

    fake_call_ai = AsyncMock(return_value='{"contradictions": []}')
    with patch.object(ecosystem_auditor, "__name__", "nervous_system.ecosystem_auditor"), \
         patch("ai_provider.call_ai", fake_call_ai):
        # Should NOT raise — CLI route doesn't need the API key.
        await ecosystem_auditor.run_gate6_contradiction(supabase)

    fake_call_ai.assert_awaited_once()
    # Verify model hint is the MAX-FIRST routed one.
    call_kwargs = fake_call_ai.await_args.kwargs
    assert call_kwargs.get("model") == "claude"


async def test_gate_6_parses_valid_response(monkeypatch):
    """GATE 6 parses the call_ai JSON response and logs contradictions."""
    monkeypatch.setenv("ECOSYSTEM_G6_DRY_RUN", "true")

    decisions = [
        {"decision_ref": f"DEC-{i}", "title": f"decision {i}", "domain": "d", "category": "c"}
        for i in range(4)
    ]
    supabase = mock_supabase_chain(final_data=decisions)

    fake_response = '{"contradictions": [{"refs": ["DEC-0", "DEC-1"], "description": "conflict"}]}'
    fake_call_ai = AsyncMock(return_value=fake_response)

    with patch("ai_provider.call_ai", fake_call_ai):
        await ecosystem_auditor.run_gate6_contradiction(supabase)

    fake_call_ai.assert_awaited_once()
    # _log_gate_run writes to ecosystem_audit_log with the contradictions action.
    logged_tables = [c.args[0] for c in supabase.table.call_args_list]
    assert "ecosystem_audit_log" in logged_tables
    inserted = supabase.insert.call_args.args[0]
    assert inserted["gate_name"] == "G6_contradiction"
    assert inserted["rows_affected"] == 1
    assert inserted["actions_taken"][0]["refs"] == ["DEC-0", "DEC-1"]


async def test_gate_6_handles_non_dict_response(monkeypatch):
    """If extract_json returns a non-dict (e.g. list, None), GATE 6 raises loudly."""
    monkeypatch.setenv("ECOSYSTEM_G6_DRY_RUN", "true")

    decisions = [
        {"decision_ref": f"DEC-{i}", "title": f"decision {i}", "domain": "d", "category": "c"}
        for i in range(4)
    ]
    supabase = mock_supabase_chain(final_data=decisions)

    # Return a JSON list instead of object
    fake_call_ai = AsyncMock(return_value="[1, 2, 3]")
    with patch("ai_provider.call_ai", fake_call_ai):
        with pytest.raises(RuntimeError, match="expected dict JSON"):
            await ecosystem_auditor.run_gate6_contradiction(supabase)
