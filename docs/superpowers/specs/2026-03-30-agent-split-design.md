# CTO Bot Agent Split — Design Spec

## Problem

The CTO Bot currently runs a single Claude CLI `-p` call for every message. This one session acts as router, brainstormer, auditor, code editor, and deployer simultaneously. The result:

- **Bloated context:** System prompt (~2KB) + repo context (~4KB) + job history (~1KB) + conversation (~5KB) + action format docs + deploy instructions — before any tool output (WebFetch HTML, file reads) even enters the window.
- **Quality degradation:** By the time Claude is deciding what to edit in a file, the useful signal is buried under pages of crawl results and conversation history.
- **Timeout risk:** Complex tasks (crawl 25 pages + fix issues) push against the 600s timeout.

## Solution

Split the single Claude CLI call into 4 specialized agents, each with a focused context window and tool set. A lightweight Router Agent classifies every message first, then dispatches to the right specialist.

## Architecture

```
Telegram Message
    |
[CTO Bot Python process — unchanged]
    |
[Router Agent] — classify intent (~3s)
    |
    |-- "chat" / "build" / "data" --> [Brainstorm Agent]
    |                                     |
    |                                     |- conversational reply
    |                                     |- or ACTION:BUILD --> orchestrator
    |                                     |- or ACTION:DATA --> instant Supabase
    |                                     |- or ACTION:CONFIG --> manual queue
    |
    |-- "audit" -----------------------> [Auditor Agent]
    |                                     |
    |                                     |- crawl pages, read code
    |                                     |- produce issue report (JSON)
    |                                     |
    |                                     |- high-confidence fixes --> [Fixer Agent] (auto-chained)
    |                                     |- ambiguous issues --> back to Telegram for user decision
    |
    |-- "fix" -------------------------> [Fixer Agent] (direct, for explicit fix requests)
```

## Agent Specifications

### Router Agent

**Purpose:** Classify user intent and extract target repo. Nothing else.

**Tools:** None — pure text classification.

**System prompt (~500 bytes):**
```
You are a message classifier for a software development bot. Given a user message
and a list of repos, return a JSON object with the intent and target.

Intents:
- "chat": questions, brainstorming, discussion, status checks, planning
- "audit": requests to check, crawl, verify, test, or review live pages or code quality
- "fix": explicit request to fix a specific known issue
- "build": request to create a new feature or page (will go through build pipeline)
- "data": request to update data (prices, text, toggles) in the database

Repos: {repo_list}

Return ONLY valid JSON: {"intent": "...", "repo": "...", "detail": "..."}
```

**Input:** User's latest message + last 2 conversation messages (for follow-up context like "yes do it") + repo list.

**Output:** `{"intent": "audit", "repo": "ihsandms", "detail": "check all pages work and fix weird UI"}`

**Timeout:** 30s. If it fails, fall back to "chat" intent (brainstorm agent handles everything as before).

### Brainstorm Agent

**Purpose:** Conversational CTO (admin) or product advisor (client). Brainstorms, clarifies requirements, proposes approaches. Emits ACTION blocks when the user confirms.

**Tools:** `Read, Glob, Grep` — can inspect code for context, but cannot edit files or browse the web.

**System prompt:** The existing admin/client persona prompts, stripped of:
- Deploy instructions (not its job)
- Audit/crawl instructions (not its job)
- Added: explicit instruction that it cannot edit files directly, only emit ACTION blocks

**Context includes:**
- Conversation history (last 10 messages)
- Repo context (CLAUDE.md, STATUS.md, recent commits, file tree)
- Job history (completed/running jobs)
- ACTION format documentation (DATA/CONFIG/BUILD)

**Context excludes:**
- Deploy instructions
- Page crawl results
- File edit tools

**Output:** Text reply to user, optionally containing ACTION:DATA, ACTION:CONFIG, or ACTION:BUILD blocks.

**Timeout:** 300s (5 min).

### Auditor Agent

**Purpose:** Crawl live pages, inspect source code, produce a structured report of issues. Read-only — cannot edit anything.

**Tools:** `WebFetch, WebSearch, Read, Glob, Grep, Bash` — read-only access. No Edit, no Write.

**System prompt (~800 bytes):**
```
You are a QA auditor for a web application. Your job is to:
1. Crawl every route on the live site
2. Check each page loads without errors
3. Inspect the source code for issues (broken imports, placeholder text, inconsistent styling)
4. Report findings as a JSON array

For each issue found:
{
  "page": "/admin/donors",
  "severity": "high" | "medium" | "low",
  "description": "Duplicate navigation cards both linking to /my",
  "fix_confidence": "high" | "medium" | "low",
  "file_path": "app/page.tsx",
  "suggested_fix": "Remove the duplicate My Portal card, keep Donor Portal"
}

Rules:
- fix_confidence "high" = obvious fix, single file, no ambiguity
- fix_confidence "medium" = likely fix but needs verification
- fix_confidence "low" = needs human decision (infrastructure, design choice, etc.)
- Do NOT attempt to fix anything. Only report.
```

