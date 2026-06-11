# CADENCE-008 A — cc-ihsanos Inbox-Drain Headless Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A launchd-scheduled, single-cycle headless worker that every 30 min polls the cc-ihsanos agent inbox, identifies work bearing an explicit machine-checkable execution grant, executes ONLY that work in an isolated CI-gated git worktree via `claude -p`, posts a per-cycle work report, and hard-stops/escalates on CI-red or ambiguity.

**Architecture:** Single-cycle entrypoint (`python -m ihsanos_drain.main`) launched by a `dev.wingmen.ihsanos-drain` LaunchAgent with `StartInterval=1800` (oneshot per fire, no long-lived process — mirrors `dev.wingmen.cc-orchestrator.scheduled`, NOT the always-on cc-cai daemon). Each cycle: register/heartbeat in `long_running_claude_callers` → check kill-switch env flag → check daily token budget → poll inbox → apply the grant predicate → (gated) execute in worktree → CI-gate → merge or escalate → post work report → exit. Worktree + headless-invoke reuse `ralph_runner` patterns. Kill-switch mirrors `cc_cai_daemon/kill_switch.py` env-flag pattern.

**Tech Stack:** Python 3.9 (`.venv`), psycopg + supabase-py, asyncio subprocess for `claude -p`, launchd, Supabase (`agent_messages`, `strategic_decisions`, `long_running_claude_callers`, new `drain_token_ledger`).

