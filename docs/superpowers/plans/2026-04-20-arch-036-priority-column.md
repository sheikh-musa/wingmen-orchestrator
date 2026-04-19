# ARCH-036 Priority Column on Narrowed agent_messages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a P0–P3 `priority` column to the post-ARCH-035 narrowed `agent_messages` channel so boot-briefings and the Telegram notifier sort urgent traffic first, and suppress P3 FYI traffic from Telegram entirely.

**Architecture:** One migration adds `priority TEXT NOT NULL DEFAULT 'P2'` with a 4-value CHECK and a second CHECK enforcing `(priority IN ('P2','P3') OR requires_response = true)` as anti-inflation guard. A partial index on `(priority, created_at) WHERE read_at IS NULL` keeps the boot-briefing open-inbox sort cheap. Two Python files adopt the new sort order (`priority ASC, requires_response DESC, created_at ASC`); the notifier additionally prepends a priority glyph (🔴 P0 / 🟠 P1 / 🟡 P2) and returns `None` for P3 so the Telegram path short-circuits. Same migration-apply pattern as ARCH-035: ping CAI to run the `.sql` via Supabase MCP, then commit code atomically.

**Tech Stack:** Postgres (CHECK constraints, partial index), Python (`supabase-py`), pytest.

**Spec:** ARCH-036 in `strategic_decisions` (status=active, parent=ARCH-035), clarified by CAI-RESP-047 (Q1 P3 suppression confirmed load-bearing, Q2 uniform P2 default accepted).

---

## File Structure

### Commit 1 — docs-first (ihsanos repo, single file)

- Modify: `WINGMEN_CONSTRAINTS.md` — amend the "Three-channel governance taxonomy" section with a **Priority rubric (ARCH-036)** subsection

### Migration apply — out of band via CAI

Per memory `feedback_migration_apply_via_cai.md` — ping CAI with file contents + rationale once Task 2 lands the `.sql` file. CAI runs `apply_migration` via Supabase MCP under Musa's delegation default. Task 3 blocks until CAI reports applied.

### Commit 2 — atomic code commit (orchestrator repo, 4 files)

- Create: `supabase/migrations/20260420_arch036_priority_column.sql` — schema change
- Modify: `nervous_system/agent_messages_poll.py` — select `priority`, refactor `_format_telegram` into a glyph-prepending wrapper + body helper, drop P3 entirely, update ORDER BY
- Modify: `scripts/build_launch_context.py` — select `priority`, change ORDER BY on `agent_messages` inbox section, prepend glyph in row formatter
- Modify: `tests/test_agent_messages_poll.py` — four new test classes covering P3 suppression, glyph prefix, default P2, and sort-order integration

### Commit 3 — STATUS.md + digest (orchestrator repo)

- Modify: `STATUS.md`
- Post: `agent_messages` row to `cai` with structured session digest

### Commit boundaries

- Commit 1 (docs) lands independently. Safe even if Commit 2 is rolled back.
- Migration apply is out-of-band; CAI reports applied before Commit 2 is staged.
- Commit 2 ships atomically only after migration is live AND all tests pass AND live-verify matrix passes.
- Commit 3 ships after Commit 2 is pushed and orchestrator has cycled (picks up the new poller code).

---

## Task 1: WINGMEN_CONSTRAINTS.md priority-rubric amendment

**Files:**
- Modify: `/Users/sheikhmusa/wingmen/projects/ihsanos/WINGMEN_CONSTRAINTS.md`

- [ ] **Step 1: Locate the ARCH-035 section**

Run:
```bash
grep -n "Three-channel governance taxonomy" /Users/sheikhmusa/wingmen/projects/ihsanos/WINGMEN_CONSTRAINTS.md
```

Expected: one match on the heading line inserted by ARCH-035 Task 1 (commit `8a96c6c`).

- [ ] **Step 2: Find the end of the section**

Read the file from the heading line forward until the next `## ` heading or end-of-file. The priority rubric block goes at the end of the existing ARCH-035 section, before the next top-level `##`.

- [ ] **Step 3: Append the priority-rubric subsection**

Use Edit to add this block immediately before the next `## ` heading (or append at EOF if the ARCH-035 section is the last one):

````markdown

### Priority rubric (ARCH-036)

Per ARCH-036 (2026-04-20), parent ARCH-035. Every `agent_messages` row carries a `priority` column defaulting to `P2`. The boot-briefing and Telegram notifier sort `priority ASC, requires_response DESC, created_at ASC`.

| Level | When | Examples |
|-------|------|----------|
| P0 | Production-impacting blocker, ALL forward motion stops | Live data corruption, governance blackout, payment broken |
| P1 | Blocks current in-flight sprint, agent cannot proceed | Scope ambiguity on active task, dependency missing, schema conflict |
| P2 | **Default.** Important but not blocking; agent keeps working | Design proposals, routine reviews, status updates that need ack |
| P3 | FYI / observability; no response expected | Completion announcements, session digests, heartbeat updates that survive the banned-prefix filter |

**Anti-inflation CHECK:** `(priority IN ('P2','P3') OR requires_response = true)`. An agent cannot ship a P0 or P1 without `requires_response=true` — urgency must demand a response structurally, not by convention.

**Telegram integration:** P0/P1/P2 prepend glyph 🔴/🟠/🟡 on Telegram push. **P3 is suppressed from Telegram entirely** — Telegram is interrupt-capable, P3 is passive. P3 still appears in boot-briefing inbox and `agent_messages` table scans; it just doesn't interrupt Musa's phone. Per CAI-RESP-047, this remains load-bearing alongside the ARCH-035 banned-prefix filter: the filter catches structural noise, the P3 suppression catches semantic low-urgency traffic (`BUG-XXX shipped`, FYI heads-ups) that writers phrase without the banned prefixes.