**Context includes:**
- Deploy URL
- File tree (routes list)
- Repo local path

**Context excludes:**
- Conversation history (doesn't need it)
- Persona prompt (not conversational)
- ACTION format docs (doesn't emit actions)
- Job history (irrelevant to auditing)

**Output:** JSON array of issues + a human-readable summary for Telegram.

**Timeout:** 600s (10 min) — crawling many pages takes time.

**Auto-chain logic (in Python, not in the agent):**
After the auditor returns:
1. Parse the JSON issue array
2. Filter issues where `fix_confidence == "high"`
3. For each high-confidence issue, dispatch a Fixer Agent (sequentially, one per file)
4. Collect all fix results
5. Compose a single Telegram message: audit summary + what was auto-fixed + what needs your decision

### Fixer Agent

**Purpose:** Take a single, specific issue and fix it. Edit the file, commit, push, deploy. Surgical — one issue at a time.

**Tools:** `Read, Edit, Write, Bash` — can edit files and run commands. No WebFetch (doesn't need to browse).

**System prompt (~400 bytes):**
```
You are a surgical code fixer. You receive a specific issue to fix in a specific file.

Steps:
1. Read the file
2. Make the minimal edit to fix the issue
3. Run: cd {repo_path} && git add {file} && git commit -m "fix: {description}"
4. Report what you changed (do NOT push or deploy — Python handles that in batch)

Rules:
- Change ONLY what's needed to fix the issue. No refactoring.
- If the fix is unclear or risky, report back instead of editing.
- One file per fix. If it spans multiple files, report back.
```

**Context includes:**
- The specific issue (from auditor output or user request)
- Repo local path
- The target file path

**Context excludes:**
- Everything else. No conversation, no persona, no page HTML, no file tree.

**Output:** Text summary: what changed, commit hash, deploy status.

**Timeout:** 120s (2 min) — single file edits should be fast.

**Deploy:** After all fixes in a batch are committed and pushed, run one `npx vercel --prod --yes` (or rely on GitHub auto-deploy). Don't deploy after every single fix.

## Implementation Plan

### Changes to `cto_bot.py`

**New functions:**
- `_route_message(user_msg, user) -> dict` — calls Router Agent, returns intent JSON
- `_run_brainstorm(update, user, chat_id, history, user_msg)` — current logic minus audit/fix
- `_run_audit(update, user, chat_id, user_msg)` — calls Auditor, chains to Fixer
- `_run_fix(update, user, chat_id, issue)` — calls Fixer for a single issue
- `_call_claude(prompt, tools, timeout) -> str` — shared helper for Claude CLI subprocess

**Modified functions:**
- `_process_message()` — replace the single Claude CLI call with: Router → dispatch to specialist
- `_build_system_prompt()` — simplify, remove audit/deploy instructions (moved to agent-specific prompts)

**New files:**
- `agents/router_prompt.py` — Router Agent system prompt builder
- `agents/auditor_prompt.py` — Auditor Agent system prompt builder
- `agents/fixer_prompt.py` — Fixer Agent system prompt builder
- `agents/__init__.py`

### What stays unchanged
- `wingmen_orch.py` — orchestrator job pipeline
- `watchdog.py` — health monitoring
- `context_loader.py` — repo context loading
- `spec_generator.py` — build spec generation
- `deploy_manager.py` — Vercel deployment
- `status_reporter.py` — notifications
- ACTION:BUILD/DATA/CONFIG format and parsing
- Telegram command handlers (/status, /jobs, /build, etc.)
- Client vs admin permission model
- Conversation history persistence

### Migration
The refactor is internal to `_process_message()`. No database changes, no new services, no new processes. The CTO Bot Python process stays as the single entry point — it just dispatches to different Claude CLI calls based on the router's classification.

**Rollback:** If the router misclassifies, the brainstorm agent handles it as before (it's the most general). The fallback is the current behavior.

## Success Criteria

1. Router correctly classifies messages >90% of the time (with "chat" as safe fallback)
2. Auditor produces actionable JSON reports without editing anything
3. Fixer makes clean single-file commits without touching unrelated code
4. Brainstorm agent responses are same quality or better (less context noise)
5. End-to-end "audit and fix" flow completes without user intervention for obvious issues
6. No regression in existing flows (commands, client brainstorming, build pipeline)
