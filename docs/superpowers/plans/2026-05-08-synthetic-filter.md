# Synthetic Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dispatch-time auto-reject filter for synthetic E2E bug reports per BUG-PIPELINE-SYNTHETIC-FILTER-001 + CAI-RESP-141. Two env-flag gated phases (shadow → enforce), additive migration with audit-column adds + boot_briefing view extension + backfill.

**Architecture:** New module `nervous_system/synthetic_filter.py` with pure `classify(bug)` and side-effecting `apply_classification(supabase, bug, classification, mode)`. `bug_reports_poll.py` calls these inside its existing per-bug loop. Two env flags (`ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED`, `ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE`) gate behavior. Migration adds `rejected_at`/`rejected_by` columns, backfills historical synthetic rows to `status='rejected'`, and `CREATE OR REPLACE VIEW boot_briefing` to expose 24h counters from `notification_log`.

**Tech Stack:** Python 3.9, supabase-py async client, psycopg (live-DB tests), pytest + pytest-asyncio, PostgreSQL (Supabase).

**Spec reference:** `docs/superpowers/specs/2026-05-08-synthetic-filter-design.md`

---

## File Structure

| Path | Purpose | New/Modified |
|---|---|---|
| `supabase/migrations/20260508_bug_reports_synthetic_filter.sql` | Schema additions, backfill, view replace, assertion gate | NEW |
| `nervous_system/synthetic_filter.py` | `SyntheticClassification` dataclass, `classify()`, `apply_classification()`, mode-flag helpers | NEW |
| `nervous_system/bug_reports_poll.py` | Insert classifier call into existing per-bug loop | MODIFIED |
| `tests/test_synthetic_filter.py` | Pure-unit tests for classifier + mode helpers | NEW |
| `tests/test_synthetic_filter_integration.py` | Live-DB tests for migration + apply_classification + boot_briefing | NEW |
| `.env.example` | Document the two new env flags | MODIFIED |

---

## Pre-Flight

- [ ] **Step 0.1: Create feature branch from latest main**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git checkout main && git pull origin main
git checkout -b feat/synthetic-filter
```

- [ ] **Step 0.2: Verify environment**

```bash
source .venv/bin/activate
python -c "import psycopg, supabase, pytest_asyncio; print('ok')"
```

Expected: `ok`. If not, install missing deps via `pip install -r requirements.txt`.

- [ ] **Step 0.3: Verify DATABASE_URL is set (for live-DB tests)**

```bash
grep -E "^(DATABASE_URL|SUPABASE_DB_URL)=" .env | head -1
```

Expected: a line with a valid postgres URL. Live-DB tests skip silently if neither is set.

---

## Task 1: Migration — `rejected_at` + `rejected_by` columns

**Files:**
- Create: `supabase/migrations/20260508_bug_reports_synthetic_filter.sql`
- Test: `tests/test_synthetic_filter_integration.py`

- [ ] **Step 1.1: Write failing schema-assertion test**

Create `tests/test_synthetic_filter_integration.py` with:

```python
"""Live-DB tests for BUG-PIPELINE-SYNTHETIC-FILTER-001.

Schema assertions, apply_classification round-trips, boot_briefing
view exposure, backfill verification. All gated on DATABASE_URL —
skip silently in CI without secrets.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)


@pytestmark_integration
def test_rejected_at_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_name='bug_reports' AND column_name='rejected_at'"
            )
            r = cur.fetchone()
    assert r is not None, "rejected_at column missing"
    assert r[0] == "timestamp with time zone"
    assert r[1] == "YES", "should be nullable"


@pytestmark_integration
def test_rejected_by_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_name='bug_reports' AND column_name='rejected_by'"
            )
            r = cur.fetchone()
    assert r is not None, "rejected_by column missing"
    assert r[0] == "text"
    assert r[1] == "YES", "should be nullable"
```

- [ ] **Step 1.2: Run tests, verify they fail**

```bash
source .venv/bin/activate && python -m pytest tests/test_synthetic_filter_integration.py::test_rejected_at_column_exists tests/test_synthetic_filter_integration.py::test_rejected_by_column_exists -v
```

Expected: FAIL with "rejected_at column missing" / "rejected_by column missing".

- [ ] **Step 1.3: Write migration file (Section 1 only)**

Create `supabase/migrations/20260508_bug_reports_synthetic_filter.sql` with:

```sql
-- BUG-PIPELINE-SYNTHETIC-FILTER-001 + CAI-RESP-141
-- Dispatch-time auto-reject filter for synthetic E2E test bug reports.
-- Adds audit columns, backfills historical synthetic rows to status='rejected',
-- extends boot_briefing view with two 24h counter arms.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, UPDATE WHERE excludes already-rejected,
-- CREATE OR REPLACE VIEW. Additive only; qualifies for pre-apply per CAI-RESP-102.

BEGIN;

-- Section 1: audit columns (mirrors resolved_at + verified_at pattern)
ALTER TABLE bug_reports
  ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS rejected_by TEXT;

COMMENT ON COLUMN bug_reports.rejected_at IS
  'When the row was set status=rejected by the synthetic-filter or operator. '
  'Per BUG-PIPELINE-SYNTHETIC-FILTER-001 (2026-05-08).';
COMMENT ON COLUMN bug_reports.rejected_by IS
  'Identity that set status=rejected (e.g. cc-orchestrator-filter, '
  'cc-orchestrator-filter-backfill, or operator). '
  'Per BUG-PIPELINE-SYNTHETIC-FILTER-001 (2026-05-08).';

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260508120000',
    'bug_reports_synthetic_filter',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
```

- [ ] **Step 1.4: Apply migration to live DB**

```bash
source .venv/bin/activate && python3 <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
sql = open('/Users/sheikhmusa/wingmen/orchestrator/supabase/migrations/20260508_bug_reports_synthetic_filter.sql').read()
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(sql)
        print('migration applied')
PY
```

Expected output: `migration applied`.

- [ ] **Step 1.5: Run schema tests, verify they pass**

```bash
python -m pytest tests/test_synthetic_filter_integration.py::test_rejected_at_column_exists tests/test_synthetic_filter_integration.py::test_rejected_by_column_exists -v
```

Expected: 2 passed.

- [ ] **Step 1.6: Commit**

```bash
git add supabase/migrations/20260508_bug_reports_synthetic_filter.sql tests/test_synthetic_filter_integration.py
git commit -m "feat(synthetic-filter): migration section 1 — rejected_at + rejected_by columns"
```

---

## Task 2: Pure classifier — `SyntheticClassification` dataclass + rule (a)

**Files:**
- Create: `nervous_system/synthetic_filter.py`
- Test: `tests/test_synthetic_filter.py`

- [ ] **Step 2.1: Write failing test for rule (a)**

Create `tests/test_synthetic_filter.py` with:

```python
"""Pure-unit tests for synthetic_filter.classify and mode helpers.

No DB. Per CAI-RESP-141: rule (c) dropped (no repro_steps column).
Two rules only — (a) E2E placeholder phrase, (b) reporter substring.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.synthetic_filter import SyntheticClassification, classify


class TestRuleA:
    """Rule (a): description ~* '^E2E test bug report\\.?\\s*$'"""

    def test_exact_phrase_no_period_classifies(self):
        bug = {"description": "E2E test bug report", "reporter_name": "real human"}
        result = classify(bug)
        assert result is not None
        assert result.rule == "a_e2e_placeholder"
        assert result.matched_text == "E2E test bug report"

    def test_exact_phrase_with_period_classifies(self):
        bug = {"description": "E2E test bug report.", "reporter_name": "real human"}
        result = classify(bug)
        assert result is not None
        assert result.rule == "a_e2e_placeholder"

    def test_case_insensitive(self):
        bug = {"description": "e2e TEST bug Report", "reporter_name": "real human"}
        result = classify(bug)
        assert result is not None
        assert result.rule == "a_e2e_placeholder"

    def test_trailing_whitespace_tolerated(self):
        bug = {"description": "E2E test bug report.   \n", "reporter_name": "real human"}
        result = classify(bug)
        assert result is not None

    def test_extra_text_after_phrase_does_not_match(self):
        # Rule (a) requires the WHOLE description to be the phrase.
        bug = {"description": "E2E test bug report — actually broken", "reporter_name": "real human"}
        result = classify(bug)
        assert result is None
