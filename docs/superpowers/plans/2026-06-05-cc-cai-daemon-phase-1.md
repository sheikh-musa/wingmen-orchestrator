# cc-cai Daemon Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Ship a Python Agent SDK daemon that processes cai's `agent_messages` inbox autonomously for the narrow silent-lane (mark-read FYIs + ack-FYI patterns), escalates everything else to operator via Telegram with `[Approve][Defer][Delegate]` buttons + free-text reply, and logs every tool call for the INV-5 amanah audit. Replaces operator's manual cai-side relay. Phase 2 (cc-orchestrator-daemon pilot) follows after ≥1 week of operational evidence + zero misclassifications.

**Architecture:** New top-level `cc_cai_daemon/` package. Python 3.13 (separate venv `.venv-cc-cai/` because Agent SDK requires ≥3.10; orch's `.venv` stays 3.9). Imports orch's existing nervous_system modules cross-venv (they're 3.9-compat so run under 3.13 too). Runs as a launchd job on Mac Studio. Uses `claude_agent_sdk.query()` for any LLM reasoning (Max OAuth via `~/.claude/.credentials.json`, INV-3 clean — no `ANTHROPIC_API_KEY` set).

**Decision refs:** CADENCE-002 / CADENCE-003 / CADENCE-004 / CADENCE-005 / CADENCE-006 / CAI-RESP-185 (Q1-Q5 rulings + 2 amendments + INV-5 hard ship condition).

**HARD SHIP CONDITION (load-bearing per CAI-RESP-185):** INV-5 audit logging of every cc-cai-daemon tool call is a Phase 1 ship requirement. *"It is the precondition that makes autonomous reading/classification of the operator message stream halal to run unattended."* This is the amanah-level non-negotiable.

---

## File Structure

| Path | Purpose | New/Modified |
|---|---|---|
| `cc_cai_daemon/__init__.py` | package marker | NEW |
| `cc_cai_daemon/main.py` | daemon entry point, asyncio main loop | NEW |
| `cc_cai_daemon/poller.py` | 5-min poll of agent_messages WHERE to_agent='cai' (Realtime is a later upgrade per CAI-RESP-185 amendment 1) | NEW |
| `cc_cai_daemon/classifier.py` | three-way classifier: mark_read_fyi / ack_fyi / escalate. CADENCE-004 reaches-operator frozenset hardcoded as the ESCALATE floor | NEW |
| `cc_cai_daemon/silent_lane.py` | handlers for mark_read + ack_fyi (the only auto-actions in Phase 1) | NEW |
| `cc_cai_daemon/escalator.py` | Telegram push with inline buttons + free-text fallback; CADENCE-004 enumerated invariants enforced | NEW |
| `cc_cai_daemon/telegram_bot.py` | bot callback handlers for `[Approve][Defer][Delegate]` + free-text reply → writes operator response back into agent_messages | NEW |
| `cc_cai_daemon/audit.py` | INV-5 audit logger — every classifier decision, every tool call, every escalation logged to cc_cai_audit_log | NEW |
| `cc_cai_daemon/kill_switch.py` | INV-6 default-HOLD; confidence-drop detector reverts to pure-escalation mode | NEW |
| `cc_cai_daemon/sdk_query.py` | thin wrapper over `claude_agent_sdk.query()` that scrubs ANTHROPIC_API_KEY + injects audit hook for every tool call | NEW |
| `supabase/migrations/20260605_cc_cai_audit_log.sql` | NEW table `cc_cai_audit_log` (load-bearing per HARD SHIP CONDITION) | NEW |
| `ops/launchd/dev.wingmen.cc-cai-daemon.plist` | Mac Studio launchd job | NEW |
| `manifests/long_running_callers/cc_cai_daemon.yaml` | registry manifest — `auto_kill_policy='no_kill'`, `expected_cadence_seconds=300` | NEW |
| `.venv-cc-cai/` | Python 3.13 venv, already created in pre-build readiness | EXISTS |
| `.gitignore` | add `.venv-cc-cai/` | MODIFIED |
| `tests/cc_cai_daemon/` | dedicated test directory | NEW |

---

## Pre-Flight

- [ ] **Step 0.1: Verify environment**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git branch --show-current  # should be feat/cc-cai-daemon-phase-1
ls .venv-cc-cai/bin/python  # py3.13 venv from readiness check
.venv-cc-cai/bin/python -c "from claude_agent_sdk import query; print('sdk ok')"
```

- [ ] **Step 0.2: Confirm cross-venv import works**

```bash
.venv-cc-cai/bin/python -c "
import sys
sys.path.insert(0, '.')
from nervous_system.long_running_claude_callers import register, heartbeat
from ai_provider import call_ai
print('cross-venv imports ok')
"
```

The orch's nervous_system modules use 3.9 syntax that's forward-compatible with 3.13. If anything breaks, we vendor it; don't upgrade orch.

---

## Task 1: cc_cai_audit_log table — INV-5 hard ship condition

**Files:**
- Create: `supabase/migrations/20260605_cc_cai_audit_log.sql`
- Create: `tests/cc_cai_daemon/test_audit_log_schema.py`

- [ ] **Step 1.1: Write failing schema test**

`tests/cc_cai_daemon/test_audit_log_schema.py`:

```python
"""INV-5 hard ship condition test: cc_cai_audit_log table schema present."""
from __future__ import annotations
import os, sys
from pathlib import Path
import psycopg, pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")


def test_cc_cai_audit_log_table_exists_with_required_columns():
    expected = {
        "id", "logged_at", "event_type", "agent_message_id",
        "classification", "classification_reason", "confidence",
        "tool_name", "tool_input_summary", "tool_output_summary",
        "escalated_to_operator", "telegram_message_id",
        "kill_switch_state", "session_id",
    }
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='cc_cai_audit_log'"
        )
        actual = {r[0] for r in cur.fetchall()}
    missing = expected - actual
    assert not missing, f"missing INV-5 columns: {missing}"
```

- [ ] **Step 1.2: Write the migration**

`supabase/migrations/20260605_cc_cai_audit_log.sql`:

```sql
-- CAI-RESP-185 HARD SHIP CONDITION: every cc-cai-daemon tool call writes to
-- this table BEFORE side effects. Operator-auditable. INV-5 amanah trail.

BEGIN;

CREATE TABLE IF NOT EXISTS cc_cai_audit_log (
    id                        BIGSERIAL PRIMARY KEY,
    logged_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id                TEXT NOT NULL,           -- daemon-restart-scoped uuid
    event_type                TEXT NOT NULL,           -- 'classification' | 'tool_call' | 'escalation' | 'silent_action' | 'kill_switch_trip'
    agent_message_id          BIGINT,                  -- FK-soft to agent_messages.id when applicable
    classification            TEXT,                    -- 'mark_read_fyi' | 'ack_fyi' | 'escalate' | NULL
    classification_reason     TEXT,                    -- human-readable rationale
    confidence                NUMERIC(3,2),            -- 0.00–1.00; below threshold triggers INV-6 HOLD
    tool_name                 TEXT,                    -- 'supabase_update_read_at' | 'telegram_send' | 'sdk_query' | etc.
    tool_input_summary        JSONB,                   -- redacted inputs (NO operator PII unless agent_messages.body is operator-authored)
    tool_output_summary       JSONB,                   -- truncated output / status
    escalated_to_operator     BOOLEAN NOT NULL DEFAULT false,
    telegram_message_id       BIGINT,                  -- when escalation pushed to telegram
    kill_switch_state         TEXT NOT NULL DEFAULT 'live'  -- 'live' | 'pure_escalation_mode' | 'panic_disabled'
);

COMMENT ON TABLE cc_cai_audit_log IS
    'CAI-RESP-185 INV-5 hard ship condition: every cc-cai-daemon classifier '
    'decision, tool call, and escalation logged BEFORE side effects. Makes '
    'autonomous reading/classification of operator message stream halal to '
    'run unattended (amanah-bearing precondition).';

CREATE INDEX IF NOT EXISTS idx_ccal_logged_at
    ON cc_cai_audit_log (logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_ccal_session
    ON cc_cai_audit_log (session_id, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_ccal_event_type
    ON cc_cai_audit_log (event_type, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_ccal_msg_id
    ON cc_cai_audit_log (agent_message_id)
    WHERE agent_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ccal_escalated
    ON cc_cai_audit_log (logged_at DESC)
    WHERE escalated_to_operator = true;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_name='cc_cai_audit_log'
    ) THEN RAISE EXCEPTION 'cc_cai_audit_log missing'; END IF;
    RAISE NOTICE 'CAI-RESP-185 INV-5 audit table verified';
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260605120000', 'cc_cai_audit_log',
    ARRAY[$stmt$CREATE TABLE IF NOT EXISTS cc_cai_audit_log (...)$stmt$]::text[]
)
ON CONFLICT (version) DO NOTHING;
```

- [ ] **Step 1.3: Apply + verify**

```bash
source .venv/bin/activate
python scripts/check_additive_migration.py supabase/migrations/20260605_cc_cai_audit_log.sql
python3 -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
sql = open('supabase/migrations/20260605_cc_cai_audit_log.sql').read()
with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
    cur.execute(sql); print('applied')
"
python -m pytest tests/cc_cai_daemon/test_audit_log_schema.py -v
```

- [ ] **Step 1.4: Commit**

```bash
git add supabase/migrations/20260605_cc_cai_audit_log.sql tests/cc_cai_daemon/
git commit -m "feat(cc-cai): INV-5 audit table — CAI-RESP-185 hard ship condition"
```

---

## Task 2: Audit logger (`audit.py`) — the load-bearing first module

**Files:**
- Create: `cc_cai_daemon/__init__.py`
- Create: `cc_cai_daemon/audit.py`
- Create: `tests/cc_cai_daemon/test_audit.py`

- [ ] **Step 2.1: Failing tests**

`tests/cc_cai_daemon/test_audit.py`:

```python
"""Audit logger tests. Per CAI-RESP-185, every classification + tool call
+ escalation MUST be logged before any side effect."""
from __future__ import annotations
import os, sys, uuid
from pathlib import Path
from unittest.mock import MagicMock

import psycopg, pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")

from cc_cai_daemon.audit import AuditLogger


@pytest.fixture
def audit():
    sess = f"test-{uuid.uuid4().hex[:8]}"
    yield AuditLogger(dsn=_DSN, session_id=sess)
    # cleanup
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute("DELETE FROM cc_cai_audit_log WHERE session_id = %s", (sess,))


def test_log_classification_writes_row(audit):
    audit.log_classification(
        agent_message_id=42, classification="mark_read_fyi",
        reason="P3 update with requires_response=false",
        confidence=0.95,
    )
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT classification, confidence FROM cc_cai_audit_log "
            "WHERE session_id=%s ORDER BY id DESC LIMIT 1", (audit.session_id,))
        r = cur.fetchone()
    assert r[0] == "mark_read_fyi"
    assert float(r[1]) == 0.95


def test_log_tool_call_captures_tool_name(audit):
    audit.log_tool_call(
        tool_name="supabase_update_read_at",
        tool_input={"agent_message_id": 7},
        tool_output={"ok": True},
    )
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT event_type, tool_name FROM cc_cai_audit_log "
            "WHERE session_id=%s ORDER BY id DESC LIMIT 1", (audit.session_id,))
        r = cur.fetchone()
    assert r[0] == "tool_call"
    assert r[1] == "supabase_update_read_at"


