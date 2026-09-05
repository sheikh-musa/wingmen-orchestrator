"""Tests for bug pipeline and diagnostic agent."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import json
import os
import tempfile

from agents.diagnostic import build_diagnostic_prompt, parse_diagnostic_response
from bug_pipeline import (
    VALID_TRANSITIONS,
    _get_test_command,
    create_bug_report,
    _assign_auto_fix_tier,
    handle_verification,
)
from tests.conftest import mock_supabase_chain


class TestDiagnosticAgent:

    def test_build_prompt_includes_description(self):
        prompt = build_diagnostic_prompt(
            description="Button crashes on click",
            page_url="/dashboard",
            screenshot_description=None,
            repo_path="/test/repo",
            repo_context="# Test App",
            recent_commits="abc123 fix: something",
        )
        assert "Button crashes on click" in prompt
        assert "/dashboard" in prompt

    def test_build_prompt_without_optional_fields(self):
        prompt = build_diagnostic_prompt(
            description="Something is broken",
            page_url=None,
            screenshot_description=None,
            repo_path="/test/repo",
            repo_context="",
            recent_commits="",
        )
        assert "Something is broken" in prompt
        assert "Page:" not in prompt

    def test_parse_valid_json(self):
        response = json.dumps({
            "root_cause": "Missing null check",
            "confidence": "high",
            "affected_files": ["src/app.tsx"],
            "proposed_diff": "- old\n+ new",
            "diagnosis_full": "The issue is...",
        })
        result = parse_diagnostic_response(response)
        assert result["root_cause"] == "Missing null check"
        assert result["confidence"] == "high"
        assert result["affected_files"] == ["src/app.tsx"]

    def test_parse_json_in_code_block(self):
        response = '```json\n{"root_cause": "Bug", "confidence": "medium", "affected_files": [], "proposed_diff": "", "diagnosis_full": ""}\n```'
        result = parse_diagnostic_response(response)
        assert result["confidence"] == "medium"

    def test_parse_invalid_response_falls_back(self):
        result = parse_diagnostic_response("This is not JSON at all")
        assert result["confidence"] == "low"
        assert "Unable to determine" in result["root_cause"]

    def test_parse_invalid_confidence_defaults_to_low(self):
        response = json.dumps({"root_cause": "x", "confidence": "super_high", "affected_files": [], "proposed_diff": "", "diagnosis_full": ""})
        result = parse_diagnostic_response(response)
        assert result["confidence"] == "low"


class TestStatusTransitions:

    def test_valid_transitions(self):
        assert "diagnosing" in VALID_TRANSITIONS["new"]
        assert "proposed" in VALID_TRANSITIONS["diagnosing"]
        assert "approved" in VALID_TRANSITIONS["proposed"]
        assert "deploying" in VALID_TRANSITIONS["approved"]
        assert "deployed" in VALID_TRANSITIONS["deploying"]
        assert "verified" in VALID_TRANSITIONS["deployed"]

    def test_rejected_is_terminal(self):
        assert VALID_TRANSITIONS["rejected"] == []

    def test_verified_is_terminal(self):
        assert VALID_TRANSITIONS["verified"] == []

    def test_still_broken_can_retry_or_escalate(self):
        assert "new" in VALID_TRANSITIONS["still_broken"]
        assert "escalated" in VALID_TRANSITIONS["still_broken"]

    def test_escalated_is_terminal(self):
        assert VALID_TRANSITIONS["escalated"] == []


class TestGetTestCommand:

    def test_vitest_config_ts(self, tmp_path):
        (tmp_path / "vitest.config.ts").touch()
        cmd = _get_test_command("test-repo", {"local_path": str(tmp_path)})
        assert cmd == "npx vitest run --reporter=verbose"

    def test_vitest_config_js(self, tmp_path):
        (tmp_path / "vitest.config.js").touch()
        cmd = _get_test_command("test-repo", {"local_path": str(tmp_path)})
        assert cmd == "npx vitest run --reporter=verbose"

    def test_pytest_ini(self, tmp_path):
        (tmp_path / "pytest.ini").touch()
        cmd = _get_test_command("test-repo", {"local_path": str(tmp_path)})
        assert cmd == "python -m pytest -x --tb=short"

    def test_pyproject_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        cmd = _get_test_command("test-repo", {"local_path": str(tmp_path)})
        assert cmd == "python -m pytest -x --tb=short"

    def test_package_json_fallback(self, tmp_path):
        (tmp_path / "package.json").touch()
        cmd = _get_test_command("test-repo", {"local_path": str(tmp_path)})
        assert "npm test" in cmd

    def test_no_test_setup(self, tmp_path):
        cmd = _get_test_command("test-repo", {"local_path": str(tmp_path)})
        assert cmd is None

    def test_empty_path(self):
        cmd = _get_test_command("test-repo", {"local_path": ""})
        assert cmd is None


class TestCreateBugReportDedup:

    @pytest.mark.asyncio
    async def test_dedup_by_description_substring(self):
        existing_bugs = [{
            "id": 99,
            "description": "Button crashes on click when form is open on the dashboard page and causes error",
            "client_id": None,
        }]
        sb = mock_supabase_chain(existing_bugs)

        result = await create_bug_report(
            sb,
            client_id=None,
            reporter_name="Tester",
            reporter_email=None,
            reporter_source="telegram",
            auth_provider="telegram",
            repo_name="ihsanos",
            description="Button crashes on click when form is open on the dashboard page",
        )

        assert result["id"] == 99
        sb.insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_by_client_id_and_prefix(self):
        prefix = "x" * 80
        existing_bugs = [{
            "id": 88,
            "description": prefix + " unique_old_suffix_that_differs_completely",
            "client_id": 42,
        }]
        sb = mock_supabase_chain(existing_bugs)

        result = await create_bug_report(
            sb,
            client_id=42,
            reporter_name="Tester",
            reporter_email=None,
            reporter_source="telegram",
            auth_provider="telegram",
            repo_name="ihsanos",
            description=prefix + " unique_new_suffix_that_differs_completely",
        )

        assert result["id"] == 88
        sb.insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_dedup_when_descriptions_differ(self):
        existing_bugs = [{
            "id": 99,
            "description": "Completely different bug about login timeout on mobile",
            "client_id": None,
        }]
        new_bug = {"id": 100, "repo_name": "ihsanos", "description": "Button crashes on click"}
        sb = mock_supabase_chain()
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=existing_bugs),
            MagicMock(data=[new_bug]),
        ])

        with patch("bug_pipeline._run_diagnosis", new_callable=AsyncMock), \
             patch("asyncio.create_task"):
            result = await create_bug_report(
                sb,
                client_id=None,
                reporter_name="Tester",
                reporter_email=None,
                reporter_source="telegram",
                auth_provider="telegram",
                repo_name="ihsanos",
                description="Button crashes on click",
            )

        assert result["id"] == 100
        sb.insert.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_dedup_when_existing_is_closed(self):
        new_bug = {"id": 101, "repo_name": "ihsanos", "description": "Button crashes"}
        sb = mock_supabase_chain()
        sb.execute = AsyncMock(side_effect=[
            MagicMock(data=[]),
            MagicMock(data=[new_bug]),
        ])

        with patch("bug_pipeline._run_diagnosis", new_callable=AsyncMock), \
             patch("asyncio.create_task"):
            result = await create_bug_report(
                sb,
                client_id=None,
                reporter_name="Tester",
                reporter_email=None,
                reporter_source="telegram",
                auth_provider="telegram",
                repo_name="ihsanos",
                description="Button crashes",
            )

        assert result["id"] == 101
        sb.insert.assert_called_once()

    @pytest.mark.asyncio
    async def test_dedup_case_insensitive(self):
        existing_bugs = [{
            "id": 77,
            "description": "button crashes on click and scroll",
            "client_id": None,
        }]
        sb = mock_supabase_chain(existing_bugs)

        result = await create_bug_report(
            sb,
            client_id=None,
            reporter_name="Tester",
            reporter_email=None,
            reporter_source="telegram",
            auth_provider="telegram",
            repo_name="ihsanos",
            description="BUTTON CRASHES ON CLICK",
        )

        assert result["id"] == 77
        sb.insert.assert_not_called()


class TestAssignAutoFixTier:

    @pytest.mark.asyncio
    async def test_tier1_high_confidence_qa_sourced_few_files_first_attempt(self):
        bug_data = {
            "confidence": "high",
            "qa_finding_id": 123,
            "affected_files": ["a.tsx"],
            "retry_count": 0,
            "severity": "medium",
        }
        sb = mock_supabase_chain(bug_data)
        sb.single.return_value = sb

        tier = await _assign_auto_fix_tier(sb, "bug-1")
        assert tier == 1

    @pytest.mark.asyncio
    async def test_tier3_critical_severity_always(self):
        bug_data = {
            "confidence": "high",
            "qa_finding_id": 123,
            "affected_files": ["a.tsx"],
            "retry_count": 0,
            "severity": "critical",
        }
        sb = mock_supabase_chain(bug_data)
        sb.single.return_value = sb

        tier = await _assign_auto_fix_tier(sb, "bug-1")
        assert tier == 3

    @pytest.mark.asyncio
    async def test_tier3_low_confidence(self):
        bug_data = {
            "confidence": "low",
            "qa_finding_id": 123,
            "affected_files": ["a.tsx"],
            "retry_count": 0,
            "severity": "medium",
        }
        sb = mock_supabase_chain(bug_data)
        sb.single.return_value = sb

        tier = await _assign_auto_fix_tier(sb, "bug-1")
        assert tier == 3

    @pytest.mark.asyncio
    async def test_tier3_retried_bug(self):
        bug_data = {
            "confidence": "high",
            "qa_finding_id": 123,
            "affected_files": ["a.tsx"],
            "retry_count": 1,
            "severity": "medium",
        }
        sb = mock_supabase_chain(bug_data)
        sb.single.return_value = sb

        tier = await _assign_auto_fix_tier(sb, "bug-1")
        assert tier == 3

    @pytest.mark.asyncio
    async def test_tier2_medium_confidence(self):
        bug_data = {
            "confidence": "medium",
            "qa_finding_id": 123,
            "affected_files": ["a.tsx"],
            "retry_count": 0,
            "severity": "medium",
        }
        sb = mock_supabase_chain(bug_data)
        sb.single.return_value = sb

        tier = await _assign_auto_fix_tier(sb, "bug-1")
        assert tier == 2

    @pytest.mark.asyncio
    async def test_tier2_high_confidence_not_qa_sourced(self):
        bug_data = {
            "confidence": "high",
            "qa_finding_id": None,
            "affected_files": ["a.tsx"],
            "retry_count": 0,
            "severity": "medium",
        }
        sb = mock_supabase_chain(bug_data)
        sb.single.return_value = sb

        tier = await _assign_auto_fix_tier(sb, "bug-1")
        assert tier == 2


class TestHandleVerification:

    @pytest.mark.asyncio
    async def test_verified_transitions_to_verified(self):
        sb = mock_supabase_chain()
        with patch("bug_pipeline._update_status", new_callable=AsyncMock) as mock_update:
            await handle_verification(sb, "bug-1", verified=True)
            mock_update.assert_called_once_with(sb, "bug-1", "verified")

    @pytest.mark.asyncio
    async def test_still_broken_retries_when_under_max(self):
        bug_data = {"retry_count": 0, "diagnosis_full": "initial diagnosis"}
        sb = mock_supabase_chain(bug_data)
        sb.single.return_value = sb

        with patch("bug_pipeline._update_status", new_callable=AsyncMock) as mock_update:
            await handle_verification(sb, "bug-1", verified=False)
            mock_update.assert_not_called()

        update_arg = sb.update.call_args[0][0]
        assert update_arg["status"] == "new"
        assert update_arg["retry_count"] == 1
        assert update_arg["prev_diagnosis"] == "initial diagnosis"
        assert update_arg["confidence"] is None
        assert update_arg["root_cause"] is None
        assert update_arg["proposed_diff"] is None

    @pytest.mark.asyncio
    async def test_still_broken_escalates_after_max_retries(self):
        bug_data = {"retry_count": 2, "diagnosis_full": "tried twice"}
        sb = mock_supabase_chain(bug_data)
        sb.single.return_value = sb

        with patch("bug_pipeline._update_status", new_callable=AsyncMock) as mock_update:
            await handle_verification(sb, "bug-1", verified=False)
            mock_update.assert_called_once_with(sb, "bug-1", "escalated")
