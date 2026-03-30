# Agent Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the CTO Bot's single Claude CLI call into 4 specialized agents (Router, Brainstorm, Auditor, Fixer) for better quality and smaller context windows.

**Architecture:** A new `_call_claude()` helper wraps all Claude CLI subprocess calls. `_process_message()` calls Router first to classify intent, then dispatches to the right specialist agent. Agent prompts live in `agents/` module. Auditor auto-chains to Fixer for high-confidence issues.

**Tech Stack:** Python 3.9, asyncio subprocess, Claude CLI (`~/.local/bin/claude`), pytest

**Spec:** `docs/superpowers/specs/2026-03-30-agent-split-design.md`

---

## File Structure

```
agents/
  __init__.py          — exports build_router_prompt, build_brainstorm_prompt, build_auditor_prompt, build_fixer_prompt
  router.py            — Router Agent prompt builder + JSON parser
  brainstorm.py        — Brainstorm Agent prompt builder (admin + client variants)
  auditor.py           — Auditor Agent prompt builder + issue JSON parser
  fixer.py             — Fixer Agent prompt builder
cto_bot.py             — Modified: _process_message, new _call_claude, new dispatch functions
tests/
  test_agents.py       — Unit tests for prompt builders + parsers
```

---

### Task 1: Create `_call_claude()` shared helper

Extract the Claude CLI subprocess logic into a reusable function so all 4 agents use the same call pattern.

**Files:**
- Modify: `cto_bot.py:2064-2115` (extract subprocess logic)
- Test: `tests/test_agents.py` (new file)

- [ ] **Step 1: Write the test for `_call_claude`**

Create `tests/test_agents.py`:

```python
"""Tests for agent dispatch and shared helpers."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import json

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.asyncio
async def test_call_claude_returns_stdout():
    """_call_claude returns stripped stdout from Claude CLI."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"  Hello world  ", b"")

    with patch("cto_bot.asyncio.create_subprocess_exec", return_value=mock_proc):
        from cto_bot import _call_claude
        result = await _call_claude("test prompt", tools="Read", timeout=30)
        assert result == "Hello world"


@pytest.mark.asyncio
async def test_call_claude_returns_empty_on_no_output():
    """_call_claude returns empty string when Claude produces no output."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"some error")

    with patch("cto_bot.asyncio.create_subprocess_exec", return_value=mock_proc):
        from cto_bot import _call_claude
        result = await _call_claude("test prompt", tools="Read", timeout=30)
        assert result == ""


@pytest.mark.asyncio
async def test_call_claude_timeout_kills_process():
    """_call_claude kills the process on timeout and returns empty string."""
    mock_proc = AsyncMock()
    mock_proc.communicate.side_effect = asyncio.TimeoutError()
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()

    with patch("cto_bot.asyncio.create_subprocess_exec", return_value=mock_proc):
        from cto_bot import _call_claude
        result = await _call_claude("test prompt", tools="Read", timeout=1)
        assert result == ""
        mock_proc.kill.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py -v`
Expected: FAIL — `_call_claude` not defined

- [ ] **Step 3: Implement `_call_claude` in `cto_bot.py`**

Add this function before `_process_message` (around line 1995):

```python
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
CLAUDE_ENV = {
    "HOME": os.path.expanduser("~"),
    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    "USER": os.environ.get("USER", ""),
    "SHELL": os.environ.get("SHELL", ""),
    "LANG": os.environ.get("LANG", ""),
}


async def _call_claude(prompt: str, *, tools: str = "", timeout: int = 300) -> str:
    """Call Claude CLI and return the text response. Returns empty string on failure."""
    args = [CLAUDE_BIN, "-p", prompt, "--output-format", "text"]
    if tools:
        args += ["--allowedTools", tools, "--permission-mode", "bypassPermissions"]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=CLAUDE_ENV,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.error(f"Claude CLI timed out after {timeout}s")
        return ""

    result = stdout.decode(errors="replace").strip()
    if not result:
        logger.error(f"Claude CLI empty output: {stderr.decode(errors='replace')[:200]}")
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add cto_bot.py tests/test_agents.py
git commit -m "feat: add _call_claude shared helper for agent dispatch"
```

---

### Task 2: Create Router Agent

Build the router that classifies messages into intents.

**Files:**
- Create: `agents/__init__.py`
- Create: `agents/router.py`
- Test: `tests/test_agents.py` (append)

- [ ] **Step 1: Write the router tests**

Append to `tests/test_agents.py`:

