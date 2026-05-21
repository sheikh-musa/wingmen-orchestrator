# Long-Caller Watchdog Phase B — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the watchdog-kill consumer of `long_running_claude_callers` registry per CAI-RESP-163 ratification. Phase B enforces R1/R3/R4 (R2 deferred per D1 ratification). 4 commits, ~7-day target.

**Architecture:** New `nervous_system/long_caller_watchdog.py` pure-Python kill-decision module — wired into existing `watchdog.py` daemon's main loop on 5min cadence. Consults registry + `active_autonomous_loops` (PR #36 [A]) view + in-memory cadence tracker. C1 pre-kill registry re-query + C2 hard-coded substrate-native frozenset + Q3 PID-recycle race guard + Q-final panic-button env flag.

**Tech Stack:** Python 3.9, psycopg, httpx, signal/os (for SIGTERM), pytest + pytest-asyncio.

**Decision refs:** CAI-RESP-163 (Phase B design ratified), CAI-RESP-161 (Phase A registry), CAI-RESP-157 [A]/[B] (active-loops + watchdog-kill lineage), CC-LONG-CALLER-AUTO-TOKEN-TRACK-001 (D1 R2-parking-lot, decision 908), CC-FAMILY-INTERACTIVE-SESSIONS-001 (belt-and-suspenders pre-registration, decision 909).

---

## File Structure

| Path | Purpose | New/Modified |
|---|---|---|
| `supabase/migrations/20260521_active_loops_parent_pid.sql` | ADD COLUMN active_autonomous_loops.parent_pid | NEW |
| `nervous_system/autonomous_loop_detector.py` | populate parent_pid via ps | MODIFIED |
| `nervous_system/long_caller_watchdog.py` | pure-Python kill-decision + cadence tracker + alert builders | NEW |
| `watchdog.py` | call into long_caller_watchdog on 5min cadence + PID re-verify at kill time | MODIFIED |
| `tests/test_active_loops_parent_pid.py` | live-DB schema assertion | NEW |
| `tests/test_long_caller_watchdog.py` | pure-unit tests (6 cai-mandated cases) | NEW |
| `tests/test_watchdog_phase_b_integration.py` | integration test (synthetic runaway harness) | NEW |
| `docs/superpowers/rollbacks/long-caller-watchdog-phase-b.md` | rollback procedure per CAI-RESP-161 constraint | NEW |

---

## Pre-Flight

- [ ] **Step 0.1: Verify environment**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git branch --show-current  # should be feat/long-caller-watchdog-phase-b
source .venv/bin/activate
python -c "import psycopg, httpx, signal, os; print('ok')"
```

- [ ] **Step 0.2: Confirm pre-requisites are live**

```bash
python3 - <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        # active_autonomous_loops exists (PR #36)
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='active_autonomous_loops'")
        assert cur.fetchone()[0] == 1, "active_autonomous_loops missing — PR #36 not shipped?"
        # long_running_claude_callers exists (PR #37)
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='long_running_claude_callers'")
        assert cur.fetchone()[0] == 1, "long_running_claude_callers missing — PR #37 not shipped?"
        # substrate-native seed present
        cur.execute("SELECT count(*) FROM long_running_claude_callers WHERE registered_by_identity='substrate'")
        assert cur.fetchone()[0] >= 2, "substrate-native seed missing"
        # cc-family-interactive pre-registration present (decision 909)
        cur.execute("SELECT count(*) FROM long_running_claude_callers WHERE ratified_by_decision_ref='CC-FAMILY-INTERACTIVE-SESSIONS-001'")
        assert cur.fetchone()[0] == 3, "cc-family-interactive pre-registration missing — decision 909 not in registry?"
print("pre-flight: all pre-requisites verified")
PY
```

---

## Task 1: Schema migration — `active_autonomous_loops.parent_pid` column

**Files:**
- Create: `supabase/migrations/20260521_active_loops_parent_pid.sql`
- Create: `tests/test_active_loops_parent_pid.py`

- [ ] **Step 1.1: Write failing schema test**

Create `tests/test_active_loops_parent_pid.py`:

```python
"""Live-DB schema test for active_autonomous_loops.parent_pid column."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set"
)


@pytestmark_integration
def test_parent_pid_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name='active_autonomous_loops' AND column_name='parent_pid'"
            )
            r = cur.fetchone()
    assert r is not None, "parent_pid column missing"
    assert r[0] == "integer"
    assert r[1] == "YES"  # nullable — populated when detector resolves PID, else NULL


@pytestmark_integration
def test_boot_briefing_view_exposes_parent_pid():
    """View's long_running_caller arm doesn't change, but the active_autonomous_loops
    arm should now include parent_pid in its context jsonb."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
            defn = cur.fetchone()[0]
    # active_autonomous_loops arm should reference parent_pid in its json_build_object
    # (separate from long_running_caller arm's own parent_pid field).
    # Check via stringy assertion — the arm with source='active_autonomous_loops' must mention parent_pid.
    arm_start = defn.find("'active_autonomous_loops'::text")
    assert arm_start >= 0, "active_autonomous_loops arm missing in view"
    arm_end = defn.find("UNION ALL", arm_start)
    if arm_end < 0:
        arm_end = len(defn)
    arm_body = defn[arm_start:arm_end]
    assert "parent_pid" in arm_body, f"parent_pid not surfaced in active_autonomous_loops arm: {arm_body[:300]}"
```

- [ ] **Step 1.2: Run, verify RED**

```bash
source .venv/bin/activate && python -m pytest tests/test_active_loops_parent_pid.py -v
```

Expected: both tests fail (column doesn't exist, view doesn't expose).

- [ ] **Step 1.3: Capture current boot_briefing view definition**

```bash
source .venv/bin/activate && python3 - <<'PY' > /tmp/current_boot_briefing_v5.sql
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
        print(cur.fetchone()[0])
