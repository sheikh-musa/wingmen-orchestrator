"""Turns a job description + context into a Claude-ready build prompt.

Follows Claude Code best practices:
- Focused scope per task (no sprawling multi-page changes)
- Clear acceptance criteria
- Constraints from CLAUDE.md
- Minimal, surgical changes — don't refactor unrelated code
"""

from __future__ import annotations

import asyncio
import os


CLAUDE_MD_CAP = 8000  # chars; large CLAUDE.md (ihsanos = 26KB) hung the CLI


def _truncate_for_prompt(text: str | None, cap: int) -> str:
    if not text:
        return "(empty)"
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n\n[...truncated {len(text) - cap} chars — agent can read full file at runtime...]"


async def generate_spec(job: dict, context: dict) -> str:
    """Use Claude CLI (Max subscription) to generate a structured build prompt."""
    repo_config = context["repo_config"]
    claude_md = _truncate_for_prompt(context.get('claude_md'), CLAUDE_MD_CAP)

    meta_prompt = f"""Produce a build specification document. Output ONLY the spec — no preamble, no questions, no requests for additional access. Everything you need is in the context below.

If the task seems to need information you don't have, write the spec assuming the executing agent will gather it at runtime (the agent has full filesystem and shell access; you do not need them). DO NOT respond with "I need access to..." — produce the spec.

## Project Context
- Repo: {repo_config['name']}
- GitHub: {repo_config['github']}
- Deploy URL: {repo_config.get('deploy_url', 'N/A')}
- Status: {repo_config['status']}

## Current STATUS.md
{context['status_md'] or '(no STATUS.md found)'}

## CLAUDE.md (project rules — the agent MUST follow these; full file readable at runtime if truncated)
{claude_md}

## Repo Memory
{_format_memory(context['memory'])}

## Task from User
{job['description']}

---

## Your job: Generate a build specification

Follow these rules strictly:

1. **SCOPE**: Keep changes minimal and surgical. Only modify files directly related to the task. Never refactor unrelated code, add unnecessary abstractions, or "improve" things that weren't asked for.

2. **EXISTING PATTERNS**: Study the codebase's existing patterns before proposing changes. Match the project's naming conventions, file structure, component patterns, and styling approach. Don't introduce new patterns.

3. **ACCEPTANCE CRITERIA**: List 3-5 specific, testable criteria. Each should be verifiable by looking at the UI or running a command. No vague criteria like "works correctly."

4. **FILE PLAN**: List exact files to create or modify. For modifications, describe what changes — not "update file" but "add X component/function/route."

5. **NO EXTRAS**: Don't add error handling, comments, type annotations, or features beyond what was asked. Don't create utility files for one-time operations. Three similar lines of code is better than a premature abstraction.

6. **MOBILE-FIRST**: If the project uses responsive design, ensure changes work on mobile viewports (375px).

7. **SUPABASE-FIRST AUDIT**: All build outputs and audit deliverables must be written to the Supabase `work_outputs` table. Do not rely on repo files alone for audit trail. The orchestrator handles this automatically — do not duplicate the writes.

8. **TESTS MOVE WITH CODE**: Before declaring done, grep `tests/` for every top-level function, class, or module name you modified. If a test asserts old behavior that no longer holds, update the assertion or delete the test in the same commit. Never ship a behavior change while leaving its test asserting the old behavior — stale tests block the queue via test_gate and stall every downstream job. This is non-negotiable.

Output format:

### Role
One sentence: what this repo is.

### Task
The exact task, rephrased clearly.

### Constraints
Hard rules from CLAUDE.md that apply to this task.

### Implementation Plan
Step-by-step what the agent should do. Be specific about which files, which functions, which components.

### Acceptance Criteria
Numbered list. Each criterion is testable.

### Files to Touch
Table: | File | Action | What changes |

End with: <promise>JOB_{job['id']}_DONE</promise>
"""

    claude_bin = os.path.expanduser("~/.local/bin/claude")
    safe_env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "USER", "SHELL", "LANG"}}
    safe_env["HOME"] = os.path.expanduser("~")
    safe_env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

    # Pipe meta_prompt via stdin, not argv. ihsanos has a 26KB CLAUDE.md
    # which pushed meta_prompt past whatever argv-length limit the CLI
    # internally enforces — the model fell back to "I need file access"
    # prose or hung until timeout. Stdin handles any size cleanly.
    # Why these flags:
    # --system-prompt <minimal>  — overrides the default system prompt, which
    #   triggers CLAUDE.md auto-discovery / plugin sync / hooks loading and
    #   was hanging ihsanos spec-gen at 300s (per CLI help: "ignored with
    #   --system-prompt"). Keychain auth still works (unlike --bare).
    # --dangerously-skip-permissions  — non-interactive mode has no human
    #   to answer permission prompts; without this the CLI waits forever.
    # Stdin instead of argv  — handles arbitrary prompt size cleanly.
    minimal_system_prompt = (
        "You are a senior software architect. Output exactly what the user "
        "asks for, formatted as requested. Do not request file access or "
        "tool permissions — your job is to produce the spec text."
    )
    proc = await asyncio.create_subprocess_exec(
        claude_bin,
        "--system-prompt", minimal_system_prompt,
        "--dangerously-skip-permissions",
        "-p", "-", "--output-format", "text",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=safe_env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=meta_prompt.encode("utf-8")),
            timeout=300,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("Spec generation timed out after 300s")
    result = stdout.decode(errors="replace").strip()

    if not result:
        raise RuntimeError(f"Spec generation failed: {stderr.decode(errors='replace')}")

    return result


def validate_spec(spec_text: str, job_id: int) -> tuple[bool, list[str]]:
    """Check that a generated spec has all required sections and promise tag."""
    errors = []
    lower = spec_text.lower()

    for heading in ["### role", "### task", "### acceptance criteria", "### files to touch"]:
        if heading not in lower:
            errors.append(f"Missing section: {heading.title()}")

    if "### implementation plan" not in lower and "### constraints" not in lower:
        errors.append("Missing section: ### Implementation Plan or ### Constraints")

    promise = f"<promise>JOB_{job_id}_DONE</promise>"
    if promise not in spec_text:
        errors.append(f"Missing promise tag: {promise}")

    return (len(errors) == 0, errors)


def _format_memory(memory: list[dict]) -> str:
    if not memory:
        return "(no repo memory)"
    return "\n".join(f"- {m['key']}: {m['value']}" for m in memory)