```python
from agents.router import build_router_prompt, parse_router_response


def test_build_router_prompt_includes_repos():
    repos = ["ihsandms", "dookana"]
    history = [
        {"role": "user", "content": "check the pages"},
        {"role": "assistant", "content": "Which repo?"},
    ]
    prompt = build_router_prompt("fix the homepage", repos, history)
    assert "ihsandms" in prompt
    assert "dookana" in prompt
    assert "fix the homepage" in prompt
    # Should include last 2 history messages for follow-up context
    assert "check the pages" in prompt


def test_parse_router_response_valid_json():
    raw = '{"intent": "audit", "repo": "ihsandms", "detail": "check pages"}'
    result = parse_router_response(raw)
    assert result["intent"] == "audit"
    assert result["repo"] == "ihsandms"


def test_parse_router_response_extracts_json_from_text():
    raw = 'Here is my analysis:\n{"intent": "chat", "repo": "dookana", "detail": "brainstorm"}\nDone.'
    result = parse_router_response(raw)
    assert result["intent"] == "chat"


def test_parse_router_response_fallback_on_garbage():
    raw = "I'm not sure what you mean"
    result = parse_router_response(raw)
    assert result["intent"] == "chat"
    assert result["repo"] is None


def test_parse_router_response_fallback_on_invalid_intent():
    raw = '{"intent": "destroy", "repo": "ihsandms", "detail": "nuke it"}'
    result = parse_router_response(raw)
    assert result["intent"] == "chat"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py::test_build_router_prompt_includes_repos -v`
Expected: FAIL — `agents.router` module not found

- [ ] **Step 3: Create `agents/__init__.py`**

```python
from agents.router import build_router_prompt, parse_router_response
from agents.brainstorm import build_brainstorm_prompt
from agents.auditor import build_auditor_prompt, parse_auditor_response
from agents.fixer import build_fixer_prompt
```

Note: This will fail to import until all modules exist. That's fine — we'll create them in order. For now, create a minimal version:

```python
# agents/__init__.py — populated as agents are added
```

- [ ] **Step 4: Create `agents/router.py`**

```python
"""Router Agent — classifies user messages into intents."""

from __future__ import annotations

import json
import re

VALID_INTENTS = {"chat", "audit", "fix", "build", "data"}


def build_router_prompt(user_msg: str, repos: list[str], history: list[dict]) -> str:
    """Build the Router Agent prompt with minimal context."""
    repo_list = ", ".join(repos)

    # Include last 2 messages for follow-up context ("yes do it", "proceed", etc.)
    context_lines = ""
    recent = history[-3:]  # last 2 + current (current is already in user_msg)
    for msg in recent[:-1]:  # exclude the current message
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"][:200]
        context_lines += f"{role}: {content}\n"

    return f"""You are a message classifier. Given a user message and recent context, return a JSON object.

Intents:
- "chat": questions, brainstorming, discussion, status checks, planning, follow-ups to conversation
- "audit": requests to check, crawl, verify, test, or review live pages or code quality
- "fix": explicit request to fix a specific known issue (e.g. "fix the duplicate card on homepage")
- "build": request to create a new feature or page (will go through build pipeline)
- "data": request to update data (prices, text, toggles) in the database

Repos: {repo_list}

Recent conversation:
{context_lines}
Current message: {user_msg}

Return ONLY valid JSON: {{"intent": "...", "repo": "...", "detail": "..."}}
"repo" should be null if no specific repo is mentioned or inferrable.
"detail" is a brief summary of what the user wants."""


def parse_router_response(raw: str) -> dict:
    """Parse router JSON output, with fallback to chat intent."""
    # Try to extract JSON from the response
    try:
        # Direct parse
        result = json.loads(raw.strip())
    except json.JSONDecodeError:
        # Try to find JSON in the text
        match = re.search(r'\{[^}]+\}', raw)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                return {"intent": "chat", "repo": None, "detail": ""}
        else:
            return {"intent": "chat", "repo": None, "detail": ""}

    # Validate intent
    if result.get("intent") not in VALID_INTENTS:
        result["intent"] = "chat"

    # Ensure all keys exist
    result.setdefault("repo", None)
    result.setdefault("detail", "")

    return result
```

- [ ] **Step 5: Run router tests to verify they pass**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py -k "router" -v`
Expected: 5 PASSED

- [ ] **Step 6: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add agents/__init__.py agents/router.py tests/test_agents.py
git commit -m "feat: add Router Agent with intent classification"
```

---

### Task 3: Create Brainstorm Agent prompt builder

Extract and clean up the existing system prompt into a dedicated module.

**Files:**
- Create: `agents/brainstorm.py`
- Test: `tests/test_agents.py` (append)