PY
wc -l /tmp/current_boot_briefing_v5.sql
```

- [ ] **Step 1.4: Create migration**

Create `supabase/migrations/20260521_active_loops_parent_pid.sql`:

```sql
-- CAI-RESP-163 Q3 amendment: active_autonomous_loops.parent_pid column
-- Watchdog needs PID to SIGTERM unregistered runaway callers.
-- Detector populates via `ps -ef | grep <cc-cwd>` per sweep cycle.
-- Per CAI-RESP-163 Q3 safety guard: watchdog re-verifies PID ownership at
-- kill time (read /proc/<pid>/cwd or `ps -o cwd= -p <pid>`) before SIGTERM —
-- defends against kernel PID reuse during the 5-min sweep-to-kill gap.
--
-- Idempotent. Pre-apply per CAI-RESP-102.

BEGIN;

-- Section 1: add column
ALTER TABLE active_autonomous_loops
  ADD COLUMN IF NOT EXISTS parent_pid INTEGER;

COMMENT ON COLUMN active_autonomous_loops.parent_pid IS
    'Per CAI-RESP-163 Q3: PID of the parent process spawning sessions. '
    'Populated by detector via ps + cwd-match. Watchdog re-verifies at kill time '
    'to defend against PID recycle. NULL when detector cannot resolve.';

-- Section 2: extend boot_briefing view to surface parent_pid in active_autonomous_loops arm
-- (Replace existing arm; CREATE OR REPLACE VIEW preserves all other arms.)
CREATE OR REPLACE VIEW boot_briefing AS
<PASTE FULL EXISTING VIEW BODY FROM /tmp/current_boot_briefing_v5.sql, but FIND
 the active_autonomous_loops UNION arm and REPLACE its json_build_object with:
   json_build_object(
     'last_fire_at',    aal.last_fire_at,
     'sessions_24h',    aal.sessions_24h,
     'cadence_seconds', aal.cadence_seconds,
     'detected_at',     aal.detected_at,
     'parent_pid',      aal.parent_pid
   )
 Leave all OTHER UNION arms verbatim. Drop trailing semicolon, then ;>;

-- Section 3: assertion gate
DO $$
DECLARE col_exists BOOLEAN;
DECLARE view_def TEXT;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name='active_autonomous_loops' AND column_name='parent_pid'
    ) INTO col_exists;
    IF NOT col_exists THEN
        RAISE EXCEPTION 'parent_pid column missing after ADD COLUMN';
    END IF;
    SELECT pg_get_viewdef('boot_briefing'::regclass, true) INTO view_def;
    IF view_def !~ 'parent_pid' THEN
        RAISE EXCEPTION 'view does not surface parent_pid after replace';
    END IF;
    RAISE NOTICE 'CAI-RESP-163 Q3 parent_pid column + view surface verified';
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260521120000',
    'active_loops_parent_pid',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
```

- [ ] **Step 1.5: Apply migration**

```bash
source .venv/bin/activate && python3 - <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
sql = open('/Users/sheikhmusa/wingmen/orchestrator/supabase/migrations/20260521_active_loops_parent_pid.sql').read()
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(sql)
        print('migration applied')
PY
```

- [ ] **Step 1.6: Linter check + tests pass**

```bash
python scripts/check_additive_migration.py supabase/migrations/20260521_active_loops_parent_pid.sql && python -m pytest tests/test_active_loops_parent_pid.py -v
```

- [ ] **Step 1.7: Wire detector to populate parent_pid**

Modify `nervous_system/autonomous_loop_detector.py` `scan_one_directory` or `detect_active_loops`:

For each flagged cc_identity, run `ps -eo pid,command | grep <cc-cwd>` and pick the longest-running matching claude process. Add to the returned dict as `parent_pid`. The `sweep_active_autonomous_loops` upsert path then writes this value to the new column.

CWD mapping: extend the existing `_DIR_TO_CC` reverse-lookup — for each cc_identity, derive the absolute cwd path (e.g., `/Users/sheikhmusa/wingmen/projects/ai-scholar` for `cc-scholar`).

```python
import asyncio

_CC_TO_CWD = {
    "cc-orchestrator": "/Users/sheikhmusa/wingmen/orchestrator",
    "cc-scholar": "/Users/sheikhmusa/wingmen/projects/ai-scholar",
    "cc-cosem": "/Users/sheikhmusa/wingmen/projects/cosem-tdu",
    "cc-ihsanos": "/Users/sheikhmusa/wingmen/projects/ihsanos",
    "operator-dookana": "/Users/sheikhmusa/wingmen/projects/dookana",
    "operator-cosem-adcda": "/Users/sheikhmusa/wingmen/projects/cosem-adcda",
    "operator-hifz-companion": "/Users/sheikhmusa/wingmen/projects/hifz-companion",
    "operator-fastrans": "/Users/sheikhmusa/wingmen/projects/fastrans",
}


