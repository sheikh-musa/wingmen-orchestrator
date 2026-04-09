# Bug Report Pipeline -- Implementation Plan

**Date:** 2026-04-09
**Spec:** `docs/specs/2026-04-09-bug-pipeline-design.md`
**Status:** Ready for implementation

## Parallel Execution Map

```
Track 1: AI Provider + DB Schema (first -- foundation)
    |
    |-->  Track 2: Diagnostic Agent + Bug Pipeline (after Track 1)
    |-->  Track 3: Approval Handler + Telegram Integration (after Track 1)
    |
    \-->  Track 4: In-App UI + API + SDK + Notifications + Tests (after 2-3)
```

---

## Track 1: Foundation

### Task 1: AI Provider Abstraction

**Create:** `ai_provider.py`
**Test:** `tests/test_ai_provider.py`

Shared module that replaces all direct `anthropic.messages.create()` calls across the orchestrator. Every agent and nervous-system module routes through this single function.

- [ ] Create `ai_provider.py` in project root (same level as `ralph_runner.py`, `deploy_manager.py`)
- [ ] Implement `async def call_ai(prompt, system, images, model, max_tokens, json_mode) -> str`
  - Signature:
    ```python
    async def call_ai(
        prompt: str,
        system: str = "",
        images: list[str] | None = None,
        model: str = "auto",       # auto | claude | local | fast
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
    ```
  - `model="auto"`: if `images` provided, route to claude (vision); else route to local if `OLLAMA_BASE_URL` is set, otherwise claude
  - `model="claude"`: always Anthropic API via `anthropic.AsyncAnthropic`
  - `model="local"`: Ollama via `httpx.AsyncClient` POST to `{OLLAMA_BASE_URL}/api/chat`
  - `model="fast"`: reads `AI_FAST_PROVIDER` env var -- defaults to claude if unset
- [ ] Implement provider fallback: if `model="local"` and Ollama unreachable, fall back to claude with a `logger.warning`
- [ ] Read config from env vars:
  - `AI_DEFAULT_PROVIDER` (default: `claude`)
  - `AI_FAST_PROVIDER` (default: `claude`)
  - `AI_VISION_PROVIDER` (default: `claude`)
  - `OLLAMA_BASE_URL` (default: empty string = unavailable)
- [ ] Handle `json_mode=True`: append "Return ONLY valid JSON." to system prompt; for Anthropic, no special header needed (the agents already parse JSON from text)
- [ ] Handle `images`: convert image URLs/paths to Anthropic vision content blocks (`{"type": "image", "source": {"type": "base64", ...}}` or `{"type": "image", "source": {"type": "url", ...}}`)
- [ ] Use `anthropic.AsyncAnthropic()` client (instantiate once at module level, lazy)
- [ ] Log provider selection at DEBUG level: `logger.debug(f"call_ai: model={model} -> provider={resolved_provider}")`
- [ ] Create `tests/test_ai_provider.py`:
  - [ ] Test `model="auto"` with no images -> resolves to default provider
  - [ ] Test `model="auto"` with images -> resolves to claude
  - [ ] Test `model="local"` with `OLLAMA_BASE_URL` set -> routes to Ollama
  - [ ] Test `model="local"` with no `OLLAMA_BASE_URL` -> falls back to claude
  - [ ] Test `model="fast"` reads `AI_FAST_PROVIDER` env var
  - [ ] Test `json_mode=True` appends JSON instruction to system
  - [ ] Mock `anthropic.AsyncAnthropic` and `httpx.AsyncClient` -- no real API calls
- [ ] Run tests: `cd ~/wingmen/orchestrator && python -m pytest tests/test_ai_provider.py -v`
- [ ] Commit: `feat: add ai_provider.py with model-agnostic routing`

---

### Task 2: Refactor Existing Agents to Use ai_provider

**Modify:** `agents/router.py`, `agents/brainstorm.py`, `agents/auditor.py`, `agents/fixer.py`, `nervous_system/brain_sync.py`, `nervous_system/morning_brief.py`, `nervous_system/session_compress.py`, `cto_bot.py`

Replace every direct `anthropic.messages.create()` (or `_call_claude()` wrapper) with `call_ai()`.

- [ ] Audit all files for direct Anthropic SDK usage:
  - `cto_bot.py`: search for `anthropic`, `messages.create`, `_call_claude`
  - `agents/*.py`: check if any agent calls the API directly (current pattern: agents only build prompts, `cto_bot.py` calls Claude)
  - `nervous_system/*.py`: search for `anthropic`, `messages.create`