**Re-triage:** CAI may UPDATE `priority` at any time. Authorization gated by BUG-024 Phase 1 identity checks once live. Monthly audit flags agents with high P0/P1 ratios: `SELECT from_agent, priority, COUNT(*) FROM agent_messages GROUP BY 1,2`.
````

- [ ] **Step 4: Verify the edit**

Run:
```bash
grep -n "Priority rubric (ARCH-036)" /Users/sheikhmusa/wingmen/projects/ihsanos/WINGMEN_CONSTRAINTS.md
grep -n "Anti-inflation CHECK" /Users/sheikhmusa/wingmen/projects/ihsanos/WINGMEN_CONSTRAINTS.md
```

Expected: one match each.

- [ ] **Step 5: Commit**

```bash
cd /Users/sheikhmusa/wingmen/projects/ihsanos && git add WINGMEN_CONSTRAINTS.md && git commit -m "$(cat <<'EOF'
docs(constraints): priority rubric P0-P3 for narrowed agent_messages (ARCH-036)

Adds priority column rubric + anti-inflation CHECK + P3 Telegram
suppression rule under the ARCH-035 three-channel section. Ordering:
priority ASC, requires_response DESC, created_at ASC. P3 load-bearing
alongside banned-prefix filter per CAI-RESP-047.

Parent: ARCH-036 (strategic_decisions), ARCH-035, CAI-RESP-047.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

Do NOT push yet — push with Commit 2 or separately after CAI applies migration.

---

## Task 2: Migration SQL file

**Files:**
- Create: `/Users/sheikhmusa/wingmen/orchestrator/supabase/migrations/20260420_arch036_priority_column.sql`

- [ ] **Step 1: Pre-flight — confirm no blocking existing rows**

Run:
```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -c "
import os
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
r = sb.table('agent_messages').select('id, requires_response').eq('requires_response', False).execute()
print('rows with requires_response=false:', len(r.data))
# All will backfill to P2, which passes the CHECK (P2 is in allowed set for requires_response=false).
# Confirming count so we have a known-good baseline.
"
```

Expected: a count (any number). Since the CHECK is `(priority IN ('P2','P3') OR requires_response = true)` and every existing row gets `DEFAULT 'P2'`, every existing row passes regardless of `requires_response`. The count is informational — zero blockers.

- [ ] **Step 2: Write the migration file**

Use the Write tool with this full content:

```sql
-- ARCH-036: Priority column on narrowed agent_messages.
--
-- Parent: ARCH-036 (strategic_decisions). References:
--   ARCH-035 (narrowed agent_messages channel)
--   CAI-RESP-047 (Q1 P3 suppression load-bearing, Q2 uniform P2 default)
--
-- Schema:
--   1. priority column TEXT NOT NULL DEFAULT 'P2' CHECK IN ('P0','P1','P2','P3')
--   2. Anti-inflation CHECK: (priority IN ('P2','P3') OR requires_response = true)
--      — P0/P1 structurally require requires_response=true.
--   3. Partial index on (priority, created_at) WHERE read_at IS NULL
--      — boot-briefing open-inbox sort path.
--
-- Backfill: every existing row receives DEFAULT 'P2'. Read rows keep P2
-- permanently (historical, irrelevant). Unread rows sort at the bottom
-- of the P2 band by created_at.

ALTER TABLE agent_messages
  ADD COLUMN priority TEXT NOT NULL DEFAULT 'P2'
  CHECK (priority IN ('P0','P1','P2','P3'));

ALTER TABLE agent_messages
  ADD CONSTRAINT agent_messages_priority_requires_response_check
  CHECK (priority IN ('P2','P3') OR requires_response = true);

CREATE INDEX idx_agent_messages_open_by_priority
  ON agent_messages (priority, created_at)
  WHERE read_at IS NULL;
```

- [ ] **Step 3: Verify file**

Run:
```bash
wc -l /Users/sheikhmusa/wingmen/orchestrator/supabase/migrations/20260420_arch036_priority_column.sql
```

Expected: ~25 lines.

Do NOT git-add yet — Commit 2 stages all files atomically at the end of Task 7.

---

## Task 3: Ping CAI to apply migration, wait for apply, smoke verify

**Files:** none modified.

- [ ] **Step 1: Post migration-apply request to CAI**

Save as `/tmp/arch036_apply_request.py` and run `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python /tmp/arch036_apply_request.py`:

```python
import os, uuid
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

