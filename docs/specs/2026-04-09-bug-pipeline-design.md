# Bug Report → Propose → Approve → Deploy Pipeline

**Date:** 2026-04-09
**Status:** Approved for implementation
**Owner:** Musa / Wingmen
**Codebase:** ~/wingmen/orchestrator (Python, Telegram bot, Supabase)

## Goal

Add a bug report pipeline to the Wingmen Bot that accepts bug reports from any client (ihsanOS, COSEM, WordPress, Telegram), auto-diagnoses using AI, proposes a fix with a diff, routes to an approver via Telegram, and deploys the fix on approval. The reporter is notified throughout and verifies the fix at the end.

## Architecture

Three-stage pipeline: **Report → Diagnose → Approve → Deploy**

```
Entry Points:
  ├── Telegram Bot (@ihsanosbot)
  └── In-App "Report Bug" button (ihsanOS / COSEM / any web app)
        ↓
  Bug Report Record (Supabase bug_reports table)
        ↓
  Diagnostic Agent (AI — model-agnostic via ai_provider.py)
  - Loads repo context (CLAUDE.md, relevant files, recent git log)
  - Identifies root cause + affected files
  - Generates fix diff
  - Assigns confidence (high/medium/low)
        ↓
  Approval Message → Eligible Approvers (Telegram)
  - Summary + diff (compact default)
  - Full diagnosis (expandable)
  - Buttons: Approve / Reject / Handle Myself
        ↓
  On Approve:
  - Apply fix via Claude Code CLI (ralph_runner)
  - Feature branch → run tests → merge if pass
  - Deploy (Vercel/Firebase depending on repo)
  - Notify reporter: "Fixed! Please verify."
        ↓
  On Reject:
  - Optional reason → notify reporter
        ↓
  On Handle Myself:
  - Status → escalated, CTO handles manually
        ↓
  Reporter Verification:
  - [Verified ✓] → closed
  - [Still broken] → re-enter pipeline (max 2 retries → escalate)
```

## Data Model

### New table: `bug_reports` (orchestrator Supabase)

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | gen_random_uuid() |
| client_id | UUID FK clients | auto-created if new user |
| reporter_name | TEXT NOT NULL | display name from any auth source |
| reporter_email | TEXT | for email notifications |
| reporter_source | TEXT NOT NULL CHECK ('telegram', 'web') | entry point |
| auth_provider | TEXT CHECK ('supabase', 'firebase', 'telegram', 'none') | which auth system |
| repo_name | TEXT NOT NULL | cosem-adcda, ihsanos, etc. (from clients table or explicit) |
| description | TEXT NOT NULL | what the user reported |
| screenshot_url | TEXT | uploaded photo |
| page_url | TEXT | which page they were on |
| status | TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'diagnosing', 'proposed', 'approved', 'deploying', 'deployed', 'verified', 'rejected', 'escalated', 'still_broken')) | |
| confidence | TEXT CHECK ('high', 'medium', 'low') | bot's assessment |
| root_cause | TEXT | one-sentence diagnosis |
| affected_files | TEXT[] | file paths |
| proposed_diff | TEXT | the fix diff |
| diagnosis_full | TEXT | expanded: error log, impact, test plan |
| approval_message_id | TEXT | Telegram message ID for callback buttons |
| approval_sent_to | TEXT[] | Telegram chat IDs of all approvers who received the message |
| approver_id | TEXT | user ID of who approved |
| approved_by | TEXT | display name of approver |
| rejection_reason | TEXT | if rejected |
| retry_count | INTEGER NOT NULL DEFAULT 0 | times re-entered pipeline (max 2) |
| job_id | UUID FK jobs | linked build job when fix is applied |
| deploy_url | TEXT | deployment URL after fix deployed |
| created_at | TIMESTAMPTZ DEFAULT now() | |
| resolved_at | TIMESTAMPTZ | when verified or closed |

No changes to existing `clients` or `jobs` tables.

## AI Provider Abstraction

### New module: `ai_provider.py`

Shared across the entire orchestrator — replaces all direct `anthropic.messages.create()` calls.

```python
async def call_ai(
    prompt: str,
    system: str = "",
    images: list[str] = [],
    model: str = "auto",       # auto, claude, local, fast
    max_tokens: int = 4096,
    json_mode: bool = False,
) -> str
```