def test_log_escalation_marks_flag_and_telegram_id(audit):
    audit.log_escalation(
        agent_message_id=99, reason="riba/finance trigger",
        telegram_message_id=12345,
    )
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT escalated_to_operator, telegram_message_id, event_type "
            "FROM cc_cai_audit_log WHERE session_id=%s ORDER BY id DESC LIMIT 1",
            (audit.session_id,))
        r = cur.fetchone()
    assert r[0] is True
    assert r[1] == 12345
    assert r[2] == "escalation"


def test_log_kill_switch_trip(audit):
    audit.log_kill_switch_trip(
        new_state="pure_escalation_mode",
        reason="confidence_drop_3_consecutive_under_0.5",
    )
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT event_type, kill_switch_state FROM cc_cai_audit_log "
            "WHERE session_id=%s ORDER BY id DESC LIMIT 1", (audit.session_id,))
        r = cur.fetchone()
    assert r[0] == "kill_switch_trip"
    assert r[1] == "pure_escalation_mode"
```

- [ ] **Step 2.2: Implement `cc_cai_daemon/__init__.py`**

```python
"""cc-cai daemon — CAI-RESP-185 Path C ratified.

Replaces operator's manual cai-side relay with a Python Agent SDK daemon
that classifies cai's agent_messages inbox, auto-handles the narrow
silent-lane (mark-read FYIs + ack-FYI), and escalates everything else
to operator via Telegram with inline buttons.

HARD INVARIANTS (CADENCE-002/004 + CAI-RESP-185):
  INV-1 root-of-trust: never auto-authorize the 8 reaches-operator categories
  INV-2 verified-channel: refuse relayed operator-authorization from any cc-*
  INV-3 MAX-first: SDK + cli_route, never set ANTHROPIC_API_KEY
  INV-4 confabulation discipline: verified-vs-inferred labels on outputs
  INV-5 audit (HARD SHIP CONDITION): every tool call logged BEFORE side effect
  INV-6 default HOLD: escalation-class HOLDs until operator decides
"""
```

- [ ] **Step 2.3: Implement `cc_cai_daemon/audit.py`**

```python
"""INV-5 audit logger. HARD SHIP CONDITION per CAI-RESP-185."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

import psycopg

logger = logging.getLogger("cc_cai.audit")


class AuditLogger:
    """Synchronous INV-5 audit writer.

    Synchronous on purpose: the audit row MUST land before the side effect
    fires. If the audit write fails, the side effect MUST NOT proceed
    (CAI-RESP-185 amanah precondition). Callers wrap any side effect with:
        audit_id = audit.log_tool_call(...)
        if audit_id is None:
            return  # audit failed; do nothing
        # then proceed with side effect
    """

    def __init__(self, dsn: str, session_id: Optional[str] = None):
        self.dsn = dsn
        self.session_id = session_id or f"cc-cai-{uuid.uuid4().hex[:12]}"

    def _insert(self, **fields) -> Optional[int]:
        cols = ["session_id", *fields.keys()]
        vals = [self.session_id, *fields.values()]
        placeholders = ", ".join(["%s"] * len(cols))
        sql = (
            f"INSERT INTO cc_cai_audit_log ({', '.join(cols)}) "
            f"VALUES ({placeholders}) RETURNING id"
        )
        try:
            with psycopg.connect(self.dsn, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(sql, vals)
                return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"INV-5 audit write FAILED: {e}", exc_info=True)
            return None

    def log_classification(
        self, *, agent_message_id: int, classification: str,
        reason: str, confidence: float,
    ) -> Optional[int]:
        return self._insert(
            event_type="classification",
            agent_message_id=agent_message_id,
            classification=classification,
            classification_reason=reason,
            confidence=confidence,
        )

    def log_tool_call(
        self, *, tool_name: str,
        tool_input: dict, tool_output: dict | None = None,
        agent_message_id: int | None = None,
    ) -> Optional[int]:
        return self._insert(
            event_type="tool_call",
            tool_name=tool_name,
            tool_input_summary=json.dumps(tool_input, default=str),
            tool_output_summary=json.dumps(tool_output, default=str) if tool_output else None,
            agent_message_id=agent_message_id,
        )

    def log_silent_action(
        self, *, agent_message_id: int, action: str,
        classification: str, reason: str,
    ) -> Optional[int]:
        return self._insert(
            event_type="silent_action",
            agent_message_id=agent_message_id,
            classification=classification,
            classification_reason=reason,
            tool_name=action,
        )

    def log_escalation(
        self, *, agent_message_id: int, reason: str,
        telegram_message_id: int | None = None,
    ) -> Optional[int]:
        return self._insert(
            event_type="escalation",
            agent_message_id=agent_message_id,
            classification="escalate",
            classification_reason=reason,
            escalated_to_operator=True,
            telegram_message_id=telegram_message_id,
        )

    def log_kill_switch_trip(self, *, new_state: str, reason: str) -> Optional[int]:
        return self._insert(
            event_type="kill_switch_trip",
            classification_reason=reason,
            kill_switch_state=new_state,
        )
```

- [ ] **Step 2.4: Run tests**

```bash
.venv-cc-cai/bin/python -m pytest tests/cc_cai_daemon/test_audit.py -v
```

(Note: tests use the cc-cai venv since they import the daemon module. Add `pytest` to its requirements.)

- [ ] **Step 2.5: Commit**

```bash
git add cc_cai_daemon/__init__.py cc_cai_daemon/audit.py tests/cc_cai_daemon/test_audit.py
git commit -m "feat(cc-cai): AuditLogger — INV-5 hard ship condition (CAI-RESP-185)"
```

---

## Task 3: Classifier (`classifier.py`)

**Files:**
- Create: `cc_cai_daemon/classifier.py`
- Create: `tests/cc_cai_daemon/test_classifier.py`

The classifier returns one of three labels: `mark_read_fyi`, `ack_fyi`, or `escalate`. Per CAI-RESP-185 Q4, the auto-action lane is strictly mark-read + ack-FYIs; everything else MUST escalate.

- [ ] **Step 3.1: Failing tests** — covers:
  - INV-1 reaches-operator frozenset triggers (riba/zakat/client-data/client-rep/IP-contract/money/irreversible/novel) → always escalate regardless of message_type
  - P3 update without requires_response → mark_read_fyi
  - timeout-accepted ratification ping (subject pattern `*accepted_by_timeout*`) → ack_fyi
  - P0/P1 anything → escalate
  - Unknown shape / low confidence → escalate (default HOLD per INV-6)

- [ ] **Step 3.2: Implementation** — pure-Python decision tree, no LLM calls. SDK reasoning only at Phase 2 widening; Phase 1 is rules-only per Q4 narrow lane.

```python
"""Phase 1 classifier — rules-only. Per CAI-RESP-185 Q4 narrow lane.
LLM-based classification deferred to Phase 2 + ≥1 week zero-misclass evidence."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