- [ ] In `cto_bot.py`: replace the `_call_claude()` function's internals (or the Anthropic SDK calls that feed it) with `await call_ai(prompt, system=..., model=...)`
  - Router calls: `model="fast"` (simple classification)
  - Brainstorm calls: `model="auto"` (text reasoning)
  - Fixer/auditor: these go through `ralph_runner` (Claude CLI), so they do NOT use `call_ai()` -- leave them as-is
- [ ] In `nervous_system/brain_sync.py`: replace Anthropic calls with `call_ai(prompt, model="auto")`
- [ ] In `nervous_system/morning_brief.py`: replace with `call_ai(prompt, model="auto")`
- [ ] In `nervous_system/session_compress.py`: replace with `call_ai(prompt, model="fast")`
- [ ] Add `from ai_provider import call_ai` to each modified file
- [ ] Run full test suite: `python -m pytest tests/ -v`
- [ ] Verify no regressions -- all existing tests must pass
- [ ] Commit: `refactor: migrate all agents to ai_provider abstraction`

---

### Task 3: Bug Reports Database Schema

**Modify:** `schema.sql`
**Apply to:** Supabase project `tscuymavysscrvoberrr`

- [ ] Add `bug_reports` table definition to `schema.sql` (append after existing tables):
  ```sql
  create table bug_reports (
    id uuid primary key default gen_random_uuid(),
    client_id bigint references clients(id),
    reporter_name text not null,
    reporter_email text,
    reporter_source text not null check (reporter_source in ('telegram', 'web')),
    auth_provider text check (auth_provider in ('supabase', 'firebase', 'telegram', 'none')),
    repo_name text not null,
    description text not null,
    screenshot_url text,
    page_url text,
    status text not null default 'new' check (status in (
      'new', 'diagnosing', 'proposed', 'approved', 'deploying',
      'deployed', 'verified', 'rejected', 'escalated', 'still_broken'
    )),
    confidence text check (confidence in ('high', 'medium', 'low')),
    root_cause text,
    affected_files text[],
    proposed_diff text,
    diagnosis_full text,
    approval_message_id text,
    approval_sent_to text[],
    approver_id text,
    approved_by text,
    rejection_reason text,
    retry_count integer not null default 0,
    job_id bigint references jobs(id),
    deploy_url text,
    created_at timestamptz not null default now(),
    resolved_at timestamptz
  );
  alter table bug_reports enable row level security;
  create policy "service role full access" on bug_reports
    using (true) with check (true);
  ```
- [ ] Apply migration to Supabase via MCP `execute_sql` or SQL Editor
- [ ] Verify table exists: query `select count(*) from bug_reports`
- [ ] Commit: `feat: add bug_reports table schema`

---

## Track 2: Diagnostic Agent + Pipeline

### Task 4: Diagnostic Agent

**Create:** `agents/diagnostic.py`
**Test:** `tests/test_diagnostic.py`

Follows the exact pattern of `agents/fixer.py` and `agents/router.py`: pure functions that build prompts and parse responses. No API calls inside the agent -- the caller (bug_pipeline.py) handles the AI call.

- [ ] Create `agents/diagnostic.py` with:
  - [ ] `build_diagnostic_prompt(bug_report: dict, repo_context: dict) -> str`
    - `bug_report` dict: `description`, `screenshot_url`, `page_url`, `repo_name`, `retry_count`, `root_cause` (from prior attempt if retry)
    - `repo_context` dict: `claude_md`, `status_md`, `recent_commits`, `file_tree`, `repo_path` (from `context_loader.load_brainstorm_context()`)
    - Prompt instructs AI to return JSON with: `root_cause`, `confidence`, `affected_files`, `proposed_diff`, `diagnosis_full`
    - If `retry_count > 0`, include prior `root_cause` and instruct: "Previous fix did not resolve the issue. Try a different approach."
    - Include page URL to file mapping hint (Next.js App Router: `/dashboard/X` -> `src/app/dashboard/X/page.tsx`)
  - [ ] `parse_diagnostic_response(raw: str) -> dict`
    - Extract JSON from response (handle markdown code blocks, same pattern as `parse_auditor_response` in `agents/auditor.py`)
    - Return dict with keys: `root_cause`, `confidence`, `affected_files`, `proposed_diff`, `diagnosis_full`
    - Validate `confidence` is one of `high`, `medium`, `low` -- default to `low` if missing/invalid
    - Validate `affected_files` is a list -- default to `[]` if missing
    - Return `{"error": "..."}` dict if parsing fails completely
- [ ] Create `tests/test_diagnostic.py`:
  - [ ] Test `build_diagnostic_prompt` includes bug description, repo context, page_url
  - [ ] Test `build_diagnostic_prompt` with retry includes prior root_cause and retry instruction
  - [ ] Test `parse_diagnostic_response` with valid JSON response
  - [ ] Test `parse_diagnostic_response` with JSON in markdown code block
  - [ ] Test `parse_diagnostic_response` with garbage input returns error dict
  - [ ] Test `parse_diagnostic_response` validates confidence values
  - [ ] Test `parse_diagnostic_response` defaults affected_files to empty list