- [ ] **Step 1: Write brainstorm prompt tests**

Append to `tests/test_agents.py`:

```python
from agents.brainstorm import build_brainstorm_prompt


def test_brainstorm_prompt_admin():
    user = {"name": "Musa", "repos": ["ihsandms", "dookana"], "role": "admin"}
    prompt = build_brainstorm_prompt(
        user=user,
        repo_context="--- PROJECT RULES ---\nUse Tailwind",
        history=[{"role": "user", "content": "build a new page"}],
        user_msg="build a new page",
    )
    assert "CTO" in prompt or "architect" in prompt
    assert "ACTION:BUILD" in prompt
    assert "ACTION:DATA" in prompt
    assert "ihsandms" in prompt
    # Should NOT contain audit/deploy instructions
    assert "crawl" not in prompt.lower()
    assert "npx vercel" not in prompt


def test_brainstorm_prompt_client():
    user = {"name": "Ahmad", "repos": ["dookana"], "role": "client"}
    prompt = build_brainstorm_prompt(
        user=user,
        repo_context="",
        history=[],
        user_msg="update price",
    )
    assert "advisor" in prompt.lower() or "partner" in prompt.lower()
    assert "Ahmad" in prompt
    assert "ACTION:DATA" in prompt
    # Clients should not see technical jargon
    assert "git" not in prompt
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py -k "brainstorm" -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create `agents/brainstorm.py`**

Extract the admin and client prompts from `cto_bot.py:1669-1813` into this module. Strip out deploy instructions and audit references.

```python
"""Brainstorm Agent — conversational CTO (admin) or product advisor (client)."""

from __future__ import annotations


def build_brainstorm_prompt(
    *,
    user: dict,
    repo_context: str,
    history: list[dict],
    user_msg: str,
) -> str:
    """Build the Brainstorm Agent prompt with conversation history and repo context."""

    if user.get("role") == "admin":
        persona = _admin_persona(user)
    else:
        persona = _client_persona(user)

    # Build conversation
    conv_parts = [f"SYSTEM:\n{persona}\n{repo_context}\n"]
    recent = history[-10:]
    for msg in recent:
        role_label = "USER" if msg["role"] == "user" else "ASSISTANT"
        content = msg["content"] if msg["role"] == "user" else msg["content"][:500]
        conv_parts.append(f"{role_label}:\n{content}\n")

    prompt = "\n---\n".join(conv_parts)
    prompt += "\n---\nRespond as ASSISTANT. Keep it concise (Telegram message, max 3 paragraphs)."
    return prompt


def _admin_persona(user: dict) -> str:
    return f"""You are a senior CTO and software architect working with Musa at Wingmen. You think like a principal engineer — opinionated, decisive, and practical. Keep responses concise (Telegram).

## HOW YOU THINK

1. SIMPLEST SOLUTION FIRST: Always propose the simplest approach that solves the problem. No premature abstractions, no over-engineering.

2. EXISTING PATTERNS OVER NEW ONES: Before suggesting anything, consider what already exists in the codebase. Match existing naming, file structure, component patterns, and styling.

3. PRODUCT THINKING: Don't just build what's asked — think about WHY it's being asked. Consider the end user, the business context, the demo timeline.

4. ARCHITECTURAL OPINIONS: Have strong opinions, loosely held. Give a clear recommendation with reasoning, not a list of pros/cons.

5. SCOPE RUTHLESSLY: For demos, build the minimum that tells the story. For production, build the minimum that ships.

## CLARIFICATION — MANDATORY

When Musa gives notes, requirements, or vague requests:
- Do NOT immediately generate build specs
- Ask 2-3 sharp clarifying questions about intent, scope, and priority
- Then give YOUR recommendation with reasoning
- If Musa says "you decide", make the call and state it clearly

## TASK SIZING — MANDATORY

Large tasks MUST be broken into smaller, focused jobs:
- Split by page/component/feature — one job per logical unit
- Each job should touch 3-5 files maximum
- Jobs run sequentially within the same repo

## BUILDING

When intent is clear and Musa confirms:
- Include exactly ONE [ACTION:BUILD] block per message — never multiple
- After queuing it, say "Queued. Ready for the next one?" and wait
- Be specific: name exact files, components, functions

[ACTION:BUILD] surgical description — exact files, exact changes, exact acceptance criteria [/ACTION]

For data changes: [ACTION:DATA] TABLE/OP/DATA/WHERE format [/ACTION]

For infrastructure: [ACTION:CONFIG] description of setup needed [/ACTION]

NEVER include action blocks without Musa confirming first.

## COMMANDS
/screenshots, /preview, /undo, /digest, /mu, /jobs, /status

Projects: {', '.join(user['repos'])}
"""


