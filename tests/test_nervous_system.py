"""Tests for nervous system scheduled tasks."""

from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import json

import pytest
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.brain_sync import _load_active_repos, _scan_repo, _generate_brain_md, RepoState


def test_load_active_repos(tmp_path):
    repos_file = tmp_path / "REPOS.json"
    repos_file.write_text(json.dumps({"repos": [
        {"name": "active-repo", "status": "active", "local_path": "/tmp"},
        {"name": "specced-repo", "status": "specced", "local_path": "/tmp"},
    ]}))
    with patch("nervous_system.brain_sync.REPOS_JSON", repos_file):
        result = _load_active_repos()
        assert len(result) == 1
        assert result[0]["name"] == "active-repo"


@pytest.mark.asyncio
async def test_scan_repo_missing_path():
    repo = {"name": "ghost", "local_path": "/nonexistent/path", "status": "active"}
    state = await _scan_repo(repo)
    assert state.scan_succeeded is False
    assert state.health == "red"
    assert "not found" in state.scan_error


def test_generate_brain_md():
    repos = [
        RepoState(name="test-repo", health="green", commits_24h=3),
    ]
    md = _generate_brain_md(repos, [], [])
    assert "test-repo" in md
    assert "🟢" in md
    assert "3 commits today" in md


def test_generate_brain_md_with_blockers():
    repos = [
        RepoState(name="blocked-repo", health="yellow", commits_24h=0, blockers=["Waiting on API key"], contradictions=["Stale STATUS.md"]),
    ]
    md = _generate_brain_md(repos, [], [])
    assert "Blocked: Waiting on API key" in md
    assert "⚠️ Stale STATUS.md" in md