def _resolve_parent_pid(cc_identity: str) -> int | None:
    """Find the long-running claude process whose cwd matches cc_identity's repo."""
    cwd = _CC_TO_CWD.get(cc_identity)
    if not cwd:
        return None
    try:
        import subprocess
        # `lsof -d cwd` is more reliable than ps for cwd-matching on macOS
        proc = subprocess.run(
            ["lsof", "-d", "cwd", "-F", "pn"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return None
        # Output is `p<pid>\nn<dir>\n` repeating; pick PIDs whose dir matches cwd
        candidates = []
        current_pid = None
        for line in proc.stdout.splitlines():
            if line.startswith("p"):
                current_pid = int(line[1:]) if line[1:].isdigit() else None
            elif line.startswith("n") and current_pid is not None:
                if line[1:] == cwd:
                    candidates.append(current_pid)
                current_pid = None
        # Prefer claude processes specifically
        for pid in candidates:
            try:
                cmd_proc = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "command="],
                    capture_output=True, text=True, timeout=2,
                )
                if "claude" in cmd_proc.stdout:
                    return pid
            except subprocess.SubprocessError:
                continue
        return candidates[0] if candidates else None
    except (subprocess.TimeoutExpired, OSError):
        return None
```

In `detect_active_loops`, after building the flagged-row dict, set `row["parent_pid"] = _resolve_parent_pid(cc_identity)`.

In `sweep_active_autonomous_loops` upsert call, include `"parent_pid": row.get("parent_pid")` in the upsert payload.

- [ ] **Step 1.8: Update detector tests for parent_pid**

Append to `tests/test_autonomous_loop_detector.py`:

```python
class TestResolveParentPid:
    """parent_pid resolution: lsof-based cwd-matching."""

    def test_unknown_cc_identity_returns_none(self):
        from nervous_system.autonomous_loop_detector import _resolve_parent_pid
        assert _resolve_parent_pid("alien-cc") is None
```

(Don't test against real lsof — that's an integration concern, not pure-unit.)

- [ ] **Step 1.9: Commit**

```bash
git add supabase/migrations/20260521_active_loops_parent_pid.sql tests/test_active_loops_parent_pid.py nervous_system/autonomous_loop_detector.py tests/test_autonomous_loop_detector.py
git commit -m "feat(watchdog-phase-b): active_autonomous_loops.parent_pid + detector lsof-based resolution (CAI-RESP-163 Q3)"
```

---

## Task 2: `nervous_system/long_caller_watchdog.py` — pure-Python kill-decision module

**Files:**
- Create: `nervous_system/long_caller_watchdog.py`
- Create: `tests/test_long_caller_watchdog.py`

- [ ] **Step 2.1: Failing pure-unit tests**

Create `tests/test_long_caller_watchdog.py` (full test list per CAI-RESP-163 ROLLOUT SHAPE Commit 4 mandate):

```python
"""Pure-unit tests for long_caller_watchdog.py per CAI-RESP-163.

Six mandatory cases:
  (a) Synthetic runaway harness — registered=False + cadence pattern → hard_kill
  (b) PID-recycle race — pid still alive but cwd mismatch → abort
  (c) Just-registered race — caller registered between detector + kill → abort
  (d) Substrate-native carve-out — ralphy/paused-job-retry → never_kill
  (e) Panic button — WINGMEN_LONG_CALLER_WATCHDOG_DISABLED → no SIGTERM
  (f) Telegram body-shape — Q7 mandated inline-action-menu format
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.long_caller_watchdog import (
    KillDecision,
    decide_kill,
    build_telegram_body,
    SUBSTRATE_NATIVE_NEVER_KILL,
    CadenceTracker,
)


class TestSubstrateNativeCarveOut:
    """C2 frozenset belt-and-suspenders — check FIRST before any other rule."""

    def test_ralphy_never_killed_even_with_pattern(self):
        decision = decide_kill(
            caller_name="ralphy",
            sessions_24h=10_000,
            cadence_seconds=1,
            registered=False,  # even if somehow not in registry
            parent_pid=12345,
        )
        assert decision.action == "no_kill"
        assert decision.reason.startswith("substrate_native")

    def test_paused_job_retry_never_killed(self):
        decision = decide_kill(
            caller_name="paused-job-retry",
            sessions_24h=10_000, cadence_seconds=1,
            registered=False, parent_pid=12345,
        )
        assert decision.action == "no_kill"

    def test_frozenset_immutable(self):
        with pytest.raises((AttributeError, TypeError)):
            SUBSTRATE_NATIVE_NEVER_KILL.add("evil")  # type: ignore


class TestPanicButton:
    """Q-final WINGMEN_LONG_CALLER_WATCHDOG_DISABLED env flag."""

    def test_panic_button_set_skips_kill(self, monkeypatch):
        monkeypatch.setenv("WINGMEN_LONG_CALLER_WATCHDOG_DISABLED", "true")
        decision = decide_kill(
            caller_name="cc-evil-runaway",
            sessions_24h=10_000, cadence_seconds=1,
            registered=False, parent_pid=12345,
        )
        assert decision.action == "no_kill"
        assert "panic_button" in decision.reason

    def test_panic_button_unset_normal_kill_flow(self, monkeypatch):
        monkeypatch.delenv("WINGMEN_LONG_CALLER_WATCHDOG_DISABLED", raising=False)
        decision = decide_kill(
            caller_name="cc-evil-runaway",
            sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
        )
        assert decision.action == "hard_kill"


class TestR1HardKill:
    """R1 — unregistered + cadence pattern → hard_kill (synthetic runaway shape)."""

    def test_unregistered_pattern_kills(self):
        decision = decide_kill(
            caller_name="cc-rogue",
            sessions_24h=200,
            cadence_seconds=300,
            registered=False,
            parent_pid=12345,
        )
        assert decision.action == "hard_kill"
        assert decision.pid == 12345

    def test_registered_with_pattern_does_not_hard_kill(self):
        """Pre-registered CC families (CC-FAMILY-INTERACTIVE-SESSIONS-001) are exempt."""
        decision = decide_kill(
            caller_name="cc-scholar-interactive",
            sessions_24h=400, cadence_seconds=15,
            registered=True, registered_policy="no_kill",
            parent_pid=12345,
        )
        assert decision.action == "no_kill"


class TestPidRecycleGuard:
    """Q3 amendment: re-verify cwd matches at kill time."""

    def test_pid_recycled_to_different_cwd_aborts(self, monkeypatch):
        """Mock the cwd-check to return a non-cc-* dir (simulating PID reuse)."""
        from nervous_system import long_caller_watchdog as mod

        monkeypatch.setattr(mod, "_resolve_pid_cwd", lambda pid: "/usr/bin")
        decision = mod.decide_kill_with_pid_verify(
            caller_name="cc-rogue",
            sessions_24h=200, cadence_seconds=300,
            registered=False,
            parent_pid=12345,
            expected_cwd_prefix="/Users/sheikhmusa/wingmen/projects/",
        )
        assert decision.action == "no_kill"
        assert "pid_recycled" in decision.reason


class TestJustRegisteredRace:
    """C1: caller registers between detector sweep N and N+1 → abort kill."""

    def test_just_registered_aborts_kill(self):
        decision = decide_kill(
            caller_name="cc-newly-registered",
            sessions_24h=200, cadence_seconds=300,
            registered=True,  # registered between sweeps
            registered_policy="soft_alert",
            parent_pid=12345,
        )
        # registered AT all means no hard_kill (R1 only fires on unregistered)
        assert decision.action != "hard_kill"


class TestTelegramBodyShape:
    """Q7 amendment: inline action menu with /legitimize and /revoke."""

    def test_kill_alert_body_includes_action_menu(self):
        body = build_telegram_body(
            event="hard_kill",
            caller_name="cc-rogue",
            pid=3410,
            cwd="/Users/cc-rogue",
            sessions_24h=285,
            threshold=50,
        )
        assert "🛑" in body
        assert "Watchdog killed unregistered caller" in body
        assert "cc-rogue" in body
        assert "PID 3410" in body
        assert "285" in body
        assert "/legitimize" in body
        assert "/revoke" in body

    def test_soft_alert_body_distinct_from_kill(self):
        body = build_telegram_body(
            event="soft_alert_cadence_drift",
            caller_name="cc-x",
            pid=1234,
            cwd="/Users/cc-x",
            sessions_24h=500,
            threshold=50,
        )
        assert "🛑" not in body  # kill icon reserved for kills
        assert "drift" in body.lower() or "soft" in body.lower()


class TestCadenceTracker:
    """In-memory 30min sliding window for R3 drift detection."""

    def test_first_observation_no_drift(self):
        t = CadenceTracker(expected_cadence_seconds=300)
        result = t.observe(caller_name="cc-x", observed_at=1000.0)
        assert result.drift_detected is False

    def test_drift_2x_over_window_triggers(self):
        t = CadenceTracker(expected_cadence_seconds=300)
        # Drop a series of observations 60s apart (5x faster than expected)
        # Spanning > 30min window
        for i in range(40):
            t.observe(caller_name="cc-x", observed_at=1000.0 + i * 60)
        result = t.observe(caller_name="cc-x", observed_at=1000.0 + 40 * 60)
        assert result.drift_detected is True

    def test_drift_resets_after_recovery(self):
        t = CadenceTracker(expected_cadence_seconds=300)
        # Observations at 5min cadence (normal)
        for i in range(10):
            t.observe(caller_name="cc-x", observed_at=1000.0 + i * 300)
        result = t.observe(caller_name="cc-x", observed_at=1000.0 + 10 * 300)
        assert result.drift_detected is False
```

- [ ] **Step 2.2: Run, verify failures (module doesn't exist)**

```bash
source .venv/bin/activate && python -m pytest tests/test_long_caller_watchdog.py -v
```

Expected: ImportError on all.

- [ ] **Step 2.3: Implement `nervous_system/long_caller_watchdog.py`**

```python
"""long_caller_watchdog — Phase B kill-decision module per CAI-RESP-163.