**Provider routing:**

| model param | Provider | Use case |
|-------------|----------|----------|
| "auto" | Picks based on task (vision → claude, text → local if available, else claude) | Default for most agents |
| "claude" | Anthropic API | Code reasoning, diff generation, complex diagnosis |
| "local" | Ollama (when Gazzabyte server ready) | Brainstorm, summaries, routine analysis |
| "fast" | Gemma 2B via Ollama or Gemini free tier | Intent routing, classifications, simple tasks |

**Configuration via env vars:**
```
AI_DEFAULT_PROVIDER=claude
AI_FAST_PROVIDER=claude
AI_VISION_PROVIDER=claude
OLLAMA_BASE_URL=              # empty = not available, falls back to claude
```

**Migration:** All existing agents (router, brainstorm, auditor, fixer, brain_sync, morning_brief, session_compress) refactored to use `call_ai()` instead of direct Anthropic SDK calls.

### Model assignment in bug pipeline

| Step | Model | Rationale |
|------|-------|-----------|
| Intent routing ("is this a bug?") | fast | Simple classification |
| Diagnosis (root cause + files) | auto (claude or local 31B) | Needs code reasoning |
| Screenshot analysis | claude (vision) or GLM-5V | Vision required |
| Diff generation | claude | Needs precise code output |
| Approval message formatting | No AI (template) | Just string formatting |

## Entry Points

### Telegram

User messages the bot with a bug description or screenshot.

**Flow:**
1. Bot receives message → router agent classifies as "bug_report" intent
2. Bot asks 1-2 clarifying questions max (page? screenshot?)
3. Auto-detect repo from `clients.repo` or `clients.platform`
4. Create `bug_reports` record (status: new)
5. Kick off diagnostic agent

**Unknown user:** Bot doesn't recognize chat ID → inline registration:
- "What's your name and which organization?"
- "Which product?" [ihsanOS] [COSEM] [Other]
- Auto-creates `clients` row → continues to bug report

### In-App (ihsanOS)

**UI:** Floating `<BugReportButton />` component on all dashboard pages (bottom-left, subtle).

Tap → slide-up panel:
- Description textarea (required)
- Screenshot upload (optional)
- Page URL auto-captured
- Submit button

**Implementation:** Client component in dashboard layout. Server action writes to orchestrator Supabase `bug_reports` table. User identity from Supabase Auth context. Auto-creates `clients` record if needed.

### In-App (COSEM / any web app)

**Embeddable SDK:**
```html
<script src="https://ihsanos.com/bug-report.js" data-repo="cosem-adcda" />
```

Or React component:
```tsx
<BugReport repo="cosem-adcda" apiUrl="https://ihsanos.com/api/bug-report" />
```

**API endpoint:** `POST /api/bug-report` on ihsanOS — public, validates input, creates `bug_reports` record.

**COSEM auth:** SDK reads `firebase.auth().currentUser` and sends `reporter_name`, `reporter_email`, `reporter_uid`, `auth_provider: "firebase"` in the POST body.

### Cross-auth handling

| Source | Auth System | Identification |
|--------|-------------|---------------|
| ihsanOS in-app | Supabase Auth | Server action reads auth context |
| COSEM in-app | Firebase Auth | Client sends Firebase user in POST body |
| Telegram | Bot chat | `telegram_chat_id` in clients table |
| Unknown | None | Inline registration (name + email) |

**Rule:** Never block a bug report because the user isn't registered. Auto-create client record and continue.

### Notification routing back to reporter

| Entry source | Notification channel |
|-------------|---------------------|
| Telegram | Reply in Telegram chat |
| ihsanOS in-app | Email (Supabase email on file) |
| COSEM in-app | Email (Firebase email on file) |

## Diagnostic Agent

### Process