- [ ] Run tests: `python -m pytest tests/test_diagnostic.py -v`
- [ ] Commit: `feat: add diagnostic agent with prompt builder and response parser`

---

### Task 5: Bug Pipeline Orchestrator

**Create:** `bug_pipeline.py`
**Test:** `tests/test_bug_pipeline.py`

Central orchestrator for the full bug lifecycle. Uses `ai_provider.call_ai()`, `context_loader`, `agents/diagnostic.py`, and `ralph_runner`.

- [ ] Create `bug_pipeline.py` in project root with:
  - [ ] `async def create_bug_report(data: dict, supabase: SupabaseAsyncClient) -> str`
    - Insert into `bug_reports` table, return UUID
    - `data` keys: `reporter_name`, `reporter_email`, `reporter_source`, `auth_provider`, `repo_name`, `description`, `screenshot_url`, `page_url`, `client_id` (optional)
    - If `client_id` is None and `reporter_source == "telegram"`, look up by `telegram_chat_id` in `clients`
    - If still no client, auto-create one in `clients` table
  - [ ] `async def run_diagnosis(bug_id: str, supabase: SupabaseAsyncClient) -> dict`
    - Set status to `diagnosing`
    - Load repo context via `context_loader.load_brainstorm_context()`
    - Build prompt via `agents.diagnostic.build_diagnostic_prompt()`
    - Call AI via `call_ai(prompt, model="auto", images=[screenshot_url] if screenshot)`
    - Parse response via `agents.diagnostic.parse_diagnostic_response()`
    - Update bug_report: `status='proposed'`, `confidence`, `root_cause`, `affected_files`, `proposed_diff`, `diagnosis_full`
    - If parse fails, set `status='escalated'` with `root_cause="Diagnosis failed"`
    - Return the parsed diagnosis dict
  - [ ] `async def apply_fix(bug_id: str, supabase: SupabaseAsyncClient) -> dict`
    - Set status to `deploying`
    - Create a `jobs` record (linked via `job_id`)
    - Build a targeted fixer prompt: "Apply this exact diff. Do not change anything else."
    - Execute via `ralph_runner.run_claude()`
    - Return `{"success": bool, "summary": str}`
  - [ ] `async def handle_verification(bug_id: str, verified: bool, supabase: SupabaseAsyncClient) -> None`
    - If `verified`: set `status='verified'`, `resolved_at=now()`
    - If not verified: increment `retry_count`
      - If `retry_count <= 2`: set `status='new'` (re-enter pipeline)
      - If `retry_count > 2`: set `status='escalated'`
  - [ ] Meta-safety check: if `repo_name` matches orchestrator repo, auto-set `status='escalated'` and skip diagnosis
  - [ ] Helper: `async def _update_bug(bug_id, supabase, **fields)` -- DRY update helper
- [ ] Create `tests/test_bug_pipeline.py`:
  - [ ] Test `create_bug_report` inserts record and returns UUID (mock Supabase)
  - [ ] Test `create_bug_report` auto-creates client when `client_id` is None
  - [ ] Test `run_diagnosis` transitions status `new -> diagnosing -> proposed`
  - [ ] Test `run_diagnosis` sets `escalated` on parse failure
  - [ ] Test `handle_verification` with `verified=True` closes the bug
  - [ ] Test `handle_verification` with `verified=False` increments retry_count
  - [ ] Test `handle_verification` escalates after 2 retries
  - [ ] Test meta-safety: orchestrator repo auto-escalates
  - [ ] Mock `call_ai`, `context_loader`, `ralph_runner` -- no real calls
- [ ] Run tests: `python -m pytest tests/test_bug_pipeline.py -v`
- [ ] Commit: `feat: add bug_pipeline orchestrator with full lifecycle management`

---

### Task 6: Router Integration

**Modify:** `agents/router.py`, `cto_bot.py`

Add `bug_report` as a recognized intent so the router can classify incoming messages.

- [ ] In `agents/router.py`:
  - [ ] Add `"bug_report"` to `VALID_INTENTS` set
  - [ ] Add to the prompt's intent list:
    ```
    - "bug_report": user is reporting a bug, issue, error, crash, or something broken. Keywords: "bug", "broken", "crash", "error", "not working", "issue"
    ```