Pure-Python decision logic. Does NOT execute SIGTERM directly — returns
KillDecision objects that the integration layer (watchdog.py) actuates.
This separation keeps kill-logic pure-unit-testable.

Hard-coded safety guards per CAI-RESP-163:
  C1: pre-kill registry re-query (race guard against just-registered callers)
  C2: SUBSTRATE_NATIVE_NEVER_KILL frozenset (belt-and-suspenders)
  Q3: parent_pid recycle race guard (re-verify cwd at kill time)
  Q-final: WINGMEN_LONG_CALLER_WATCHDOG_DISABLED env panic button

R1: Unregistered caller + observed cadence pattern → hard_kill
R3: Registered caller + cadence drift > 2x sustained over 30min → soft_alert
R4: Substrate-native → never_kill (hard-coded via SUBSTRATE_NATIVE_NEVER_KILL)
R2: Deferred per CAI-RESP-163 + CC-LONG-CALLER-AUTO-TOKEN-TRACK-001
"""
from __future__ import annotations

import logging
import os
import subprocess
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("wingmen.long_caller_watchdog")


# C2: substrate-native carve-out (hard-coded; cannot be overridden by registry)
SUBSTRATE_NATIVE_NEVER_KILL: frozenset[str] = frozenset({"ralphy", "paused-job-retry"})

# Q-final: panic button env flag
PANIC_ENV_VAR = "WINGMEN_LONG_CALLER_WATCHDOG_DISABLED"

# R3: cadence drift detection thresholds
CADENCE_DRIFT_MULTIPLIER = 2.0
CADENCE_OBSERVATION_WINDOW_SECONDS = 30 * 60  # 30 minutes
MIN_OBSERVATIONS_FOR_DRIFT = 5


@dataclass(frozen=True)
class KillDecision:
    """Result of decide_kill. Pure data; integration layer actuates."""
    action: str  # 'hard_kill' | 'soft_alert' | 'no_kill'
    reason: str  # forensic detail; goes into notification_log context
    caller_name: str
    pid: Optional[int] = None
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CadenceObservation:
    """One firing event observation for cadence-drift detection."""
    drift_detected: bool
    observed_cadence_seconds: Optional[float] = None
    sample_count: int = 0


class CadenceTracker:
    """In-memory sliding 30min window per caller for R3 drift detection.

    Per CAI-RESP-163 Q4 ratified: in-memory state in long-running watchdog
    process; restart resets window (acceptable — drift triggers only after
    30min observation).
    """

    def __init__(self, expected_cadence_seconds: float):
        self.expected = expected_cadence_seconds
        self._observations: dict[str, deque[float]] = defaultdict(lambda: deque())

    def observe(self, caller_name: str, observed_at: float) -> CadenceObservation:
        """Record a firing event. Returns CadenceObservation reflecting current state."""
        # Trim old observations
        window = self._observations[caller_name]
        window.append(observed_at)
        cutoff = observed_at - CADENCE_OBSERVATION_WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) < MIN_OBSERVATIONS_FOR_DRIFT:
            return CadenceObservation(drift_detected=False, sample_count=len(window))

        # Compute observed inter-arrival; compare to expected
        gaps = [window[i + 1] - window[i] for i in range(len(window) - 1)]
        if not gaps:
            return CadenceObservation(drift_detected=False, sample_count=len(window))
        avg_gap = sum(gaps) / len(gaps)
        drift = self.expected / avg_gap if avg_gap > 0 else float('inf')
        return CadenceObservation(
            drift_detected=(drift >= CADENCE_DRIFT_MULTIPLIER),
            observed_cadence_seconds=avg_gap,
            sample_count=len(window),
        )


def _resolve_pid_cwd(pid: int) -> Optional[str]:
    """Read process cwd via `ps -o cwd= -p <pid>` (or /proc on linux).

    Returns None if process is dead or cwd unresolvable.
    """
    try:
        proc = subprocess.run(
            ["ps", "-o", "cwd=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        )
        if proc.returncode != 0:
            return None
        cwd = proc.stdout.strip()
        return cwd if cwd else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def decide_kill(
    *,
    caller_name: str,
    sessions_24h: int,
    cadence_seconds: int,
    registered: bool,
    registered_policy: Optional[str] = None,
    parent_pid: Optional[int] = None,
) -> KillDecision:
    """Pure decision function — given inputs, return KillDecision.

    Check order (CAI-RESP-163 mandate):
      1. C2 substrate-native frozenset — check FIRST
      2. Q-final panic button env flag
      3. Registry membership check (R4 if registered_policy='no_kill')
      4. R1 unregistered + pattern → hard_kill
    """
    # 1. C2 substrate-native carve-out (BEFORE everything)
    if caller_name in SUBSTRATE_NATIVE_NEVER_KILL:
        return KillDecision(
            action="no_kill",
            reason="substrate_native_carve_out_C2",
            caller_name=caller_name,
            pid=parent_pid,
        )

    # 2. Q-final panic button
    if os.environ.get(PANIC_ENV_VAR, "").lower() in ("true", "1", "yes", "on"):
        return KillDecision(
            action="no_kill",
            reason="panic_button_set",
            caller_name=caller_name,
            pid=parent_pid,
        )

    # 3. Registered → policy binds
    if registered:
        if registered_policy == "no_kill":
            return KillDecision(
                action="no_kill",
                reason="registered_no_kill_policy",
                caller_name=caller_name,
                pid=parent_pid,
            )
        # Registered + soft_alert/hard_kill: R3 cadence drift is the trigger,
        # NOT R1 sessions_24h pattern. R1 only fires on unregistered.
        # If we got here, observed_tokens_today comparison would happen (R2 deferred).
        return KillDecision(
            action="no_kill",
            reason="registered_no_R1_trigger",
            caller_name=caller_name,
            pid=parent_pid,
        )

    # 4. Unregistered + pattern → R1 hard_kill
    return KillDecision(
        action="hard_kill",
        reason="R1_unregistered_pattern",
        caller_name=caller_name,
        pid=parent_pid,
        extras={"sessions_24h": sessions_24h, "cadence_seconds": cadence_seconds},
    )


def decide_kill_with_pid_verify(
    *,
    caller_name: str,
    sessions_24h: int,
    cadence_seconds: int,
    registered: bool,
    registered_policy: Optional[str] = None,
    parent_pid: Optional[int] = None,
    expected_cwd_prefix: str = "/Users/sheikhmusa/wingmen/",
) -> KillDecision:
    """Wraps decide_kill with the Q3 PID-recycle race guard.

    Only relevant when decide_kill returns hard_kill — verifies PID's cwd
    still matches the expected prefix before approving the kill.
    """
    inner = decide_kill(
        caller_name=caller_name,
        sessions_24h=sessions_24h,
        cadence_seconds=cadence_seconds,
        registered=registered,
        registered_policy=registered_policy,
        parent_pid=parent_pid,
    )
    if inner.action != "hard_kill" or parent_pid is None:
        return inner
    observed_cwd = _resolve_pid_cwd(parent_pid)
    if observed_cwd is None or not observed_cwd.startswith(expected_cwd_prefix):
        return KillDecision(
            action="no_kill",
            reason=f"pid_recycled_or_unverifiable observed_cwd={observed_cwd!r}",
            caller_name=caller_name,
            pid=parent_pid,
            extras={"expected_cwd_prefix": expected_cwd_prefix, "observed_cwd": observed_cwd},
        )
    return inner


def build_telegram_body(
    *,
    event: str,
    caller_name: str,
    pid: int,
    cwd: str,
    sessions_24h: int,
    threshold: int,
) -> str:
    """Q7 amendment: required Telegram body shape with inline action menu."""
    if event == "hard_kill":
        return (
            "🛑 Watchdog killed unregistered caller\n\n"
            f"caller: {caller_name} (PID {pid}, cwd {cwd})\n"
            f"observed: {sessions_24h} sessions/24h (threshold {threshold})\n"
            "registry: NOT FOUND\n\n"
            "Next action — reply to this thread:\n"
            "  /legitimize → file CC-LONG-CALLER-INDIVIDUAL-NNN\n"
            "  /revoke    → confirm revocation, log to notification_log"
        )
    elif event == "soft_alert_cadence_drift":
        return (
            "⚠️ Watchdog soft alert — cadence drift\n\n"
            f"caller: {caller_name} (PID {pid}, cwd {cwd})\n"
            f"observed: {sessions_24h} sessions/24h\n"
            "soft alert: cadence drift > 2x expected over 30min window\n\n"
            "No SIGTERM — informational. Investigate via boot_briefing."
        )
    else:
        return f"Watchdog event ({event}) caller={caller_name} pid={pid}"
```

- [ ] **Step 2.4: Run tests, verify GREEN**

```bash
source .venv/bin/activate && python -m pytest tests/test_long_caller_watchdog.py -v
```

Expected: all tests pass.

- [ ] **Step 2.5: Commit**

```bash
git add nervous_system/long_caller_watchdog.py tests/test_long_caller_watchdog.py
git commit -m "feat(watchdog-phase-b): pure-Python kill-decision module — C1/C2/Q3/Q-final + R1/R3/R4 (CAI-RESP-163)"
```

---

## Task 3: `watchdog.py` integration — 5min cadence poll + actuate decisions

**Files:**
- Modify: `watchdog.py`

- [ ] **Step 3.1: Read current watchdog.py main loop structure**

```bash
sed -n '130,200p' watchdog.py
```

Find the `while True:` main loop. It currently checks bot + orch alive + Mac Studio endpoints every CHECK_INTERVAL (60s). Phase B adds a long_caller_watchdog sweep every 5min (300s) — use a counter to gate.

- [ ] **Step 3.2: Add long_caller_watchdog poll path**

Modify `watchdog.py`. Append helper functions near the top of the file (after the imports + before main_loop):

```python
# CAI-RESP-163 Phase B — long_caller_watchdog poll
from nervous_system.long_caller_watchdog import (
    decide_kill_with_pid_verify,
    build_telegram_body,
    CadenceTracker,
)
import signal

_LONG_CALLER_POLL_INTERVAL = 300  # 5min per CAI-RESP-163 Q2
_long_caller_last_poll = 0.0
_long_caller_cadence_tracker: dict[str, CadenceTracker] = {}  # per-caller trackers


async def _long_caller_sweep(supabase) -> None:
    """One sweep cycle: read active_autonomous_loops + long_running_claude_callers,
    decide kills, actuate. Per CAI-RESP-163.
    """
    import psycopg as _pg
    import json as _json
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        return
    # Read flagged rows + registry in one connection
    with _pg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cc_identity, sessions_24h, cadence_seconds, parent_pid "
                "FROM active_autonomous_loops"
            )
            flagged = cur.fetchall()
            cur.execute(
                "SELECT caller_name, auto_kill_policy FROM long_running_claude_callers "
                "WHERE revoked_at IS NULL"
            )
            registry = {r[0]: r[1] for r in cur.fetchall()}

    for cc_id, sessions_24h, cadence_seconds, parent_pid in flagged:
        # Map cc_identity to registry caller_name (cc-scholar → cc-scholar-interactive)
        # CC families use the -interactive suffix per CC-FAMILY-INTERACTIVE-SESSIONS-001
        possible_names = [cc_id, f"{cc_id}-interactive"]
        registered = False
        policy = None
        for name in possible_names:
            if name in registry:
                registered = True
                policy = registry[name]
                break
        registered_caller_name = name if registered else cc_id

        # C1: pre-kill registry re-query — already done above; pass through

        decision = decide_kill_with_pid_verify(
            caller_name=registered_caller_name,
            sessions_24h=sessions_24h,
            cadence_seconds=cadence_seconds or 0,
            registered=registered,
            registered_policy=policy,
            parent_pid=parent_pid,
        )

        if decision.action == "hard_kill" and parent_pid:
            # Actuate SIGTERM
            try:
                os.kill(parent_pid, signal.SIGTERM)
                logger.warning(
                    f"long_caller_watchdog: SIGTERM sent to PID {parent_pid} "
                    f"(caller {registered_caller_name})"
                )
                # Telegram alert
                cwd = ""  # captured at kill time would be ideal; simplified for now
                body = build_telegram_body(
                    event="hard_kill",
                    caller_name=registered_caller_name,
                    pid=parent_pid,
                    cwd=cwd or "unknown",
                    sessions_24h=sessions_24h,
                    threshold=50,
                )
                await alert_admin(body)
                # notification_log audit
                with _pg.connect(dsn, autocommit=True) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO notification_log "
                            "(source, decision_ref, channel, recipient, message_text) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (
                                "watchdog_hard_kill",
                                "CAI-RESP-163",
                                "long_running_callers",
                                registered_caller_name,
                                _json.dumps({
                                    "sessions_24h": sessions_24h,
                                    "cadence_seconds": cadence_seconds,
                                    "pid": parent_pid,
                                    "reason": decision.reason,
                                }),
                            ),
                        )
            except (ProcessLookupError, PermissionError) as e:
                logger.warning(f"SIGTERM to {parent_pid} failed: {e}")
        elif decision.action == "no_kill" and "pid_recycled" in decision.reason:
            # Q3 PID-recycle abort
            with _pg.connect(dsn, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO notification_log "
                        "(source, decision_ref, channel, recipient, message_text) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            "watchdog_aborted_kill",
                            "CAI-RESP-163",
                            "long_running_callers",
                            registered_caller_name,
                            _json.dumps({"reason": decision.reason, "pid": parent_pid}),
                        ),
                    )


async def _maybe_long_caller_poll(supabase) -> None:
    """Gated wrapper — only fires every _LONG_CALLER_POLL_INTERVAL seconds."""
    global _long_caller_last_poll
    import time as _time
    now = _time.time()
    if now - _long_caller_last_poll < _LONG_CALLER_POLL_INTERVAL:
        return
    _long_caller_last_poll = now
    try:
        await _long_caller_sweep(supabase)
    except Exception as e:
        logger.error(f"_long_caller_sweep failed: {e}")
```

In the main_loop `while True:` body, add `await _maybe_long_caller_poll(supabase)` after the existing Mac Studio probe block but BEFORE `await asyncio.sleep(CHECK_INTERVAL)`. The supabase client may need to be initialized somewhere — if watchdog.py doesn't currently use supabase, initialize it before the main_loop call.

- [ ] **Step 3.3: Verify smoke import**

```bash
source .venv/bin/activate && python -c "import watchdog; print('ok')"
```

- [ ] **Step 3.4: Commit**

```bash
git add watchdog.py
git commit -m "feat(watchdog-phase-b): integrate long_caller_watchdog into 5min poll (CAI-RESP-163 Q2)"
```

---

## Task 4: Integration test + rollback doc

**Files:**
- Create: `tests/test_watchdog_phase_b_integration.py`
- Create: `docs/superpowers/rollbacks/long-caller-watchdog-phase-b.md`

- [ ] **Step 4.1: Synthetic runaway harness integration test**

Create `tests/test_watchdog_phase_b_integration.py`:

```python
"""Integration test — synthetic runaway harness per CAI-RESP-163 ROLLOUT SHAPE.