```
Input: bug_report (description, screenshot, page_url, repo_name)
    ↓
Step 1: Load repo context
  - CLAUDE.md (project overview)
  - Relevant source files (page_url → route → component mapping)
  - Recent git log (last 10 commits)
    ↓
Step 2: Diagnose via call_ai()
  Prompt: "Given this bug report and codebase context, identify:
    1. Root cause (one sentence)
    2. Affected files (exact paths)
    3. Confidence (high/medium/low)
    4. Proposed fix (exact diff)
    5. Full diagnosis (error analysis, impact assessment, test plan)"
    ↓
Step 3: Parse response → update bug_report
  status: new → proposed
  Populate: confidence, root_cause, affected_files, proposed_diff, diagnosis_full
    ↓
Step 4: Route approval to eligible approvers
```

### Page URL → File Mapping

- ihsanOS (Next.js App Router): `/dashboard/school/attendance` → `src/app/dashboard/school/attendance/page.tsx`
- COSEM (React/Vite): `/attendance` → `src/pages/Attendance.jsx` (from App.jsx route config)
- Other repos: agent searches for the relevant component by name/path

### Screenshot Analysis

If screenshot provided, include as vision input in the `call_ai()` call. Claude/GLM-5V can read error messages, identify broken UI elements, or spot console errors directly from the image.

### Confidence Scoring

| Level | Criteria | Approval routing |
|-------|----------|-----------------|
| High | Single file, clear error pattern (null check, typo, missing import) | Any admin can approve |
| Medium | 2-3 files, root cause identified but fix touches logic | Any admin (full diagnosis auto-shown) |
| Low | Multi-file, unclear cause, bot unsure about fix | Super admin only (Musa) |

## Approval Flow

### Telegram Message Format

**Default (compact):**
```
🐛 Bug #42 — COSEM-TDU

Reporter: Ahmad (Telegram)
Issue: Attendance page crashes on save
Root cause: Missing null check on session.user
Confidence: 🟢 High
Files: src/pages/Attendance.jsx

━━━━━━━━━━━━━━━━━

--- src/pages/Attendance.jsx (line 45)
- const userId = session.user.id;
+ const userId = session.user?.id;
+ if (!userId) return;

━━━━━━━━━━━━━━━━━

[Full Diagnosis]  [Approve ✓]  [Reject ✗]  [Handle Myself]
```

**"Full Diagnosis" (expandable, auto-shown for medium/low):**
```
📋 Full Diagnosis — Bug #42

Error: TypeError: Cannot read property 'id' of null
Trigger: User clicks Save with expired auth session
Impact: All users on Attendance page affected
Last commit: "feat: add bulk attendance" (2h ago) ← likely introduced it

Test plan:
1. Log in, wait for session to expire
2. Navigate to Attendance, click Save
3. Should show "Session expired" instead of crash

Other affected files: None
```

### Approval Permissions

| Role | Can approve | Confidence restriction |
|------|-----------|----------------------|
| super_admin (Musa) | Any repo, any confidence | None |
| support (Syukor) | Any repo | High + medium only. Low → super_admin. |
| org_admin (e.g., BAPA admin) | ihsanOS bugs for their org only | High only. Medium/low → platform admins. |

### Multi-Approver Behavior

- Approval message sent to ALL eligible approvers simultaneously
- First tap wins — others' messages edited to "✓ Approved by [name]"
- Prevents double-approval or conflicting actions

### Button Callbacks

| Button | Action |
|--------|--------|
| Approve | Apply fix → feature branch → tests → deploy → notify reporter |
| Reject | Ask optional reason → notify reporter "investigating differently" |
| Handle Myself | Status → escalated. CTO handles manually, marks resolved when done. |
| Full Diagnosis | Send expanded message with error log, impact, test plan |

### Auto-Escalation

- 4 hours no response → reminder to all approvers
- 24 hours no response → escalate to super_admin only with urgency flag

### Meta-Safety

Cannot approve fixes to the orchestrator itself. Any bug in `~/wingmen/orchestrator` is auto-escalated to Handle Myself — the bot should never modify its own code through the automated pipeline.

## Post-Approval Deployment