- [ ] In `cto_bot.py`:
  - [ ] Add handler for `intent == "bug_report"` in the message routing logic
  - [ ] When triggered, enter a bug collection flow:
    1. Acknowledge: "Got it -- sounds like a bug. Let me collect a few details."
    2. If no screenshot in original message, ask: "Can you send a screenshot?"
    3. If no page URL inferrable, ask: "Which page were you on?"
    4. After collecting, call `bug_pipeline.create_bug_report()` then `bug_pipeline.run_diagnosis()`
    5. On diagnosis complete, call `approval_handler` (Task 7) to send approval message
  - [ ] Handle photo messages: if a photo is sent with text that looks like a bug report, treat as `bug_report` intent (the router prompt already handles this via the "screenshot" hint)
- [ ] Update existing router tests in `tests/test_agents.py`:
  - [ ] Test `"bug_report"` is in `VALID_INTENTS`
  - [ ] Test `parse_router_response` accepts `"bug_report"` intent
- [ ] Run tests: `python -m pytest tests/test_agents.py -v`
- [ ] Commit: `feat: add bug_report intent to router agent`

---

## Track 3: Approval + Telegram

### Task 7: Approval Handler

**Create:** `approval_handler.py`
**Test:** `tests/test_approval_handler.py`

Handles who can approve what, builds the Telegram approval messages, and processes callbacks.

- [ ] Create `approval_handler.py` in project root with:
  - [ ] Define approver roles and rules:
    ```python
    APPROVER_ROLES = {
        "super_admin": {"repos": "*", "confidence": ["high", "medium", "low"]},
        "support": {"repos": "*", "confidence": ["high", "medium"]},
        "org_admin": {"confidence": ["high"]},  # repos filtered by org
    }
    ```
  - [ ] `async def get_eligible_approvers(bug_report: dict, supabase: SupabaseAsyncClient) -> list[dict]`
    - Query `clients` table for users with admin roles
    - Filter by confidence level (low -> super_admin only)
    - Filter org_admins by their org's repos
    - Return list of `{"chat_id": str, "name": str, "role": str}`
  - [ ] `def build_approval_message(bug_report: dict) -> tuple[str, InlineKeyboardMarkup]`
    - Format the compact approval message per spec (bug number, reporter, issue, root cause, confidence emoji, files, diff snippet)
    - Build inline keyboard with buttons:
      - `[Full Diagnosis]` callback_data: `bug_diag_{bug_id}`
      - `[Approve]` callback_data: `bug_approve_{bug_id}`
      - `[Reject]` callback_data: `bug_reject_{bug_id}`
      - `[Handle Myself]` callback_data: `bug_handle_{bug_id}`
    - For medium/low confidence, auto-include the full diagnosis in the message body (no need to tap "Full Diagnosis")
  - [ ] `def build_full_diagnosis_message(bug_report: dict) -> str`
    - Format expanded diagnosis: error analysis, trigger, impact, last commit, test plan
    - Used when approver taps "Full Diagnosis" button
  - [ ] `async def handle_approval_callback(action: str, bug_id: str, approver: dict, supabase: SupabaseAsyncClient) -> dict`
    - `action="approve"`: update bug status to `approved`, set `approver_id`, `approved_by`
    - `action="reject"`: update status to `rejected` (rejection_reason collected separately)
    - `action="handle"`: update status to `escalated`
    - Return `{"action": action, "bug_id": bug_id, "message_updates": [...]}` with message IDs to edit
  - [ ] Multi-approver dedup: check if bug is still in `proposed` status before processing -- if already acted on, return "already handled" message
- [ ] Create `tests/test_approval_handler.py`:
  - [ ] Test `get_eligible_approvers` returns super_admin for low confidence
  - [ ] Test `get_eligible_approvers` returns support + super_admin for high confidence
  - [ ] Test `build_approval_message` includes all required fields
  - [ ] Test `build_approval_message` auto-includes diagnosis for medium confidence
  - [ ] Test `handle_approval_callback` approve transitions to `approved`
  - [ ] Test `handle_approval_callback` dedup -- second approve returns "already handled"
  - [ ] Test `handle_approval_callback` reject transitions to `rejected`
  - [ ] Mock Supabase queries
- [ ] Run tests: `python -m pytest tests/test_approval_handler.py -v`
- [ ] Commit: `feat: add approval_handler with role-based routing and dedup`

---

### Task 8: Telegram Callbacks Integration

**Modify:** `cto_bot.py`

Wire approval buttons to the bug pipeline. Add the bug report conversation flow for Telegram users.