```

- [ ] **Step 2.2: Run tests, verify they fail**

```bash
python -m pytest tests/test_synthetic_filter.py::TestRuleA -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'nervous_system.synthetic_filter'`.

- [ ] **Step 2.3: Implement classifier with rule (a)**

Create `nervous_system/synthetic_filter.py` with:

```python
"""Dispatch-time auto-reject filter for synthetic E2E test bug reports.

Per BUG-PIPELINE-SYNTHETIC-FILTER-001 + CAI-RESP-141. Two classification
rules (rule c dropped per CL1 — no repro_steps column on bug_reports).
Two env flags gate behavior: ENABLED (kill-switch) + ENFORCE (mode).

This module has two boundaries:
  - classify(bug) — pure function returning SyntheticClassification | None
  - apply_classification(...) — side-effecting; writes notification_log,
    updates bug_reports in enforce mode

Called from nervous_system/bug_reports_poll.py inside the per-bug loop.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal, Optional


# Rule (a): description matches "E2E test bug report" with optional trailing
# period and whitespace, case-insensitive. Anchored to whole-string.
_RULE_A_PATTERN = re.compile(r"^\s*E2E test bug report\.?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class SyntheticClassification:
    """Result of classifying a bug_report as synthetic-test-shaped."""
    rule: Literal["a_e2e_placeholder", "b_test_reporter"]
    matched_text: str
    reason: str = "synthetic_e2e_test"


def classify(bug: dict) -> Optional[SyntheticClassification]:
    """Classify a bug_report row against cai's two rules.

    Returns None if the bug does not match any rule (i.e. proceed to dispatch).
    Returns a SyntheticClassification if any rule matches.
    """
    description = (bug.get("description") or "").strip()

    # Rule (a): E2E placeholder phrase
    if _RULE_A_PATTERN.match(description):
        return SyntheticClassification(
            rule="a_e2e_placeholder",
            matched_text=description,
        )

    return None
```

- [ ] **Step 2.4: Run tests, verify they pass**

```bash
python -m pytest tests/test_synthetic_filter.py::TestRuleA -v
```

Expected: 5 passed.

- [ ] **Step 2.5: Commit**

```bash
git add nervous_system/synthetic_filter.py tests/test_synthetic_filter.py
git commit -m "feat(synthetic-filter): SyntheticClassification + rule (a) E2E placeholder"
```

---

## Task 3: Classifier — rule (b) reporter substring

**Files:**
- Modify: `nervous_system/synthetic_filter.py`
- Modify: `tests/test_synthetic_filter.py`

- [ ] **Step 3.1: Write failing tests for rule (b)**

Append to `tests/test_synthetic_filter.py`:

```python
class TestRuleB:
    """Rule (b): reporter_name contains substring '(Test)' (parens included)."""

    def test_test_suffix_classifies(self):
        bug = {"description": "real bug", "reporter_name": "BAPA Admin (Test)"}
        result = classify(bug)
        assert result is not None
        assert result.rule == "b_test_reporter"
        assert result.matched_text == "BAPA Admin (Test)"

    def test_test_prefix_classifies(self):
        bug = {"description": "real bug", "reporter_name": "(Test) Account"}
        result = classify(bug)
        assert result is not None
        assert result.rule == "b_test_reporter"

    def test_test_middle_classifies(self):
        bug = {"description": "real bug", "reporter_name": "Foo (Test) Bar"}
        result = classify(bug)
        assert result is not None
        assert result.rule == "b_test_reporter"

    def test_test_word_without_parens_does_not_match(self):
        bug = {"description": "real bug", "reporter_name": "Test User"}
        assert classify(bug) is None

    def test_my_test_user_does_not_match(self):
        bug = {"description": "real bug", "reporter_name": "MyTest User"}
        assert classify(bug) is None

    def test_case_sensitive_parens(self):
        # Spec says "(Test)" with capital T — lower-case "(test)" should NOT match
        bug = {"description": "real bug", "reporter_name": "Foo (test) Bar"}
        assert classify(bug) is None
```

- [ ] **Step 3.2: Run tests, verify they fail**

```bash
python -m pytest tests/test_synthetic_filter.py::TestRuleB -v
```

Expected: 3 failures (3 positive cases) — rule (b) not implemented yet. The 3 negative cases pass trivially because `classify` returns None.

- [ ] **Step 3.3: Implement rule (b)**

Modify `nervous_system/synthetic_filter.py` — add rule (b) check after rule (a):

```python
def classify(bug: dict) -> Optional[SyntheticClassification]:
    """Classify a bug_report row against cai's two rules.

    Returns None if the bug does not match any rule (i.e. proceed to dispatch).
    Returns a SyntheticClassification if any rule matches.
    """
    description = (bug.get("description") or "").strip()
    reporter_name = bug.get("reporter_name") or ""

    # Rule (a): E2E placeholder phrase
    if _RULE_A_PATTERN.match(description):
        return SyntheticClassification(
            rule="a_e2e_placeholder",
            matched_text=description,
        )

    # Rule (b): reporter contains "(Test)" substring (case-sensitive parens)
    if "(Test)" in reporter_name:
        return SyntheticClassification(
            rule="b_test_reporter",
            matched_text=reporter_name,
        )

    return None
```

- [ ] **Step 3.4: Run tests, verify they pass**

```bash
python -m pytest tests/test_synthetic_filter.py -v
```

Expected: 11 passed (5 from Task 2 + 6 from Task 3).

- [ ] **Step 3.5: Commit**

```bash
git add nervous_system/synthetic_filter.py tests/test_synthetic_filter.py
git commit -m "feat(synthetic-filter): rule (b) reporter substring"
```

---

## Task 4: Classifier — negative tests confirming rule (c) drop + None for normal bugs

**Files:**
- Modify: `tests/test_synthetic_filter.py`

- [ ] **Step 4.1: Write tests asserting normal bugs do not classify**

Append to `tests/test_synthetic_filter.py`:

```python
class TestRuleCDropped:
    """Rule (c) was dropped per CAI-RESP-141 CL1 (no repro_steps column).

    These tests assert that the patterns rule (c) WOULD have caught now
    pass through unclassified — protects against accidental rule (c)
    re-introduction.
    """

    def test_empty_description_does_not_classify(self):
        bug = {"description": "", "reporter_name": "real human"}
        assert classify(bug) is None

    def test_whitespace_only_description_does_not_classify(self):
        bug = {"description": "   \n  ", "reporter_name": "real human"}
        assert classify(bug) is None

    def test_none_fields_do_not_crash(self):
        bug = {"description": None, "reporter_name": None}
        assert classify(bug) is None

    def test_missing_fields_do_not_crash(self):
        bug = {}
        assert classify(bug) is None


class TestNormalBugs:
    """Realistic bug_report rows must classify as None."""

    def test_real_bug_with_full_description(self):
        bug = {
            "description": "When selecting a vehicle the menu is cutoff at the bottom",
            "reporter_name": "Mulifatullah Bin Atan",
        }
        assert classify(bug) is None

    def test_terse_real_bug(self):
        # Important: terse bugs are NOT synthetic. Rule (c) was dropped
        # specifically because it would have falsely flagged these.
        bug = {"description": "page crashes on load", "reporter_name": "musa"}
        assert classify(bug) is None
```

- [ ] **Step 4.2: Run tests, verify they pass**

```bash
python -m pytest tests/test_synthetic_filter.py -v
```

Expected: 17 passed (11 prior + 6 new).

- [ ] **Step 4.3: Commit**

```bash
git add tests/test_synthetic_filter.py
git commit -m "test(synthetic-filter): rule (c) drop regression + normal-bug negative tests"
```

---

## Task 5: Mode-flag helpers — `_filter_enabled` + `_filter_mode`

**Files:**
- Modify: `nervous_system/synthetic_filter.py`
- Modify: `tests/test_synthetic_filter.py`

- [ ] **Step 5.1: Write failing tests for mode helpers**

Append to `tests/test_synthetic_filter.py`:

```python
from nervous_system.synthetic_filter import _filter_enabled, _filter_mode


class TestModeHelpers:
    """ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED + ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE
    env flag resolution. Defaults: ENABLED=true, ENFORCE=false → shadow mode."""

    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", raising=False)
        assert _filter_enabled() is True

    def test_default_mode_shadow(self, monkeypatch):
        monkeypatch.delenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", raising=False)
        assert _filter_mode() == "shadow"

    def test_disabled_via_false(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", "false")
        assert _filter_enabled() is False

    def test_disabled_via_off(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", "off")
        assert _filter_enabled() is False

    def test_disabled_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", "FALSE")
        assert _filter_enabled() is False

    def test_enforce_when_true(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", "true")
        assert _filter_mode() == "enforce"

    def test_enforce_when_one(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", "1")
        assert _filter_mode() == "enforce"

    def test_unrecognized_value_treated_as_shadow(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", "maybe")
        assert _filter_mode() == "shadow"
```

- [ ] **Step 5.2: Run tests, verify they fail**

```bash
python -m pytest tests/test_synthetic_filter.py::TestModeHelpers -v
```

Expected: FAIL with `ImportError: cannot import name '_filter_enabled'`.

- [ ] **Step 5.3: Implement mode helpers**

Append to `nervous_system/synthetic_filter.py`:

```python
def _filter_enabled() -> bool:
    """Kill-switch. Set ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED=false to bypass
    the filter entirely and revert to PR #28-only behavior. Default: true."""
    return os.environ.get("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", "true").lower() \
        not in ("false", "0", "no", "off")


