"""Auto-provisioning engine — scaffolds new sites from templates.

Takes a template (portfolio/storefront/landing), creates a repo,
customizes config, deploys to Vercel, and sets up the client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

import httpx
from supabase import AsyncClient as SupabaseAsyncClient

logger = logging.getLogger("wingmen.provisioner")

PROJECTS_DIR = Path(os.path.expanduser("~/wingmen/projects"))
REPOS_JSON = Path(__file__).parent / "REPOS.json"


def _slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug[:30]


async def provision_site(
    supabase: SupabaseAsyncClient,
    client_id: int,
    template_id: str,
    site_name: str,
    config: dict,
) -> dict:
    """Full provisioning pipeline: template → repo → customize → deploy.

    Returns {"success": bool, "slug": str, "deploy_url": str, "repo_name": str}
    """
    slug = _slugify(site_name)
    repo_name = f"site-{slug}"

    logger.info(f"Provisioning {template_id} site: {site_name} → {repo_name}")

    # 1. Get template info
    template_result = await supabase.table("site_templates").select("*").eq(
        "id", template_id
    ).limit(1).execute()

    if not template_result.data:
        return {"success": False, "error": f"Template '{template_id}' not found"}

    template = template_result.data[0]

    # 2. Log provisioning
    prov_result = await supabase.table("provisions").insert({
        "client_id": client_id,
        "template_id": template_id,
        "slug": slug,
        "site_name": site_name,
        "config": json.dumps(config),
        "status": "creating",
        "repo_name": repo_name,
    }).execute()

    prov_id = prov_result.data[0]["id"] if prov_result.data else None

    try:
        # 3. Create repo from template
        await _create_repo_from_template(template["github_template"], repo_name)

        # 4. Clone locally
        local_path = str(PROJECTS_DIR / repo_name)
        await _clone_repo(repo_name, local_path)

        # 5. Customize config
        await _customize_site(local_path, template_id, site_name, config)

        # 6. Push customizations
        await _git_push_changes(local_path, f"setup: configure {site_name}")

        # 7. Deploy to Vercel
        deploy_url = await _deploy_to_vercel(repo_name, slug)

        # 8. Update REPOS.json
        _add_to_repos_json(repo_name, local_path, slug, deploy_url)

        # 9. Link client to repo
        await supabase.table("client_repos").insert({
            "client_id": client_id,
            "repo_name": repo_name,
        }).execute()

        # 10. Update provision status
        if prov_id:
            await supabase.table("provisions").update({
                "status": "completed",
                "deploy_url": deploy_url,
            }).eq("id", prov_id).execute()

        logger.info(f"Provisioned {site_name} → {deploy_url}")
        return {
            "success": True,
            "slug": slug,
            "deploy_url": deploy_url,
            "repo_name": repo_name,
        }

    except Exception as e:
        logger.error(f"Provisioning failed for {site_name}: {e}")
        if prov_id:
            await supabase.table("provisions").update({
                "status": "failed",
            }).eq("id", prov_id).execute()
        return {"success": False, "error": str(e)}


async def _create_repo_from_template(template_repo: str, new_repo_name: str) -> None:
    """Create a new repo from a template using GitHub API."""
    # gh api to create from template
    proc = await asyncio.create_subprocess_exec(
        "gh", "repo", "create", f"sheikh-musa/{new_repo_name}",
        "--private", "--template", template_repo,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"Repo creation failed: {stderr.decode()}")
    # Wait for GitHub to finish templating
    await asyncio.sleep(3)


async def _clone_repo(repo_name: str, local_path: str) -> None:
    """Clone the new repo locally."""
    if Path(local_path).exists():
        return

    parent = str(Path(local_path).parent)
    Path(parent).mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "gh", "repo", "clone", f"sheikh-musa/{repo_name}", Path(local_path).name,
        cwd=parent,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.wait_for(proc.communicate(), timeout=60)


async def _customize_site(local_path: str, template_id: str, site_name: str, config: dict) -> None:
    """Replace template placeholders with actual values."""
    config_file = Path(local_path) / "lib" / "config.ts"
    if not config_file.exists():
        return

    content = config_file.read_text()

    if template_id == "portfolio":
        replacements = {
            "{{SITE_NAME}}": site_name,
            "{{TAGLINE}}": config.get("tagline", ""),
            "{{DESCRIPTION}}": config.get("description", ""),
            "{{PRIMARY_COLOR}}": config.get("primary_color", "#111111"),
            "{{EMAIL}}": config.get("email", ""),
        }
    elif template_id == "storefront":
        replacements = {
            "{{STORE_NAME}}": site_name,
            "{{TAGLINE}}": config.get("tagline", ""),
            "{{PHONE}}": config.get("phone", ""),
            "{{PAYNOW_UEN}}": config.get("paynow_uen", ""),
            "{{PAYNOW_NAME}}": config.get("paynow_name", site_name.upper()),
            "{{PRIMARY_COLOR}}": config.get("primary_color", "#2d6a4f"),
        }

        # Also write products
        products = config.get("products", [])
        if products:
            products_ts = "export const products: Product[] = [\n"
            for i, p in enumerate(products):
                name = p.get("name", f"Item {i+1}")
                price = p.get("price", 0)
                category = p.get("category", "main")
                products_ts += f'  {{ id: {i+1}, name: "{name}", price: {price}, category: "{category}", available: true }},\n'
            products_ts += "];"

            # Replace the placeholder products array
            import re
            content = re.sub(
                r'export const products: Product\[\] = \[[\s\S]*?\];',
                products_ts,
                content,
            )
    else:
        replacements = {
            "{{SITE_NAME}}": site_name,
            "{{TAGLINE}}": config.get("tagline", ""),
        }

    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)

    config_file.write_text(content)

    # Update CSS colors if provided
    primary = config.get("primary_color")
    if primary:
        css_file = Path(local_path) / "app" / "globals.css"
        if css_file.exists():
            css = css_file.read_text()
            css = re.sub(r'--primary:\s*[^;]+;', f'--primary: {primary};', css)
            css_file.write_text(css)


async def _git_push_changes(local_path: str, message: str) -> None:
    """Commit and push customizations."""
    async def _run(cmd):
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)
        return proc.returncode

    await _run(["git", "add", "-A"])
    await _run(["git", "commit", "-m", message])
    await _run(["git", "push"])


async def _deploy_to_vercel(repo_name: str, slug: str) -> str:
    """Import project to Vercel and get deploy URL."""
    token = os.environ.get("VERCEL_TOKEN", "")
    team_id = os.environ.get("VERCEL_TEAM_ID", "")

    headers = {"Authorization": f"Bearer {token}"}
    params = {"teamId": team_id} if team_id else {}

    async with httpx.AsyncClient(timeout=30) as client:
        # Create Vercel project linked to GitHub repo
        resp = await client.post(
            "https://api.vercel.com/v10/projects",
            headers=headers,
            params=params,
            json={
                "name": repo_name,
                "gitRepository": {
                    "type": "github",
                    "repo": f"sheikh-musa/{repo_name}",
                },
                "framework": "nextjs",
            },
        )

        if resp.status_code in (200, 201):
            project = resp.json()
            deploy_url = f"https://{repo_name}.vercel.app"
            logger.info(f"Vercel project created: {deploy_url}")
            return deploy_url
        else:
            # Project might already exist
            deploy_url = f"https://{repo_name}.vercel.app"
            logger.warning(f"Vercel project creation returned {resp.status_code}, assuming {deploy_url}")
            return deploy_url


def _add_to_repos_json(repo_name: str, local_path: str, slug: str, deploy_url: str) -> None:
    """Add the new repo to REPOS.json."""
    with open(REPOS_JSON) as f:
        data = json.load(f)

    # Check if already exists
    for repo in data["repos"]:
        if repo["name"] == repo_name:
            return

    data["repos"].append({
        "name": repo_name,
        "github": f"https://github.com/sheikh-musa/{repo_name}",
        "local_path": f"~/wingmen/projects/{repo_name}",
        "vercel_project": repo_name,
        "deploy_url": deploy_url,
        "supabase_project_ref": "tscuymavysscrvoberrr",
        "status": "active",
        "priority": 3,
    })

    with open(REPOS_JSON, "w") as f:
        json.dump(data, f, indent=2)
