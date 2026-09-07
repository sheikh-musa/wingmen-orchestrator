"""Tests for CAI-RESP-174 Q3: ralph_runner Gate 2 shadow A/B logging."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import legacy.ralph_runner as ralph_runner

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



@pytest.fixture
def shadow_log_in_tmp(tmp_path, monkeypatch):
    """Redirect the shadow log to a per-test path."""
    log = tmp_path / "ralph_gate2_shadow.jsonl"
    monkeypatch.setattr(ralph_runner, "_RALPH_GATE2_SHADOW_LOG", log)
    return log


async def test_shadow_aligned_match_recorded(shadow_log_in_tmp):
    """Primary and shadow both return aligned=True → match=True in the log."""
    fake_call_ai = AsyncMock(
        return_value='{"aligned": true, "confidence": 9, "mismatches": []}'
    )
    with patch("ai_provider.call_ai", fake_call_ai):
        shadow, err = await ralph_runner._shadow_call_ai_gate2("dummy prompt")
    assert err is None
    assert shadow == {"aligned": True, "confidence": 9, "mismatches": []}
    fake_call_ai.assert_awaited_once()


async def test_shadow_call_ai_failure_propagates_error(shadow_log_in_tmp):
    """call_ai raising → shadow returns (None, error_msg) — primary unaffected."""
    fake_call_ai = AsyncMock(side_effect=RuntimeError("CLI route blew up"))
    with patch("ai_provider.call_ai", fake_call_ai):
        shadow, err = await ralph_runner._shadow_call_ai_gate2("dummy prompt")
    assert shadow is None
    assert "CLI route blew up" in err
    assert "shadow call_ai failed" in err


async def test_shadow_non_dict_response_flagged(shadow_log_in_tmp):
    """call_ai returns a list JSON → shadow returns (None, type-error)."""
    fake_call_ai = AsyncMock(return_value="[1, 2, 3]")
    with patch("ai_provider.call_ai", fake_call_ai):
        shadow, err = await ralph_runner._shadow_call_ai_gate2("dummy prompt")
    assert shadow is None
    assert "non-dict JSON" in err


def test_append_gate2_shadow_log_writes_jsonl(shadow_log_in_tmp):
    """_append_gate2_shadow_log writes one JSON object per line."""
    ralph_runner._append_gate2_shadow_log({"a": 1, "b": "x"})
    ralph_runner._append_gate2_shadow_log({"a": 2, "b": "y"})
    lines = shadow_log_in_tmp.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"a": 1, "b": "x"}
    assert json.loads(lines[1]) == {"a": 2, "b": "y"}


def test_append_gate2_shadow_log_creates_parent_dir(tmp_path, monkeypatch):
    """If logs/ doesn't exist, _append_gate2_shadow_log creates it."""
    nested = tmp_path / "nonexistent" / "ralph_gate2_shadow.jsonl"
    monkeypatch.setattr(ralph_runner, "_RALPH_GATE2_SHADOW_LOG", nested)
    ralph_runner._append_gate2_shadow_log({"hello": "world"})
    assert nested.exists()
    assert json.loads(nested.read_text().strip()) == {"hello": "world"}