def _filter_mode() -> Literal["shadow", "enforce"]:
    """Mode toggle. Default 'shadow' — classify and log only, do not block
    dispatch. Set ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE=true to flip to
    'enforce' — classify, log, AND set bug_reports.status='rejected'."""
    if os.environ.get("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", "false").lower() \
            in ("true", "1", "yes", "on"):
        return "enforce"
    return "shadow"
```

- [ ] **Step 5.4: Run tests, verify they pass**

```bash
python -m pytest tests/test_synthetic_filter.py::TestModeHelpers -v
```

Expected: 8 passed.

- [ ] **Step 5.5: Commit**

```bash
git add nervous_system/synthetic_filter.py tests/test_synthetic_filter.py
git commit -m "feat(synthetic-filter): mode-flag helpers (_filter_enabled + _filter_mode)"
```

---

## Task 6: `apply_classification` — notification_log INSERT only

**Files:**
- Modify: `nervous_system/synthetic_filter.py`
- Modify: `tests/test_synthetic_filter_integration.py`

- [ ] **Step 6.1: Write failing test for shadow-mode apply (notification_log only)**

Append to `tests/test_synthetic_filter_integration.py`:

```python
import asyncio
import json

from supabase import create_async_client


def _build_supabase():
    """Build async supabase client matching how wingmen_orch.py does it.

    Returns a client + the parsed URL/key for direct connection cleanup."""
    from supabase import AsyncClient
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    assert url and key, "SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY required for integration tests"

    async def _make():
        return await create_async_client(url, key)
    return asyncio.get_event_loop().run_until_complete(_make())


@pytestmark_integration
@pytest.mark.asyncio
async def test_shadow_mode_writes_notification_log_does_not_update_bug():
    """In shadow mode, apply_classification logs the classification but
    leaves bug_reports.status untouched."""
    from nervous_system.synthetic_filter import (
        SyntheticClassification, apply_classification,
    )
    from supabase import create_async_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    supabase = await create_async_client(url, key)

    # Insert a synthetic bug we can target. Use a unique reporter so we
    # can clean up. status='new' so the test asserts non-mutation.
    bug_id = str(uuid.uuid4())
    test_marker = f"synthfilter-test-{uuid.uuid4().hex[:8]}"
    insert_resp = await supabase.table("bug_reports").insert({
        "id": bug_id,
        "reporter_name": f"{test_marker} (Test)",
        "reporter_source": "web",
        "auth_provider": "none",
        "repo_name": "cosem-tdu",
        "description": "real text — but flagged via reporter rule b",
        "status": "new",
    }).execute()
    assert insert_resp.data, "test setup: bug insert failed"

    try:
        classification = SyntheticClassification(
            rule="b_test_reporter",
            matched_text=f"{test_marker} (Test)",
        )
        bug = insert_resp.data[0]

        await apply_classification(supabase, bug, classification, mode="shadow")

        # Bug status unchanged
        check_bug = await supabase.table("bug_reports").select("status, rejected_at").eq("id", bug_id).execute()
        assert check_bug.data[0]["status"] == "new", \
            f"shadow mode must not update status, got {check_bug.data[0]['status']}"
        assert check_bug.data[0]["rejected_at"] is None

        # notification_log entry exists
        log_check = await supabase.table("notification_log").select("source, message_text").eq("recipient", bug_id).execute()
        assert len(log_check.data) == 1
        entry = log_check.data[0]
        assert entry["source"] == "synthetic_filter"
        msg = json.loads(entry["message_text"])
        assert msg["mode"] == "shadow"
        assert msg["would_reject"] is True
        assert msg["rule"] == "b_test_reporter"
        assert msg["bug_id"] == bug_id
    finally:
        # Cleanup
        await supabase.table("notification_log").delete().eq("recipient", bug_id).execute()
        await supabase.table("bug_reports").delete().eq("id", bug_id).execute()
```

- [ ] **Step 6.2: Run test, verify it fails**

```bash
python -m pytest tests/test_synthetic_filter_integration.py::test_shadow_mode_writes_notification_log_does_not_update_bug -v
```

Expected: FAIL with `ImportError: cannot import name 'apply_classification'`.

- [ ] **Step 6.3: Implement `apply_classification` (shadow path only)**

Append to `nervous_system/synthetic_filter.py`:

```python
import json
import logging

logger = logging.getLogger("wingmen.synthetic_filter")

_DECISION_REF = "BUG-PIPELINE-SYNTHETIC-FILTER-001"
_REJECTED_BY = "cc-orchestrator-filter"


async def apply_classification(
    supabase,
    bug: dict,
    classification: SyntheticClassification,
    mode: Literal["shadow", "enforce"],
) -> None:
    """Apply a classification result.

    - Writes a notification_log entry (always, both modes).
    - In enforce mode: ALSO updates bug_reports.status='rejected' + audit fields.
    - boot_briefing counters are computed by the view from notification_log
      on read — no Python-side counter writes needed.
    """
    bug_id = bug["id"]
    desc_excerpt = (bug.get("description") or "")[:80]

    log_payload = {
        "bug_id": bug_id,
        "rule": classification.rule,
        "matched_text": classification.matched_text,
        "mode": mode,
        "would_reject": True,
        "reporter_name": bug.get("reporter_name"),
        "description_excerpt": desc_excerpt,
    }

    await supabase.table("notification_log").insert({
        "source": "synthetic_filter",
        "decision_ref": _DECISION_REF,
        "channel": "bug_reports",
        "recipient": bug_id,
        "message_text": json.dumps(log_payload),
    }).execute()

    logger.info(
        f"synthetic_filter: bug {bug_id} classified rule={classification.rule} "
        f"mode={mode} reporter={bug.get('reporter_name')!r}"
    )
