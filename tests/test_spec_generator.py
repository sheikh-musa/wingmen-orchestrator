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
async def test_generate_spec_calls_claude_cli(sample_job, sample_context):
    """Spec generator should shell out to claude CLI."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(
        return_value=(b"## Role\nihsandms is...\n<promise>JOB_42_DONE</promise>", b"")
    )
    mock_proc.returncode = 0

    with patch("spec_generator.asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await spec_generator.generate_spec(sample_job, sample_context)

    assert "JOB_42_DONE" in result
    assert "<promise>" in result


@pytest.mark.asyncio
async def test_generate_spec_raises_on_empty_output(sample_job, sample_context):
    """Should raise if CLI returns empty."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"some error"))
    mock_proc.returncode = 1

    with patch("spec_generator.asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(RuntimeError, match="Spec generation failed"):
            await spec_generator.generate_spec(sample_job, sample_context)


def test_format_memory_empty():
    assert spec_generator._format_memory([]) == "(no repo memory)"


def test_format_memory_items():
    memory = [{"key": "a", "value": "1"}, {"key": "b", "value": "2"}]
    result = spec_generator._format_memory(memory)
    assert "- a: 1" in result
    assert "- b: 2" in result