- [ ] Add `CallbackQueryHandler` for bug-related callbacks:
  - [ ] Pattern: `^bug_(diag|approve|reject|handle)_(.+)$`
  - [ ] Extract `action` and `bug_id` from `callback_data`
  - [ ] Call `approval_handler.handle_approval_callback()`
  - [ ] After approve: kick off `bug_pipeline.apply_fix()` in background task
  - [ ] After reject: ask approver for optional reason, then update bug and notify reporter
  - [ ] After handle: update status, notify reporter "Being handled manually"
  - [ ] Edit the original approval message to show outcome: "Approved by [name]" / "Rejected by [name]" / "Escalated -- [name] handling manually"
  - [ ] Edit ALL copies of the approval message (sent to multiple approvers) using `approval_sent_to` message IDs
- [ ] Add `bug_diag` callback:
  - [ ] Fetch `diagnosis_full` from bug_report
  - [ ] Send as a new message (not edit) via `build_full_diagnosis_message()`
- [ ] Add unknown-user inline registration flow:
  - [ ] If `telegram_chat_id` not found in `clients` table during bug report
  - [ ] Ask: "What's your name?" -> "Which product?" [ihsanOS] [COSEM] [Other]
  - [ ] Auto-create `clients` record, then continue bug report flow
- [ ] Register the `CallbackQueryHandler` in the bot's application builder (same pattern as existing handlers)
- [ ] Manual test checklist (no automated test -- Telegram integration):
  - [ ] Send bug report -> bot collects details -> diagnosis runs -> approval message sent
  - [ ] Tap Approve -> fix applies -> reporter notified
  - [ ] Tap Reject -> reporter notified
  - [ ] Tap Handle Myself -> status escalated
  - [ ] Second approver tapping after first -> sees "already handled"
- [ ] Commit: `feat: wire bug approval callbacks into Telegram bot`

---

### Task 9: Bug Notification System

**Create:** `bug_notifier.py`

Routes notifications to reporters and approvers via Telegram or email.

- [ ] Create `bug_notifier.py` in project root with:
  - [ ] `async def notify_reporter(bug_id: str, event: str, supabase: SupabaseAsyncClient, bot: Bot) -> None`
    - Events: `acknowledged`, `proposed`, `deployed`, `rejected`, `escalated`
    - Route based on `reporter_source`:
      - `telegram`: send message via `bot.send_message(chat_id, text)`
      - `web`: send email (use Supabase edge function or simple SMTP via `aiosmtplib`)
    - Message templates per event:
      - `acknowledged`: "We received your bug report and are investigating."
      - `proposed`: "We've identified the issue and a fix is being reviewed."
      - `deployed`: "Your bug has been fixed! Please verify: {deploy_url}" + verification buttons
      - `rejected`: "We reviewed your report and are taking a different approach."
      - `escalated`: "Your report has been escalated to a senior developer."
  - [ ] `async def notify_approvers(bug_id: str, supabase: SupabaseAsyncClient, bot: Bot) -> None`
    - Get eligible approvers via `approval_handler.get_eligible_approvers()`
    - Build approval message via `approval_handler.build_approval_message()`
    - Send to each approver, collect message IDs
    - Store message IDs in `bug_reports.approval_sent_to`
  - [ ] `async def send_reminder(bug_id: str, supabase: SupabaseAsyncClient, bot: Bot) -> None`
    - Resend approval message to approvers with "Reminder: this bug is awaiting your review"
    - Used by the escalation scheduler (Task 14)
- [ ] No dedicated test file -- notification logic is mostly template strings + Telegram/email calls. Covered by integration test in Task 15.
- [ ] Commit: `feat: add bug_notifier for reporter and approver notifications`

---

## Track 4: In-App + Integration

### Task 10: ihsanOS Bug Report Button

**Repo:** `~/wingmen/projects/ihsanos` (NOT the orchestrator)
**Create:** `src/shared/ui/bug-report-button.tsx`, `src/app/api/bug-report/route.ts`

Floating bug report button on all ihsanOS dashboard pages.

- [ ] Create `src/shared/ui/bug-report-button.tsx`:
  - [ ] Client component (`"use client"`)
  - [ ] Floating button: bottom-left corner, subtle styling, small bug icon
  - [ ] On click: slide-up panel with:
    - Description textarea (required)
    - Screenshot upload (optional, via file input -> upload to Supabase Storage)
    - Page URL auto-captured from `window.location.pathname`
    - Submit button
  - [ ] On submit: POST to `/api/bug-report` with description, screenshot_url, page_url
  - [ ] Success state: "Thanks! We're looking into this." -> panel closes after 2s
  - [ ] Error state: "Something went wrong. Please try again."
  - [ ] Use existing ihsanOS design tokens (Tailwind classes, shadcn components if available)