```

- [ ] **Step 6.4: Run test, verify it passes**

```bash
python -m pytest tests/test_synthetic_filter_integration.py::test_shadow_mode_writes_notification_log_does_not_update_bug -v
```

Expected: 1 passed.

- [ ] **Step 6.5: Commit**

```bash
git add nervous_system/synthetic_filter.py tests/test_synthetic_filter_integration.py
git commit -m "feat(synthetic-filter): apply_classification — notification_log shadow path"
```

---

## Task 7: `apply_classification` — enforce-mode bug_reports UPDATE

**Files:**
- Modify: `nervous_system/synthetic_filter.py`
- Modify: `tests/test_synthetic_filter_integration.py`

- [ ] **Step 7.1: Write failing test for enforce-mode apply**

Append to `tests/test_synthetic_filter_integration.py`:

```python
@pytestmark_integration
@pytest.mark.asyncio
async def test_enforce_mode_writes_log_and_rejects_bug():
    """In enforce mode, apply_classification logs AND sets status='rejected'
    + rejection_reason + rejected_at + rejected_by."""
    from nervous_system.synthetic_filter import (
        SyntheticClassification, apply_classification,
    )
    from supabase import create_async_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    supabase = await create_async_client(url, key)

    bug_id = str(uuid.uuid4())
    test_marker = f"synthfilter-test-{uuid.uuid4().hex[:8]}"
    insert_resp = await supabase.table("bug_reports").insert({
        "id": bug_id,
        "reporter_name": f"{test_marker} (Test)",
        "reporter_source": "web",
        "auth_provider": "none",
        "repo_name": "cosem-tdu",
        "description": "test",
        "status": "new",
    }).execute()
    assert insert_resp.data

    try:
        classification = SyntheticClassification(
            rule="b_test_reporter",
            matched_text=f"{test_marker} (Test)",
        )
        bug = insert_resp.data[0]

        await apply_classification(supabase, bug, classification, mode="enforce")

        check = await supabase.table("bug_reports").select(
            "status, rejection_reason, rejected_at, rejected_by"
        ).eq("id", bug_id).execute()
        row = check.data[0]
        assert row["status"] == "rejected"
        assert row["rejection_reason"] == "synthetic_e2e_test"
        assert row["rejected_at"] is not None
        assert row["rejected_by"] == "cc-orchestrator-filter"

        log_check = await supabase.table("notification_log").select("message_text").eq("recipient", bug_id).execute()
        msg = json.loads(log_check.data[0]["message_text"])
        assert msg["mode"] == "enforce"
    finally:
        await supabase.table("notification_log").delete().eq("recipient", bug_id).execute()
        await supabase.table("bug_reports").delete().eq("id", bug_id).execute()
```

- [ ] **Step 7.2: Run test, verify it fails**

```bash
python -m pytest tests/test_synthetic_filter_integration.py::test_enforce_mode_writes_log_and_rejects_bug -v
```

Expected: FAIL — bug status is "new", expected "rejected".

- [ ] **Step 7.3: Implement enforce-mode UPDATE**

Modify `apply_classification` in `nervous_system/synthetic_filter.py` — add the enforce-mode branch after the notification_log insert:

```python
async def apply_classification(
    supabase,
    bug: dict,
    classification: SyntheticClassification,
    mode: Literal["shadow", "enforce"],
) -> None:
    """Apply a classification result.

    - Writes a notification_log entry (always, both modes).
    - In enforce mode: ALSO updates bug_reports.status='rejected' + audit fields.
    - boot_briefing counters are computed by the view from notification_log
      on read — no Python-side counter writes needed.
    """
    bug_id = bug["id"]
    desc_excerpt = (bug.get("description") or "")[:80]

    log_payload = {
        "bug_id": bug_id,
        "rule": classification.rule,
        "matched_text": classification.matched_text,
        "mode": mode,
        "would_reject": True,
        "reporter_name": bug.get("reporter_name"),
        "description_excerpt": desc_excerpt,
    }

    await supabase.table("notification_log").insert({
        "source": "synthetic_filter",
        "decision_ref": _DECISION_REF,
        "channel": "bug_reports",
        "recipient": bug_id,
        "message_text": json.dumps(log_payload),
    }).execute()

    if mode == "enforce":
        await supabase.table("bug_reports").update({
            "status": "rejected",
            "rejection_reason": classification.reason,
            "rejected_at": "now()",   # supabase-py serializes; alt: datetime.now(tz=timezone.utc).isoformat()
            "rejected_by": _REJECTED_BY,
        }).eq("id", bug_id).execute()

    logger.info(
        f"synthetic_filter: bug {bug_id} classified rule={classification.rule} "
        f"mode={mode} reporter={bug.get('reporter_name')!r}"
    )
```

**Note on `"now()"` literal:** supabase-py forwards strings as-is to PostgREST, which accepts SQL function literals for timestamp columns. If this turns out not to work, swap to `datetime.now(timezone.utc).isoformat()` from Python's `datetime` module.

- [ ] **Step 7.4: Run test, verify it passes**

```bash
python -m pytest tests/test_synthetic_filter_integration.py::test_enforce_mode_writes_log_and_rejects_bug -v
```

Expected: 1 passed. If it fails on the `"now()"` literal, switch to `datetime.now(timezone.utc).isoformat()` and add `from datetime import datetime, timezone` at the top of `synthetic_filter.py`.

- [ ] **Step 7.5: Commit**

```bash
git add nervous_system/synthetic_filter.py tests/test_synthetic_filter_integration.py
git commit -m "feat(synthetic-filter): apply_classification — enforce-mode bug_reports UPDATE"
```

---

## Task 8: Migration Section 2 — historical backfill

**Files:**
- Modify: `supabase/migrations/20260508_bug_reports_synthetic_filter.sql`
- Modify: `tests/test_synthetic_filter_integration.py`

- [ ] **Step 8.1: Snapshot pre-backfill state for assertion**

```bash
source .venv/bin/activate && python3 <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        # Count rows that SHOULD be flipped by backfill
        cur.execute("""
            SELECT count(*) FROM bug_reports
             WHERE status IN ('new','diagnosing')
               AND (
                 description ~* '^E2E test bug report\\.?\\s*$'
                 OR reporter_name LIKE '%(Test)%'
                 OR is_test = true
               )
        """)
        print(f"backfill candidates: {cur.fetchone()[0]}")

        cur.execute("SELECT count(*) FROM bug_reports WHERE status='rejected'")
        print(f"already-rejected: {cur.fetchone()[0]}")
PY
```

Note the candidate count for use in step 8.4 verification.

- [ ] **Step 8.2: Write failing test for backfill outcome**

Append to `tests/test_synthetic_filter_integration.py`:

```python
@pytestmark_integration
def test_backfill_flipped_existing_synthetic_to_rejected():
    """Section 2 backfill: rows matching cai a/b OR PR #28 is_test=true,
    in non-terminal status, must now have status='rejected' + audit fields."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*) FROM bug_reports
                 WHERE rejected_by = 'cc-orchestrator-filter-backfill'
                   AND rejection_reason = 'synthetic_e2e_test'
                   AND status = 'rejected'
            """)
            backfilled = cur.fetchone()[0]
    assert backfilled > 0, (
        "expected backfill to flip at least one historical row "
        "(cc-x-e2e + (Test) reporters known to exist)"
    )


