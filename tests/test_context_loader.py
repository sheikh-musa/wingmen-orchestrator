"""Tests for context_loader module."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Ensure orchestrator dir is on path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import context_loader


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temporary repo directory with CLAUDE.md and STATUS.md."""
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    (repo_dir / "CLAUDE.md").write_text("# Test Repo Rules\n- No blocking calls")
    (repo_dir / "STATUS.md").write_text("# Status\nPhase: testing")
    return repo_dir


@pytest.fixture
def repos_json(tmp_path, tmp_repo):
    """Create a temporary REPOS.json."""
    repos = {
        "repos": [
            {
                "name": "test-repo",
                "github": "https://github.com/test/test-repo",
                "local_path": str(tmp_repo),
                "vercel_project": "test-repo",
                "deploy_url": "https://test-repo.vercel.app",
                "supabase_project_ref": "xxx",
                "status": "active",
                "priority": 1,
            }
        ]
    }
    repos_file = tmp_path / "REPOS.json"
    repos_file.write_text(json.dumps(repos))
    return repos_file


@pytest.fixture
def mock_supabase():
    """Create a mock Supabase async client."""
    mock = MagicMock()
    execute_mock = AsyncMock(
        return_value=MagicMock(data=[{"key": "last_deploy", "value": "2026-03-30"}])
    )
    mock.table.return_value.select.return_value.eq.return_value.execute = execute_mock
    return mock


def test_get_repo_config_found(repos_json):
    with patch.object(context_loader, "REPOS_JSON", repos_json):
        config = context_loader.get_repo_config("test-repo")
        assert config["name"] == "test-repo"
        assert config["priority"] == 1


def test_get_repo_config_not_found(repos_json):
    with patch.object(context_loader, "REPOS_JSON", repos_json):
        with pytest.raises(ValueError, match="not found"):
            context_loader.get_repo_config("nonexistent")


def test_get_repo_config_matches_alias(tmp_path, tmp_repo):
    """msg #815: hifz BugReportButton posts repo='hifz' but canonical name is
    'hifz-companion'. Aliases let the canonical entry handle both without
    duplicating the row."""
    repos = {
        "repos": [
            {
                "name": "hifz-companion",
                "aliases": ["hifz"],
                "github": "https://github.com/sheikh-musa/hifz-companion",
                "local_path": str(tmp_repo),
                "vercel_project": "hifz-companion",
                "deploy_url": "https://hifz-companion.vercel.app",
                "supabase_project_ref": "xxx",
                "status": "active",
                "priority": 3,
            }
        ]
    }
    repos_file = tmp_path / "REPOS.json"
    repos_file.write_text(json.dumps(repos))
    with patch.object(context_loader, "REPOS_JSON", repos_file):
        # Canonical name still works.
        assert context_loader.get_repo_config("hifz-companion")["name"] == "hifz-companion"
        # Alias resolves to same canonical entry.
        cfg = context_loader.get_repo_config("hifz")
        assert cfg["name"] == "hifz-companion"
        assert cfg["priority"] == 3


def test_get_repo_config_alias_not_found_still_raises(tmp_path, tmp_repo):
    """Unknown names (not in name nor aliases) still raise ValueError."""
    repos = {
        "repos": [
            {
                "name": "hifz-companion",
                "aliases": ["hifz"],
                "github": "https://example.com",
                "local_path": str(tmp_repo),
                "vercel_project": None,
                "deploy_url": None,
                "supabase_project_ref": None,
                "status": "active",
                "priority": 3,
            }
        ]
    }
    repos_file = tmp_path / "REPOS.json"
    repos_file.write_text(json.dumps(repos))
    with patch.object(context_loader, "REPOS_JSON", repos_file):
        with pytest.raises(ValueError, match="not found"):
            context_loader.get_repo_config("unknown")


@pytest.mark.asyncio
async def test_load_context(repos_json, mock_supabase, tmp_repo):
    with patch.object(context_loader, "REPOS_JSON", repos_json):
        ctx = await context_loader.load_context("test-repo", mock_supabase)

        assert "No blocking calls" in ctx["claude_md"]
        assert "Phase: testing" in ctx["status_md"]
        assert len(ctx["memory"]) == 1
        assert ctx["memory"][0]["key"] == "last_deploy"
        assert ctx["repo_config"]["name"] == "test-repo"
        assert ctx["repo_path"] == str(tmp_repo)


@pytest.mark.asyncio
async def test_load_context_missing_files(repos_json, mock_supabase, tmp_path):
    """Context loads gracefully when CLAUDE.md / STATUS.md don't exist."""
    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()

    # Patch REPOS.json to point to empty dir
    repos = {
        "repos": [{
            "name": "empty-repo",
            "github": "https://github.com/test/empty-repo",
            "local_path": str(empty_repo),
            "vercel_project": None,
            "deploy_url": None,
            "supabase_project_ref": "xxx",
            "status": "specced",
            "priority": 5,
        }]
    }
    repos_file = tmp_path / "REPOS2.json"
    repos_file.write_text(json.dumps(repos))

    with patch.object(context_loader, "REPOS_JSON", repos_file):
        ctx = await context_loader.load_context("empty-repo", mock_supabase)
        assert ctx["claude_md"] == ""
        assert ctx["status_md"] == ""


class TestRepoConfigValidation:

    def test_ihsanos_deploy_url_is_correct(self):
        repos = json.loads((Path(__file__).parent.parent / "REPOS.json").read_text())
        ihsanos = next(r for r in repos["repos"] if r["name"] == "ihsanos")
        assert ihsanos["deploy_url"] == "https://ihsanos.com"

    def test_all_active_repos_have_deploy_url(self):
        repos = json.loads((Path(__file__).parent.parent / "REPOS.json").read_text())
        for repo in repos["repos"]:
            if repo["status"] == "active" and (repo.get("vercel_project") or repo.get("firebase_project")):
                assert repo["deploy_url"], f"{repo['name']} has a deploy target but no deploy_url"