- [ ] Create `src/app/api/bug-report/route.ts`:
  - [ ] `POST` handler
  - [ ] Read auth context from Supabase server-side (`createRouteHandlerClient`)
  - [ ] Validate: `description` required, `page_url` required
  - [ ] If user not in orchestrator `clients` table, auto-create (use Supabase service role key for orchestrator DB)
  - [ ] Insert into orchestrator Supabase `bug_reports` table:
    ```json
    {
      "reporter_name": "user.name from auth",
      "reporter_email": "user.email from auth",
      "reporter_source": "web",
      "auth_provider": "supabase",
      "repo_name": "ihsanos",
      "description": "...",
      "screenshot_url": "...",
      "page_url": "..."
    }
    ```
  - [ ] Return `{ success: true, bug_id: "uuid" }`
  - [ ] NOTE: The orchestrator Supabase is a DIFFERENT project from ihsanOS Supabase. Use env var `ORCHESTRATOR_SUPABASE_URL` and `ORCHESTRATOR_SUPABASE_SERVICE_KEY` for the orchestrator DB connection.
- [ ] Add `<BugReportButton />` to the dashboard layout (`src/app/dashboard/layout.tsx` or equivalent)
- [ ] Add PostHog capture: `posthog.capture('bug_reported', { source: 'ihsanos_web', has_screenshot: bool })`
- [ ] Commit (in ihsanos repo): `feat: add floating bug report button to dashboard`

---

### Task 11: Bug Report SDK for External Apps

**Repo:** `~/wingmen/projects/ihsanos` (served as static asset)
**Create:** `public/bug-report.js`

Embeddable script that any web app (COSEM, dookana, WordPress) can include via a single `<script>` tag.

- [ ] Create `public/bug-report.js`:
  - [ ] Self-executing function that renders a floating button + slide-up panel
  - [ ] Read config from script tag attributes:
    - `data-repo`: repo name (required)
    - `data-api`: API URL (default: `https://ihsanos.com/api/bug-report`)
  - [ ] Auto-detect auth:
    - Check `firebase.auth().currentUser` (Firebase apps like COSEM)
    - Check `supabase.auth.getUser()` (Supabase apps)
    - If neither available, show name + email fields in the form
  - [ ] On submit: POST to `data-api` URL with:
    ```json
    {
      "repo": "from data-repo attribute",
      "reporter_name": "from auth or form",
      "reporter_email": "from auth or form",
      "reporter_uid": "from auth if available",
      "auth_provider": "firebase | supabase | none",
      "description": "user input",
      "page_url": "window.location.pathname",
      "screenshot_url": null
    }
    ```
  - [ ] Inject minimal CSS (inline styles, no external deps, no conflicts with host page)
  - [ ] Usage: `<script src="https://ihsanos.com/bug-report.js" data-repo="cosem-adcda" />`
  - [ ] No screenshot upload in SDK v1 (simplify -- just description + page URL)
- [ ] Update the `/api/bug-report` route (from Task 10) to handle external requests:
  - [ ] Accept `auth_provider: "firebase" | "none"` (not just Supabase auth)
  - [ ] CORS: allow requests from any origin (the SDK runs on client domains)
- [ ] Commit (in ihsanos repo): `feat: add embeddable bug-report.js SDK`

---

### Task 12: Post-Approval Deployment

**Modify:** `bug_pipeline.py`

Extend `apply_fix()` with the full deploy lifecycle: feature branch -> tests -> merge/PR -> deploy.

- [ ] Add `async def deploy_fix(bug_id: str, supabase: SupabaseAsyncClient) -> dict` to `bug_pipeline.py`:
  - [ ] Step 1: Create feature branch
    - Branch name: `bugfix/bug-{short_id}-{slugified_root_cause[:30]}`
    - `git checkout -b {branch}` via `asyncio.create_subprocess_exec`
  - [ ] Step 2: Apply fix via `ralph_runner.run_claude()`
    - Targeted prompt: "Apply this exact diff to {affected_files}. Commit as: fix: {root_cause} (#BUG-{short_id})"
    - Pass `proposed_diff` and `affected_files` in the prompt
  - [ ] Step 3: Run tests
    - Detect test runner from repo config or file presence:
      - `package.json` exists -> `npm test`
      - `pytest.ini` or `tests/` exists -> `python -m pytest`
    - Execute via `asyncio.create_subprocess_exec`, capture exit code
    - If tests FAIL: set `status='escalated'`, notify approver "Tests failed, escalating"
  - [ ] Step 4: Merge + Deploy
    - High confidence: `git checkout main && git merge {branch}` + deploy
    - Medium/low confidence: `git push -u origin {branch}` + create PR via GitHub API (or `gh pr create`)
    - Deploy targets (from `REPOS.json` config):
      - `vercel_project` set -> `deploy_manager.deploy(repo_name)`
      - `firebase_project` set -> `firebase deploy --project {id}` via subprocess
      - Neither -> `git push origin main` only
  - [ ] Step 5: Update bug_report
    - Set `status='deployed'`, `deploy_url`
    - Notify reporter via `bug_notifier.notify_reporter(bug_id, "deployed")`