```
Approve tapped
    ↓
Edit Telegram message → "✓ Approved by [name] — deploying..."
    ↓
Step 1: Feature branch
  git checkout -b bugfix/bug-42-attendance-null-check
    ↓
Step 2: Apply fix via ralph_runner (Claude Code CLI)
  Targeted prompt: "Apply this exact fix. Do not change anything else."
  Commit: "fix: null check on attendance save (#BUG-42)"
    ↓
Step 3: Run tests
  npm test / pytest (depending on repo)
  If tests FAIL → status: fix_failed → auto-escalate → notify approver
    ↓
Step 4: Merge + Deploy
  High confidence → merge to main, auto-deploy
  Medium/low → create PR (manual merge later, but deploy preview)

  Deployment targets:
  - ihsanOS → Vercel (deploy_manager.py)
  - COSEM → Firebase (firebase deploy via CLI)
  - dookana → Vercel
  - Other → git push only
    ↓
Step 5: Notify
  Reporter: "Your bug has been fixed! 🎉 Please verify: {deploy_url}"
  Approver: "Bug #42 deployed successfully."
    ↓
Step 6: Verification
  Reporter taps: [Verified ✓] or [Still broken]
  Verified → status: verified, resolved_at = now()
  Still broken → retry_count += 1
    If retry_count <= 2 → status: new (re-enter pipeline with prior context)
    If retry_count > 2 → status: escalated (permanent, CTO handles)
```

### Safety Rails

- Fix applied on feature branch, not main (high confidence merges automatically, medium/low creates PR)
- Tests must pass before deploy — failed tests = auto-escalate, never deploy broken code
- Reporter verifies — loop not closed until person who reported confirms
- Max 2 retry cycles — prevents infinite fix loops
- Orchestrator repo excluded from automated fixes (meta-safety)

## In-App Bug Report UI

### ihsanOS Component

`<BugReportButton />` — floating button on all dashboard pages.

- Bottom-left position, subtle, doesn't obstruct content
- Tap → slide-up panel with: description textarea, screenshot upload, auto-captured page URL
- Submit → server action writes to `bug_reports` table
- "Thanks! We're looking into this." → panel closes
- No email field, no category, no severity — system knows who they are from auth

Added to dashboard layout. Visible to all authenticated users.

### COSEM / External App SDK

Embeddable script or React component:

```html
<!-- Drop-in for any web app -->
<script src="https://ihsanos.com/bug-report.js"
  data-repo="cosem-adcda"
  data-api="https://ihsanos.com/api/bug-report" />
```

The SDK:
- Renders floating button + panel (same UX as ihsanOS)
- Reads current auth (Firebase, Supabase, or none)
- POSTs to ihsanOS API endpoint
- Works in any web app with one script tag

### API Endpoint

`POST https://ihsanos.com/api/bug-report`

```json
{
  "repo": "cosem-adcda",
  "reporter_name": "Ahmad",
  "reporter_email": "ahmad@tdu.sg",
  "reporter_uid": "firebase-uid-123",
  "auth_provider": "firebase",
  "description": "Attendance crashes on save",
  "page_url": "/attendance",
  "screenshot_url": null
}
```

Returns: `{ success: true, bug_id: "uuid", message: "We're looking into this." }`

Auto-creates `clients` record if reporter not found.

## Roles & Workflows

### Reporter (any user, any platform)

**Discovery:** Floating bug button in-app, or messages Telegram bot.
**Workflow:** Describe issue → optional screenshot → submit → wait → notified when fixed → verify.
**Notifications:** Immediate acknowledgment, fix deployed notification, verification request.
**Restrictions:** Sees only their own reports. Cannot see diff, diagnosis, or approver identity.

### Support Admin (Syukor)

**Discovery:** Telegram approval message.
**Workflow:** Read summary + diff → Approve/Reject/Handle Myself → optionally view full diagnosis.
**Notifications:** New bugs needing approval, 4h reminder, 24h escalation.
**Restrictions:** High + medium confidence only. Low → super_admin. Cannot approve orchestrator fixes.

### Super Admin (Musa)

**Discovery:** Telegram approval message.
**Workflow:** Same as support, but all confidence levels, all repos.
**Notifications:** Same + escalation notifications.
**Restrictions:** None.

### Org Admin (e.g., BAPA admin)

**Discovery:** Telegram or email.
**Workflow:** Approve ihsanOS bugs for their org only.
**Notifications:** Only bugs from their org's users.
**Restrictions:** High confidence only for their org. No COSEM, no orchestrator, no other orgs.

### Bot (automated)

**Workflow:** Report → diagnose → propose → route approval → apply → deploy → notify → verify.
**Restrictions:** Never deploys without approval. Never merges without passing tests. Max 2 retries. Never modifies its own codebase.