@pytestmark_integration
def test_backfill_did_not_touch_terminal_rows():
    """Backfill must not flip already-rejected/deployed/verified rows."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            # No row in 'verified' or 'deployed' status should have rejected_by
            cur.execute("""
                SELECT count(*) FROM bug_reports
                 WHERE status IN ('verified', 'deployed')
                   AND rejected_by IS NOT NULL
            """)
            leaked = cur.fetchone()[0]
    assert leaked == 0, f"{leaked} terminal rows incorrectly tagged with rejected_by"
```

- [ ] **Step 8.3: Run tests, verify they fail**

```bash
python -m pytest tests/test_synthetic_filter_integration.py::test_backfill_flipped_existing_synthetic_to_rejected -v
```

Expected: FAIL — backfill not applied yet.

- [ ] **Step 8.4: Add Section 2 (backfill) to migration**

Modify `supabase/migrations/20260508_bug_reports_synthetic_filter.sql` — replace the existing single-section file with this expanded version (keep Section 1, add Section 2):

```sql
-- BUG-PIPELINE-SYNTHETIC-FILTER-001 + CAI-RESP-141
-- Dispatch-time auto-reject filter for synthetic E2E test bug reports.
-- Adds audit columns, backfills historical synthetic rows to status='rejected',
-- extends boot_briefing view with two 24h counter arms.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, UPDATE WHERE excludes already-rejected,
-- CREATE OR REPLACE VIEW. Additive only; qualifies for pre-apply per CAI-RESP-102.

BEGIN;

-- Section 1: audit columns (mirrors resolved_at + verified_at pattern)
ALTER TABLE bug_reports
  ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS rejected_by TEXT;

COMMENT ON COLUMN bug_reports.rejected_at IS
  'When the row was set status=rejected by the synthetic-filter or operator. '
  'Per BUG-PIPELINE-SYNTHETIC-FILTER-001 (2026-05-08).';
COMMENT ON COLUMN bug_reports.rejected_by IS
  'Identity that set status=rejected (e.g. cc-orchestrator-filter, '
  'cc-orchestrator-filter-backfill, or operator). '
  'Per BUG-PIPELINE-SYNTHETIC-FILTER-001 (2026-05-08).';

-- Section 2: backfill historical synthetic rows
-- Per rule-scope-A decision: union of cai's rules (a) + (b) AND PR #28's
-- is_test=true sweep. Non-terminal status only (new, diagnosing). Skips
-- already-rejected/deployed/verified rows.
UPDATE bug_reports
   SET status            = 'rejected',
       rejection_reason  = COALESCE(rejection_reason, 'synthetic_e2e_test'),
       rejected_at       = now(),
       rejected_by       = 'cc-orchestrator-filter-backfill'
 WHERE status IN ('new', 'diagnosing')
   AND (
     description ~* '^E2E test bug report\.?\s*$'   -- cai rule (a)
     OR reporter_name LIKE '%(Test)%'                -- cai rule (b) substring
     OR is_test = true                               -- PR #28 hygiene sweep
   );

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260508120000',
    'bug_reports_synthetic_filter',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
```

Then re-apply the migration. The backfill `UPDATE` is the only new effect; column adds are no-ops the second time.

```bash
source .venv/bin/activate && python3 <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
sql = open('/Users/sheikhmusa/wingmen/orchestrator/supabase/migrations/20260508_bug_reports_synthetic_filter.sql').read()
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(sql)
        print('migration re-applied')
PY
```

- [ ] **Step 8.5: Run tests, verify they pass**

```bash
python -m pytest tests/test_synthetic_filter_integration.py::test_backfill_flipped_existing_synthetic_to_rejected tests/test_synthetic_filter_integration.py::test_backfill_did_not_touch_terminal_rows -v
```

Expected: 2 passed. If `test_backfill_flipped_existing_synthetic_to_rejected` fails with 0 rows, double-check the snapshot from Step 8.1 — there should have been candidates. If genuinely zero, that's a pre-existing data-state issue, not a code bug.

- [ ] **Step 8.6: Commit**

```bash
git add supabase/migrations/20260508_bug_reports_synthetic_filter.sql tests/test_synthetic_filter_integration.py
git commit -m "feat(synthetic-filter): migration section 2 — backfill historical synthetic rows"
```

---

## Task 9: Migration Section 3 — `boot_briefing` view extension

**Files:**
- Modify: `supabase/migrations/20260508_bug_reports_synthetic_filter.sql`
- Modify: `tests/test_synthetic_filter_integration.py`

- [ ] **Step 9.1: Capture current boot_briefing view definition**

```bash
source .venv/bin/activate && python3 <<'PY' > /tmp/current_boot_briefing.sql
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
        print(cur.fetchone()[0])
PY
cat /tmp/current_boot_briefing.sql | head -3
```

This dumps the existing 11-arm UNION ALL view. Use this as the base for the CREATE OR REPLACE statement.

- [ ] **Step 9.2: Write failing test for view exposure**

Append to `tests/test_synthetic_filter_integration.py`:

```python
@pytestmark_integration
def test_boot_briefing_view_has_synthetic_filter_arms():
    """View definition should reference synthetic_filter / filtered_24h /
    shadow_24h after the migration."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
            defn = cur.fetchone()[0]
    assert "synthetic_filter" in defn, "boot_briefing view missing synthetic_filter source"
    assert "filtered_24h" in defn, "boot_briefing view missing filtered_24h key"
    assert "shadow_24h" in defn, "boot_briefing view missing shadow_24h key"