def _client_persona(user: dict) -> str:
    return f"""You are {user['name']}'s dedicated project advisor from Wingmen. You're a smart, experienced product person who happens to have an AI dev team behind you.

## YOUR PERSONALITY
- Warm, professional, proactive. Like a trusted business partner who happens to know tech.
- Never use jargon (no "API", "component", "deploy", "responsive"). Use plain language.
- Be opinionated — don't ask "what do you prefer?" when you know the right answer.
- Anticipate needs.
- Celebrate wins.

## HOW YOU THINK
- Their time is valuable. Minimize back-and-forth.
- Most requests are simple (update text, change price, fix a typo). Handle these instantly with DATA actions.
- When something requires building, explain in their terms: "I'll get our dev team to add that. It usually takes about 10 minutes."

## HANDLING REQUESTS

1. LISTEN & CLARIFY: Ask 1-2 essential questions. Don't over-clarify obvious requests.
2. RECOMMEND: Give your opinion. Don't present options — present a recommendation.
3. CONFIRM: Brief summary + "Should I go ahead?"
4. EXECUTE: Only after they confirm.

## ACTION FORMAT (user never sees these blocks)

[ACTION:DATA]
TABLE: products
OP: update
WHERE: id=42
DATA: {{"price": 6.00}}
[/ACTION]

[ACTION:CONFIG] description of setup needed [/ACTION]

[ACTION:BUILD] detailed technical spec for dev agent [/ACTION]

## RULES
- NEVER include action blocks on the first message about a topic
- Text before action blocks = what user sees (friendly, non-technical)
- Text inside action blocks = for dev agent (technical, specific)
- Prefer DATA over BUILD — most things are data changes

{user['name']}'s project{'s' if len(user['repos']) > 1 else ''}: {', '.join(user['repos'])}
"""
```

- [ ] **Step 4: Run brainstorm tests**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py -k "brainstorm" -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add agents/brainstorm.py tests/test_agents.py
git commit -m "feat: add Brainstorm Agent prompt builder"
```

---

### Task 4: Create Auditor Agent

Build the read-only auditor that crawls pages and produces structured issue reports.

**Files:**
- Create: `agents/auditor.py`
- Test: `tests/test_agents.py` (append)

- [ ] **Step 1: Write auditor tests**

Append to `tests/test_agents.py`:

```python
from agents.auditor import build_auditor_prompt, parse_auditor_response


def test_auditor_prompt_includes_deploy_url():
    prompt = build_auditor_prompt(
        deploy_url="https://ihsandms.vercel.app",
        repo_path="/Users/sheikhmusa/wingmen/projects/ihsandms",
        file_tree="app/page.tsx\napp/admin/page.tsx",
        detail="check all pages work",
    )
    assert "https://ihsandms.vercel.app" in prompt
    assert "app/page.tsx" in prompt
    assert "fix_confidence" in prompt
    # Should NOT contain conversation or persona
    assert "CTO" not in prompt
    assert "ACTION:BUILD" not in prompt


def test_parse_auditor_response_valid_json():
    raw = """Here's my audit:
```json
[{"page": "/", "severity": "high", "description": "Duplicate cards", "fix_confidence": "high", "file_path": "app/page.tsx", "suggested_fix": "Remove duplicate"}]
```
Summary: 1 issue found."""
    issues, summary = parse_auditor_response(raw)
    assert len(issues) == 1
    assert issues[0]["severity"] == "high"
    assert issues[0]["fix_confidence"] == "high"
    assert "1 issue" in summary.lower() or len(summary) > 0


def test_parse_auditor_response_no_json():
    raw = "I couldn't access the site, it seems to be down."
    issues, summary = parse_auditor_response(raw)
    assert issues == []
    assert len(summary) > 0


def test_parse_auditor_response_filters_high_confidence():
    raw = """```json
[
  {"page": "/", "severity": "high", "description": "Dup cards", "fix_confidence": "high", "file_path": "app/page.tsx", "suggested_fix": "Remove dup"},
  {"page": "/admin", "severity": "low", "description": "SSL issue", "fix_confidence": "low", "file_path": null, "suggested_fix": "Check Cloudflare"}
]
```
Found 2 issues."""
    issues, summary = parse_auditor_response(raw)
    high = [i for i in issues if i["fix_confidence"] == "high"]
    low = [i for i in issues if i["fix_confidence"] == "low"]
    assert len(high) == 1
    assert len(low) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py -k "auditor" -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create `agents/auditor.py`**

```python
"""Auditor Agent — crawls live pages and reports issues as structured JSON."""