### Status Lifecycle

```
new → diagnosing → proposed → approved → deploying → deployed → verified ✓
                 → rejected (closed)
                 → escalated (CTO handles manually)
        deployed → still_broken → new (re-enter, max 2)
                                → escalated (after 2 failed fixes)
```

## Reuse from Existing Orchestrator

| Existing | Reuse |
|----------|-------|
| `ralph_runner.py` | Execute fix via Claude Code CLI |
| `deploy_manager.py` | Deploy to Vercel after approval |
| `context_loader.py` | Load repo context for diagnosis |
| `cto_bot.py` | Telegram interface (extend with approval callbacks) |
| Router agent | Classify "is this a bug report?" intent |
| Fixer agent pattern | Diagnostic agent follows same structure |
| `clients` table | Identify reporter, auto-register unknowns |
| `jobs` table | Track fix as a build job |
| REPOS.json | Map repo names to paths and deploy targets |

## New Components

| File | Purpose |
|------|---------|
| `ai_provider.py` | Model-agnostic AI abstraction (shared, replaces all direct Anthropic calls) |
| `bug_pipeline.py` | Orchestrates full bug lifecycle |
| `diagnostic_agent.py` | Diagnoses bug, generates diff, scores confidence |
| `approval_handler.py` | Telegram inline keyboard callbacks, multi-approver logic |
| `bug_notifier.py` | Sends notifications to reporter (Telegram, email) |
| ihsanOS: `<BugReportButton />` | In-app floating bug report UI |
| ihsanOS: `POST /api/bug-report` | API endpoint for external apps |
| SDK: `bug-report.js` | Embeddable script for COSEM and other apps |

## Testing

### Unit Tests
- `ai_provider.py` — model routing, fallback behavior, provider selection
- `diagnostic_agent.py` — prompt construction, confidence scoring, diff parsing
- `approval_handler.py` — routing logic (who approves what), multi-approver dedup
- `bug_pipeline.py` — status transitions, retry logic, escalation timing

### Integration Tests
- Full pipeline: report → mock diagnosis → approval → mock deploy → verify notification
- Cross-auth: Firebase user → auto-register → correct repo
- Telegram: unknown user → registration → bug report created
- Retry: still_broken → re-enter → escalate after 2

### E2E Tests
- ihsanOS: click bug button → fill form → submit → verify DB record
- COSEM SDK: POST /api/bug-report → verify DB record
- Telegram: simulate message → bot clarifies → report created

### PostHog Analytics
- `bug_reported` — source, repo, has_screenshot
- `bug_diagnosed` — confidence, time_to_diagnose
- `bug_approved` — approver_role, time_to_approve
- `bug_deployed` — time_to_deploy, tests_passed
- `bug_verified` — time_to_verify, was_still_broken
- `bug_escalated` — reason, confidence_at_escalation

### Super Admin Metrics
- Bugs reported this week/month
- Mean time to fix (report → verified)
- Fix success rate (verified vs still_broken)
- By repo breakdown
- Confidence accuracy (high confidence fixes that worked first try %)

## Quranic Foundations

| Decision | Foundation | Rationale |
|----------|-----------|-----------|
| Reporter always notified | Ihsan | Excellence in communication — never leave someone in the dark |
| Approval required | Amanah | Trust — no code reaches production without human judgment |
| Diff shown to approver | Adl | Justice — transparent, auditable decision-making |
| Auto-escalation on timeout | Amanah | Accountability — bugs don't get lost in the queue |
| Max 2 retries | Tawbah | Graceful correction — try again, but know when to escalate |
| Meta-safety (no self-modification) | Amanah | The bot should never modify its own judgment system |
| Cross-platform support | Ihsan | Meet users where they are — Telegram, web, any app |

## Out of Scope (future)

- Automated regression detection (bot notices a deploy broke something before users report)
- Scheduled fix batching (collect low-priority bugs, fix in weekly batch)
- Client-facing bug status dashboard (web UI to track their reports)
- Voice bug reports (user sends voice note, Whisper transcribes)
- Auto-generated release notes from fixed bugs
- Fix quality scoring (track which approvers approve fixes that need retries)
