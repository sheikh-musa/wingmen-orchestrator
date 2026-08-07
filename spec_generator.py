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
import re
import subprocess


CLAUDE_MD_CAP = 8000  # chars; large CLAUDE.md (ihsanos = 26KB) hung the CLI


def _truncate_for_prompt(text: str | None, cap: int) -> str:
    if not text:
        return "(empty)"
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n\n[...truncated {len(text) - cap} chars — agent can read full file at runtime...]"


def _grep_codebase(repo_path: str, pattern: str, max_results: int = 15) -> str:
    """Run ripgrep in the repo and return formatted results, or empty string on failure."""
    if not repo_path or not os.path.isdir(repo_path):
        return ""
    try:
        result = subprocess.run(
            ["rg", "-n", "--max-count=3", "--glob=*.ts", "--glob=*.tsx",
             "--glob=*.py", "--glob=*.js", "-l", pattern, repo_path],
            capture_output=True, text=True, timeout=10
        )
        files = result.stdout.strip().splitlines()[:max_results]
        if not files:
            return ""
        # For each file, get matching lines (up to 3 per file)
        lines = []
        for f in files:
            r2 = subprocess.run(
                ["rg", "-n", "--max-count=3", pattern, f],
                capture_output=True, text=True, timeout=5
            )
            rel = f.replace(repo_path + "/", "")
            for line in r2.stdout.strip().splitlines():
                lines.append(f"  {rel}: {line.strip()}")
        return "\n".join(lines)
    except Exception:
        return ""


def _extract_quoted_strings(text: str) -> list[str]:
    """Extract strings likely to be error messages or identifiers from description."""
    # Match content in quotes, backticks, or after "error:" / "message:"
    patterns = [
        r'["`\'](Failed[^"`\']{3,80})["`\']',
        r'["`\'](Error[^"`\']{3,80})["`\']',
        r'["`\'](Cannot[^"`\']{3,80})["`\']',
        r'["`\'](Unable[^"`\']{3,80})["`\']',
    ]
    found = []
    for p in patterns:
        found.extend(re.findall(p, text, re.IGNORECASE))
    return list(dict.fromkeys(found))[:5]  # dedupe, cap at 5