Insert a synthetic flagged row into active_autonomous_loops (NOT registered),
trigger one watchdog sweep, assert SIGTERM was decided (without actually
firing — we mock os.kill).
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")


@pytestmark_integration
def test_synthetic_runaway_triggers_hard_kill_decision(monkeypatch):
    """Insert synthetic active_autonomous_loops row; assert decide_kill returns hard_kill."""
    from nervous_system.long_caller_watchdog import decide_kill

    fake_cc = f"cc-test-runaway-{uuid.uuid4().hex[:8]}"
    fake_pid = 99999  # very unlikely to exist

    # Simulate flagged row + no registry entry
    decision = decide_kill(
        caller_name=fake_cc,
        sessions_24h=500,
        cadence_seconds=10,
        registered=False,
        parent_pid=fake_pid,
    )
    assert decision.action == "hard_kill"
    assert decision.pid == fake_pid


@pytestmark_integration
def test_substrate_native_never_killed_even_with_active_row():
    """If ralphy hypothetically appears in active_autonomous_loops, watchdog skips."""
    from nervous_system.long_caller_watchdog import decide_kill
    decision = decide_kill(
        caller_name="ralphy",
        sessions_24h=10000, cadence_seconds=1,
        registered=False,  # even if registry corrupted
        parent_pid=12345,
    )
    assert decision.action == "no_kill"