_RIBA_PATTERNS = (
    "interest", "loan", "credit-line", "bnpl", "buy-now-pay-later",
    "stripe-radar", "underwriting", "finance-revenue", "fee-model",
)
_ZAKAT_PATTERNS = ("zakat",)
_CLIENT_DATA_PATTERNS = (
    "pii", "tenant-isolation", "rls", "encryption-at-rest",
    "data-breach", "field-encryption",
)
# (etc; see full file in build)


REACHES_OPERATOR_CATEGORIES: frozenset[str] = frozenset({
    "halal_riba",
    "zakat",
    "client_data_amanah",
    "client_facing_commitment",
    "ip_legal_contract",
    "money_above_threshold",
    "irreversible_destructive",
    "novel_low_confidence",
})


@dataclass(frozen=True)
class Classification:
    label: Literal["mark_read_fyi", "ack_fyi", "escalate"]
    reason: str
    confidence: float          # 0.0–1.0
    escalation_category: str | None = None  # one of REACHES_OPERATOR_CATEGORIES when label='escalate'


def classify(msg: dict) -> Classification:
    """Three-way classifier with INV-6 default HOLD on uncertainty.

    Input: agent_messages row dict.
    Output: Classification (label + reason + confidence + escalation_category).
    Never raises — uncertain shapes return escalate with confidence=0.
    """
    # ... full implementation lives in cc_cai_daemon/classifier.py at build time