from __future__ import annotations

import json
import re


def build_auditor_prompt(
    *,
    deploy_url: str,
    repo_path: str,
    file_tree: str,
    detail: str,
) -> str:
    """Build the Auditor Agent prompt. Read-only — no edit instructions."""
    return f"""You are a QA auditor for a web application. Your job:
1. Use WebFetch to crawl every route on the live site
2. Check each page loads without errors (look for error text, blank pages, broken layouts)
3. Use Read/Grep to inspect source code for issues (broken imports, placeholder text, inconsistent styling)
4. Report ALL findings as a JSON array

Live site: {deploy_url}
Repo path: {repo_path}
User request: {detail}

Known routes (from file tree):
{file_tree}

For each issue, output this JSON format inside a ```json code block:
[
  {{
    "page": "/admin/donors",
    "severity": "high" | "medium" | "low",
    "description": "Duplicate navigation cards both linking to /my",
    "fix_confidence": "high" | "medium" | "low",
    "file_path": "app/page.tsx",
    "suggested_fix": "Remove the duplicate My Portal card, keep Donor Portal"
  }}
]

Confidence levels:
- "high" = obvious fix, single file, no ambiguity (will be auto-fixed)
- "medium" = likely fix but needs verification
- "low" = needs human decision (infrastructure, design choice, etc.)

Rules:
- Do NOT edit any files. Only report.
- Crawl ALL routes, not just a sample.
- If a page returns an error or blank content, that's a "high" severity issue.
- After the JSON block, write a brief human-readable summary.
"""


def parse_auditor_response(raw: str) -> tuple[list[dict], str]:
    """Parse auditor output into (issues_list, human_summary).

    Returns ([], raw_text) if no valid JSON found.
    """
    # Try to extract JSON array from code block
    json_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', raw)
    if not json_match:
        # Try bare JSON array
        json_match = re.search(r'(\[\s*\{[\s\S]*?\}\s*\])', raw)

    issues = []
    if json_match:
        try:
            issues = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Everything outside the JSON block is the summary
    if json_match:
        summary = raw[:json_match.start()].strip() + "\n" + raw[json_match.end():].strip()
        summary = summary.strip()
    else:
        summary = raw.strip()

    if not summary:
        summary = f"Audit complete. {len(issues)} issue(s) found."

    return issues, summary
```

- [ ] **Step 4: Run auditor tests**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py -k "auditor" -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add agents/auditor.py tests/test_agents.py
git commit -m "feat: add Auditor Agent with structured issue reporting"
```

---

### Task 5: Create Fixer Agent

Build the surgical fixer that takes a single issue and edits one file.

**Files:**
- Create: `agents/fixer.py`
- Test: `tests/test_agents.py` (append)

- [ ] **Step 1: Write fixer tests**

Append to `tests/test_agents.py`:

```python
from agents.fixer import build_fixer_prompt


def test_fixer_prompt_includes_issue_details():
    issue = {
        "page": "/",
        "severity": "high",
        "description": "Duplicate cards both linking to /my",
        "fix_confidence": "high",
        "file_path": "app/page.tsx",
        "suggested_fix": "Remove the duplicate My Portal card",
    }
    prompt = build_fixer_prompt(
        issue=issue,
        repo_path="/Users/sheikhmusa/wingmen/projects/ihsandms",
    )
    assert "app/page.tsx" in prompt
    assert "Duplicate cards" in prompt
    assert "Remove the duplicate" in prompt
    assert "/Users/sheikhmusa/wingmen/projects/ihsandms" in prompt
    # Should NOT contain audit or brainstorm instructions
    assert "crawl" not in prompt.lower()
    assert "ACTION:BUILD" not in prompt
    # Should NOT push (batch push handled by Python)
    assert "git push" not in prompt


def test_fixer_prompt_minimal_context():
    issue = {
        "description": "Fix typo",
        "file_path": "app/admin/page.tsx",
        "suggested_fix": "Change 'Donr' to 'Donor'",
    }
    prompt = build_fixer_prompt(
        issue=issue,
        repo_path="/tmp/test",
    )
    # Should be short — no conversation history, no file tree
    assert len(prompt) < 1500
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py -k "fixer" -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create `agents/fixer.py`**

```python
"""Fixer Agent — surgical single-file code fixer."""

from __future__ import annotations


