"""Turns a job description + context into a Claude-ready build prompt."""

import anthropic


async def generate_spec(job: dict, context: dict) -> str:
    """Use Claude to generate a structured build prompt from job + context."""
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

    client = anthropic.AsyncAnthropic()
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": meta_prompt}],
    )
    return message.content[0].text


def _format_memory(memory: list[dict]) -> str:
    if not memory:
        return "(no repo memory)"
    return "\n".join(f"- {m['key']}: {m['value']}" for m in memory)