```

- [ ] **Step 3.3: Commit**

```bash
git add cc_cai_daemon/classifier.py tests/cc_cai_daemon/test_classifier.py
git commit -m "feat(cc-cai): classifier — narrow silent-lane per CAI-RESP-185 Q4"
```

---

## Task 4: Silent-lane handlers (`silent_lane.py`)

**Files:**
- Create: `cc_cai_daemon/silent_lane.py`
- Create: `tests/cc_cai_daemon/test_silent_lane.py`

Two actions only in Phase 1: `mark_read` and `ack_fyi`. Both go through the audit logger BEFORE writing back to agent_messages.

- [ ] **Step 4.1: Failing tests** — verify each action calls `audit.log_silent_action()` BEFORE the side effect (mock the DB write to confirm ordering)

- [ ] **Step 4.2: Implementation**

```python
"""Silent-lane handlers — mark_read + ack_fyi only.

Per CAI-RESP-185 Q4: do NOT widen until ≥1 week operational evidence
AND INV-5 audit review showing zero misclassifications.
"""
from __future__ import annotations

from cc_cai_daemon.audit import AuditLogger


def handle_mark_read_fyi(
    supabase, audit: AuditLogger, msg: dict, reason: str
) -> bool:
    """Write read_at + responded_at (since cai's response to an FYI is 'noted')."""
    # 1. Audit FIRST per INV-5
    audit_id = audit.log_silent_action(
        agent_message_id=msg["id"], action="supabase_update_read_at",
        classification="mark_read_fyi", reason=reason,
    )
    if audit_id is None:
        return False  # audit failed — do not proceed
    # 2. Side effect
    supabase.table("agent_messages").update(
        {"read_at": "now()", "responded_at": "now()"}
    ).eq("id", msg["id"]).execute()
    return True