def build_fixer_prompt(*, issue: dict, repo_path: str) -> str:
    """Build the Fixer Agent prompt for a single issue."""
    file_path = issue.get("file_path", "unknown")
    description = issue.get("description", "")
    suggested_fix = issue.get("suggested_fix", "")

    return f"""You are a surgical code fixer. Fix exactly ONE issue in ONE file.

Issue: {description}
File: {repo_path}/{file_path}
Suggested fix: {suggested_fix}

Steps:
1. Read the file at {repo_path}/{file_path}
2. Make the minimal edit to fix the issue
3. Run: cd {repo_path} && git add {file_path} && git commit -m "fix: {description[:60]}"
4. Report what you changed in one sentence

Rules:
- Change ONLY what's needed. No refactoring, no style changes, no "while I'm here" improvements.
- If the fix is unclear or would require changing multiple files, respond with "SKIP: <reason>" instead of editing.
- Do NOT run git push — that's handled separately.
- Do NOT run any deploy commands.
"""
```

- [ ] **Step 4: Run fixer tests**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py -k "fixer" -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add agents/fixer.py tests/test_agents.py
git commit -m "feat: add Fixer Agent for surgical single-file fixes"
```

---

### Task 6: Update `agents/__init__.py` with all exports

**Files:**
- Modify: `agents/__init__.py`

- [ ] **Step 1: Update exports**

```python
"""Agent prompt builders and response parsers."""

from agents.router import build_router_prompt, parse_router_response
from agents.brainstorm import build_brainstorm_prompt
from agents.auditor import build_auditor_prompt, parse_auditor_response
from agents.fixer import build_fixer_prompt

__all__ = [
    "build_router_prompt",
    "parse_router_response",
    "build_brainstorm_prompt",
    "build_auditor_prompt",
    "parse_auditor_response",
    "build_fixer_prompt",
]
```

- [ ] **Step 2: Verify all imports work**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -c "from agents import *; print('All agents imported OK')"`
Expected: `All agents imported OK`

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py -v`
Expected: All 16 tests PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add agents/__init__.py
git commit -m "feat: export all agent builders from agents package"
```

---

### Task 7: Rewire `_process_message()` to use Router → Dispatch

This is the core change. Replace the single Claude CLI call with: Router classifies → dispatch to specialist.

**Files:**
- Modify: `cto_bot.py:2001-2149` (`_process_message`)
- Modify: `cto_bot.py` (add `_route_message`, `_run_brainstorm`, `_run_audit`, `_run_fix`)
- Modify: `cto_bot.py` (add import for agents module)

- [ ] **Step 1: Add agents import to cto_bot.py**

At the top of `cto_bot.py`, after the existing imports (around line 31), add:

```python
from agents.router import build_router_prompt, parse_router_response
from agents.brainstorm import build_brainstorm_prompt
from agents.auditor import build_auditor_prompt, parse_auditor_response
from agents.fixer import build_fixer_prompt
import context_loader
```

- [ ] **Step 2: Add `_route_message` function**

Add after `_call_claude` (before `_process_message`):

```python
async def _route_message(user_msg: str, user: dict, history: list[dict]) -> dict:
    """Use Router Agent to classify message intent. Falls back to 'chat' on failure."""
    prompt = build_router_prompt(user_msg, user["repos"], history)
    raw = await _call_claude(prompt, timeout=30)
    if not raw:
        return {"intent": "chat", "repo": None, "detail": user_msg}
    result = parse_router_response(raw)
    logger.info(f"Router: {user['name']} -> {result['intent']} (repo={result.get('repo')})")
    return result
```

- [ ] **Step 3: Add `_run_brainstorm` function**

Add after `_route_message`:

```python
async def _run_brainstorm(update: Update, user: dict, chat_id: str, history: list[dict], user_msg: str) -> str:
    """Run the Brainstorm Agent and return the reply text."""
    repo_name = get_active_repo(chat_id, user)
    repo_context = ""
    if repo_name:
        repo_context = await _load_repo_context_block(repo_name)

    prompt = build_brainstorm_prompt(
        user=user,
        repo_context=repo_context,
        history=history,
        user_msg=user_msg,
    )

    reply = await _call_claude(prompt, tools="Read,Glob,Grep", timeout=300)
    if not reply:
        return "I had trouble processing that. Please try again."

    # Parse action blocks (DATA/CONFIG/BUILD)
    reply = await _parse_and_execute_actions(reply, user, chat_id)
    return reply
