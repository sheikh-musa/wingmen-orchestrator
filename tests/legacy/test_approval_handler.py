"""Tests for approval handler."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("MUSA_TELEGRAM_ID", "123456")

from legacy.approval_handler import get_eligible_approvers, build_approval_message, build_full_diagnosis_message, Approver

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



class TestEligibleApprovers:

    @pytest.mark.asyncio
    async def test_musa_always_eligible(self):
        supabase = AsyncMock()
        bug = {"confidence": "low", "repo_name": "ihsanos"}
        approvers = await get_eligible_approvers(supabase, bug)
        assert any(a.role == "super_admin" for a in approvers)

    @pytest.mark.asyncio
    async def test_returns_approver_list(self):
        supabase = AsyncMock()
        bug = {"confidence": "high", "repo_name": "ihsanos"}
        approvers = await get_eligible_approvers(supabase, bug)
        assert len(approvers) >= 1
        assert all(isinstance(a, Approver) for a in approvers)


class TestApprovalMessage:

    def test_builds_compact_message(self):
        bug = {
            "id": "test-123",
            "repo_name": "cosem-adcda",
            "reporter_name": "Ahmad",
            "reporter_source": "telegram",
            "description": "Button crashes",
            "root_cause": "Missing null check",
            "confidence": "high",
            "affected_files": ["src/pages/Attendance.jsx"],
            "proposed_diff": "- old\n+ new",
        }
        text, keyboard = build_approval_message(bug)
        assert "cosem-adcda" in text
        assert "Ahmad" in text
        assert "Missing null check" in text
        assert "\U0001f7e2" in text  # high confidence green circle
        assert keyboard is not None

    def test_handles_missing_fields(self):
        bug = {
            "id": "test-456",
            "repo_name": "unknown",
            "reporter_name": "User",
            "reporter_source": "web",
            "description": "Something broke",
            "root_cause": None,
            "confidence": None,
            "affected_files": None,
            "proposed_diff": None,
        }
        text, keyboard = build_approval_message(bug)
        assert "unknown" in text
        assert keyboard is not None


class TestFullDiagnosis:

    def test_builds_diagnosis_text(self):
        bug = {
            "repo_name": "ihsanos",
            "diagnosis_full": "The error occurs because...",
            "affected_files": ["src/app.tsx", "src/lib/utils.ts"],
        }
        text = build_full_diagnosis_message(bug)
        assert "The error occurs because" in text
        assert "src/app.tsx" in text
