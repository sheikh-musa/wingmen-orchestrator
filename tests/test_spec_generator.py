"""Tests for spec_generator module."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import spec_generator


@pytest.fixture
def sample_job():
    return {
        "id": 42,
        "repo_name": "ihsandms",
        "description": "Fix the order form validation on checkout page",
        "status": "queued",
        "priority": 1,
    }


@pytest.fixture
def sample_context():
    return {
        "claude_md": "# ihsandms\n- RTL-first CSS\n- 150KB page weight max",
        "status_md": "# Status\nPhase: production\nBuild Status: green",
        "memory": [
            {"key": "framework", "value": "Next.js 14"},
            {"key": "last_issue", "value": "checkout form missing"},
        ],
        "repo_config": {
            "name": "ihsandms",
            "github": "https://github.com/sheikh-musa/ihsandms",
            "deploy_url": "https://ihsandms.vercel.app",
            "status": "active",
            "priority": 1,
        },
        "repo_path": "/Users/sheikhmusa/wingmen/projects/ihsandms",
    }


@pytest.mark.asyncio
async def test_generate_spec_returns_prompt_with_promise(sample_job, sample_context):
    """Spec generator should return a prompt containing the completion promise."""
    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(text="## Role\nihsandms is...\n<promise>JOB_42_DONE</promise>")
    ]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    with patch("spec_generator.anthropic.AsyncAnthropic", return_value=mock_client):
        result = await spec_generator.generate_spec(sample_job, sample_context)

    assert "JOB_42_DONE" in result
    assert "<promise>" in result

    # Verify the API was called with correct model
    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    assert "ihsandms" in call_kwargs["messages"][0]["content"]
    assert "Fix the order form" in call_kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_generate_spec_includes_memory(sample_job, sample_context):
    """Memory items should appear in the prompt sent to Claude."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="prompt text")]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    with patch("spec_generator.anthropic.AsyncAnthropic", return_value=mock_client):
        await spec_generator.generate_spec(sample_job, sample_context)

    sent_content = mock_client.messages.create.call_args[1]["messages"][0]["content"]
    assert "framework: Next.js 14" in sent_content
    assert "last_issue: checkout form missing" in sent_content


def test_format_memory_empty():
    assert spec_generator._format_memory([]) == "(no repo memory)"


def test_format_memory_items():
    memory = [{"key": "a", "value": "1"}, {"key": "b", "value": "2"}]
    result = spec_generator._format_memory(memory)
    assert "- a: 1" in result
    assert "- b: 2" in result
