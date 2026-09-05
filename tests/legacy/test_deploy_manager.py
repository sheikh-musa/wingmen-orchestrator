"""Tests for deploy_manager.py — Vercel deployment logic."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx


def _make_resp(json_data):
    """Create a MagicMock httpx response with synchronous .json() and .raise_for_status()."""
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


class TestDeploy:
    @pytest.mark.asyncio
    async def test_skips_when_no_vercel_project(self):
        from deploy_manager import deploy

        with patch("deploy_manager.get_repo_config", return_value={"name": "test"}):
            result = await deploy("test")
        assert result == {"deployed": False, "url": None}

    @pytest.mark.asyncio
    async def test_skips_when_no_git_link(self):
        from deploy_manager import deploy

        project_resp = _make_resp({"id": "prj_1", "link": {}})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=project_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("deploy_manager.get_repo_config", return_value={"name": "test", "vercel_project": "my-project"}), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await deploy("test")
        assert result == {"deployed": False, "url": None}

    @pytest.mark.asyncio
    async def test_successful_deploy(self):
        from deploy_manager import deploy

        project_resp = _make_resp({
            "id": "prj_1",
            "link": {"repoId": "12345", "productionBranch": "main"},
        })
        deploy_resp = _make_resp({
            "id": "dpl_abc",
            "url": "my-app-123.vercel.app",
        })
        poll_resp = _make_resp({
            "readyState": "READY",
            "url": "my-app-123.vercel.app",
        })

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[project_resp, poll_resp])
        mock_client.post = AsyncMock(return_value=deploy_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("deploy_manager.get_repo_config", return_value={"name": "test", "vercel_project": "my-project"}), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await deploy("test")

        assert result["deployed"] is True
        assert "https://" in result["url"]

    @pytest.mark.asyncio
    async def test_deploy_url_gets_https_prefix(self):
        from deploy_manager import deploy

        project_resp = _make_resp({
            "id": "prj_1",
            "link": {"repoId": "12345"},
        })
        deploy_resp = _make_resp({
            "id": "dpl_abc",
            "url": "my-app.vercel.app",
        })

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=project_resp)
        mock_client.post = AsyncMock(return_value=deploy_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("deploy_manager.get_repo_config", return_value={"name": "test", "vercel_project": "my-project"}), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("deploy_manager._poll_deployment", new_callable=AsyncMock, return_value="https://my-app.vercel.app"):
            result = await deploy("test")

        assert result["url"].startswith("https://")


class TestPollDeployment:
    @pytest.mark.asyncio
    async def test_returns_url_on_ready(self):
        from deploy_manager import _poll_deployment

        resp = _make_resp({"readyState": "READY", "url": "my-app.vercel.app"})

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            url = await _poll_deployment(client, "dpl_1", {}, {})

        assert url == "https://my-app.vercel.app"

    @pytest.mark.asyncio
    async def test_raises_on_error(self):
        from deploy_manager import _poll_deployment

        resp = _make_resp({"readyState": "ERROR", "errorMessage": "Build crashed"})

        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)

        with patch("asyncio.sleep", new_callable=AsyncMock), \
             pytest.raises(RuntimeError, match="ERROR"):
            await _poll_deployment(client, "dpl_1", {}, {})

    @pytest.mark.asyncio
    async def test_retries_on_http_error(self):
        from deploy_manager import _poll_deployment

        error_resp = httpx.Response(status_code=500, request=httpx.Request("GET", "http://test"))

        good_resp = _make_resp({"readyState": "READY", "url": "app.vercel.app"})

        client = AsyncMock()
        client.get = AsyncMock(side_effect=[
            httpx.HTTPStatusError("Server Error", request=httpx.Request("GET", "http://test"), response=error_resp),
            good_resp,
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            url = await _poll_deployment(client, "dpl_1", {}, {})
        assert url == "https://app.vercel.app"