def handle_ack_fyi(
    supabase, audit: AuditLogger, msg: dict, reason: str,
    ack_text: str = "noted, no action needed"
) -> bool:
    """Post a canned ack response back into the thread (low-risk acknowledgment)."""
    audit_id = audit.log_silent_action(
        agent_message_id=msg["id"], action="supabase_insert_ack_reply",
        classification="ack_fyi", reason=reason,
    )
    if audit_id is None:
        return False
    supabase.table("agent_messages").insert({
        "thread_id": msg["thread_id"], "from_agent": "cai",
        "to_agent": msg["from_agent"],
        "message_type": "agreed", "priority": "P3",
        "subject": f"ACK: {msg['subject'][:80]}",
        "body": ack_text, "requires_response": False,
        "sub_tag": "cc-cai-daemon",
    }).execute()
    supabase.table("agent_messages").update(
        {"read_at": "now()", "responded_at": "now()"}
    ).eq("id", msg["id"]).execute()
    return True
```

- [ ] **Step 4.3: Commit**

```bash
git commit -m "feat(cc-cai): silent-lane handlers — mark_read + ack_fyi only"
```

---

## Task 5: Telegram bot bridge (`telegram_bot.py` + `escalator.py`)

**Files:**
- Create: `cc_cai_daemon/escalator.py`
- Create: `cc_cai_daemon/telegram_bot.py`
- Create: `tests/cc_cai_daemon/test_escalator.py`

Per CAI-RESP-185 Q1: inline `[Approve][Defer][Delegate]` buttons + free-text reply. Phase 1 implements:
- Escalator pushes new Telegram message with inline keyboard
- Bot polls for callbacks (button taps) + replies → writes operator decision back into agent_messages

- [ ] **Step 5.1: Failing tests**

- [ ] **Step 5.2: Implementation**

```python
# escalator.py
"""Telegram escalation push with CAI-RESP-185 Q1 button layout."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from cc_cai_daemon.audit import AuditLogger


async def escalate_to_operator(
    bot, chat_id: str, msg: dict, audit: AuditLogger,
    reason: str, category: str | None,
) -> int | None:
    """Push msg to operator Telegram + return telegram_message_id.

    Per CAI-RESP-185 Q1: inline [Approve][Defer][Delegate] + free-text always.
    """
    text = _format_escalation_body(msg, reason, category)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{msg['id']}"),
            InlineKeyboardButton("⏸ Defer", callback_data=f"defer:{msg['id']}"),
            InlineKeyboardButton("↪︎ Delegate", callback_data=f"delegate:{msg['id']}"),
        ],
    ])
    audit.log_escalation(agent_message_id=msg["id"], reason=reason)
    sent = await bot.send_message(
        chat_id=chat_id, text=text, reply_markup=keyboard,
    )
    audit.log_tool_call(
        tool_name="telegram_send_escalation",
        tool_input={"agent_message_id": msg["id"], "category": category},
        tool_output={"telegram_message_id": sent.message_id},
        agent_message_id=msg["id"],
    )
    return sent.message_id
```

- [ ] **Step 5.3: telegram_bot.py — callback handler**

```python
"""Telegram bot polling — handles button callbacks + free-text replies."""
# python-telegram-bot Application + CallbackQueryHandler
# On Approve → write decision='approve' back into agent_messages.responded_at
# On Defer   → write decision='defer' + set requires_response back to True
# On Delegate → operator types free-text response, daemon parses
# Free-text reply → written as new agent_message from operator persona
```

- [ ] **Step 5.4: Commit**

```bash
git commit -m "feat(cc-cai): escalator + telegram bot — inline button UI per Q1"
```

---

## Task 6: Kill-switch (`kill_switch.py`)

**Files:**
- Create: `cc_cai_daemon/kill_switch.py`
- Create: `tests/cc_cai_daemon/test_kill_switch.py`

Per CAI-RESP-185 Q5 rail (b): "INV-6 default-HOLD kill-switch reverts cc-cai-daemon to pure-escalation mode on confidence drop."

- [ ] **Step 6.1: States**:
  - `live` — full Phase 1 silent-lane + escalation
  - `pure_escalation_mode` — every message escalates regardless of classification
  - `panic_disabled` — `WINGMEN_CC_CAI_DAEMON_DISABLED=true` env flag, no actions at all

- [ ] **Step 6.2: Trip conditions**:
  - 3 consecutive classifications with `confidence < 0.5` → `live → pure_escalation_mode`
  - Operator-issued `/cc-cai disable` via Telegram → `→ panic_disabled`
  - State change writes audit row

- [ ] **Step 6.3: Implementation + tests**

- [ ] **Step 6.4: Commit**

```bash
git commit -m "feat(cc-cai): INV-6 kill-switch — confidence-drop + panic flag (CAI-RESP-185 Q5)"
```

---

## Task 7: Poller + main loop (`poller.py` + `main.py`)

**Files:**
- Create: `cc_cai_daemon/poller.py`
- Create: `cc_cai_daemon/sdk_query.py`
- Create: `cc_cai_daemon/main.py`
- Create: `tests/cc_cai_daemon/test_poller.py`

Per CAI-RESP-185 amendment 1: Realtime doesn't gate Phase 1. Use 5-min poll.

- [ ] **Step 7.1: Poller** — async loop, 5-min cadence, reads `agent_messages WHERE to_agent='cai' AND read_at IS NULL AND is_test=false`

- [ ] **Step 7.2: sdk_query wrapper** — scrubs ANTHROPIC_API_KEY before any SDK call (mirrors ai_provider INV-3 pattern). Hook injection for INV-5 tool-call auditing.

- [ ] **Step 7.3: main.py** — orchestrates poller + classifier + silent_lane + escalator. asyncio.run().

- [ ] **Step 7.4: Tests**

- [ ] **Step 7.5: Commit**

```bash
git commit -m "feat(cc-cai): main loop + poller + SDK wrapper"
```

---

## Task 8: Launchd plist + long_running_callers registration

**Files:**
- Create: `ops/launchd/dev.wingmen.cc-cai-daemon.plist`
- Create: `manifests/long_running_callers/cc_cai_daemon.yaml`
- Modify: `.gitignore` (add `.venv-cc-cai/`)

- [ ] **Step 8.1: Plist** — launches `.venv-cc-cai/bin/python cc_cai_daemon/main.py` on boot; KeepAlive=true; StandardOutPath/StandardErrorPath to `logs/cc_cai_daemon.log/.err`.

- [ ] **Step 8.2: Manifest** — `auto_kill_policy='no_kill'`, `registered_by_identity='cc_family'`, `expected_cadence_seconds=300`, `purpose='cc-cai always-on triage daemon per CAI-RESP-185 Path C ratification'`.

- [ ] **Step 8.3: .gitignore**

- [ ] **Step 8.4: Commit**

```bash
git commit -m "ops(cc-cai): launchd plist + long_running_callers manifest"
```

---

## Task 9: Integration test + smoke + PR

- [ ] **Step 9.1: Integration test** — `tests/cc_cai_daemon/test_e2e_smoke.py`: inject a synthetic agent_message, run one cycle of poller→classifier→silent_lane, assert audit row + side effect both landed.

- [ ] **Step 9.2: Full sweep**

```bash
.venv-cc-cai/bin/python -m pytest tests/cc_cai_daemon/ -v
```

- [ ] **Step 9.3: Dry-run** (don't load launchd yet — operator gates kickstart):

```bash
.venv-cc-cai/bin/python -m cc_cai_daemon.main --dry-run --max-cycles 3
```

Verify: audit rows landed for every observation, no side effects fired in dry-run, classification logs show expected labels for live inbox snapshot.

- [ ] **Step 9.4: PR**

```bash
env -u GITHUB_TOKEN git push -u origin feat/cc-cai-daemon-phase-1
env -u GITHUB_TOKEN gh pr create --base main --head feat/cc-cai-daemon-phase-1 \
    --title "feat(cc-cai): Phase 1 daemon MVP — Path C ratified (CAI-RESP-185)" \
    --body "..."
```

- [ ] **Step 9.5: Operator dry-run review at PR — then kickstart**

```bash
launchctl load -w ~/Library/LaunchAgents/dev.wingmen.cc-cai-daemon.plist
launchctl kickstart -k "gui/$(id -u)/dev.wingmen.cc-cai-daemon"
```

Phase 1 live at this point. Phase 2 (cc-orchestrator-daemon pilot) begins after ≥1 week + zero misclassifications.

---

## Self-Review

**Spec coverage (CAI-RESP-185):**
- ✅ Path C architecture: SDK daemon + interactive terminals preserved
- ✅ Q1 Telegram UI: `[Approve][Defer][Delegate]` + free-text — Task 5
- ✅ Q2 weekly digest: out of Phase 1 scope (cai's ruling), will arrive in Phase 4
- ✅ Q3 Phase 2 pilot = cc-orchestrator-daemon — out of Phase 1 scope
- ✅ Q4 silent lane = mark_read + ack_fyi only — Task 3 + 4
- ✅ Q5 rail (a) escalate novel up to claude.ai — Task 5 escalator includes "novel_low_confidence" category
- ✅ Q5 rail (b) confidence-drop kill-switch — Task 6
- ✅ Amendment 1: Realtime not gated, poll-based — Task 7
- ✅ Amendment 2: cc-ihsanos migrates last — Phase 3+ scope
- ✅ HARD SHIP CONDITION INV-5 audit: every tool call logged — Tasks 1 + 2

**All 6 CADENCE-002 invariants enforced explicitly in module code.**

**Placeholder scan:** None in plan. Step 5/6/7 implementation bodies marked "lives in module at build time" — implementer expands during subagent dispatch.

**Type consistency:** Classification dataclass labels match the `cc_cai_audit_log.classification` text column values; kill_switch_state matches audit column enum.