body = r'''ARCH-036 migration ready to apply. Standalone — not bundled with ARCH-035 (that one already landed).

**File:** supabase/migrations/20260420_arch036_priority_column.sql (orchestrator repo, not yet committed — apply first, then I atomic-commit the .sql alongside the code adapters per our usual pattern).

**DDL summary:**
  1. ALTER TABLE agent_messages ADD COLUMN priority TEXT NOT NULL DEFAULT 'P2' CHECK (priority IN ('P0','P1','P2','P3'))
  2. ALTER TABLE agent_messages ADD CONSTRAINT agent_messages_priority_requires_response_check CHECK (priority IN ('P2','P3') OR requires_response = true)
  3. CREATE INDEX idx_agent_messages_open_by_priority ON agent_messages (priority, created_at) WHERE read_at IS NULL

**Why now:** unblocks notifier + build_launch_context code adapters (Task 4/5 of the ARCH-036 plan). Backfill is implicit — DEFAULT 'P2' covers every existing row; every existing row passes the anti-inflation CHECK because P2 is in the allowed set regardless of requires_response.

**Pre-flight done:** counted existing requires_response=false rows; all backfill to P2 cleanly.

**Expected side effects:**
- Every existing agent_messages row gets priority='P2'.
- New INSERTs with priority IN ('P0','P1') must have requires_response=true or be rejected.
- Partial index built.

**Post-apply smoke asks (please run and report back, or tell me to run and report back):**
  (a) INSERT with priority='P0', requires_response=false → expect CHECK violation 23514
  (b) INSERT with priority='P0', requires_response=true, from_agent='cai', to_agent='cc-ihsanos', message_type='question', subject='SMOKETEST', body='x' → expect success
  (c) Count existing rows now having priority='P2' → expect all of them
  (d) Confirm index exists: SELECT indexname FROM pg_indexes WHERE tablename='agent_messages' AND indexname='idx_agent_messages_open_by_priority'
  (e) Delete the P0 smoke row from (b) after verify

If any unexpected state (existing row violates CHECK, index build fails, column collision) — pause and ping me.

Thread: ARCH-036-apply. I'll post the Commit 2 SHA after the code lands.'''

sb.table('agent_messages').insert({
    'from_agent': 'cc-ihsanos',
    'to_agent': 'cai',
    'message_type': 'review_request',
    'subject': 'ARCH-036 migration ready to apply — priority column on agent_messages',
    'body': body,
    'requires_response': True,
}).execute()
print('posted ARCH-036 migration-apply request to cai')
```

- [ ] **Step 2: Tell Musa**

Per CLAUDE.md: when posting requires_response=True, tell Musa in conversation: "I've posted a question to cai — watch Telegram for her reply and paste it here."

- [ ] **Step 3: Wait for CAI reply + verify apply**

When CAI reports applied, run independent verification:
```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -c "
import os
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
# Column exists?
r = sb.table('agent_messages').select('id, priority, requires_response').limit(3).execute()
print('sample rows:')
for row in r.data:
    print(' ', row)
# Every existing row P2?
r2 = sb.table('agent_messages').select('id', count='exact').neq('priority', 'P2').execute()
print('rows NOT priority=P2 (should be 0 right after apply):', r2.count if hasattr(r2, 'count') else 'n/a')
"
```

Expected: all rows show `priority='P2'`. Zero rows are non-P2.

- [ ] **Step 4: Independently verify CHECK constraints**

Run:
```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -c "
import os
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

# Case A: P0 without requires_response=true should be rejected.
try:
    sb.table('agent_messages').insert({
        'from_agent': 'cai',
        'to_agent': 'cc-ihsanos',
        'message_type': 'question',
        'subject': 'SMOKETEST-A-should-fail',
        'body': 'x',
        'requires_response': False,
        'priority': 'P0',
    }).execute()
    print('Case A: UNEXPECTED SUCCESS — CHECK not enforced')
except Exception as e:
    print('Case A (P0+requires_response=false rejected): PASS')
    print('  err:', str(e)[:200])

# Case B: bad priority value should be rejected.
try:
    sb.table('agent_messages').insert({
        'from_agent': 'cai',
        'to_agent': 'cc-ihsanos',
        'message_type': 'question',
        'subject': 'SMOKETEST-B-should-fail',
        'body': 'x',
        'requires_response': True,
        'priority': 'P9',
    }).execute()
    print('Case B: UNEXPECTED SUCCESS — value CHECK not enforced')
except Exception as e:
    print('Case B (P9 rejected): PASS')
    print('  err:', str(e)[:200])