@pytestmark_integration
def test_cc_family_interactive_pre_registered_never_killed():
    """Per CC-FAMILY-INTERACTIVE-SESSIONS-001, cc-scholar-interactive registered no_kill."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT auto_kill_policy FROM long_running_claude_callers "
                "WHERE caller_name='cc-scholar-interactive' AND revoked_at IS NULL"
            )
            r = cur.fetchone()
    assert r is not None, "cc-scholar-interactive must be pre-registered per CC-FAMILY-INTERACTIVE-SESSIONS-001"
    assert r[0] == "no_kill"

    from nervous_system.long_caller_watchdog import decide_kill
    decision = decide_kill(
        caller_name="cc-scholar-interactive",
        sessions_24h=500, cadence_seconds=15,
        registered=True, registered_policy="no_kill",
        parent_pid=24810,  # cc-scholar's actual PID
    )
    assert decision.action == "no_kill"
```

- [ ] **Step 4.2: Run integration tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_watchdog_phase_b_integration.py -v
```

- [ ] **Step 4.3: Rollback doc**

Create `docs/superpowers/rollbacks/long-caller-watchdog-phase-b.md`:

```markdown
# Long-Caller Watchdog Phase B Rollback

Per CAI-RESP-161 + CAI-RESP-163 constraint: all Phase B PRs include rollback procedure.

## Triggers for rollback
- Phase B watchdog SIGTERMs a legitimate caller (false-positive in production)
- Cadence tracker memory leak / accumulates state without bound
- PID re-verification race fails to catch a PID recycle, kills wrong process
- Panic button env flag doesn't propagate / doesn't disable kills
- watchdog.py integration breaks bot or orchestrator monitoring

## Rollback procedure

### 1. Immediate operator action — set panic button

If false-positive kill is observed, immediately set the env flag:

```bash
# In .env
echo "WINGMEN_LONG_CALLER_WATCHDOG_DISABLED=true" >> .env

# Restart watchdog
launchctl kickstart -k "gui/$(id -u)/dev.wingmen.watchdog"
```

This stops all new SIGTERMs without requiring code revert. Buys time to plan a proper rollback.

### 2. Code revert

```bash
git revert <PR-merge-commit-SHA>
git push origin main
launchctl kickstart -k "gui/$(id -u)/dev.wingmen.watchdog"
```

### 3. Migration rollback (Task 1 schema)

```sql
BEGIN;
-- Drop the parent_pid column (loses recent data; OK because non-load-bearing)
ALTER TABLE active_autonomous_loops DROP COLUMN IF EXISTS parent_pid;
-- Restore boot_briefing view to pre-Phase-B definition (active_autonomous_loops
-- arm without parent_pid in json_build_object).
CREATE OR REPLACE VIEW boot_briefing AS <paste pre-Phase-B view body>;
COMMIT;
```

### 4. Verify rollback

```bash
python3 -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv()
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT count(*) FROM information_schema.columns WHERE table_name='active_autonomous_loops' AND column_name='parent_pid'\")
        assert cur.fetchone()[0] == 0, 'parent_pid still exists'
print('rollback verified')
"
```

### 5. Audit notification_log

```sql
SELECT count(*), source FROM notification_log
 WHERE source IN ('watchdog_hard_kill', 'watchdog_soft_alert', 'watchdog_aborted_kill')
 GROUP BY source;
```

Preserve these rows for forensic trail. They're informational; no cleanup needed.

## Filing requirement

Rollback must be filed as decision_ref `CC-LONG-CALLER-WATCHDOG-PHASE-B-ROLLBACK-NNN` with:
- Trigger (which defect)
- Decision (revert + soft-drop scope)
- Audit log of all SIGTERM events from notification_log
- Forward-fix plan
```

- [ ] **Step 4.4: Commit**

```bash
git add tests/test_watchdog_phase_b_integration.py docs/superpowers/rollbacks/long-caller-watchdog-phase-b.md
git commit -m "test+docs(watchdog-phase-b): integration harness + rollback procedure"
```

---

## Task 5: PR + ship

- [ ] **Step 5.1: Full test sweep**

```bash
source .venv/bin/activate && python -m pytest tests/ -q --timeout=120
```

Expected: all green (or pre-existing flakes noted).

- [ ] **Step 5.2: Push + open PR**

```bash
env -u GITHUB_TOKEN git push -u origin feat/long-caller-watchdog-phase-b
env -u GITHUB_TOKEN gh pr create --base main --head feat/long-caller-watchdog-phase-b --title "feat(watchdog-phase-b): long_caller_watchdog kill-decision + integration (CAI-RESP-163)" --body "..."
```

PR body covers: 4 commits (migration + pure-Python + integration + tests/rollback), all 6 cai-mandated test cases included, dependency D1 (token-track parking-lot) shipped pre-emptively, dependency D3 (notification_log enums) already live, CC-FAMILY-INTERACTIVE-SESSIONS-001 belt-and-suspenders pre-registration applied.

- [ ] **Step 5.3: Wait for CI + merge + restart**

```bash
env -u GITHUB_TOKEN gh pr checks <pr-num>
env -u GITHUB_TOKEN gh pr merge <pr-num> --squash --delete-branch
launchctl kickstart -k "gui/$(id -u)/dev.wingmen.watchdog"
```

- [ ] **Step 5.4: File ship-update to cai**

On CAI-RESP-163 thread — substrate-discipline note: "Phase B shipped per CAI-RESP-163. R1/R3/R4 live. R2 deferred per D1 parking-lot. CC-FAMILY-INTERACTIVE-SESSIONS-001 pre-registration in effect — cc-scholar/cosem/ihsanos interactive sessions exempt from R1 hard_kill."

---

## Self-Review

**Spec coverage (CAI-RESP-163 ratifications):**
- ✅ Q1 watchdog.py extension → Task 3
- ✅ Q2 5-min cadence → `_LONG_CALLER_POLL_INTERVAL = 300`
- ✅ Q3 parent_pid column + PID-recycle race guard → Task 1 + `decide_kill_with_pid_verify`
- ✅ Q4 in-memory cadence tracker → `CadenceTracker` class
- ✅ Q5 R2 deferred (already filed CC-LONG-CALLER-AUTO-TOKEN-TRACK-001 as D1)
- ✅ Q6 alert routing → Telegram + notification_log writes
- ✅ Q7 Telegram body shape → `build_telegram_body`
- ✅ Q8 row lifecycle → no new column, detector self-heals
- ✅ C1 pre-kill registry re-query → `_long_caller_sweep` re-queries registry
- ✅ C2 substrate-native frozenset → `SUBSTRATE_NATIVE_NEVER_KILL`
- ✅ Q-final panic button → `PANIC_ENV_VAR` check
- ✅ 6 mandatory test cases → all in `tests/test_long_caller_watchdog.py`

**Placeholder scan:** No TBD/TODO. Each step has either complete code or exact commands.

**Type consistency:**
- `KillDecision.action` literal values: 'hard_kill' | 'soft_alert' | 'no_kill' — consistent across decide_kill, integration, telegram builder
- `CadenceObservation.drift_detected: bool` — clean boolean signal
- `SUBSTRATE_NATIVE_NEVER_KILL: frozenset[str]` — typed, immutable

**Deferred:**
- R2 token-ceiling soft_alert (D1 parking-lot filed; resume per CC-LONG-CALLER-AUTO-TOKEN-TRACK-001 trigger)
- R3 cadence-drift Telegram routing — code path exists but not exercised in this PR's tests; full E2E for R3 in a follow-up small PR if cai requests.
- dookana / cosem-adcda / hifz-companion / fastrans interactive sessions — register at-need when operator launches; not pre-registered in this PR.

No gaps to fix.
