"""Triggers Vercel deploy after successful build."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from context_loader import get_repo_config

logger = logging.getLogger("wingmen.deploy")

VERCEL_API = "https://api.vercel.com"


async def deploy(repo_name: str) -> dict:
    """Deploy a repo to Vercel. Returns {"deployed": bool, "url": str|None}."""
    config = get_repo_config(repo_name)

    if not config.get("vercel_project"):
        logger.info(f"{repo_name}: no vercel_project configured, skipping deploy")
        return {"deployed": False, "url": None}

    token = os.environ["VERCEL_TOKEN"]
    team_id = os.environ.get("VERCEL_TEAM_ID", "")

    headers = {"Authorization": f"Bearer {token}"}
    params = {}
    if team_id:
        params["teamId"] = team_id

    async with httpx.AsyncClient(timeout=30) as client:
        # Get the project to find the latest deployment source
        resp = await client.get(
            f"{VERCEL_API}/v9/projects/{config['vercel_project']}",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()
        project = resp.json()

        # Trigger a new production deployment via the deployments API
        # This requires the project to have a git repo connected
        github_info = project.get("link", {})

        if not github_info:
            logger.warning(f"{repo_name}: no git repo linked in Vercel, skipping")
            return {"deployed": False, "url": None}

        deploy_payload = {
            "name": config["vercel_project"],
            "project": project["id"],
            "target": "production",
            "gitSource": {
                "type": "github",
                "repoId": str(github_info.get("repoId", "")),
                "ref": github_info.get("productionBranch", "main"),
            },
        }

        resp = await client.post(
            f"{VERCEL_API}/v13/deployments",
            headers=headers,
            params=params,
            json=deploy_payload,
        )
        resp.raise_for_status()
        deployment = resp.json()

        deploy_id = deployment["id"]
        deploy_url = deployment.get("url", "")
        if deploy_url and not deploy_url.startswith("https://"):
            deploy_url = f"https://{deploy_url}"

        logger.info(f"{repo_name}: deployment {deploy_id} created → {deploy_url}")

        # Poll until ready or failed (max 5 min)
        final_url = await _poll_deployment(client, deploy_id, headers, params)
        return {"deployed": True, "url": final_url or deploy_url}


async def _poll_deployment(
    client: httpx.AsyncClient,
    deploy_id: str,
    headers: dict,
    params: dict,
    timeout_seconds: int = 300,
) -> str | None:
    """Poll deployment status until READY or ERROR."""
    elapsed = 0
    interval = 10

    while elapsed < timeout_seconds:
        await asyncio.sleep(interval)
        elapsed += interval

        last_err = None
        for attempt in range(3):
            try:
                resp = await client.get(
                    f"{VERCEL_API}/v13/deployments/{deploy_id}",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                last_err = None
                break
            except httpx.HTTPError as e:
                last_err = e
                if attempt < 2:
                    backoff = 2 ** attempt
                    logger.warning(f"Vercel API error (attempt {attempt + 1}/3), retrying in {backoff}s: {e}")
                    await asyncio.sleep(backoff)

        if last_err is not None:
            raise RuntimeError(f"Vercel API failed after 3 retries: {last_err}")

        data = resp.json()
        state = data.get("readyState", data.get("state", ""))

        if state == "READY":
            url = data.get("url", "")
            if url and not url.startswith("https://"):
                url = f"https://{url}"
            logger.info(f"Deployment {deploy_id} READY → {url}")
            return url
        elif state in ("ERROR", "CANCELED"):
            logger.error(f"Deployment {deploy_id} failed: {state}")
            raise RuntimeError(f"Vercel deployment {state}: {data.get('errorMessage', '')}")

        logger.debug(f"Deployment {deploy_id}: {state} ({elapsed}s)")

    logger.warning(f"Deployment {deploy_id} timed out after {timeout_seconds}s")
    return None