# Case C: valid P0+requires_response=true succeeds, then delete.
r = sb.table('agent_messages').insert({
    'from_agent': 'cai',
    'to_agent': 'cc-ihsanos',
    'message_type': 'question',
    'subject': 'SMOKETEST-C-valid-P0',
    'body': 'x',
    'requires_response': True,
    'priority': 'P0',
}).execute()
msg_id = r.data[0]['id']
print(f'Case C (P0+requires_response=true accepted): PASS — msg id={msg_id}')
sb.table('agent_messages').delete().eq('id', msg_id).execute()
print('  cleanup done')
"
```

Expected: all three cases print PASS.

- [ ] **Step 5: Record outcome inline below**

- **Task 3 outcome (YYYY-MM-DD):** fill in actual — "all 3 CHECK cases PASS; N existing rows backfilled to P2; idx_agent_messages_open_by_priority present".

If any case fails → STOP. Do not proceed to Task 4 with a broken CHECK.

---

## Task 4: agent_messages_poll.py — priority select, sort, glyph, P3 suppression (TDD)

**Files:**
- Modify: `/Users/sheikhmusa/wingmen/orchestrator/nervous_system/agent_messages_poll.py`
- Modify: `/Users/sheikhmusa/wingmen/orchestrator/tests/test_agent_messages_poll.py`

### Step 1 — Write failing tests (RED)

- [ ] **Step 1a: Append new test class to the test file**

Append to `tests/test_agent_messages_poll.py` (after the existing `TestBannedPrefixRejection` class from ARCH-035):

```python
# ARCH-036: priority glyph prefix + P3 suppression
class TestPriorityFormat:
    """Per ARCH-036: _format_telegram prepends priority glyph (🔴 P0, 🟠 P1,
    🟡 P2) to every Telegram message, and returns None for P3 so the caller
    skips Telegram send entirely. P3 still appears in boot-briefing inbox
    and table scans; it just doesn't interrupt Musa's phone."""

    def _msg(self, priority="P2", requires_response=False, message_type="update",
             subject="X", from_agent="cai", to_agent="musa"):
        return {
            "id": 1,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "message_type": message_type,
            "subject": subject,
            "body": "body-text",
            "requires_response": requires_response,
            "priority": priority,
        }

    def test_p0_prepends_red_glyph(self):
        from nervous_system.agent_messages_poll import _format_telegram
        out = _format_telegram(self._msg(priority="P0", requires_response=True))
        assert out is not None
        assert out.startswith("\U0001f534 ")  # 🔴

    def test_p1_prepends_orange_glyph(self):
        from nervous_system.agent_messages_poll import _format_telegram
        out = _format_telegram(self._msg(priority="P1", requires_response=True))
        assert out is not None
        assert out.startswith("\U0001f7e0 ")  # 🟠

    def test_p2_prepends_yellow_glyph(self):
        from nervous_system.agent_messages_poll import _format_telegram
        out = _format_telegram(self._msg(priority="P2"))
        assert out is not None
        assert out.startswith("\U0001f7e1 ")  # 🟡

    def test_p3_returns_none(self):
        from nervous_system.agent_messages_poll import _format_telegram
        out = _format_telegram(self._msg(priority="P3"))
        assert out is None, "P3 must be suppressed from Telegram entirely"

    def test_missing_priority_defaults_to_p2(self):
        # Defensive — if the row is malformed / pre-migration / test fixture
        # missing priority, default to P2 (yellow) rather than crashing.
        from nervous_system.agent_messages_poll import _format_telegram
        m = self._msg()
        del m["priority"]
        out = _format_telegram(m)
        assert out is not None
        assert out.startswith("\U0001f7e1 ")  # 🟡

    def test_null_priority_defaults_to_p2(self):
        from nervous_system.agent_messages_poll import _format_telegram
        out = _format_telegram(self._msg(priority=None))
        assert out is not None
        assert out.startswith("\U0001f7e1 ")  # 🟡

    def test_p0_blocker_glyph_before_existing_blocker_format(self):
        # Composition check — priority glyph wraps the existing per-type
        # formatter output, it does not replace it.
        from nervous_system.agent_messages_poll import _format_telegram
        out = _format_telegram(self._msg(
            priority="P0", requires_response=True,
            message_type="blocker", subject="payment down",
        ))
        assert out is not None
        assert out.startswith("\U0001f534 ")           # 🔴 first
        assert "\U0001f6a8" in out                     # 🚨 BLOCKER still appears
        assert "payment down" in out

    def test_p0_requires_response_uses_needs_input_format(self):
        # When requires_response=true the existing formatter returns the
        # "CC needs your input" format; glyph still prepends.
        from nervous_system.agent_messages_poll import _format_telegram
        out = _format_telegram(self._msg(
            priority="P0", requires_response=True,
            message_type="question", subject="scope check",
        ))
        assert out is not None
        assert out.startswith("\U0001f534 ")
        assert "CC needs your input" in out
```

- [ ] **Step 1b: Run tests, verify RED**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agent_messages_poll.py::TestPriorityFormat -v
```

Expected: all 8 new tests FAIL — most will fail because the existing `_format_telegram` does not prepend any glyph. `test_p3_returns_none` may pass or fail depending on the current default path (currently returns `"📦 {subject}"` for updates — so it will FAIL, which is correct).

If any test ERRORS (import error, attribute error) rather than FAILS, fix the test before proceeding — can't distinguish "RED for right reason" from "broken test" otherwise.

### Step 2 — Make tests pass (GREEN)

- [ ] **Step 2a: Refactor _format_telegram into wrapper + body helper**

Edit `/Users/sheikhmusa/wingmen/orchestrator/nervous_system/agent_messages_poll.py`.

**Target:** the existing `_format_telegram` function (lines ~83–150 per current file). Rename it to `_format_telegram_body` and add a new thin `_format_telegram` wrapper above it that applies the priority glyph and P3 suppression.

Use Edit with old_string = the exact current `_format_telegram` signature and docstring:

```python
def _format_telegram(msg: dict) -> str | None:
    """Format an agent_messages row into a Telegram string.

    Returns None if this message should not be routed to Telegram.
    """
```

new_string:

```python
# ARCH-036: priority glyph prefix + P3 suppression.
# P0/P1/P2 prepend a colored circle. P3 is suppressed from Telegram entirely
# (Telegram is interrupt-capable, P3 is passive FYI). See CAI-RESP-047 for why
# P3 suppression is load-bearing alongside the ARCH-035 banned-prefix filter.
_PRIORITY_GLYPH = {"P0": "\U0001f534", "P1": "\U0001f7e0", "P2": "\U0001f7e1"}


def _format_telegram(msg: dict) -> str | None:
    """Format an agent_messages row into a Telegram string with priority glyph.

    Returns None if this message should not be routed to Telegram (P3, or the
    body formatter filtered it out e.g. CC-to-CC peer traffic).
    """
    priority = msg.get("priority") or "P2"
    if priority == "P3":
        return None
    base = _format_telegram_body(msg)
    if base is None:
        return None
    glyph = _PRIORITY_GLYPH.get(priority, _PRIORITY_GLYPH["P2"])
    return f"{glyph} {base}"


def _format_telegram_body(msg: dict) -> str | None:
    """Format an agent_messages row into a Telegram string (no priority glyph).

    Returns None if this message should not be routed to Telegram.
    """
```

The rest of the function body (starting with `from_agent: str = msg.get("from_agent", "?")`) remains identical — the docstring-below-signature change is the only modification to the original logic.

- [ ] **Step 2b: Add priority to the SELECT + fix ORDER BY**