@pytestmark_integration
def test_boot_briefing_synthetic_filter_zero_count_omitted():
    """When no synthetic_filter notification_log entries exist in 24h,
    the boot_briefing view rows should be ABSENT (HAVING count > 0)."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            # Cleanup any test entries first
            cur.execute("""
                DELETE FROM notification_log
                 WHERE source='synthetic_filter'
                   AND created_at >= now() - interval '24 hours'
            """)
            cur.execute("""
                SELECT key FROM boot_briefing
                 WHERE source = 'synthetic_filter'
            """)
            keys = [r[0] for r in cur.fetchall()]
    assert keys == [], f"expected no synthetic_filter rows when log empty, got {keys}"
```

- [ ] **Step 9.3: Run tests, verify they fail**

```bash
python -m pytest tests/test_synthetic_filter_integration.py::test_boot_briefing_view_has_synthetic_filter_arms tests/test_synthetic_filter_integration.py::test_boot_briefing_synthetic_filter_zero_count_omitted -v
```

Expected: at least the first test fails — view doesn't have synthetic_filter arms yet.

- [ ] **Step 9.4: Add Section 3 to migration with full view replace**

Modify `supabase/migrations/20260508_bug_reports_synthetic_filter.sql` — add the CREATE OR REPLACE VIEW statement *inside* the same BEGIN/COMMIT block, after Section 2.

The view definition must include all 11 existing UNION arms PLUS the two new ones. Copy the existing view definition from `/tmp/current_boot_briefing.sql` (Step 9.1) and append the two new arms before COMMIT:

```sql
-- Section 3: extend boot_briefing view with synthetic_filter 24h counters
-- Two new UNION ALL arms compute counts on-read from notification_log;
-- no Python-side writes, no race conditions, self-healing.
CREATE OR REPLACE VIEW boot_briefing AS
 SELECT 'repo_context'::text AS source,
    rc.repo AS key,
    json_build_object('phase', rc.current_phase, 'blockers', rc.blockers, 'test_health', rc.test_health, 'updated_at', rc.updated_at) AS context
   FROM repo_context rc
UNION ALL
 SELECT 'repo_snapshot'::text AS source,
    rs.repo_name AS key,
    json_build_object('commit_sha', "left"(rs.commit_sha, 8), 'commit_timestamp', rs.commit_timestamp, 'branch', rs.branch, 'file_count', rs.file_count, 'total_loc', rs.total_loc, 'test_count', rs.test_count, 'migration_count', rs.migration_count, 'route_count', rs.route_count, 'schema_tables', rs.schema_tables) AS context
   FROM ( SELECT DISTINCT ON (repo_snapshot.repo_name) repo_snapshot.repo_name,
            repo_snapshot.commit_sha,
            repo_snapshot.commit_timestamp,
            repo_snapshot.branch,
            repo_snapshot.file_count,
            repo_snapshot.total_loc,
            repo_snapshot.test_count,
            repo_snapshot.migration_count,
            repo_snapshot.route_count,
            repo_snapshot.schema_tables
           FROM repo_snapshot
          ORDER BY repo_snapshot.repo_name, repo_snapshot.commit_timestamp DESC) rs
UNION ALL
 SELECT 'active_decision'::text AS source,
    sd.decision_ref AS key,
        CASE
            WHEN (sd.decided_at >= (now() - '14 days'::interval)) THEN json_build_object('title', "left"(sd.title, 80), 'domain', sd.domain, 'category', sd.category, 'repos', sd.repos_affected, 'source', sd.source, 'challenge_status', sd.challenge_status, 'execution_status', sd.execution_status, 'decided_at', sd.decided_at, 'cai_session_id', sd.cai_session_id, 'decision', sd.decision, 'reasoning', sd.reasoning)
            ELSE json_build_object('title', "left"(sd.title, 80), 'domain', sd.domain, 'category', sd.category, 'repos', sd.repos_affected, 'source', sd.source, 'challenge_status', sd.challenge_status, 'execution_status', sd.execution_status, 'decided_at', sd.decided_at, 'cai_session_id', sd.cai_session_id, 'stub_reason', 'older_than_14_days_fetch_full_via_decision_ref')
        END AS context
   FROM strategic_decisions sd
  WHERE (sd.status = 'active'::text)
UNION ALL
 SELECT 'open_qa_failure'::text AS source,
    ((((qf.repo || '/'::text) || qf.role) || '/'::text) || qf.flow) AS key,
    json_build_object('role', qf.role, 'flow', qf.flow, 'error', qf.error, 'found_at', qf.found_at) AS context
   FROM qa_findings qf
  WHERE ((qf.status = 'fail'::text) AND (qf.resolved_at IS NULL))
UNION ALL
 SELECT 'latest_cc_session'::text AS source,
    sub.repo_name AS key,
    json_build_object('narrative', "left"(sub.narrative, 500), 'outcome', sub.outcome, 'commit_sha', sub.commit_sha, 'created_at', sub.created_at) AS context
   FROM ( SELECT DISTINCT ON (cws.repo_name) cws.repo_name,
            cws.narrative,
            cws.outcome,
            cws.commit_sha,
            cws.created_at
           FROM cc_work_sessions cws
          ORDER BY cws.repo_name, cws.created_at DESC) sub
UNION ALL
 SELECT 'latest_digest'::text AS source,
    dig.title AS key,
    json_build_object('topics', dig.topics_covered, 'open_questions', dig.open_questions, 'action_items', dig.action_items, 'session_date', dig.session_date) AS context
   FROM ( SELECT sd.session_date,
            sd.title,
            sd.topics_covered,
            sd.open_questions,
            sd.action_items
           FROM session_digests sd
          ORDER BY sd.created_at DESC
         LIMIT 1) dig
UNION ALL
 SELECT 'last_cai_session'::text AS source,
    lc.cai_session_id AS key,
    json_build_object('cai_session_id', lc.cai_session_id, 'last_decided_at', lc.last_decided_at, 'gap_days', (EXTRACT(day FROM (now() - lc.last_decided_at)))::integer) AS context
   FROM ( SELECT strategic_decisions.cai_session_id,
            max(strategic_decisions.decided_at) AS last_decided_at
           FROM strategic_decisions
          WHERE ((strategic_decisions.decided_by = 'cai'::text) AND (strategic_decisions.cai_session_id IS NOT NULL))
          GROUP BY strategic_decisions.cai_session_id
          ORDER BY (max(strategic_decisions.decided_at)) DESC
         LIMIT 1) lc
UNION ALL
 SELECT 'unverified_decisions'::text AS source,
    COALESCE(sd.decided_by, 'unknown'::text) AS key,
    json_build_object('count', count(*), 'oldest_decided', min(sd.decided_at), 'newest_decided', max(sd.decided_at)) AS context
   FROM strategic_decisions sd
  WHERE ((sd.decided_by_verified IS NULL) AND (sd.status = 'active'::text))
  GROUP BY sd.decided_by
UNION ALL
 SELECT 'manual_override_bugs'::text AS source,
    (br.id)::text AS key,
    json_build_object('repo_name', br.repo_name, 'status', br.status, 'override_reason_prefix', "left"(br.manual_override_reason, 80), 'created_at', br.created_at, 'resolved_at', br.resolved_at) AS context
   FROM bug_reports br
  WHERE (br.manual_override_reason IS NOT NULL)
UNION ALL
 SELECT 'inbox_sla_violation'::text AS source,
    (isv.message_id)::text AS key,
    json_build_object('agent', isv.agent, 'priority', isv.priority, 'violation', isv.violation_type, 'elapsed_min', isv.elapsed_minutes, 'threshold_min', isv.threshold_minutes, 'from', isv.from_agent, 'subject', "left"(isv.subject, 80), 'created_at', isv.created_at) AS context
   FROM inbox_sla_violations isv
UNION ALL
 SELECT 'paused_job_review_needed'::text AS source,
    (j.id)::text AS key,
    json_build_object('repo_name', j.repo_name, 'description', "left"(COALESCE(j.description, ''::text), 100), 'fail_count', j.fail_count, 'result_summary_prefix', "left"(COALESCE(j.result_summary, ''::text), 200), 'updated_at', j.updated_at, 'classification',
        CASE
            WHEN (j.result_summary ~~ '%[paused_jobs_policy auto-retry%'::text) THEN 'allowlist_re_paused'::text
            ELSE 'other'::text
        END) AS context
   FROM jobs j
  WHERE ((j.status = 'paused'::text) AND (j.fail_count >= 3) AND (j.updated_at < (now() - '01:00:00'::interval)) AND (COALESCE(j.result_summary, ''::text) !~~ '%ghost success prevented%'::text))
UNION ALL
 SELECT 'paused_job_permanent_review'::text AS source,
    (j.id)::text AS key,
    json_build_object('repo_name', j.repo_name, 'description', "left"(COALESCE(j.description, ''::text), 100), 'fail_count', j.fail_count, 'result_summary_prefix', "left"(COALESCE(j.result_summary, ''::text), 200), 'updated_at', j.updated_at, 'note', 'ghost-success-prevented; retry won''t help — needs spec rewrite or manual decision') AS context
   FROM jobs j
  WHERE ((j.status = 'paused'::text) AND (j.fail_count >= 3) AND (j.updated_at < (now() - '24:00:00'::interval)) AND (COALESCE(j.result_summary, ''::text) ~~ '%ghost success prevented%'::text))
UNION ALL
 SELECT 'synthetic_filter'::text AS source,
        'filtered_24h'::text     AS key,
        json_build_object(
          'count',   count(*),
          'last_at', max(created_at),
          'mode',    'enforce'
        ) AS context
   FROM notification_log
  WHERE source = 'synthetic_filter'
    AND (message_text::jsonb->>'mode') = 'enforce'
    AND created_at >= now() - interval '24 hours'
  HAVING count(*) > 0
UNION ALL
 SELECT 'synthetic_filter'::text AS source,
        'shadow_24h'::text       AS key,
        json_build_object(
          'count',   count(*),
          'last_at', max(created_at),
          'mode',    'shadow'
        ) AS context
   FROM notification_log
  WHERE source = 'synthetic_filter'
    AND (message_text::jsonb->>'mode') = 'shadow'
    AND created_at >= now() - interval '24 hours'
  HAVING count(*) > 0;
```

(Insert this CREATE OR REPLACE VIEW block between Section 2's UPDATE statement and the COMMIT. The remainder of the file stays as written.)

Re-apply the migration:

```bash
source .venv/bin/activate && python3 <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
sql = open('/Users/sheikhmusa/wingmen/orchestrator/supabase/migrations/20260508_bug_reports_synthetic_filter.sql').read()
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(sql)
        print('migration applied with view replace')
PY
```

- [ ] **Step 9.5: Run view tests, verify they pass**

```bash
python -m pytest tests/test_synthetic_filter_integration.py::test_boot_briefing_view_has_synthetic_filter_arms tests/test_synthetic_filter_integration.py::test_boot_briefing_synthetic_filter_zero_count_omitted -v
```

Expected: 2 passed.

- [ ] **Step 9.6: Commit**

```bash
git add supabase/migrations/20260508_bug_reports_synthetic_filter.sql tests/test_synthetic_filter_integration.py
git commit -m "feat(synthetic-filter): migration section 3 — boot_briefing view extension"
```

---

## Task 10: Migration Section 4 — assertion gate

**Files:**
- Modify: `supabase/migrations/20260508_bug_reports_synthetic_filter.sql`

- [ ] **Step 10.1: Add Section 4 (DO $$ assertion block) before COMMIT**

In `supabase/migrations/20260508_bug_reports_synthetic_filter.sql`, insert the following block AFTER the CREATE OR REPLACE VIEW statement and BEFORE the `COMMIT;`:

```sql
-- Section 4: assertion gate — fail loud per CAI-RESP-080 CHALLENGE-1
DO $$
DECLARE
    backfilled_count INT;
    view_def TEXT;
    arm_count INT;
BEGIN
    -- Assert backfill flipped at least one row
    SELECT count(*) INTO backfilled_count
      FROM bug_reports
     WHERE rejected_by = 'cc-orchestrator-filter-backfill';
    IF backfilled_count = 0 THEN
        RAISE WARNING 'synthetic-filter backfill matched 0 rows — patterns may be wrong (or this is a fresh DB)';
    ELSE
        RAISE NOTICE 'synthetic-filter backfill: % rows flipped to status=rejected', backfilled_count;
    END IF;

    -- Assert boot_briefing view contains both new UNION arms
    SELECT pg_get_viewdef('boot_briefing'::regclass, true) INTO view_def;
    SELECT (regexp_count(view_def, 'synthetic_filter')) INTO arm_count;
    IF arm_count < 2 THEN
        RAISE EXCEPTION 'boot_briefing view missing synthetic_filter UNION arms — only % occurrences found', arm_count;
    END IF;
    RAISE NOTICE 'boot_briefing view: % synthetic_filter references found', arm_count;
END $$;
```

- [ ] **Step 10.2: Re-apply migration to verify the gate runs cleanly**

```bash
source .venv/bin/activate && python3 <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
sql = open('/Users/sheikhmusa/wingmen/orchestrator/supabase/migrations/20260508_bug_reports_synthetic_filter.sql').read()
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(sql)
        # Drain notices
        for n in c.notices:
            print(n.strip())
        print('migration re-applied — gate passed')
PY
```

Expected: NOTICE lines mentioning the backfill row count and synthetic_filter arm count. No EXCEPTION.

- [ ] **Step 10.3: Run all integration tests to confirm nothing regressed**

```bash
python -m pytest tests/test_synthetic_filter_integration.py -v
```

Expected: all green.

- [ ] **Step 10.4: Commit**

```bash
git add supabase/migrations/20260508_bug_reports_synthetic_filter.sql
git commit -m "feat(synthetic-filter): migration section 4 — assertion gate"
```

---

## Task 11: Wire `synthetic_filter` into `bug_reports_poll`

**Files:**
- Modify: `nervous_system/bug_reports_poll.py`
- Modify: `tests/test_synthetic_filter_integration.py`

- [ ] **Step 11.1: Write failing end-to-end test (shadow mode does not block dispatch)**

Append to `tests/test_synthetic_filter_integration.py`:

```python
@pytestmark_integration
@pytest.mark.asyncio
async def test_poll_shadow_mode_logs_but_dispatches(monkeypatch):
    """In shadow mode, a synthetic bug should: (a) be classified+logged,
    (b) STILL get a job created (dispatch proceeds)."""
    from supabase import create_async_client
    from nervous_system.bug_reports_poll import poll_bug_reports

    monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", "true")
    monkeypatch.delenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", raising=False)

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    supabase = await create_async_client(url, key)

    bug_id = str(uuid.uuid4())
    test_marker = f"synthfilter-poll-{uuid.uuid4().hex[:8]}"
    await supabase.table("bug_reports").insert({
        "id": bug_id,
        "reporter_name": f"{test_marker} (Test)",
        "reporter_source": "web",
        "auth_provider": "none",
        "repo_name": "cosem-tdu",
        "description": "real text",
        "status": "new",
    }).execute()

    try:
        await poll_bug_reports(supabase)

        check = await supabase.table("bug_reports").select(
            "status, job_id"
        ).eq("id", bug_id).execute()
        row = check.data[0]
        # Shadow: dispatch happened — bug moved to diagnosing
        assert row["status"] == "diagnosing", \
            f"shadow mode should dispatch, got status={row['status']}"
        assert row["job_id"] is not None

        log_check = await supabase.table("notification_log").select(
            "message_text"
        ).eq("recipient", bug_id).execute()
        assert len(log_check.data) == 1
        msg = json.loads(log_check.data[0]["message_text"])
        assert msg["mode"] == "shadow"
    finally:
        # Cleanup: bug + notification_log + the spawned job
        check = await supabase.table("bug_reports").select("job_id").eq("id", bug_id).execute()
        if check.data and check.data[0].get("job_id"):
            await supabase.table("jobs").delete().eq("id", check.data[0]["job_id"]).execute()
        await supabase.table("notification_log").delete().eq("recipient", bug_id).execute()
        await supabase.table("bug_reports").delete().eq("id", bug_id).execute()


@pytestmark_integration
@pytest.mark.asyncio
async def test_poll_enforce_mode_rejects_does_not_dispatch(monkeypatch):
    """In enforce mode, a synthetic bug should be rejected and NOT dispatched."""
    from supabase import create_async_client
    from nervous_system.bug_reports_poll import poll_bug_reports

    monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", "true")
    monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", "true")

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    supabase = await create_async_client(url, key)

    bug_id = str(uuid.uuid4())
    test_marker = f"synthfilter-poll-{uuid.uuid4().hex[:8]}"
    await supabase.table("bug_reports").insert({
        "id": bug_id,
        "reporter_name": f"{test_marker} (Test)",
        "reporter_source": "web",
        "auth_provider": "none",
        "repo_name": "cosem-tdu",
        "description": "real text",
        "status": "new",
    }).execute()

    try:
        await poll_bug_reports(supabase)

        check = await supabase.table("bug_reports").select(
            "status, job_id, rejection_reason, rejected_by"
        ).eq("id", bug_id).execute()
        row = check.data[0]
        assert row["status"] == "rejected"
        assert row["job_id"] is None
        assert row["rejection_reason"] == "synthetic_e2e_test"
        assert row["rejected_by"] == "cc-orchestrator-filter"
    finally:
        await supabase.table("notification_log").delete().eq("recipient", bug_id).execute()
        await supabase.table("bug_reports").delete().eq("id", bug_id).execute()
```

- [ ] **Step 11.2: Run tests, verify they fail**

```bash
python -m pytest tests/test_synthetic_filter_integration.py::test_poll_shadow_mode_logs_but_dispatches tests/test_synthetic_filter_integration.py::test_poll_enforce_mode_rejects_does_not_dispatch -v
```

Expected: failures — `bug_reports_poll` doesn't yet call the classifier.

- [ ] **Step 11.3: Wire classifier into the poll loop**

Open `nervous_system/bug_reports_poll.py`. After the existing `import` block at the top (around line 30), add:

```python
from nervous_system.synthetic_filter import (
    classify as _synth_classify,
    apply_classification as _synth_apply,
    _filter_enabled as _synth_filter_enabled,
    _filter_mode as _synth_filter_mode,
)
```

Then locate the `for bug in bugs:` loop (around line 113). Insert the classifier call IMMEDIATELY after `bug_id = bug["id"]` and before `repo_name = ...`:

```python
        for bug in bugs:
            bug_id = bug["id"]

            # BUG-PIPELINE-SYNTHETIC-FILTER-001: dispatch-time classifier.
            # Shadow mode logs but proceeds; enforce mode rejects + skips.
            if _synth_filter_enabled():
                classification = _synth_classify(bug)
                if classification is not None:
                    mode = _synth_filter_mode()
                    try:
                        await _synth_apply(supabase, bug, classification, mode=mode)
                    except Exception as e:
                        logger.error(
                            f"bug_reports_poll: synthetic_filter apply failed for "
                            f"bug {bug_id}: {e} — falling through to dispatch (fail-open)"
                        )
                    else:
                        if mode == "enforce":
                            logger.info(
                                f"bug_reports_poll: bug {bug_id} REJECTED by synthetic_filter "
                                f"(rule={classification.rule}); skipping dispatch"
                            )
                            continue   # skip dispatch
                        # shadow: fall through to dispatch

            repo_name = (bug.get("repo_name") or "ihsanos").strip()
            description = (bug.get("description") or "")[:80]
            ...
```

- [ ] **Step 11.4: Run tests, verify they pass**

```bash
python -m pytest tests/test_synthetic_filter_integration.py::test_poll_shadow_mode_logs_but_dispatches tests/test_synthetic_filter_integration.py::test_poll_enforce_mode_rejects_does_not_dispatch -v
```

Expected: 2 passed.

- [ ] **Step 11.5: Run full poll-related test sweep to catch regressions**

```bash
python -m pytest tests/ -k "bug_reports_poll or synthetic_filter or bug_pipeline" -v
```

Expected: all green. If older `bug_reports_poll` tests fail because they now expect classifier env vars to be set, add `monkeypatch.delenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", raising=False)` + `monkeypatch.delenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", raising=False)` at the start of the affected test (defaults are already shadow mode + enabled, which preserves dispatch behavior).

- [ ] **Step 11.6: Commit**

```bash
git add nervous_system/bug_reports_poll.py tests/test_synthetic_filter_integration.py
git commit -m "feat(synthetic-filter): wire classifier into bug_reports_poll loop"
```

---

## Task 12: Filter-disabled short-circuit test + `.env.example` documentation

**Files:**
- Modify: `tests/test_synthetic_filter_integration.py`
- Modify: `.env.example`

- [ ] **Step 12.1: Write filter-disabled test**

Append to `tests/test_synthetic_filter_integration.py`:

```python
@pytestmark_integration
@pytest.mark.asyncio
async def test_poll_filter_disabled_short_circuits(monkeypatch):
    """ENABLED=false: filter never runs, no notification_log entry,
    dispatch proceeds normally."""
    from supabase import create_async_client
    from nervous_system.bug_reports_poll import poll_bug_reports

    monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", "false")

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    supabase = await create_async_client(url, key)

    bug_id = str(uuid.uuid4())
    test_marker = f"synthfilter-disabled-{uuid.uuid4().hex[:8]}"
    await supabase.table("bug_reports").insert({
        "id": bug_id,
        "reporter_name": f"{test_marker} (Test)",
        "reporter_source": "web",
        "auth_provider": "none",
        "repo_name": "cosem-tdu",
        "description": "real text",
        "status": "new",
    }).execute()

    try:
        await poll_bug_reports(supabase)

        check = await supabase.table("bug_reports").select("status, job_id").eq("id", bug_id).execute()
        row = check.data[0]
        # Filter off: dispatch proceeds, status becomes diagnosing (synthetic gets dispatched)
        assert row["status"] == "diagnosing"
        assert row["job_id"] is not None

        log_check = await supabase.table("notification_log").select("id").eq("recipient", bug_id).execute()
        assert len(log_check.data) == 0, "filter disabled should not write notification_log"
    finally:
        check = await supabase.table("bug_reports").select("job_id").eq("id", bug_id).execute()
        if check.data and check.data[0].get("job_id"):
            await supabase.table("jobs").delete().eq("id", check.data[0]["job_id"]).execute()
        await supabase.table("bug_reports").delete().eq("id", bug_id).execute()
```

- [ ] **Step 12.2: Run test, verify it passes**

The behavior already exists from Task 11 (the `if _synth_filter_enabled():` guard). This test just confirms it.

```bash
python -m pytest tests/test_synthetic_filter_integration.py::test_poll_filter_disabled_short_circuits -v
```

Expected: 1 passed.

- [ ] **Step 12.3: Document env flags in `.env.example`**

Open `.env.example` (or create if absent). Append:

```bash
# BUG-PIPELINE-SYNTHETIC-FILTER-001 (2026-05-08) — dispatch-time auto-reject
# filter for synthetic E2E test bug reports.
#
# ENABLED kill-switch: set to "false" to bypass the filter entirely and
# revert to PR #28-only behavior (intake-side is_test flag still works).
# Default: true.
ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED=true
#
# ENFORCE mode toggle. Default "false" → shadow mode (classify + log, do
# not block dispatch). Flip to "true" AFTER 48h+ shadow window with 5+
# samples and zero false-positives observed. Restart orchestrator after
# changing.
ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE=false
```

- [ ] **Step 12.4: Commit**

```bash
git add .env.example tests/test_synthetic_filter_integration.py
git commit -m "feat(synthetic-filter): filter-disabled short-circuit test + .env.example docs"
```

---

## Task 13: PR + ship

**Files:**
- (no code changes)

- [ ] **Step 13.1: Run the full test suite locally**

```bash
python -m pytest tests/ -q --timeout=120
```

Expected: all green. Note: pre-existing 5 failures in `test_auto_announce_trigger_fix.py` (PR #27 side-effect, separate follow-up). If those are still failing, they're orthogonal to this PR — leave them.

- [ ] **Step 13.2: Push branch + open PR**

```bash
git push -u origin feat/synthetic-filter
gh pr create --base main --head feat/synthetic-filter \
  --title "feat(synthetic-filter): dispatch-time auto-reject for E2E test bugs (BUG-PIPELINE-SYNTHETIC-FILTER-001)" \
  --body "$(cat <<'EOF'
## Summary
- Implements BUG-PIPELINE-SYNTHETIC-FILTER-001 + CAI-RESP-141 clarifications. Two-rule classifier (cai's a/b; rule c dropped per CL1 — no `repro_steps` column), dispatch-time gate in `bug_reports_poll`, shadow → enforce phased rollout via two env flags.
- Migration: adds `rejected_at` + `rejected_by` columns, backfills historical synthetic rows (cai a/b ∪ PR #28 `is_test=true`) to `status='rejected'`, extends `boot_briefing` view with two 24h counter UNION arms (filtered_24h + shadow_24h).
- Defaults to shadow mode on deploy (`ENABLED=true`, `ENFORCE=false`). Operator flips `ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE=true` after 48h+ shadow with zero FPs over 5+ samples.

## Test plan
- [x] 17 pure-unit tests (`tests/test_synthetic_filter.py`) — rule a positive/negative, rule b positive/negative, rule c drop regression, mode helpers
- [x] Live-DB integration tests (`tests/test_synthetic_filter_integration.py`) — schema asserts, shadow + enforce + disabled paths, backfill outcome, view exposure
- [x] Migration applied to live DB; backfill row count + boot_briefing arm count both confirmed by Section 4 assertion gate
- [x] Verified existing `bug_reports_poll` tests still green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 13.3: Wait for CI green**

```bash
gh pr checks $(gh pr view --json number -q .number)
```

If pre-existing flake (asyncio teardown unraisable) recurs, that was fixed in PR #28 — should not repeat. If a different test fails, investigate before merging.

- [ ] **Step 13.4: Merge PR**

```bash
gh pr merge $(gh pr view --json number -q .number) --squash --delete-branch
git checkout main && git pull origin main
```

- [ ] **Step 13.5: Restart orchestrator to load new code**

```bash
./scripts/restart_orch.sh
```

Verify the process is alive + new env vars are in effect:

```bash
launchctl list | grep wingmen.orchestrator
grep ORCHESTRATOR_SYNTHETIC_FILTER /Users/sheikhmusa/wingmen/orchestrator/.env
```

Expected: orchestrator PID returned + both env flags present (default values).

- [ ] **Step 13.6: File ship-update to cai (P3 update on the existing #1422 thread)**

```bash
source .venv/bin/activate && python3 <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')

with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute("SELECT thread_id FROM agent_messages WHERE id=1422")
        thread_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO agent_messages
              (thread_id, from_agent, to_agent, message_type, subject, body,
               requires_response, is_test, sub_tag, priority)
            VALUES (%s, 'cc-orchestrator', 'cai', 'update', %s, %s,
                    false, false, 'cc-orchestrator-2', 'P3')
            RETURNING id
        """, (thread_id,
              "BUG-PIPELINE-SYNTHETIC-FILTER-001 — shipped to main (shadow mode active)",
              "Shipped per CAI-RESP-141. Filter live in shadow mode. "
              "ENABLED=true ENFORCE=false. Backfill flipped historical synthetic rows. "
              "boot_briefing view exposes filtered_24h + shadow_24h. "
              "Operator-controlled cutover after 48h+ / 5+ FP-free samples."))
        print(f"sent: msg #{cur.fetchone()[0]}")
