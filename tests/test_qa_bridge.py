"""Tests for QA bridge — dedup, rate limiting, and finding-to-bug bridging."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from nervous_system.qa_bridge import poll_qa_findings, _process_finding
from tests.conftest import mock_supabase_chain


class TestPollQaFindings:

    @pytest.mark.asyncio
    async def test_empty_findings_returns_early(self):
        sb = mock_supabase_chain([])
        with patch("nervous_system.qa_bridge._process_finding", new_callable=AsyncMock) as mock_process:
            await poll_qa_findings(sb)
            mock_process.assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_up_to_10_findings(self):
        findings = [
            {"id": i, "repo_name": "test", "title": f"Bug {i}", "source": "ci", "description": ""}
            for i in range(10)
        ]
        sb = mock_supabase_chain(findings)
        with patch("nervous_system.qa_bridge._process_finding", new_callable=AsyncMock) as mock_process:
            await poll_qa_findings(sb)
            assert mock_process.call_count == 10
        sb.limit.assert_called_with(10)

    @pytest.mark.asyncio
    async def test_individual_finding_failure_does_not_block_others(self):
        findings = [
            {"id": 1, "repo_name": "test", "title": "Bug 1", "source": "ci", "description": ""},
            {"id": 2, "repo_name": "test", "title": "Bug 2", "source": "ci", "description": ""},
            {"id": 3, "repo_name": "test", "title": "Bug 3", "source": "ci", "description": ""},
        ]
        sb = mock_supabase_chain(findings)

        call_count = 0

        async def side_effect(supabase, finding):
            nonlocal call_count
            call_count += 1
            if finding["id"] == 1:
                raise ValueError("processing failed")

        with patch("nervous_system.qa_bridge._process_finding", side_effect=side_effect):
            await poll_qa_findings(sb)

        assert call_count == 3


class TestProcessFinding:

    @pytest.mark.asyncio
    async def test_dedup_by_title_in_existing_description(self):
        existing_bugs = [{
            "id": "bug-1",
            "description": "Found issue: login page timeout. Needs fix ASAP",
        }]
        finding = {
            "id": "finding-1",
            "repo_name": "ihsanos",
            "title": "login page timeout",
            "source": "ci",
            "description": "Page takes 30s to load",
        }
        sb = mock_supabase_chain(existing_bugs)

        await _process_finding(sb, finding)

        sb.update.assert_called()
        update_arg = sb.update.call_args[0][0]
        assert update_arg["status"] == "duplicate"
        assert update_arg["bug_report_id"] == "bug-1"

    @pytest.mark.asyncio
    async def test_dedup_by_description_prefix_80chars(self):
        shared_prefix = "a" * 80
        existing_bugs = [{
            "id": "bug-2",
            "description": shared_prefix + " existing suffix here",
        }]
        finding = {
            "id": "finding-2",
            "repo_name": "ihsanos",
            "title": "Unrelated Title That Does Not Match",
            "source": "ci",
            "description": shared_prefix + " different new suffix",
        }
        sb = mock_supabase_chain(existing_bugs)

        await _process_finding(sb, finding)

        sb.update.assert_called()
        update_arg = sb.update.call_args[0][0]
        assert update_arg["status"] == "duplicate"

    @pytest.mark.asyncio
    async def test_dedup_by_qa_finding_id_reference(self):
        existing_bugs = [{
            "id": "bug-3",
            "description": "Bug from QA: qa_finding_id=finding-3. Login broken",
        }]
        finding = {
            "id": "finding-3",
            "repo_name": "ihsanos",
            "title": "Totally Different Title",
            "source": "sentry",
            "description": "Some other description entirely",
        }
        sb = mock_supabase_chain(existing_bugs)

        await _process_finding(sb, finding)

        sb.update.assert_called()
        update_arg = sb.update.call_args[0][0]
        assert update_arg["status"] == "duplicate"

    @pytest.mark.asyncio
    async def test_new_finding_creates_bug_report(self):
        new_bug = {"id": "new-bug-1"}
        finding = {
            "id": "finding-4",
            "repo_name": "ihsanos",
            "title": "New unique bug",
            "source": "lighthouse",
            "description": "Performance score dropped below threshold",
            "screenshot_url": None,
            "page_url": "/dashboard",
            "severity": "medium",
        }
        sb = mock_supabase_chain([])

        with patch("bug_pipeline.create_bug_report", new_callable=AsyncMock, return_value=new_bug) as mock_create:
            await _process_finding(sb, finding)
            mock_create.assert_called_once()

        update_calls = [c[0][0] for c in sb.update.call_args_list]
        assert {"status": "bridged", "bug_report_id": "new-bug-1"} in update_calls
        assert {"qa_finding_id": "finding-4"} in update_calls