Find this block in `poll_agent_messages` (lines ~162–168 per current file):

```python
        result = await supabase.table("agent_messages").select(
            "id, from_agent, to_agent, message_type, subject, body, "
            "requires_response, created_at"
        ).is_("read_at", "null").is_(
            "forwarded_to_telegram_at", "null"
        ).order("created_at", desc=False).execute()
```

Replace with:

```python
        # ARCH-036: sort priority-first so urgent traffic drains before backlog.
        result = await supabase.table("agent_messages").select(
            "id, from_agent, to_agent, message_type, subject, body, "
            "requires_response, priority, created_at"
        ).is_("read_at", "null").is_(
            "forwarded_to_telegram_at", "null"
        ).order("priority", desc=False).order(
            "requires_response", desc=True
        ).order("created_at", desc=False).execute()
```

- [ ] **Step 2c: Run the new tests, verify GREEN**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agent_messages_poll.py::TestPriorityFormat -v
```

Expected: 8/8 PASS.

- [ ] **Step 2d: Full poll-suite regression check**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agent_messages_poll.py -v
```

Expected: all pre-existing tests still PASS (including the ARCH-035 `TestBannedPrefixRejection` class). No new failures.

- [ ] **Step 2e: Full project regression check**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest -x 2>&1 | tail -15
```

Expected: same pass/fail baseline as STATUS.md reports (post-ARCH-035 = 355 pass, 7 pre-existing failures, plus now +8 from this task = 363 pass). No NEW failures. If a new failure appears, diagnose before moving on.

Do NOT git-add yet — Commit 2 stages all 4 files atomically.

---

## Task 5: build_launch_context.py — priority sort + glyph in inbox

**Files:**
- Modify: `/Users/sheikhmusa/wingmen/orchestrator/scripts/build_launch_context.py`

- [ ] **Step 1: Update SELECT to include priority**

Find the inbox query (lines ~107–117 per current file):

```python
    # ── 3. Unread inbox (requires_response first) ─────────────────────────────
    inbox = (
        client.table("agent_messages")
        .select("id,from_agent,to_agent,message_type,subject,body,requires_response,created_at")
        .or_(f"to_agent.eq.{agent_id},to_agent.is.null")
        .is_("read_at", "null")
        .order("requires_response", desc=True)
        .order("created_at", desc=False)
        .execute()
        .data
    )
```

Replace with:

```python
    # ── 3. Unread inbox (ARCH-036 priority ASC, requires_response DESC, created_at ASC) ─
    inbox = (
        client.table("agent_messages")
        .select("id,from_agent,to_agent,message_type,subject,body,requires_response,priority,created_at")
        .or_(f"to_agent.eq.{agent_id},to_agent.is.null")
        .is_("read_at", "null")
        .order("priority", desc=False)
        .order("requires_response", desc=True)
        .order("created_at", desc=False)
        .execute()
        .data
    )
```

- [ ] **Step 2: Prepend priority glyph in the inbox row formatter**

Find the inbox rendering block (lines ~121–133):

```python
        for m in inbox:
            flag = "[NEEDS RESPONSE] " if m.get("requires_response") and not m.get("responded_at") else ""
            to = m.get("to_agent") or "broadcast"
            parts.append(
                f"  #{m['id']} {flag}[{m['message_type']}] "
                f"{m['from_agent']}→{to}: {m.get('subject','')}"
            )
            if m.get("body"):
                # Indent body, truncate at 400 chars
                body = m["body"][:400] + ("…" if len(m["body"]) > 400 else "")
                for line in body.splitlines():
                    parts.append(f"    {line}")
```

Replace with:

```python
        # ARCH-036: [Pn] tag shows priority; glyph reserved for Telegram (P3
        # suppressed there). Boot briefing SHOWS all priorities including P3.
        _PRIO_TAG = {"P0": "[P0]", "P1": "[P1]", "P2": "[P2]", "P3": "[P3]"}
        for m in inbox:
            flag = "[NEEDS RESPONSE] " if m.get("requires_response") and not m.get("responded_at") else ""
            prio = _PRIO_TAG.get(m.get("priority") or "P2", "[P2]")
            to = m.get("to_agent") or "broadcast"
            parts.append(
                f"  #{m['id']} {prio} {flag}[{m['message_type']}] "
                f"{m['from_agent']}→{to}: {m.get('subject','')}"
            )
            if m.get("body"):
                # Indent body, truncate at 400 chars
                body = m["body"][:400] + ("…" if len(m["body"]) > 400 else "")
                for line in body.splitlines():
                    parts.append(f"    {line}")
```

Rationale for `[P0]` tag over emoji in the boot briefing: the briefing is pasted into the CC prompt as plain text and must be grep-friendly. Telegram gets the colored-glyph version from `agent_messages_poll.py`.

- [ ] **Step 3: Dry-run verify**

First, insert a test row at each priority level (cleanup at end of step):

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -c "
import os
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
ids = []
for prio, req in [('P0', True), ('P1', True), ('P2', False), ('P3', False)]:
    r = sb.table('agent_messages').insert({
        'from_agent': 'cai',
        'to_agent': 'cc-ihsanos',
        'message_type': 'question',
        'subject': f'ARCH-036-DRYRUN-{prio}',
        'body': f'dryrun {prio}',
        'requires_response': req,
        'priority': prio,
    }).execute()
    ids.append(r.data[0]['id'])
print('inserted test row ids:', ids)
# Write ids to /tmp so the cleanup step below can read them
with open('/tmp/arch036_dryrun_ids.txt', 'w') as f:
    f.write(','.join(str(i) for i in ids))
"
```