**Two hard gates before live execution (NON-NEGOTIABLE):**
1. **CADENCE-008 challenge window** must be CLOSED (was open until 2026-06-11 14:17 UTC).
2. **Grant predicate ratified by cai** (question #2066). Until ratified, the execute arm is NOT wired; worker runs REPORT-ONLY.

**Build order:** Tasks 1–6 (report-only scaffold) are safe to build + even deploy now — they cannot execute. Task 7 (execute arm) is BLOCKED on both gates above; do not implement its `claude -p` spawn until #2066 is ratified.

---

## File Structure

- Create: `ihsanos_drain/__init__.py` — package marker
- Create: `ihsanos_drain/kill_switch.py` — env-flag gate (`WINGMEN_IHSANOS_DRAIN_DISABLED`)
- Create: `ihsanos_drain/token_budget.py` — daily token-spend ledger + cap enforcement
- Create: `ihsanos_drain/grant.py` — the execution-grant predicate (pure function over a decision row)
- Create: `ihsanos_drain/poller.py` — poll cc-ihsanos inbox (both unread classes)
- Create: `ihsanos_drain/report.py` — per-cycle work-report writer (from_agent='substrate')
- Create: `ihsanos_drain/runner.py` — worktree + `claude -p` invocation + CI-gate **(GATED — Task 7)**
- Create: `ihsanos_drain/main.py` — single-cycle orchestrator entrypoint
- Create: `scripts/apply_drain_token_ledger.py` — psycopg-apply for the new table (decision-962 safe)
- Create: `ops/launchd/dev.wingmen.ihsanos-drain.plist` — LaunchAgent template (StartInterval=1800)
- Create: `manifests/long_running_callers/ihsanos_drain.yaml` — registry manifest
- Test: `tests/ihsanos_drain/test_kill_switch.py`, `test_token_budget.py`, `test_grant.py`, `test_poller.py`, `test_report.py`, `test_main_cycle.py`

---

## Task 1: Kill-switch env-flag gate

Mirrors `cc_cai_daemon/kill_switch.py:38` (`PANIC_ENV_VAR`) — env-based, no DB lookup.

**Files:**
- Create: `ihsanos_drain/__init__.py`
- Create: `ihsanos_drain/kill_switch.py`
- Test: `tests/ihsanos_drain/__init__.py`, `tests/ihsanos_drain/test_kill_switch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ihsanos_drain/test_kill_switch.py
import os
from ihsanos_drain.kill_switch import drain_disabled

def test_enabled_when_flag_unset(monkeypatch):
    monkeypatch.delenv("WINGMEN_IHSANOS_DRAIN_DISABLED", raising=False)
    assert drain_disabled() is False

def test_disabled_for_truthy_values(monkeypatch):
    for v in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("WINGMEN_IHSANOS_DRAIN_DISABLED", v)
        assert drain_disabled() is True

def test_enabled_for_falsey_values(monkeypatch):
    for v in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("WINGMEN_IHSANOS_DRAIN_DISABLED", v)
        assert drain_disabled() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/ihsanos_drain/test_kill_switch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ihsanos_drain'`

- [ ] **Step 3: Write minimal implementation**

```python
# ihsanos_drain/kill_switch.py
"""CADENCE-008 A kill-switch: env-flag panic gate (mirrors cc_cai_daemon)."""
from __future__ import annotations
import os

PANIC_ENV_VAR = "WINGMEN_IHSANOS_DRAIN_DISABLED"
_TRUTHY = {"1", "true", "yes", "on"}

def drain_disabled() -> bool:
    """True if the operator has tripped the kill switch. Default: enabled (False)."""
    return os.environ.get(PANIC_ENV_VAR, "").strip().lower() in _TRUTHY
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/ihsanos_drain/test_kill_switch.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add ihsanos_drain/__init__.py ihsanos_drain/kill_switch.py tests/ihsanos_drain/
git commit -m "feat(cadence-008a): ihsanos_drain kill-switch env gate"
```

---

## Task 2: Daily token-budget ledger + cap

Schema fields `expected_tokens_per_day` / `max_tokens_per_day` exist on `long_running_claude_callers` but are NOT enforced. Build a spend ledger + a `within_budget()` check the cycle calls before any spawn.

**Files:**
- Create: `scripts/apply_drain_token_ledger.py`
- Create: `ihsanos_drain/token_budget.py`
- Test: `tests/ihsanos_drain/test_token_budget.py`

- [ ] **Step 1: Write the apply script (new table)**

```python
# scripts/apply_drain_token_ledger.py
"""CADENCE-008 A: drain_token_ledger table (psycopg-apply, decision-962 safe).

Tracks per-cycle token spend so the runner can enforce a daily cap. CLAUDE.md
forbids `supabase db push` against prod; use this direct psycopg-apply.

Usage:
  python scripts/apply_drain_token_ledger.py            # dry-run (no writes)
  python scripts/apply_drain_token_ledger.py --apply    # commit
"""
from __future__ import annotations
import os, sys
import psycopg
from dotenv import load_dotenv

APPLY_SQL = """
create table if not exists drain_token_ledger (
    id           biggenerated by default as identity primary key,
    caller_name  text not null,
    cycle_at     timestamptz not null default now(),
    tokens_spent integer not null default 0,
    note         text
);
create index if not exists drain_token_ledger_caller_day_idx
    on drain_token_ledger (caller_name, cycle_at);
"""

def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(APPLY_SQL)
        cur.execute("select to_regclass('public.drain_token_ledger')")
        assert cur.fetchone()[0] is not None, "table not created"
        if apply:
            conn.commit(); print("APPLIED drain_token_ledger.")
        else:
            conn.rollback(); print("DRY-RUN (rolled back). Re-run with --apply.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

NOTE: fix the obvious typo before running — column line must read
`id bigint generated by default as identity primary key,`. (Verify with a dry-run.)

- [ ] **Step 2: Write the failing test**

```python
# tests/ihsanos_drain/test_token_budget.py
from ihsanos_drain.token_budget import within_budget, record_spend

def test_within_budget_true_when_under_cap():
    assert within_budget(spent_today=1000, cap=200_000) is True

def test_within_budget_false_when_at_or_over_cap():
    assert within_budget(spent_today=200_000, cap=200_000) is False
    assert within_budget(spent_today=250_000, cap=200_000) is False

def test_within_budget_true_when_cap_none():
    # No cap configured => unbounded (but caller should set one).
    assert within_budget(spent_today=999_999, cap=None) is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/ihsanos_drain/test_token_budget.py -v`
Expected: FAIL (`ModuleNotFoundError` / `ImportError`)

- [ ] **Step 4: Write minimal implementation**

```python
# ihsanos_drain/token_budget.py
"""CADENCE-008 A daily token-budget enforcement over drain_token_ledger."""
from __future__ import annotations
from typing import Optional

def within_budget(spent_today: int, cap: Optional[int]) -> bool:
    """True if another cycle is allowed. cap=None => unbounded."""
    if cap is None:
        return True
    return spent_today < cap

def spent_today_sql(caller_name: str) -> tuple[str, tuple]:
    """SELECT total tokens spent by caller since local midnight (UTC day)."""
    return (
        "SELECT COALESCE(SUM(tokens_spent), 0) FROM drain_token_ledger "
        "WHERE caller_name = %s AND cycle_at >= date_trunc('day', now())",
        (caller_name,),
    )

def record_spend_sql(caller_name: str, tokens: int, note: str) -> tuple[str, tuple]:
    return (
        "INSERT INTO drain_token_ledger (caller_name, tokens_spent, note) "
        "VALUES (%s, %s, %s)",
        (caller_name, tokens, note),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/ihsanos_drain/test_token_budget.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Dry-run the apply script, then commit (do NOT --apply until reviewed)**

```bash
.venv/bin/python scripts/apply_drain_token_ledger.py   # dry-run, expect "DRY-RUN"
git add scripts/apply_drain_token_ledger.py ihsanos_drain/token_budget.py tests/ihsanos_drain/test_token_budget.py
git commit -m "feat(cadence-008a): drain_token_ledger schema + budget check"
```

---

## Task 3: Execution-grant predicate (RATIFICATION-GATED LOGIC, pure function buildable now)

The predicate itself is a pure function over a decision row — safe to build + test now. Whether `execution_status='granted'` is the ratified signal is pending cai #2066; if cai amends, only the constants here change. The function returns a structured verdict; the cycle treats anything non-`GRANTED` as report-only.

**Files:**
- Create: `ihsanos_drain/grant.py`
- Test: `tests/ihsanos_drain/test_grant.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ihsanos_drain/test_grant.py
from ihsanos_drain.grant import evaluate_grant, GRANTED, REPORT_ONLY, REFUSED_MIGRATION

def _row(**kw):
    base = dict(execution_status="granted", repos_affected=["ihsanos"],
                challenge_status="ratified", decision="do the thing")
    base.update(kw)
    return base

def test_granted_when_all_conditions_met():
    v = evaluate_grant(_row(), is_migration=False, migration_filename=None)
    assert v.status == GRANTED

def test_report_only_when_execution_status_not_granted():
    for s in ("implemented", "ip_gate_cleared", None, "archived"):
        v = evaluate_grant(_row(execution_status=s), is_migration=False, migration_filename=None)
        assert v.status == REPORT_ONLY

def test_report_only_when_not_ihsanos_executor():
    v = evaluate_grant(_row(repos_affected=["orchestrator"]), is_migration=False, migration_filename=None)
    assert v.status == REPORT_ONLY

def test_report_only_when_challenge_window_open():
    v = evaluate_grant(_row(challenge_status="challenge_window"), is_migration=False, migration_filename=None)
    assert v.status == REPORT_ONLY

def test_migration_refused_when_filename_not_named_in_decision():
    v = evaluate_grant(_row(decision="apply the schema change"),
                       is_migration=True, migration_filename="20260612_add_x.sql")
    assert v.status == REFUSED_MIGRATION

def test_migration_granted_when_filename_named_in_decision():
    v = evaluate_grant(_row(decision="apply 20260612_add_x.sql exactly"),
                       is_migration=True, migration_filename="20260612_add_x.sql")
    assert v.status == GRANTED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/ihsanos_drain/test_grant.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Write minimal implementation**

```python
# ihsanos_drain/grant.py
"""CADENCE-008 A execution-grant predicate (cai #2066, conservative v1).

Verdict over a strategic_decisions row. Anything not GRANTED is report-only;
ambiguity in the caller => hard stop + escalate (handled in main cycle).
The grant SIGNAL is execution_status == GRANT_SIGNAL; pending cai ratification
of #2066 the cycle keeps the execute arm unwired regardless of this verdict.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

GRANT_SIGNAL = "granted"          # set ONLY by the ruling author (cai)
EXECUTOR_REPO = "ihsanos"
GRANTED = "granted"
REPORT_ONLY = "report_only"
REFUSED_MIGRATION = "refused_migration"

@dataclass(frozen=True)
class Verdict:
    status: str
    reason: str

def evaluate_grant(row: dict, *, is_migration: bool,
                   migration_filename: Optional[str]) -> Verdict:
    if row.get("execution_status") != GRANT_SIGNAL:
        return Verdict(REPORT_ONLY, "execution_status is not 'granted'")
    repos = row.get("repos_affected") or []
    if EXECUTOR_REPO not in repos:
        return Verdict(REPORT_ONLY, "ruling does not name ihsanos as executor")
    if row.get("challenge_status") == "challenge_window":
        return Verdict(REPORT_ONLY, "ruling still in challenge window")
    if is_migration:
        decision_text = row.get("decision") or ""
        if not migration_filename or migration_filename not in decision_text:
            return Verdict(REFUSED_MIGRATION,
                           "migration filename not literally named in ruling")
    return Verdict(GRANTED, "all grant conditions satisfied")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/ihsanos_drain/test_grant.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add ihsanos_drain/grant.py tests/ihsanos_drain/test_grant.py
git commit -m "feat(cadence-008a): execution-grant predicate (cai #2066 v1)"
```

---

## Task 4: cc-ihsanos inbox poller

Mirrors `cc_cai_daemon/poller.py:25-63` with `to_agent='cc-ihsanos'`.

**Files:**
- Create: `ihsanos_drain/poller.py`
- Test: `tests/ihsanos_drain/test_poller.py`

- [ ] **Step 1: Write the failing test** (asserts the SQL shape + params; no live DB)

```python
# tests/ihsanos_drain/test_poller.py
from ihsanos_drain.poller import inbox_query

def test_inbox_query_targets_cc_ihsanos_unread_nontest():
    sql, params = inbox_query(limit=50)
    assert "to_agent = %s" in sql
    assert "read_at IS NULL" in sql
    assert "is_test = false" in sql
    assert "skipped_at IS NULL" in sql
    assert "ORDER BY priority ASC, created_at ASC" in sql
    assert params == ("cc-ihsanos", 50)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/ihsanos_drain/test_poller.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Write minimal implementation**

```python
# ihsanos_drain/poller.py
"""CADENCE-008 A: poll the cc-ihsanos agent inbox (both unread classes)."""
from __future__ import annotations

TO_AGENT = "cc-ihsanos"

def inbox_query(limit: int = 50) -> tuple[str, tuple]:
    sql = (
        "SELECT id, thread_id, from_agent, to_agent, message_type, subject, "
        "body, requires_response, priority, created_at, sub_tag "
        "FROM agent_messages "
        "WHERE to_agent = %s AND read_at IS NULL AND is_test = false "
        "AND skipped_at IS NULL "
        "ORDER BY priority ASC, created_at ASC LIMIT %s"
    )
    return sql, (TO_AGENT, limit)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/ihsanos_drain/test_poller.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ihsanos_drain/poller.py tests/ihsanos_drain/test_poller.py
git commit -m "feat(cadence-008a): cc-ihsanos inbox poller query"
```

---

## Task 5: Per-cycle work-report writer

Posts `from_agent='substrate'` (per #1990 — automation is not an agent), `sub_tag='substrate-ihsanos-drain'` (sub_tag CHECK requires the from_agent prefix), `to_agent='cai'`. In report-only mode the subject is prefixed `[REPORT-ONLY]`.

**Files:**
- Create: `ihsanos_drain/report.py`
- Test: `tests/ihsanos_drain/test_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ihsanos_drain/test_report.py
from ihsanos_drain.report import build_report_row

def test_report_row_is_substrate_with_prefixed_subtag():
    row = build_report_row(summary="polled 3, 0 granted", report_only=True)
    assert row["from_agent"] == "substrate"
    assert row["sub_tag"] == "substrate-ihsanos-drain"
    assert row["to_agent"] == "cai"
    assert row["message_type"] == "update"
    assert row["requires_response"] is False
    assert row["subject"].startswith("[REPORT-ONLY]")

def test_live_report_has_no_report_only_prefix():
    row = build_report_row(summary="executed grant for #123", report_only=False)
    assert not row["subject"].startswith("[REPORT-ONLY]")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/ihsanos_drain/test_report.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Write minimal implementation**

```python
# ihsanos_drain/report.py
"""CADENCE-008 A per-cycle work report → agent_messages (from_agent='substrate')."""
from __future__ import annotations

FROM_AGENT = "substrate"
SUB_TAG = "substrate-ihsanos-drain"

def build_report_row(*, summary: str, report_only: bool) -> dict:
    prefix = "[REPORT-ONLY] " if report_only else ""
    return {
        "from_agent": FROM_AGENT,
        "to_agent": "cai",
        "message_type": "update",
        "subject": f"{prefix}ihsanos-drain cycle: {summary[:80]}",
        "body": summary[:4000],
        "requires_response": False,
        "is_test": False,
        "from_agent_verified": False,
        "sub_tag": SUB_TAG,
        "priority": "P3",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/ihsanos_drain/test_report.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ihsanos_drain/report.py tests/ihsanos_drain/test_report.py
git commit -m "feat(cadence-008a): per-cycle work-report writer"
```

---

## Task 6: Single-cycle orchestrator (REPORT-ONLY mode) + launchd + manifest

Wires Tasks 1–5 into one cycle. In report-only mode it NEVER spawns `claude -p`; it polls, evaluates grants, and posts a report listing what it WOULD execute. Live execution (Task 7) is added only after both gates clear. The cycle is gated by `DRAIN_EXECUTE_ENABLED` env (default false) AND `drain_disabled()`.

**Files:**
- Create: `ihsanos_drain/main.py`
- Create: `ops/launchd/dev.wingmen.ihsanos-drain.plist`
- Create: `manifests/long_running_callers/ihsanos_drain.yaml`
- Test: `tests/ihsanos_drain/test_main_cycle.py`

- [ ] **Step 1: Write the failing test** (cycle returns a structured result; injected fakes for DB)

```python
# tests/ihsanos_drain/test_main_cycle.py
from ihsanos_drain.main import run_cycle

class FakeCur:
    def __init__(self, rows): self._rows = rows; self.inserted = []
    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("INSERT"): self.inserted.append((sql, params))
        self._last = sql
    def fetchall(self): return self._rows
    def fetchone(self): return (0,)
    def __enter__(self): return self
    def __exit__(self, *a): return False

def test_report_only_cycle_does_not_execute(monkeypatch):
    monkeypatch.setenv("DRAIN_EXECUTE_ENABLED", "false")
    monkeypatch.delenv("WINGMEN_IHSANOS_DRAIN_DISABLED", raising=False)
    cur = FakeCur(rows=[])  # empty inbox
    result = run_cycle(cur, caller_name="ihsanos-drain", token_cap=200_000)
    assert result["executed"] == 0
    assert result["mode"] == "report_only"
    # a report row was written
    assert any("INSERT INTO agent_messages" in s for s, _ in cur.inserted)

def test_kill_switch_short_circuits(monkeypatch):
    monkeypatch.setenv("WINGMEN_IHSANOS_DRAIN_DISABLED", "1")
    cur = FakeCur(rows=[])
    result = run_cycle(cur, caller_name="ihsanos-drain", token_cap=200_000)
    assert result["mode"] == "disabled"
    assert result["executed"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/ihsanos_drain/test_main_cycle.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Write minimal implementation** (report-only; execute arm is a guarded stub)

```python
# ihsanos_drain/main.py
"""CADENCE-008 A single-cycle drain orchestrator. Report-only until both gates clear:
 (1) CADENCE-008 challenge window closed, (2) cai #2066 grant predicate ratified.
Launched per-fire by dev.wingmen.ihsanos-drain (StartInterval=1800)."""
from __future__ import annotations
import os

from ihsanos_drain.kill_switch import drain_disabled
from ihsanos_drain.poller import inbox_query
from ihsanos_drain.report import build_report_row

def _execute_enabled() -> bool:
    return os.environ.get("DRAIN_EXECUTE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}

def run_cycle(cur, *, caller_name: str, token_cap) -> dict:
    if drain_disabled():
        return {"mode": "disabled", "executed": 0, "polled": 0}

    sql, params = inbox_query(limit=50)
    cur.execute(sql, params)
    rows = cur.fetchall()
    polled = len(rows)

    # REPORT-ONLY: execute arm (Task 7) is intentionally absent until gates clear.
    mode = "report_only"
    executed = 0
    summary = f"polled {polled}, granted 0, executed 0 (report-only; execute_enabled={_execute_enabled()})"

    report = build_report_row(summary=summary, report_only=True)
    cur.execute(
        "INSERT INTO agent_messages "
        "(from_agent, to_agent, message_type, subject, body, requires_response, "
        " is_test, from_agent_verified, sub_tag, priority) "
        "VALUES (%(from_agent)s, %(to_agent)s, %(message_type)s, %(subject)s, "
        " %(body)s, %(requires_response)s, %(is_test)s, %(from_agent_verified)s, "
        " %(sub_tag)s, %(priority)s)",
        report,
    )
    return {"mode": mode, "executed": executed, "polled": polled}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/ihsanos_drain/test_main_cycle.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Create the launchd plist** (DO NOT load it yet — gated)

```xml
<!-- ops/launchd/dev.wingmen.ihsanos-drain.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>dev.wingmen.ihsanos-drain</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/sheikhmusa/wingmen/orchestrator/.venv/bin/python</string>
    <string>-m</string><string>ihsanos_drain.main</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/sheikhmusa/wingmen/orchestrator</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>WINGMEN_IHSANOS_DRAIN_DISABLED</key><string>0</string>
    <key>DRAIN_EXECUTE_ENABLED</key><string>false</string>
  </dict>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>/Users/sheikhmusa/wingmen/orchestrator/logs/ihsanos-drain.log</string>
  <key>StandardErrorPath</key><string>/Users/sheikhmusa/wingmen/orchestrator/logs/ihsanos-drain.err</string>
  <key>ExitTimeOut</key><integer>600</integer>
</dict>
</plist>
```

- [ ] **Step 6: Create the manifest**

```yaml
# manifests/long_running_callers/ihsanos_drain.yaml
caller_name: ihsanos-drain
registered_by_identity: cc_family
expected_cadence_seconds: 1800
expected_tokens_per_day: 200000
max_tokens_per_day: 400000
auto_kill_policy: kill_on_red
notes: >
  CADENCE-008 A cc-ihsanos inbox-drain worker. Report-only until cai #2066
  grant predicate ratified AND CADENCE-008 challenge window closed.
```

- [ ] **Step 7: Add `main.py` `__main__` entrypoint (real DB wiring)**

Append to `ihsanos_drain/main.py`:

```python
def _main() -> int:
    import psycopg
    from dotenv import load_dotenv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        result = run_cycle(cur, caller_name="ihsanos-drain", token_cap=400_000)
    print(result)
    return 0

if __name__ == "__main__":
    raise SystemExit(_main())
```

- [ ] **Step 8: Commit**

```bash
git add ihsanos_drain/main.py ops/launchd/dev.wingmen.ihsanos-drain.plist manifests/long_running_callers/ihsanos_drain.yaml tests/ihsanos_drain/test_main_cycle.py
git commit -m "feat(cadence-008a): report-only drain cycle + launchd plist + manifest"
```

---

## Task 7: Execute arm — worktree + `claude -p` + CI-gate  **(BLOCKED — DO NOT BUILD YET)**

**Blocked on BOTH:**
1. cai ratification of the grant predicate (#2066) — its outcome determines `GRANT_SIGNAL`, the migration sub-rule strictness, and whether `execution_status='granted'` is the signal.
2. CADENCE-008 challenge window closed (2026-06-11 14:17 UTC).

**Contract (to be turned into TDD tasks once unblocked):**
- For each polled message whose linked ruling returns `evaluate_grant(...).status == GRANTED`:
  - Create isolated worktree `/tmp/wingmen-wt-ihsanos-drain-<msg_id>` on branch `ihsanos-drain-<msg_id>` (reuse `ralph_runner._create_worktree` shape, `ralph_runner.py:20-58`).
  - Spawn `claude -p <fixed-prompt>` with the env whitelist + 30-min timeout (`ralph_runner.py:301-355`). Prompt embeds: the ruling text, the granted scope, and the HARD rule "never apply a migration not named in the ruling."
  - **CI-gate:** after the session, run the repo's CI/test command in the worktree; on RED → abort merge, escalate (`message_type='blocker'`, `requires_response=True`), leave worktree for forensics.
  - On GREEN → fast-forward merge to main (`ralph_runner._merge_and_remove_worktree`, `ralph_runner.py:61-87`); record token spend via `token_budget.record_spend_sql`.
  - Ambiguity (no linked ruling, multiple candidate rulings, REFUSED_MIGRATION) → NEVER execute; escalate.
- Replace the report-only summary with real per-item commit/CI links.
- Flip `run_cycle` to honor `DRAIN_EXECUTE_ENABLED=true` only when `_execute_enabled()` AND not `drain_disabled()` AND `within_budget(...)`.

**Go-live steps (operator-gated, after Task 7 built + reviewed):**
1. Verify both gates cleared.
2. `cp ops/launchd/dev.wingmen.ihsanos-drain.plist ~/Library/LaunchAgents/`
3. `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.wingmen.ihsanos-drain.plist`
4. First fire stays report-only (`DRAIN_EXECUTE_ENABLED=false`); operator reviews a few cycles' reports, THEN flips the env to `true` and kickstarts.

---

## Self-Review Notes

- **Spec coverage:** A.1 poll (Task 4) ✓; A.2 execute-only-granted (Task 3 predicate + Task 7) ✓; A.3 migration-name rule (Task 3 `REFUSED_MIGRATION`) ✓; A.4 per-cycle work report (Task 5) ✓; A.5 hard-stop/escalate (Task 7 CI-gate + ambiguity) ✓; heartbeat registration (manifest Task 6, wire in Task 7 `_main`) ✓; kill switch (Task 1) ✓; token cap (Task 2) ✓; worktree/CI-gate (Task 7) ✓.
- **#1980 amendment:** the worker NEVER writes `from_agent='musa'`; all its posts are `from_agent='substrate'`. It does not touch the button-authority path.
- **Type consistency:** `evaluate_grant` returns `Verdict`; `within_budget(spent_today, cap)`; `inbox_query(limit)`; `build_report_row(summary=, report_only=)` — names match across tasks.
- **Open item:** the linkage from a polled `agent_messages` row to its governing `strategic_decisions` ruling is resolved in Task 7 (not needed for report-only). Define it when #2066 is answered (likely via thread_id ↔ decision_ref or an explicit `unblocks`/grant reference).