def _resolve_base_ref(repo_path: str) -> tuple[str, str]:
    """Pin the spec to the repo's current default-branch HEAD (CAI-RESP-358).

    Jobs 185 and 187 both shipped specs with no base ref — the executing agent
    branched from whatever was checked out, sometimes days stale. Every spec now
    carries an exact (branch, sha) resolved AT SPEC TIME; unresolvable = raise,
    never emit an unpinned spec.
    """
    import subprocess
    # Best-effort freshen; a fetch failure must not block pinning (offline dev),
    # the local origin/<branch> ref is still an honest pin.
    subprocess.run(["git", "-C", repo_path, "fetch", "origin", "--quiet"],
                   capture_output=True, timeout=30)
    head = subprocess.run(
        ["git", "-C", repo_path, "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True, timeout=10).stdout.strip()
    branch = head.rsplit("/", 1)[-1] if head else "main"
    sha = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", f"origin/{branch}"],
        capture_output=True, text=True, timeout=10).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError(
            f"cannot pin base ref for {repo_path} (origin/{branch} unresolvable) — "
            "refusing to emit an unpinned spec (CAI-RESP-358)")
    return branch, sha


def _base_ref_section(branch: str, sha: str) -> str:
    return f"""

### Base Ref (pinned — CAI-RESP-358)
Branch from EXACTLY this commit:

    git fetch origin && git checkout -b <your-job-branch> {sha}

Pinned at spec time: origin/{branch} @ `{sha}`.
If origin/{branch} has moved past this sha when you execute, re-verify the spec's
file assumptions against the new HEAD before proceeding — never silently rebase.
"""


def _build_grounding_section(job: dict, context: dict) -> str:
    """Pre-flight grep the repo to inject real file paths into the prompt.

    This prevents the spec generator from hallucinating file names it
    cannot verify (it has no filesystem access). We grep for:
    - The exact error strings mentioned in the description
    - Route/page paths from the session_prompt
    - The job description keywords
    """
    repo_path = context.get("repo_path") or context.get("repo_config", {}).get("path", "")
    if not repo_path:
        return "(pre-flight grep skipped — no repo_path in context)"

    description = job.get("description", "")
    session_prompt = job.get("session_prompt", "") or ""

    results = []

    # 1. Grep for exact error strings in the description
    for err_str in _extract_quoted_strings(description + " " + session_prompt):
        hits = _grep_codebase(repo_path, re.escape(err_str[:60]))
        if hits:
            results.append(f"Error string `{err_str[:60]}` found at:\n{hits}")

    # 2. Grep for page paths mentioned in session_prompt (e.g. /dashboard/settings)
    page_paths = re.findall(r'/(?:dashboard|super-admin|app|api)[/\w\-\[\]]+', session_prompt)
    for path in list(dict.fromkeys(page_paths))[:3]:
        # Convert URL path to likely filename fragment
        slug = path.strip("/").replace("/", "/").split("?")[0]
        hits = _grep_codebase(repo_path, re.escape(slug.split("/")[-1]))
        if hits:
            results.append(f"Page `{path}` — related files:\n{hits}")

    # 3. Grep for the core action verb from description (e.g. "save", "update", "delete")
    words = re.findall(r'\b(save|update|delete|create|submit|upload|import|export|login|sign|fetch|load)\b',
                       description, re.IGNORECASE)
    if words:
        verb = words[0].lower()
        hits = _grep_codebase(repo_path, f"action.*{verb}|{verb}.*action|{verb}.*Action")
        if hits:
            results.append(f"Actions matching `{verb}`:\n{hits}")

    if not results:
        return "(pre-flight grep found no matches — agent must grep at runtime)"

    return "\n\n".join(results)


async def generate_spec(job: dict, context: dict) -> str:
    """Use Claude CLI (Max subscription) to generate a structured build prompt."""
    repo_config = context["repo_config"]
    claude_md = _truncate_for_prompt(context.get('claude_md'), CLAUDE_MD_CAP)

    # Pre-flight grep: ground the spec in real file paths before asking Claude to write it.
    # The spec generator has NO filesystem access, so without this it hallucinates paths.
    grounding = _build_grounding_section(job, context)

    # CAI-RESP-358: pin the base ref BEFORE generating — unresolvable raises,
    # so an unpinned spec can never be emitted (jobs 185/187 proved the gap).
    repo_path = context.get("repo_path") or repo_config.get("path", "") or os.path.expanduser(
        repo_config.get("local_path", "").replace("~", "~"))
    base_branch, base_sha = _resolve_base_ref(os.path.expanduser(repo_path))

    # For bug reports, include the original session_prompt as the primary task anchor.
    # The spec generator must not replace it with a hallucinated implementation plan.
    original_prompt = job.get("session_prompt", "") or ""
    triggered_by = job.get("triggered_by", "")
    original_prompt_section = ""
    if triggered_by == "bug_report" and original_prompt.strip():
        original_prompt_section = f"""
## Original Bug Report Prompt (anchor — do not contradict)
The following was written by the bug reporter's pipeline with knowledge of the actual page.
Your spec MUST build on this, not replace it:

{_truncate_for_prompt(original_prompt, 2000)}
"""

    meta_prompt = f"""Produce a build specification document. Output ONLY the spec — no preamble, no questions, no requests for additional access. Everything you need is in the context below.

## CRITICAL: You have NO filesystem access.

You cannot read files, run commands, or verify paths. Therefore:
- **NEVER invent file paths** based on naming conventions or assumptions.
- Every file path in your spec MUST come from one of three sources:
  1. The "Pre-flight grep results" section below (real paths found by searching the repo)
  2. The "Original Bug Report Prompt" section below (written with real page knowledge)
  3. Explicit paths named in CLAUDE.md or STATUS.md
- If you cannot find the file via the grep results, write in the Implementation Plan:
  `Run: rg -n "<search term>" src/ to locate the file`
  Do NOT guess a path like `src/actions/some-name.ts` if it's not in the grep results.

## Project Context
- Repo: {repo_config['name']}
- GitHub: {repo_config['github']}
- Deploy URL: {repo_config.get('deploy_url', 'N/A')}
- Status: {repo_config['status']}

## Pre-flight grep results (VERIFIED real file paths from the actual codebase)
{grounding}

## Current STATUS.md
{context['status_md'] or '(no STATUS.md found)'}

## CLAUDE.md (project rules — the agent MUST follow these; full file readable at runtime if truncated)
{claude_md}

## Repo Memory
{_format_memory(context['memory'])}
{original_prompt_section}
## Task from User
{job['description']}

---

## Your job: Generate a build specification

Follow these rules strictly:

1. **SCOPE**: Keep changes minimal and surgical. Only modify files directly related to the task. Never refactor unrelated code, add unnecessary abstractions, or "improve" things that weren't asked for.

2. **GROUNDED FILE PATHS ONLY**: Use ONLY file paths from the "Pre-flight grep results" above. If a path is not there, tell the executing agent to grep for it — do not invent it.

3. **INVESTIGATE-FIRST for bugs**: For bug reports, the Implementation Plan step 1 must always be: grep for the exact error string, read the matched file, understand the data flow. Do NOT prescribe a fix before the agent has read the code.

4. **NO CLARIFYING QUESTIONS in the spec**: The executing agent must NEVER ask for more information. The spec must be self-contained. If information is missing, tell the agent how to find it at runtime (grep, read file, check DB schema).

5. **ACCEPTANCE CRITERIA**: List 3-5 specific, testable criteria. Each should be verifiable by looking at the UI or running a command. No vague criteria like "works correctly."

6. **NO EXTRAS**: Don't add error handling, comments, type annotations, or features beyond what was asked.

7. **MOBILE-FIRST**: If the project uses responsive design, ensure changes work on mobile viewports (375px).

8. **TESTS MOVE WITH CODE**: Before declaring done, grep `tests/` for every function you modified. Update stale test assertions in the same commit.

Output format:

### Role
One sentence: what this repo is.

### Task
The exact task, rephrased clearly.

### Constraints
Hard rules from CLAUDE.md that apply to this task.

### Implementation Plan
Step-by-step what the agent should do. Step 1 for bugs: grep for the error string, read the file, understand root cause BEFORE proposing a fix.

### Acceptance Criteria
Numbered list. Each criterion is testable.

### Files to Touch
Table: | File | Action | What changes |
Note: every file path here MUST be from the pre-flight grep results or original prompt. If unknown, write "TBD — agent must grep for: <pattern>"

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
        "tool permissions — your job is to produce the spec text. "
        "NEVER invent file paths — only use paths provided in the prompt."
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

    # CAI-RESP-358: append the pinned base ref DETERMINISTICALLY — never trust
    # the model to echo a sha. Resolved before the CLI call (raises if unpinnable).
    return result + _base_ref_section(base_branch, base_sha)


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

    # CAI-RESP-358: a spec without an exact pinned base sha is invalid, full stop.
    if "### base ref" not in lower:
        errors.append("Missing section: ### Base Ref (pinned — CAI-RESP-358)")
    elif not re.search(r"origin/\S+ @ `[0-9a-f]{40}`", spec_text):
        errors.append("Base Ref section present but no pinned 40-hex sha (CAI-RESP-358)")

    return (len(errors) == 0, errors)


def _format_memory(memory: list[dict]) -> str:
    if not memory:
        return "(no repo memory)"
    return "\n".join(f"- {m['key']}: {m['value']}" for m in memory)