Then run the builder:

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m scripts.build_launch_context --agent cc-ihsanos --dry-run 2>/dev/null | grep -E "\[P[0-3]\]|ARCH-036-DRYRUN"
```

Expected output shape — four lines in this exact priority order (P0, P1, P2, P3):
```
  #N [P0] [NEEDS RESPONSE] [question] cai→cc-ihsanos: ARCH-036-DRYRUN-P0
  #M [P1] [NEEDS RESPONSE] [question] cai→cc-ihsanos: ARCH-036-DRYRUN-P1
  #K [P2] [question] cai→cc-ihsanos: ARCH-036-DRYRUN-P2
  #J [P3] [question] cai→cc-ihsanos: ARCH-036-DRYRUN-P3
```

If the order is wrong or a tag is missing → diagnose before proceeding.

- [ ] **Step 4: Clean up dry-run rows**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -c "
import os
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
with open('/tmp/arch036_dryrun_ids.txt') as f:
    ids = [int(x) for x in f.read().split(',') if x]
sb.table('agent_messages').delete().in_('id', ids).execute()
print('deleted dryrun rows:', ids)
"
rm -f /tmp/arch036_dryrun_ids.txt
```

Do NOT git-add yet.

---

## Task 6: Live wireup verification

**Files:** none modified. End-to-end live verify against deployed code.

**Precondition:** Tasks 2–5 complete. Migration applied (Task 3). All files modified locally but NOT committed yet.

**Note on deployed-path coverage:** the orchestrator runs under launchd. For the notifier to exercise the new priority path, launchd must restart so it picks up the modified `agent_messages_poll.py`. Two options:

- **Option A (preferred, actual deployed path):** cycle orchestrator after Commit 2 push. Then run this task's smoke against the live poller. This is the "true" verify.
- **Option B (simulated if launchd can't be cycled during this session):** import `_format_telegram` directly in a `.venv/bin/python -c` harness against live DB rows. Unit tests already cover the format logic; this just confirms the live DB schema + new column shape line up.

Pick A if feasible. If not, use B and note in STATUS.md that the deployed path will self-verify on next orchestrator cycle.

- [ ] **Step 1: Insert four probe rows at each priority level**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -c "
import os
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
ids = []
for prio, req in [('P0', True), ('P1', True), ('P2', False), ('P3', False)]:
    r = sb.table('agent_messages').insert({
        'from_agent': 'cai',
        'to_agent': 'musa',  # musa = forwards to Telegram per TELEGRAM_ROUTED_TARGETS
        'message_type': 'update',
        'subject': f'ARCH-036-LIVE-{prio}',
        'body': f'live probe {prio}',
        'requires_response': req,
        'priority': prio,
    }).execute()
    ids.append((prio, r.data[0]['id']))
print('inserted live probe rows:', ids)
import pathlib
pathlib.Path('/tmp/arch036_live_ids.txt').write_text(','.join(f'{p}:{i}' for p,i in ids))
"
```

- [ ] **Step 2a (Option A only): Verify Telegram receives P0/P1/P2, not P3**

Wait for one poll cycle (~5 min).

Check Musa's Telegram — expect 3 messages (P0 🔴, P1 🟠, P2 🟡) with glyphs prepended. P3 must NOT appear.

Check notification_log:
```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -c "
import os
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
r = sb.table('notification_log').select('id, decision_ref, message_text, created_at').like('decision_ref', 'ARCH-036-LIVE-%').order('created_at', desc=True).execute()
print(f'notification_log rows for ARCH-036-LIVE: {len(r.data)}')
for row in r.data:
    snippet = (row.get('message_text') or '')[:80]
    print(f'  #{row[\"id\"]} {row[\"decision_ref\"]}: {snippet}')
"
```

Expected: exactly 3 rows (P0, P1, P2). Zero rows for P3.

- [ ] **Step 2b (Option B — simulated if launchd not cycled): Direct module harness**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -c "
import os
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
# Read back the 4 probe rows and run _format_telegram directly
from nervous_system.agent_messages_poll import _format_telegram
r = sb.table('agent_messages').select('*').like('subject', 'ARCH-036-LIVE-%').order('priority').execute()
for m in r.data:
    out = _format_telegram(m)
    print(f'{m[\"priority\"]} → {repr(out[:80]) if out else None}')
"
```

Expected output (exact glyphs may render as escape sequences in terminal):
```
P0 → '\U0001f534 📦 ARCH-036-LIVE-P0'   # actually shown as 🔴 📦 ...
P1 → '\U0001f7e0 📦 ARCH-036-LIVE-P1'
P2 → '\U0001f7e1 📦 ARCH-036-LIVE-P2'
P3 → None
```

(P0/P1 because `requires_response=True` would actually trigger the "CC needs your input" branch — the exact body text depends on message_type + requires_response; the critical check is glyph for P0/P1/P2 and None for P3.)

- [ ] **Step 3: Verify build_launch_context sort order on live rows**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m scripts.build_launch_context --agent cc-ihsanos --dry-run 2>/dev/null | grep -E "ARCH-036-LIVE"
```

Expected: four lines in priority-ASC order (P0 first, P3 last).

- [ ] **Step 4: Verify the partial index is being consulted**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -c "
import os
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
# supabase-py can't EXPLAIN directly; check pg_stat_user_indexes via MCP if available.
# For our purposes: check the index exists and has non-null scans after the smoke.
r = sb.rpc('query_pg_stat_indexes', {'p_indexname': 'idx_agent_messages_open_by_priority'}).execute() if False else None
# Fallback — ask CAI via a small status msg OR skip if MCP not available. Index
# correctness is checked at DDL-apply time by Task 3 Step 4(d); runtime check
# is nice-to-have, not must-have.
print('index runtime-stats check: skipped (nice-to-have, DDL-time check sufficed in Task 3)')
"
```

- [ ] **Step 5: Clean up probe rows**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -c "
import os
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
sb.table('agent_messages').delete().like('subject', 'ARCH-036-LIVE-%').execute()
print('cleaned live probe rows')
"
rm -f /tmp/arch036_live_ids.txt
```

- [ ] **Step 6: Record outcome inline below**

- **Task 6 outcome (YYYY-MM-DD):** fill in — "3/3 Telegram rows (P0/P1/P2, glyphs correct) + 0 for P3 + build_launch_context shows 4 rows in P0→P3 order. Verify mode: A/B."

---

## Task 7: Commit 2 — atomic code commit (orchestrator)

**Files:** migration SQL (Task 2) + 2 Python files (Tasks 4+5) + test file (Task 4).

- [ ] **Step 1: Stage all Commit-2 files**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add supabase/migrations/20260420_arch036_priority_column.sql
git add nervous_system/agent_messages_poll.py
git add scripts/build_launch_context.py
git add tests/test_agent_messages_poll.py
```

- [ ] **Step 2: Pre-commit diff review**

```bash
git diff --cached --stat
```

Expected: exactly 4 files.
- migration: ~25 lines new
- agent_messages_poll.py: ~25 lines added (glyph dict + wrapper + SELECT/ORDER), ~2 lines changed (rename signature)
- build_launch_context.py: ~4 lines added (priority tag dict + inbox select/order + row formatter tweak)
- test_agent_messages_poll.py: ~80 lines added (TestPriorityFormat class)

```bash
git diff --cached | head -100
```
Sanity-scan for secrets, .env changes, or stray debug prints.

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(arch-036): priority column on narrowed agent_messages

Adds P0-P3 priority with uniform P2 default + anti-inflation CHECK +
partial index on open inbox. Notifier prepends colored-circle glyph
(🔴 P0, 🟠 P1, 🟡 P2) and suppresses P3 from Telegram entirely; boot
briefing shows all priorities with [Pn] text tag for grep-friendliness.

- Migration: ALTER TABLE ADD COLUMN priority + 2 CHECKs + partial index
- agent_messages_poll.py: glyph-prepending _format_telegram wrapper,
  P3 returns None, SELECT order priority ASC/requires_response DESC/
  created_at ASC
- build_launch_context.py: inbox SELECT includes priority, same ORDER
  BY as notifier, [Pn] tag prefixed on each inbox row
- tests: 8 new cases in TestPriorityFormat covering all 4 priority
  levels, glyph composition with existing per-type formats, default-P2
  fallback for missing/null priority

P3 suppression confirmed load-bearing by CAI-RESP-047 — complements
the ARCH-035 banned-prefix filter (structural noise) by catching
semantic low-urgency traffic (completion announcements, FYI heads-ups)
that writers phrase without the banned prefixes.

Parent: ARCH-036 (strategic_decisions), ARCH-035, CAI-RESP-047.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
git push origin main
```

- [ ] **Step 5: Push Commit 1 if not already pushed**

```bash
cd /Users/sheikhmusa/wingmen/projects/ihsanos && git push origin main
```

- [ ] **Step 6: Ask Musa to cycle orchestrator (if Task 6 used Option B)**

If Task 6 ran as Option B (simulated), prompt Musa: "ARCH-036 code is pushed. Please cycle orchestrator launchd to pick up the new poller — `launchctl kickstart -k gui/$UID/<wingmen-label>` or whatever label applies. The deployed path will self-verify on next poll cycle via the P0/P1/P2/P3 matrix I just cleaned up (or I can re-insert probes if you want to watch Telegram in real time)."

---

## Task 8: STATUS.md update + session digest + CAI ping

**Files:**
- Modify: `/Users/sheikhmusa/wingmen/orchestrator/STATUS.md`

- [ ] **Step 1: Prepend ARCH-036 section to STATUS.md**

Read current STATUS.md top section. Add a new "Last Completed (2026-04-20 — ARCH-036 priority column)" block above the ARCH-035 block:

```markdown
## Last Completed (2026-04-20 — ARCH-036 priority column on narrowed agent_messages)

### ARCH-036 — shipped
Plan: `docs/superpowers/plans/2026-04-20-arch-036-priority-column.md`
Migration: `supabase/migrations/20260420_arch036_priority_column.sql` (applied live via CAI MCP, commit `<CODE_SHA>`)
Docs commit: `<DOCS_SHA>` (ihsanos WINGMEN_CONSTRAINTS.md — priority rubric subsection)
Code commit: `<CODE_SHA>` 4-file atomic (migration + notifier + boot-briefing + tests)

**Shape delivered:**
- `agent_messages.priority` column: `TEXT NOT NULL DEFAULT 'P2' CHECK IN ('P0','P1','P2','P3')`
- Anti-inflation CHECK: `(priority IN ('P2','P3') OR requires_response = true)` — P0/P1 structurally require response
- Partial index `idx_agent_messages_open_by_priority ON (priority, created_at) WHERE read_at IS NULL`
- Notifier glyph: 🔴 P0 / 🟠 P1 / 🟡 P2; **P3 suppressed from Telegram entirely** (per CAI-RESP-047 — load-bearing with banned-prefix filter)
- Sort order (both notifier + boot-briefing): `priority ASC, requires_response DESC, created_at ASC`
- Boot briefing shows `[Pn]` text tag (grep-friendly) in the inbox section; Telegram gets colored circles

**Live verification:** [fill in Task 6 outcome — P0/P1/P2 routed with glyphs, P3 suppressed, boot-briefing sort P0→P3]

**Unit test baseline:** 355 → 363 pass (+8 from TestPriorityFormat), 7 pre-existing failures unchanged.

**Known limitations:**
- Re-triage (CAI UPDATE priority on a pending row) not yet gated — authorization arrives with BUG-024 Phase 1 per-agent identity.
- Monthly high-P0/P1-ratio audit query exists in the constraint doc but not yet automated.

Next P0: ARCH-036 follow-ups — none critical. Task #76 closes. Unblocked: #97 (banned-prefix purge cron, already filed), #73 (BUG-024 Phase 1), #55 (LEDGER spec review, paused).
```

Fill `<DOCS_SHA>` and `<CODE_SHA>` from `git log -3 --oneline` in each repo.

- [ ] **Step 2: Commit + push STATUS.md**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator && git add STATUS.md && git commit -m "$(cat <<'EOF'
chore: update STATUS.md — ARCH-036 priority column shipped

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)" && git push origin main
```

- [ ] **Step 3: Post session digest to CAI**

Per memory `feedback_session_digest.md`. Save as `/tmp/arch036_digest.py` (fill SHAs) then run:

```python
import os, json
from dotenv import load_dotenv; load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
from supabase import create_client

DOCS_SHA = "REPLACE"   # ihsanos WINGMEN_CONSTRAINTS.md commit
CODE_SHA = "REPLACE"   # orchestrator Commit 2 atomic
STATUS_SHA = "REPLACE" # orchestrator STATUS.md commit

sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
digest = {
    "arch": "ARCH-036",
    "shipped": True,
    "commits": {"docs": DOCS_SHA, "code": CODE_SHA, "status": STATUS_SHA},
    "migration_applied": True,
    "check_cases_verified": 3,          # P0+requires_response=false rejected, P9 rejected, P0+requires_response=true accepted
    "priority_glyph_tests": 8,          # TestPriorityFormat
    "live_verification": {
        "p0_telegram_glyph": True,
        "p1_telegram_glyph": True,
        "p2_telegram_glyph": True,
        "p3_telegram_suppressed": True,
        "boot_briefing_sort_p0_first": True,
    },
    "launchd_cycled_this_session": False,  # update to True if Option A used
    "tests": {"before": 355, "after": 363, "pre_existing_failures": 7},
    "next": "no P0 follow-ups for ARCH-036 — clean ship",
}
sb.table('agent_messages').insert({
    "from_agent": "cc-ihsanos",
    "to_agent": "cai",
    "message_type": "update",
    "subject": "ARCH-036 shipped — priority column live on narrowed agent_messages",
    "body": json.dumps(digest, indent=2),
    "requires_response": False,
    "priority": "P3",   # digest = FYI = P3 → this row will NOT hit Telegram, which is correct
}).execute()
print("digest posted")
```

Note the dogfood touch: the digest itself is posted as P3 so it doesn't page Musa, exercising the very suppression rule we just built.

- [ ] **Step 4: Update TaskList**

Mark task #76 (`ARCH-036: priority column on narrowed agent_messages`) completed.

---

## Verification Checklist (end of implementation)

- [ ] Commit 1 (ihsanos WINGMEN_CONSTRAINTS.md priority-rubric) pushed
- [ ] Migration SQL file written
- [ ] CAI reports migration applied via MCP; my independent verify shows all rows backfilled to P2 and all 3 CHECK cases PASS
- [ ] `TestPriorityFormat` class: 8/8 tests PASS
- [ ] Full pytest suite: +8 new passes, no NEW failures
- [ ] `build_launch_context --dry-run` shows [P0]→[P3] ordering on probe rows
- [ ] Live wireup: P0/P1/P2 Telegram rows have correct glyph, P3 absent from Telegram (Option A) OR module harness shows glyph/None matrix (Option B)
- [ ] Commit 2 (4 files atomic) pushed
- [ ] Musa cycled orchestrator (or noted deferred)
- [ ] STATUS.md updated + pushed
- [ ] Session digest posted to CAI (as P3, dogfooding)
- [ ] TaskList: #76 closed

---

## Known Limitations (shipping as-is, documented)

1. **Re-triage authorization deferred to BUG-024 Phase 1.** Any `cc-*` agent with service-role credentials could UPDATE another row's priority today — there's no per-agent identity check on priority UPDATEs. The audit query (`SELECT from_agent, priority, COUNT(*) ...`) makes the behaviour observable but not preventable. Same trust model as the rest of the `agent_messages` writes; the GUC tripwire from ARCH-035 only covers `agent_status`.
2. **Priority is point-in-time.** If a P2 row becomes urgent later, writer needs to UPDATE priority. No automatic escalation. Acceptable — manual re-triage is fine at current volume; revisit if audit shows systematic post-INSERT escalations.
3. **P3 still accumulates in boot briefing.** The Telegram suppression doesn't garbage-collect P3 rows. They remain unread in `agent_messages` until the addressed agent reads them. Existing ARCH-035 banned-prefix purge cron (task #97) will pick up some of this; pure-P3 rows survive. Acceptable at current volume.
4. **CAI-RESP-047 implicit follow-up — session digest migration.** CAI noted that session digests arguably belong in a dedicated `session_digests` table rather than `agent_messages`. Not in ARCH-036 scope. Filed as a mental follow-up — if post-ARCH-036 audit shows digest traffic dominates P3 volume, promote to an explicit TASK.