```

- [ ] **Step 4: Add `_run_audit` function**

Add after `_run_brainstorm`:

```python
async def _run_audit(update: Update, user: dict, chat_id: str, repo_name: str, detail: str) -> str:
    """Run Auditor Agent, then auto-chain Fixer for high-confidence issues."""
    try:
        config = context_loader.get_repo_config(repo_name)
    except ValueError:
        return f"I don't have a repo called '{repo_name}' configured."

    deploy_url = config.get("deploy_url", "")
    repo_path = os.path.expanduser(config.get("local_path", ""))

    if not deploy_url:
        return f"No deploy URL configured for {repo_name}. Can't crawl pages without it."

    # Get file tree for route list
    ctx_block = await _load_repo_context_block(repo_name)
    # Extract just the FILES section from the context block
    file_tree = ""
    if "--- FILES" in ctx_block:
        start = ctx_block.index("--- FILES")
        # Find next section or end
        rest = ctx_block[start + 10:]
        end_offset = rest.find("\n---")
        if end_offset >= 0:
            file_tree = ctx_block[start:start + 10 + end_offset].strip()
        else:
            file_tree = ctx_block[start:].strip()

    await update.message.reply_text(f"Auditing {repo_name} — crawling all pages. This will take a few minutes...")

    prompt = build_auditor_prompt(
        deploy_url=deploy_url,
        repo_path=repo_path,
        file_tree=file_tree,
        detail=detail,
    )

    raw = await _call_claude(prompt, tools="WebFetch,WebSearch,Read,Glob,Grep,Bash", timeout=600)
    if not raw:
        return "Audit failed — Claude didn't return results. Try again?"

    issues, summary = parse_auditor_response(raw)

    # Auto-fix high-confidence issues
    high_conf = [i for i in issues if i.get("fix_confidence") == "high"]
    fix_results = []

    if high_conf:
        await update.message.reply_text(f"Found {len(issues)} issue(s). Auto-fixing {len(high_conf)} obvious one(s)...")

        for issue in high_conf:
            fix_reply = await _run_fix(repo_name, issue)
            fix_results.append(f"- {issue['description']}: {fix_reply}")

        # Batch push + deploy after all fixes
        if fix_results:
            push_result = await _call_claude(
                f"Run these commands:\ncd {repo_path} && git push origin main 2>&1 | tail -3",
                tools="Bash",
                timeout=60,
            )
            logger.info(f"Batch push for {repo_name}: {push_result[:100]}")

    # Compose final message
    needs_decision = [i for i in issues if i.get("fix_confidence") != "high"]
    parts = [summary]

    if fix_results:
        parts.append("\n**Auto-fixed:**")
        parts.extend(fix_results)

    if needs_decision:
        parts.append("\n**Needs your decision:**")
        for i in needs_decision:
            parts.append(f"- [{i.get('severity', '?')}] {i['description']} ({i.get('suggested_fix', 'no suggestion')})")

    return "\n".join(parts)
```

- [ ] **Step 5: Add `_run_fix` function**

Add after `_run_audit`:

```python
async def _run_fix(repo_name: str, issue: dict) -> str:
    """Run Fixer Agent for a single issue. Returns a one-line result."""
    try:
        config = context_loader.get_repo_config(repo_name)
    except ValueError:
        return "SKIP: repo not found"

    repo_path = os.path.expanduser(config.get("local_path", ""))

    prompt = build_fixer_prompt(issue=issue, repo_path=repo_path)
    result = await _call_claude(prompt, tools="Read,Edit,Write,Bash", timeout=120)

    if not result:
        return "SKIP: fixer returned no output"
    if result.strip().startswith("SKIP:"):
        return result.strip()
    return result.split("\n")[0][:200]  # First line, capped
