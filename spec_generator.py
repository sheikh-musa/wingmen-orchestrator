"""Turns a job description + context into a Claude-ready build prompt."""

from __future__ import annotations

import asyncio
import os


async def generate_spec(job: dict, context: dict) -> str:
    """Use Claude CLI (Max subscription) to generate a structured build prompt."""
    repo_config = context["repo_config"]

    meta_prompt = f"""You are a spec generator for an autonomous build system.
Given the following repo context and task, generate a structured build prompt.

## Repo: {repo_config['name']}
- GitHub: {repo_config['github']}
- Deploy URL: {repo_config.get('deploy_url', 'N/A')}
- Status: {repo_config['status']}
- Priority: {repo_config['priority']}

## Current STATUS.md
{context['status_md'] or '(no STATUS.md found)'}

## CLAUDE.md (project rules)
{context['claude_md'] or '(no CLAUDE.md found)'}

## Repo Memory
{_format_memory(context['memory'])}

## Task from CTO
{job['description']}

---

Generate a structured prompt with these sections:
1. **Role**: What this repo is and who it serves
2. **Current State**: Summary of STATUS.md
3. **Task**: The exact task (verbatim from CTO)
4. **Constraints**: Hard rules from CLAUDE.md
5. **Success Criteria**: Specific, testable criteria for this task
6. **Files to Touch**: Best guess of which files need changes

End the prompt with exactly:
<promise>JOB_{job['id']}_DONE</promise>
"""

    claude_bin = os.path.expanduser("~/.local/bin/claude")
    safe_env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "USER", "SHELL", "LANG"}}
    safe_env["HOME"] = os.path.expanduser("~")
    safe_env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

    proc = await asyncio.create_subprocess_exec(
        claude_bin, "-p", meta_prompt, "--output-format", "text",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=safe_env,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    result = stdout.decode(errors="replace").strip()

    if not result:
        raise RuntimeError(f"Spec generation failed: {stderr.decode(errors='replace')}")

    return result


def _format_memory(memory: list[dict]) -> str:
    if not memory:
        return "(no repo memory)"
    return "\n".join(f"- {m['key']}: {m['value']}" for m in memory)
