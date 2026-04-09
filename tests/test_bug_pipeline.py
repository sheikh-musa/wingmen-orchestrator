"""Tests for bug pipeline and diagnostic agent."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import json
import os
import tempfile

from agents.diagnostic import build_diagnostic_prompt, parse_diagnostic_response
from bug_pipeline import VALID_TRANSITIONS, _get_test_command


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