- [ ] Add deployment target detection to `REPOS.json` schema (if not already there):
  - [ ] Ensure each repo entry has `deploy_target: "vercel" | "firebase" | "git_only"`
- [ ] Run existing tests: `python -m pytest tests/ -v`
- [ ] Commit: `feat: add post-approval deployment to bug pipeline`

---

### Task 13: Reporter Verification Flow

**Modify:** `cto_bot.py`, `bug_notifier.py`

After deploy, reporter is asked to verify the fix. Handles both Telegram and web reporters.

- [ ] In `cto_bot.py`:
  - [ ] Add callback handler for verification buttons:
    - `bug_verify_{bug_id}` -> `handle_verification(bug_id, verified=True)`
    - `bug_broken_{bug_id}` -> `handle_verification(bug_id, verified=False)`
  - [ ] After verification:
    - Verified: "Thanks for confirming! Bug closed." Edit message to remove buttons.
    - Still broken: "Sorry about that. We'll take another look." Re-enter pipeline if `retry_count <= 2`, else "Escalated to a senior developer."
- [ ] In `bug_notifier.py`:
  - [ ] When `event="deployed"` and `reporter_source="telegram"`:
    - Include inline keyboard: `[Verified] [Still broken]`
  - [ ] When `event="deployed"` and `reporter_source="web"`:
    - Send email with verification link (link to a simple page or magic link that calls the API)
    - For v1: email says "Reply to this email with 'fixed' or 'still broken'" (manual processing)
    - Or: link to `https://ihsanos.com/api/bug-verify?id={bug_id}&action=verify` (simple GET endpoint)
- [ ] Add `src/app/api/bug-verify/route.ts` to ihsanOS repo:
  - [ ] GET handler with query params `id` and `action` (verify/broken)
  - [ ] Updates orchestrator Supabase `bug_reports` table
  - [ ] Returns simple HTML page: "Thanks for your feedback!"
- [ ] Commit: `feat: add reporter verification flow for Telegram and web`

---

### Task 14: Auto-Escalation Scheduler

**Create:** `nervous_system/bug_escalation.py`
**Modify:** `wingmen_orch.py`

Scheduled task that checks for stale bugs and sends reminders or escalates.

- [ ] Create `nervous_system/bug_escalation.py`:
  - [ ] `async def check_stale_bugs(supabase: SupabaseAsyncClient, bot: Bot) -> None`
    - Query `bug_reports` where `status='proposed'` and `created_at < now() - interval '4 hours'` and no `approver_id`
    - For each:
      - If `created_at < now() - interval '24 hours'`: escalate to super_admin only
        - Update `approval_sent_to` to super_admin chat IDs only
        - Send with urgency: "URGENT: Bug #{id} has had no response for 24 hours"
      - Else (4-24h): send reminder via `bug_notifier.send_reminder()`
    - Also check `status='deployed'` with no verification after 48h: auto-close with `status='verified'` (assume fixed if no complaint)
- [ ] In `wingmen_orch.py`:
  - [ ] Add the escalation check to the existing scheduler
  - [ ] Schedule: every 30 minutes
  - [ ] Pattern: same as existing nervous system scheduled tasks (find how `morning_brief` or `brain_sync` is scheduled)
- [ ] Commit: `feat: add auto-escalation scheduler for stale bugs`

---

### Task 15: Integration Tests + Analytics + Cleanup

**Create:** `tests/test_bug_pipeline_integration.py`
**Modify:** `STATUS.md`

Full-flow mock test and final cleanup.

- [ ] Create `tests/test_bug_pipeline_integration.py`:
  - [ ] Test full pipeline flow (all mocked):
    1. `create_bug_report()` -> returns bug_id
    2. `run_diagnosis()` -> status transitions to `proposed`
    3. `notify_approvers()` -> approval message "sent"
    4. `handle_approval_callback("approve")` -> status to `approved`
    5. `deploy_fix()` -> status to `deployed`
    6. `handle_verification(verified=True)` -> status to `verified`
  - [ ] Test retry flow:
    1. Create -> diagnose -> approve -> deploy
    2. `handle_verification(verified=False)` -> status to `new`, retry_count=1
    3. Re-diagnose -> re-approve -> re-deploy
    4. `handle_verification(verified=False)` -> status to `new`, retry_count=2
    5. Third failure -> `handle_verification(verified=False)` -> status to `escalated`
  - [ ] Test rejection flow: create -> diagnose -> reject -> status to `rejected`
  - [ ] Test handle-myself flow: create -> diagnose -> handle -> status to `escalated`
  - [ ] Test meta-safety: create with `repo_name="orchestrator"` -> auto-escalated
  - [ ] Mock all external calls: `call_ai`, Supabase, Telegram bot, `ralph_runner`