```

- [ ] **Step 6: Rewrite `_process_message` to use the dispatch**

Replace the body of `_process_message` (lines 2001-2149) with:

```python
async def _process_message(update: Update, user: dict, chat_id: str, user_msg: str):
    """Core chat logic — routes to specialized agents via Router."""

    if len(user_msg) > MAX_MSG_LENGTH:
        await update.message.reply_text(f"Message too long (max {MAX_MSG_LENGTH} chars). Please shorten it.")
        return

    # Check usage limits
    limit_msg = await check_usage_limit(user, "chat")
    if limit_msg:
        await update.message.reply_text(limit_msg)
        return

    # Auto-detect repo from message or recent history
    if not get_active_repo(chat_id, user):
        for repo in user["repos"]:
            if repo.lower() in user_msg.lower():
                _active_repo[chat_id] = repo
                break
        if not get_active_repo(chat_id, user) and len(user["repos"]) == 1:
            _active_repo[chat_id] = user["repos"][0]
        if not get_active_repo(chat_id, user):
            inferred = await _load_active_repo(chat_id)
            if inferred and inferred in user["repos"]:
                _active_repo[chat_id] = inferred

    # If message contains a URL, auto-screenshot and enhance context
    url_enhanced = await handle_url_in_message(update, user, chat_id, user_msg)
    if url_enhanced:
        user_msg = url_enhanced

    # Load persisted history
    history = await get_history(chat_id)
    history.append({"role": "user", "content": user_msg})
    if len(history) > 20:
        history = history[-20:]

    # Persist user message
    await save_message(chat_id, "user", user_msg)

    async with _chat_semaphore:
        start = time.monotonic()
        try:
            await update.message.chat.send_action("typing")

            # Step 1: Route the message
            route = await _route_message(user_msg, user, history)
            intent = route["intent"]

            # Override repo if router detected one
            if route.get("repo") and route["repo"] in user["repos"]:
                _active_repo[chat_id] = route["repo"]

            # Step 2: Dispatch to specialist agent
            # Start typing indicator for long-running agents
            async def _keep_typing():
                notified = False
                try:
                    elapsed = 0
                    while True:
                        await asyncio.sleep(8)
                        elapsed += 8
                        await update.message.chat.send_action("typing")
                        if not notified and elapsed >= 30:
                            notified = True
                            try:
                                await update.message.reply_text("Working on it — using tools to check things. Hang tight...")
                            except Exception:
                                pass
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

            typing_task = asyncio.create_task(_keep_typing())
            try:
                if intent == "audit":
                    repo = get_active_repo(chat_id, user) or route.get("repo")
                    if not repo:
                        reply = "Which project should I audit? Use /repo to set it."
                    else:
                        reply = await _run_audit(update, user, chat_id, repo, route.get("detail", user_msg))

                elif intent == "fix":
                    repo = get_active_repo(chat_id, user) or route.get("repo")
                    if not repo:
                        reply = "Which project? Use /repo to set it."
                    else:
                        issue = {
                            "description": route.get("detail", user_msg),
                            "file_path": "",
                            "suggested_fix": route.get("detail", ""),
                        }
                        reply = await _run_fix(repo, issue)

                else:
                    # "chat", "build", "data" all go through brainstorm
                    reply = await _run_brainstorm(update, user, chat_id, history, user_msg)
            finally:
                typing_task.cancel()

            duration = time.monotonic() - start

            # Persist assistant reply
            await save_message(chat_id, "assistant", reply)

            # Log usage
            repo = get_active_repo(chat_id, user) or ""
            await log_usage(user.get("client_id"), "chat", repo, 0, duration)

            if len(reply) > 4000:
                reply = reply[:4000] + "\n...(truncated)"

            await update.message.reply_text(reply)

        except asyncio.TimeoutError:
            logger.error(f"Chat timeout for {user['name']}")
            await update.message.reply_text("That took too long. Please try a shorter message.")

        except Exception as e:
            logger.error(f"Chat error for {user['name']}: {e}")
            try:
                await update.message.reply_text("Sorry, something went wrong. Please try again.")
            except Exception:
                logger.error(f"Failed to send error reply to {user['name']}")
```

- [ ] **Step 7: Verify syntax**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python3 -m py_compile cto_bot.py`
Expected: No output (clean compile)

- [ ] **Step 8: Run all tests**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 9: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add cto_bot.py
git commit -m "feat: rewire _process_message with Router -> Agent dispatch"
```

---

### Task 8: Restart bot and smoke test

**Files:** None — operational verification.

- [ ] **Step 1: Restart the CTO Bot**

```bash
launchctl unload ~/Library/LaunchAgents/dev.wingmen.ctobot.plist && sleep 2 && launchctl load ~/Library/LaunchAgents/dev.wingmen.ctobot.plist
```

- [ ] **Step 2: Verify bot started**

```bash
sleep 8 && tail -5 /Users/sheikhmusa/wingmen/orchestrator/logs/cto_bot.log
```

Expected: "Wingmen CTO Bot starting (long-polling mode)..." and "Whisper model pre-loaded"

- [ ] **Step 3: Check Supabase for router logs after sending a test message**

After sending a message via Telegram, check:
```bash
tail -20 /Users/sheikhmusa/wingmen/orchestrator/logs/cto_bot.log | grep "Router:"
```

Expected: A log line like `Router: Musa -> chat (repo=ihsandms)`

- [ ] **Step 4: Commit any fixes**

If there were issues, fix and commit:
```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add -A && git commit -m "fix: smoke test fixes for agent dispatch"
```