PY
```

---

## Self-Review (run by author after writing the plan)

**Spec coverage:**
- ✅ Architecture (two-layer detection) → Tasks 11 + spec doc reference
- ✅ Rule (a) E2E placeholder → Task 2
- ✅ Rule (b) reporter substring → Task 3
- ✅ Rule (c) dropped → Task 4 (regression test)
- ✅ `SyntheticClassification` dataclass → Task 2
- ✅ `_filter_enabled` + `_filter_mode` → Task 5
- ✅ `apply_classification` shadow path → Task 6
- ✅ `apply_classification` enforce path → Task 7
- ✅ Migration: rejected_at/rejected_by → Task 1
- ✅ Migration: backfill → Task 8
- ✅ Migration: boot_briefing view → Task 9
- ✅ Migration: assertion gate → Task 10
- ✅ Wire into bug_reports_poll → Task 11
- ✅ ENABLED short-circuit → Task 12
- ✅ `.env.example` documentation → Task 12
- ✅ Ship + cai update → Task 13

**Placeholder scan:** No TBD/TODO/"add error handling" — every step has either complete code or an exact command.

**Type consistency:**
- `SyntheticClassification(rule, matched_text, reason)` — same shape across Tasks 2/3/6/7
- Rule literals `"a_e2e_placeholder"` and `"b_test_reporter"` — consistent
- `mode: Literal["shadow", "enforce"]` — consistent across `_filter_mode` and `apply_classification`
- Bug column names match the actual `bug_reports` schema (verified live during brainstorming)
- Migration version `20260508120000` is consistent

**No gaps to fix.**