- [ ] Add PostHog analytics captures to ihsanOS bug report component (in ihsanos repo):
  - [ ] `bug_reported` -- source, repo, has_screenshot
  - [ ] `bug_verified` -- was_still_broken, retry_count
- [ ] Update `STATUS.md`:
  - [ ] Add "Bug Pipeline" section with current status
  - [ ] List new files added
- [ ] Run full test suite: `python -m pytest tests/ -v`
- [ ] Commit: `feat: add integration tests and finalize bug pipeline`

---

## File Summary

### New Files (orchestrator)

| File | Task | Purpose |
|------|------|---------|
| `ai_provider.py` | 1 | Model-agnostic AI abstraction |
| `agents/diagnostic.py` | 4 | Diagnostic agent prompt builder + parser |
| `bug_pipeline.py` | 5 | Full bug lifecycle orchestrator |
| `approval_handler.py` | 7 | Approval routing, message building, callbacks |
| `bug_notifier.py` | 9 | Reporter + approver notification routing |
| `nervous_system/bug_escalation.py` | 14 | Auto-escalation scheduler |
| `tests/test_ai_provider.py` | 1 | AI provider unit tests |
| `tests/test_diagnostic.py` | 4 | Diagnostic agent unit tests |
| `tests/test_bug_pipeline.py` | 5 | Bug pipeline unit tests |
| `tests/test_approval_handler.py` | 7 | Approval handler unit tests |
| `tests/test_bug_pipeline_integration.py` | 15 | Full-flow integration tests |

### New Files (ihsanOS -- `~/wingmen/projects/ihsanos`)

| File | Task | Purpose |
|------|------|---------|
| `src/shared/ui/bug-report-button.tsx` | 10 | Floating bug report UI component |
| `src/app/api/bug-report/route.ts` | 10 | API endpoint for bug reports |
| `src/app/api/bug-verify/route.ts` | 13 | Verification endpoint for web reporters |
| `public/bug-report.js` | 11 | Embeddable SDK for external apps |

### Modified Files

| File | Tasks | Changes |
|------|-------|---------|
| `agents/router.py` | 2, 6 | Add `bug_report` intent, use `call_ai` |
| `cto_bot.py` | 2, 6, 8, 13 | Bug collection flow, approval callbacks, verification callbacks, `call_ai` migration |
| `schema.sql` | 3 | Add `bug_reports` table |
| `nervous_system/brain_sync.py` | 2 | Use `call_ai` |
| `nervous_system/morning_brief.py` | 2 | Use `call_ai` |
| `nervous_system/session_compress.py` | 2 | Use `call_ai` |
| `wingmen_orch.py` | 14 | Register escalation scheduler |
| `STATUS.md` | 15 | Document bug pipeline status |
| `tests/test_agents.py` | 6 | Add `bug_report` intent tests |

---

## Dependencies Between Tasks

```
Task 1 (ai_provider) -----> Task 2 (refactor agents)
Task 1 -----> Task 4 (diagnostic agent uses call_ai)
Task 1 -----> Task 5 (bug_pipeline uses call_ai)
Task 3 (DB schema) -----> Task 5 (bug_pipeline writes to bug_reports)
Task 4 (diagnostic) -----> Task 5 (bug_pipeline calls diagnostic)
Task 5 (bug_pipeline) -----> Task 7 (approval_handler reads bug_reports)
Task 5 -----> Task 8 (Telegram integration wires bug_pipeline)
Task 7 (approval_handler) -----> Task 8 (callbacks call approval_handler)
Task 5 -----> Task 9 (notifier reads bug_reports)
Task 8 -----> Task 12 (deploy_fix called after approve)
Task 9 -----> Task 13 (notifier sends verification messages)
Task 12 -----> Task 13 (verification after deploy)
Task 5 + 9 -----> Task 14 (escalation reads bug_reports, calls notifier)
All -----> Task 15 (integration tests)
```

## Estimated Effort

| Track | Tasks | Estimate |
|-------|-------|----------|
| Track 1: Foundation | 1-3 | 3-4 hours |
| Track 2: Diagnostic + Pipeline | 4-6 | 4-5 hours |
| Track 3: Approval + Telegram | 7-9 | 4-5 hours |
| Track 4: In-App + Integration | 10-15 | 6-8 hours |
| **Total** | **15** | **17-22 hours** |

Tracks 2 and 3 can run in parallel after Track 1 completes.
