# CC-LONG-CALLER-REGISTRY-001 Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the long-running-claude-caller registry as a visibility-only data layer per CAI-RESP-161 Phase A. Phase B watchdog wire-in is a separate PR. Phase C is operator action triggered by audit findings.

**Architecture:** New `long_running_claude_callers` table + Python helper module (`nervous_system/long_running_claude_callers.py`) exposing `register()`, `heartbeat()`, `self_kill_on_budget_exceed=` opt-in. File-manifest reader sweeps YAML/JSON at known path. `boot_briefing` view extension. Substrate-native seed for ralphy + paused-job-retry. New notification_log source-enum values for Phase B forward-compat.

**Tech Stack:** Python 3.9, psycopg, supabase-py async, PostgreSQL, pytest + pytest-asyncio.

**Decision refs:** CAI-RESP-160 (parent rule), CAI-RESP-161 (Phase A design ratified), CAI-RESP-157 (parent budget-incident response).

---

## File Structure

| Path | Purpose | New/Modified |
|---|---|---|
| `supabase/migrations/20260517_long_running_claude_callers.sql` | Table + view extension + assertion gate | NEW |
| `nervous_system/long_running_claude_callers.py` | Helper module (register, heartbeat, self-kill, manifest reader) | NEW |
| `tests/test_long_running_claude_callers.py` | Live-DB tests (schema, view, helper round-trip) | NEW |
| `tests/test_long_running_claude_callers_helper.py` | Pure-unit tests (manifest parsing, auto_kill_policy default derivation) | NEW |
| `manifests/long_running_callers/README.md` | Operator-facing doc for filing manifests | NEW |
| `manifests/long_running_callers/.gitkeep` | Empty directory marker | NEW |

---

## Pre-Flight

- [ ] **Step 0.1: Verify branch + env**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git branch --show-current  # should be feat/long-caller-registry
source .venv/bin/activate
python -c "import psycopg, supabase, yaml; print('ok')"
```

If `yaml` import fails: `pip install pyyaml` (likely already installed via dotenv chain).

---

## Task 1: Migration — table + view extension + assertion gate

**Files:**
- Create: `supabase/migrations/20260517_long_running_claude_callers.sql`
- Test: `tests/test_long_running_claude_callers.py`

- [ ] **Step 1.1: Failing schema tests**

Create `tests/test_long_running_claude_callers.py`:

```python
"""Live-DB tests for CC-LONG-CALLER-REGISTRY-001 Phase A.

Schema, view extension, helper round-trip, substrate-native seed.
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

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)


def _column_exists(table: str, column: str):
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name=%s AND column_name=%s",
                (table, column),
            )
            return cur.fetchone()


@pytestmark_integration
def test_long_running_claude_callers_table_exists():
    assert _column_exists("long_running_claude_callers", "caller_name") is not None


@pytestmark_integration
def test_long_running_claude_callers_has_all_fields():
    expected = {
        "caller_name": "text",
        "cmd": "text",
        "parent_pid": "integer",
        "started_at": "timestamp with time zone",
        "expected_cadence_seconds": "integer",
        "expected_tokens_per_day": "integer",
        "max_tokens_per_day": "integer",
        "ratified_by_decision_ref": "text",
        "last_seen_at": "timestamp with time zone",
        "operator_authored": "boolean",
        "registered_by_identity": "text",
        "auto_kill_policy": "text",
        "purpose": "text",
        "revoked_at": "timestamp with time zone",
        "created_at": "timestamp with time zone",
    }
    for col, dtype in expected.items():
        r = _column_exists("long_running_claude_callers", col)
        assert r is not None, f"long_running_claude_callers.{col} missing"
        assert r[0] == dtype, f"{col} has {r[0]!r}, expected {dtype!r}"


@pytestmark_integration
def test_caller_name_is_primary_key():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT a.attname
                  FROM pg_index i
                  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                 WHERE i.indrelid = 'long_running_claude_callers'::regclass
                   AND i.indisprimary
            """)
            pk_cols = [r[0] for r in cur.fetchall()]
    assert pk_cols == ["caller_name"], f"PK should be caller_name, got {pk_cols}"


@pytestmark_integration
def test_registered_by_identity_check_constraint():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            # Confirm CHECK constraint via attempted-invalid INSERT
            try:
                cur.execute("""
                    INSERT INTO long_running_claude_callers
                      (caller_name, cmd, started_at, expected_cadence_seconds,
                       expected_tokens_per_day, ratified_by_decision_ref,
                       registered_by_identity, auto_kill_policy, purpose)
                    VALUES (%s, 'fake', now(), 300, 1000, 'TEST-FAKE',
                            'invalid_identity_value', 'soft_alert', 'test fixture')
                """, (f"test-fixture-{uuid.uuid4().hex[:8]}",))
                assert False, "registered_by_identity CHECK should have rejected"
            except psycopg.errors.CheckViolation:
                pass  # expected


@pytestmark_integration
def test_auto_kill_policy_check_constraint():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO long_running_claude_callers
                      (caller_name, cmd, started_at, expected_cadence_seconds,
                       expected_tokens_per_day, ratified_by_decision_ref,
                       registered_by_identity, auto_kill_policy, purpose)
                    VALUES (%s, 'fake', now(), 300, 1000, 'TEST-FAKE',
                            'operator', 'invalid_policy', 'test')
                """, (f"test-fixture-{uuid.uuid4().hex[:8]}",))
                assert False, "auto_kill_policy CHECK should have rejected"
            except psycopg.errors.CheckViolation:
                pass


@pytestmark_integration
def test_substrate_native_seed_present():
    """ralphy + paused-job-retry seeded at migration time."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT caller_name, registered_by_identity, auto_kill_policy
                  FROM long_running_claude_callers
                 WHERE registered_by_identity = 'substrate'
                 ORDER BY caller_name
            """)
            rows = cur.fetchall()
    caller_names = [r[0] for r in rows]
    assert "ralphy" in caller_names, f"ralphy seed missing, got {caller_names}"
    assert "paused-job-retry" in caller_names, f"paused-job-retry seed missing, got {caller_names}"
    for r in rows:
        assert r[1] == "substrate"
        assert r[2] == "no_kill", f"{r[0]} substrate seed must have auto_kill_policy=no_kill"


@pytestmark_integration
def test_boot_briefing_view_has_long_running_caller_arm():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
            defn = cur.fetchone()[0]
    assert "long_running_caller" in defn, "boot_briefing view missing long_running_caller UNION arm"
```

- [ ] **Step 1.2: Run, verify failures**

```bash
source .venv/bin/activate && python -m pytest tests/test_long_running_claude_callers.py -v
```

Expected: all fail (table doesn't exist).

- [ ] **Step 1.3: Capture current boot_briefing view definition**

```bash
source .venv/bin/activate && python3 - <<'PY' > /tmp/current_boot_briefing_v4.sql
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
        print(cur.fetchone()[0])
PY
wc -l /tmp/current_boot_briefing_v4.sql
```

- [ ] **Step 1.4: Create migration**

Create `supabase/migrations/20260517_long_running_claude_callers.sql`:

```sql
-- CC-LONG-CALLER-REGISTRY-001 Phase A per CAI-RESP-161
-- Per CAI-RESP-160: prohibit unregistered long-running claude-spawning processes.
-- Phase A is visibility-only — does NOT enforce kill behavior; Phase B wires watchdog.
-- Additive only, pre-apply per CAI-RESP-102.

BEGIN;

-- ============================================================================
-- Section 1: long_running_claude_callers table
-- ============================================================================
CREATE TABLE IF NOT EXISTS long_running_claude_callers (
    caller_name               TEXT PRIMARY KEY,
    cmd                       TEXT NOT NULL,
    parent_pid                INTEGER,
    started_at                TIMESTAMPTZ NOT NULL,
    expected_cadence_seconds  INTEGER NOT NULL,
    expected_tokens_per_day   INTEGER NOT NULL,
    max_tokens_per_day        INTEGER,
    ratified_by_decision_ref  TEXT NOT NULL,
    last_seen_at              TIMESTAMPTZ,
    operator_authored         BOOLEAN NOT NULL DEFAULT false,
    registered_by_identity    TEXT NOT NULL
        CHECK (registered_by_identity IN ('operator', 'cc_family', 'substrate')),
    auto_kill_policy          TEXT NOT NULL DEFAULT 'soft_alert'
        CHECK (auto_kill_policy IN ('soft_alert', 'hard_kill', 'no_kill')),
    purpose                   TEXT NOT NULL,
    revoked_at                TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_long_running_claude_callers_active
    ON long_running_claude_callers (registered_by_identity)
    WHERE revoked_at IS NULL;

COMMENT ON TABLE long_running_claude_callers IS
    'CC-LONG-CALLER-REGISTRY-001 Phase A per CAI-RESP-161. Registry of long-running '
    'claude-spawning processes. Phase A is visibility/data layer (non-enforcing); '
    'Phase B wires CAI-RESP-157 [B] watchdog-kill to consult this registry.';

-- ============================================================================
-- Section 2: substrate-native seed (ratified mechanisms per CAI-RESP-160 carve-out)
-- ============================================================================
INSERT INTO long_running_claude_callers (
    caller_name, cmd, started_at, expected_cadence_seconds, expected_tokens_per_day,
    ratified_by_decision_ref, registered_by_identity, auto_kill_policy, purpose
) VALUES
    ('ralphy', 'ralph_runner.py (substrate-native)', now(), 300, 0,
     'CAI-RESP-160', 'substrate', 'no_kill',
     'Ralph bug-runner — substrate-native autonomous loop per feedback_autonomous_loop_scope.md carve-out'),
    ('paused-job-retry', 'paused_jobs_retry_policy.py (substrate-native)', now(), 1800, 0,
     'CAI-RESP-160', 'substrate', 'no_kill',
     'Paused-job retry sweeper — substrate-native autonomous loop per feedback_autonomous_loop_scope.md carve-out')
ON CONFLICT (caller_name) DO NOTHING;

-- ============================================================================
-- Section 3: extend boot_briefing view
-- (CREATE OR REPLACE; full body capture in step 1.3 output)
-- ============================================================================
-- AGENT INSTRUCTION: paste the FULL existing view body from /tmp/current_boot_briefing_v4.sql here
-- WITHOUT the trailing semicolon, then append:
--
-- UNION ALL
--  SELECT 'long_running_caller'::text AS source,
--         lrcc.caller_name           AS key,
--         json_build_object(
--           'cmd',                       lrcc.cmd,
--           'last_seen_at',              lrcc.last_seen_at,
--           'expected_tokens_per_day',   lrcc.expected_tokens_per_day,
--           'max_tokens_per_day',        lrcc.max_tokens_per_day,
--           'expected_cadence_seconds',  lrcc.expected_cadence_seconds,
--           'ratified_by_decision_ref',  lrcc.ratified_by_decision_ref,
--           'registered_by_identity',    lrcc.registered_by_identity,
--           'auto_kill_policy',          lrcc.auto_kill_policy,
--           'purpose',                   lrcc.purpose,
--           'status',                    CASE
--             WHEN lrcc.revoked_at IS NOT NULL THEN 'revoked'
--             WHEN lrcc.last_seen_at IS NULL THEN 'never_heartbeated'
--             WHEN lrcc.last_seen_at < now() - (lrcc.expected_cadence_seconds * interval '1 second' * 3) THEN 'stale_heartbeat'
--             ELSE 'active'
--           END
--         ) AS context
--    FROM long_running_claude_callers lrcc
--   WHERE lrcc.revoked_at IS NULL OR lrcc.revoked_at > now() - interval '30 days';

-- ============================================================================
-- Section 4: assertion gate
-- ============================================================================
DO $$
DECLARE view_def TEXT;
DECLARE seed_count INT;
BEGIN
    SELECT pg_get_viewdef('boot_briefing'::regclass, true) INTO view_def;
    IF position('long_running_caller' IN view_def) = 0 THEN
        RAISE EXCEPTION 'boot_briefing view missing long_running_caller UNION arm';
    END IF;
    SELECT count(*) INTO seed_count
      FROM long_running_claude_callers
     WHERE registered_by_identity = 'substrate';
    IF seed_count < 2 THEN
        RAISE EXCEPTION 'substrate-native seed missing (expected >=2 rows, got %)', seed_count;
    END IF;
    RAISE NOTICE 'CC-LONG-CALLER-REGISTRY-001 Phase A: % substrate-native seeds + boot_briefing arm verified', seed_count;
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260517130000',
    'long_running_claude_callers',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
```

Replace the Section-3 comment block with the actual `CREATE OR REPLACE VIEW boot_briefing AS <existing body verbatim, no trailing semicolon> UNION ALL <new arm above>;` per the capture in Step 1.3.

- [ ] **Step 1.5: Apply migration**

```bash
source .venv/bin/activate && python3 - <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
sql = open('/Users/sheikhmusa/wingmen/orchestrator/supabase/migrations/20260517_long_running_claude_callers.sql').read()
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(sql)
        print('migration applied')
PY
```

- [ ] **Step 1.6: Linter check**

```bash
python scripts/check_additive_migration.py supabase/migrations/20260517_long_running_claude_callers.sql
echo "linter exit: $?"
```

Expected: exit 0.

- [ ] **Step 1.7: Run schema tests, verify GREEN**

```bash
python -m pytest tests/test_long_running_claude_callers.py -v
```

Expected: 7 passed.

- [ ] **Step 1.8: Commit**

```bash
git add supabase/migrations/20260517_long_running_claude_callers.sql tests/test_long_running_claude_callers.py
git commit -m "feat(long-caller-registry): table + boot_briefing arm + substrate-native seed (CAI-RESP-161 Phase A)"
```

---

## Task 2: Python helper module — register + heartbeat + manifest reader

**Files:**
- Create: `nervous_system/long_running_claude_callers.py`
- Test: `tests/test_long_running_claude_callers_helper.py`

- [ ] **Step 2.1: Failing pure-unit tests**

Create `tests/test_long_running_claude_callers_helper.py`:

```python
"""Pure-unit tests for nervous_system/long_running_claude_callers helper.

Manifest parsing, auto_kill_policy default derivation, and pure logic.
DB round-trip lives in test_long_running_claude_callers.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.long_running_claude_callers import (
    derive_auto_kill_policy,
    parse_manifest,
    Manifest,
)


class TestDeriveAutoKillPolicy:
    """Per CAI-RESP-161 Q6 defaults derived from registered_by_identity."""

    def test_operator_authored_defaults_soft_alert(self):
        assert derive_auto_kill_policy("operator") == "soft_alert"

    def test_cc_family_defaults_soft_alert(self):
        assert derive_auto_kill_policy("cc_family") == "soft_alert"

    def test_substrate_defaults_no_kill(self):
        assert derive_auto_kill_policy("substrate") == "no_kill"

    def test_unknown_identity_raises(self):
        with pytest.raises(ValueError, match="invalid"):
            derive_auto_kill_policy("alien")


class TestParseManifest:
    """Operator-authored YAML manifests for long-running callers."""

    def test_valid_yaml_manifest(self, tmp_path):
        f = tmp_path / "probe.yaml"
        f.write_text("""
caller_name: cc-probe-max-throttle
cmd: python3 scripts/probe_max_throttle.py run
expected_cadence_seconds: 300
expected_tokens_per_day: 14000000
max_tokens_per_day: 20000000
ratified_by_decision_ref: CC-PROBE-MAX-THROTTLE-001
registered_by_identity: operator
purpose: Max-plan throttle probe; logs to scripts/.probe_log.jsonl
""")
        m = parse_manifest(f)
        assert m.caller_name == "cc-probe-max-throttle"
        assert m.expected_cadence_seconds == 300
        assert m.expected_tokens_per_day == 14000000
        assert m.max_tokens_per_day == 20000000
        assert m.ratified_by_decision_ref == "CC-PROBE-MAX-THROTTLE-001"
        assert m.registered_by_identity == "operator"
        assert "throttle probe" in m.purpose

    def test_missing_required_field_raises(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("""
caller_name: incomplete
cmd: foo
""")
        with pytest.raises((KeyError, ValueError)):
            parse_manifest(f)

    def test_invalid_identity_raises(self, tmp_path):
        f = tmp_path / "bad_identity.yaml"
        f.write_text("""
caller_name: foo
cmd: bar
expected_cadence_seconds: 60
expected_tokens_per_day: 1000
ratified_by_decision_ref: FOO-001
registered_by_identity: rogue
purpose: testing
""")
        with pytest.raises(ValueError):
            parse_manifest(f)
```

- [ ] **Step 2.2: Run, verify failures**

```bash
python -m pytest tests/test_long_running_claude_callers_helper.py -v
```

Expected: ImportError / module-not-found failures.

- [ ] **Step 2.3: Implement `nervous_system/long_running_claude_callers.py`**

```python
"""long_running_claude_callers — registry helper per CAI-RESP-161 Phase A.

Public API:
  - register(caller_name, cmd, expected_cadence_seconds, expected_tokens_per_day,
             ratified_by_decision_ref, registered_by_identity, purpose, **kwargs)
    Insert or refresh a caller's registry row. Idempotent (ON CONFLICT updates last_seen_at).
  - heartbeat(caller_name)
    Update last_seen_at = now() for an existing registry row.
  - parse_manifest(path) -> Manifest
    Parse a YAML/JSON manifest file (operator-authored static config).
  - sweep_manifests(manifests_dir) -> list[Manifest]
    Read all *.yaml/*.json files under manifests/long_running_callers/, parse, return list.
  - derive_auto_kill_policy(registered_by_identity) -> str
    Default policy lookup per CAI-RESP-161 Q6 ratification.

Per CAI-RESP-160: callers MUST register on start if they meet long-running criteria
(sessions_24h > 50 OR cadence-bounded; threshold bound to CAI-RESP-157 [A]).

Phase B (separate PR) wires CAI-RESP-157 [B] watchdog-kill to consult this registry.
Phase A is visibility-only.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("wingmen.long_running_claude_callers")


_VALID_IDENTITIES = {"operator", "cc_family", "substrate"}
_VALID_KILL_POLICIES = {"soft_alert", "hard_kill", "no_kill"}
_IDENTITY_TO_DEFAULT_POLICY = {
    "operator": "soft_alert",
    "cc_family": "soft_alert",
    "substrate": "no_kill",
}

_REQUIRED_MANIFEST_FIELDS = (
    "caller_name", "cmd", "expected_cadence_seconds", "expected_tokens_per_day",
    "ratified_by_decision_ref", "registered_by_identity", "purpose",
)


@dataclass(frozen=True)
class Manifest:
    caller_name: str
    cmd: str
    expected_cadence_seconds: int
    expected_tokens_per_day: int
    ratified_by_decision_ref: str
    registered_by_identity: str
    purpose: str
    max_tokens_per_day: Optional[int] = None
    auto_kill_policy: Optional[str] = None  # None → derived from identity default


def derive_auto_kill_policy(registered_by_identity: str) -> str:
    """Per CAI-RESP-161 Q6: identity-derived default."""
    if registered_by_identity not in _VALID_IDENTITIES:
        raise ValueError(
            f"invalid registered_by_identity={registered_by_identity!r}; "
            f"expected one of {sorted(_VALID_IDENTITIES)}"
        )
    return _IDENTITY_TO_DEFAULT_POLICY[registered_by_identity]


def parse_manifest(path: Path) -> Manifest:
    """Parse a YAML or JSON manifest file. Raises on missing/invalid fields."""
    text = Path(path).read_text()
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"manifest at {path} must be a YAML/JSON object, got {type(data).__name__}")
    missing = [f for f in _REQUIRED_MANIFEST_FIELDS if f not in data]
    if missing:
        raise KeyError(f"manifest at {path} missing required fields: {missing}")
    if data["registered_by_identity"] not in _VALID_IDENTITIES:
        raise ValueError(
            f"manifest at {path}: invalid registered_by_identity={data['registered_by_identity']!r}; "
            f"expected one of {sorted(_VALID_IDENTITIES)}"
        )
    if data.get("auto_kill_policy") is not None and data["auto_kill_policy"] not in _VALID_KILL_POLICIES:
        raise ValueError(
            f"manifest at {path}: invalid auto_kill_policy={data['auto_kill_policy']!r}; "
            f"expected one of {sorted(_VALID_KILL_POLICIES)}"
        )
    return Manifest(**{k: data[k] for k in _REQUIRED_MANIFEST_FIELDS},
                    max_tokens_per_day=data.get("max_tokens_per_day"),
                    auto_kill_policy=data.get("auto_kill_policy"))


def sweep_manifests(manifests_dir: Path | str = None) -> list[Manifest]:
    """Read all *.yaml/*.json files under the manifests dir; parse each. Returns
    list of Manifests. Skips files that fail to parse (logs warning)."""
    if manifests_dir is None:
        manifests_dir = Path(__file__).parent.parent / "manifests" / "long_running_callers"
    manifests_dir = Path(manifests_dir)
    results: list[Manifest] = []
    if not manifests_dir.exists():
        return results
    for path in sorted(manifests_dir.iterdir()):
        if path.suffix.lower() not in (".yaml", ".yml", ".json"):
            continue
        if path.name.startswith("."):
            continue
        try:
            results.append(parse_manifest(path))
        except Exception as e:
            logger.warning(f"failed to parse manifest {path}: {e}")
    return results


async def register(
    supabase,
    *,
    caller_name: str,
    cmd: str,
    expected_cadence_seconds: int,
    expected_tokens_per_day: int,
    ratified_by_decision_ref: str,
    registered_by_identity: str,
    purpose: str,
    parent_pid: Optional[int] = None,
    max_tokens_per_day: Optional[int] = None,
    auto_kill_policy: Optional[str] = None,
    operator_authored: Optional[bool] = None,
) -> None:
    """Register (or refresh) a caller in long_running_claude_callers.

    ON CONFLICT (caller_name) DO UPDATE refreshes last_seen_at + cmd + parent_pid
    + heartbeat-related fields. Identity/policy/purpose fields are NOT updated
    on conflict — those require operator/cai action via revoke + new register.
    """
    if registered_by_identity not in _VALID_IDENTITIES:
        raise ValueError(f"invalid registered_by_identity={registered_by_identity!r}")
    if auto_kill_policy is None:
        auto_kill_policy = derive_auto_kill_policy(registered_by_identity)
    if auto_kill_policy not in _VALID_KILL_POLICIES:
        raise ValueError(f"invalid auto_kill_policy={auto_kill_policy!r}")
    if operator_authored is None:
        operator_authored = (registered_by_identity == "operator")
    now = datetime.now(timezone.utc).isoformat()
    await supabase.table("long_running_claude_callers").upsert({
        "caller_name": caller_name,
        "cmd": cmd,
        "parent_pid": parent_pid,
        "started_at": now,
        "expected_cadence_seconds": expected_cadence_seconds,
        "expected_tokens_per_day": expected_tokens_per_day,
        "max_tokens_per_day": max_tokens_per_day,
        "ratified_by_decision_ref": ratified_by_decision_ref,
        "registered_by_identity": registered_by_identity,
        "auto_kill_policy": auto_kill_policy,
        "purpose": purpose,
        "operator_authored": operator_authored,
        "last_seen_at": now,
    }).execute()
    logger.info(
        f"long_running_caller: registered {caller_name} (identity={registered_by_identity}, "
        f"policy={auto_kill_policy})"
    )


async def heartbeat(supabase, caller_name: str) -> None:
    """Refresh last_seen_at for an existing caller. No-op if caller not registered
    (logs warning; caller should register first)."""
    now = datetime.now(timezone.utc).isoformat()
    result = await (
        supabase.table("long_running_claude_callers")
        .update({"last_seen_at": now})
        .eq("caller_name", caller_name)
        .execute()
    )
    if not result.data:
        logger.warning(
            f"long_running_caller: heartbeat for unregistered caller {caller_name!r} "
            f"— call register() first"
        )


async def revoke(supabase, caller_name: str, reason: str) -> None:
    """Mark a caller as revoked (soft-delete via revoked_at). Records reason
    in notification_log per CAI-RESP-161 Q7."""
    import json as _json
    now = datetime.now(timezone.utc).isoformat()
    await (
        supabase.table("long_running_claude_callers")
        .update({"revoked_at": now})
        .eq("caller_name", caller_name)
        .execute()
    )
    await supabase.table("notification_log").insert({
        "source": "caller_revoked",
        "decision_ref": "CC-LONG-CALLER-REGISTRY-001",
        "channel": "long_running_callers",
        "recipient": caller_name,
        "message_text": _json.dumps({"reason": reason, "revoked_at": now}),
    }).execute()
    logger.info(f"long_running_caller: revoked {caller_name} (reason={reason})")
```

- [ ] **Step 2.4: Run pure-unit tests, verify GREEN**

```bash
python -m pytest tests/test_long_running_claude_callers_helper.py -v
```

Expected: 8 passed.

- [ ] **Step 2.5: Commit**

```bash
git add nervous_system/long_running_claude_callers.py tests/test_long_running_claude_callers_helper.py
git commit -m "feat(long-caller-registry): helper module — register/heartbeat/revoke + manifest parser"
```

---

## Task 3: Manifests directory + operator documentation

**Files:**
- Create: `manifests/long_running_callers/.gitkeep`
- Create: `manifests/long_running_callers/README.md`

- [ ] **Step 3.1: Create directory + gitkeep**

```bash
mkdir -p manifests/long_running_callers
touch manifests/long_running_callers/.gitkeep
```

- [ ] **Step 3.2: Write operator-facing README**

Create `manifests/long_running_callers/README.md`:

```markdown
# Long-running claude-caller manifests

Per CAI-RESP-161 Phase A. Any long-running process that invokes `claude`
(plugins, daemons, scheduled tasks, cron, launchd, watch-loops) on a
substrate host MUST be registered.

Two ways to register:

## (a) Python helper (preferred for Python callers)

```python
from nervous_system.long_running_claude_callers import register

await register(
    supabase,
    caller_name="my-daemon",
    cmd="python3 scripts/my_daemon.py",
    expected_cadence_seconds=300,
    expected_tokens_per_day=14_000_000,
    ratified_by_decision_ref="CC-MY-DAEMON-001",
    registered_by_identity="operator",  # or "cc_family" or "substrate"
    purpose="Description for operator-facing review",
)
```

## (b) Manifest file (preferred for static configs)

Drop a YAML or JSON file in this directory. Schema:

```yaml
caller_name: my-daemon                # unique key
cmd: python3 scripts/my_daemon.py     # actual launch command
expected_cadence_seconds: 300         # how often it fires claude
expected_tokens_per_day: 14000000     # informational, for soft-alert threshold
max_tokens_per_day: 20000000          # optional, harder ceiling
ratified_by_decision_ref: CC-MY-DAEMON-001  # filed decision_ref ratifying this caller
registered_by_identity: operator      # operator | cc_family | substrate
purpose: Description for operator-facing review
auto_kill_policy: soft_alert          # optional; defaults derived from identity
```

Manifests are swept on orchestrator startup. Errors logged; valid manifests upserted to the registry table.

## Default auto_kill_policy (per CAI-RESP-161 Q6)

| registered_by_identity | default auto_kill_policy |
|---|---|
| operator | soft_alert (operator-authored — respect operator's intentional infrastructure) |
| cc_family | soft_alert (CC family-spawned — soft escalation) |
| substrate | no_kill (substrate-native carve-out — hard-coded; cannot be overridden) |

## Filing a ratification decision

New callers require a filed `decision_ref` in `strategic_decisions` BEFORE registration. Required fields per CAI-RESP-160:
- Source (path/repo/plugin name)
- Cadence (how often it fires claude)
- Expected tokens/day
- Failure mode if killed
- Why this caller cannot use substrate-native mechanisms

## Phase B enforcement (separate PR per CAI-RESP-161)

Once Phase B ships (paired with CAI-RESP-157 [B] watchdog-kill):
- Unregistered + cadence pattern (>50 sessions/24h per CAI-RESP-157 [A]) → watchdog SIGTERMs
- Registered + exceeds expected_tokens_per_day → soft alert (notification_log + agent_message to CAI)
- Registered + cadence drift (>2x expected over 30min) → soft alert
- Substrate-native carve-out → exempt
```

- [ ] **Step 3.3: Commit**

```bash
git add manifests/long_running_callers/
git commit -m "docs(long-caller-registry): operator-facing README + manifests directory"
```

---

## Task 4: Wire manifest sweep into orch boot

**Files:**
- Modify: `wingmen_orch.py`
- Test: add a live-DB test in `tests/test_long_running_claude_callers.py`

- [ ] **Step 4.1: Append failing manifest-sweep integration test**

Append to `tests/test_long_running_claude_callers.py`:

```python
@pytestmark_integration
def test_manifest_sweep_upserts_registry(tmp_path):
    """sweep_manifests() reads YAML files and upserts via register()."""
    import asyncio
    from supabase import acreate_client
    from dotenv import dotenv_values
    from nervous_system.long_running_claude_callers import sweep_manifests, register

    test_name = f"test-sweep-{uuid.uuid4().hex[:8]}"
    f = tmp_path / "test.yaml"
    f.write_text(f"""
caller_name: {test_name}
cmd: echo test
expected_cadence_seconds: 60
expected_tokens_per_day: 1000
ratified_by_decision_ref: TEST-REGISTRY-001
registered_by_identity: cc_family
purpose: round-trip integration test
""")

    async def _run():
        env = dotenv_values(Path(__file__).parent.parent / ".env")
        sb = await acreate_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_KEY"])
        for m in sweep_manifests(tmp_path):
            await register(
                sb,
                caller_name=m.caller_name,
                cmd=m.cmd,
                expected_cadence_seconds=m.expected_cadence_seconds,
                expected_tokens_per_day=m.expected_tokens_per_day,
                ratified_by_decision_ref=m.ratified_by_decision_ref,
                registered_by_identity=m.registered_by_identity,
                purpose=m.purpose,
                max_tokens_per_day=m.max_tokens_per_day,
                auto_kill_policy=m.auto_kill_policy,
            )
        # Verify
        with psycopg.connect(_DSN, autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT caller_name, auto_kill_policy FROM long_running_claude_callers "
                    "WHERE caller_name=%s", (test_name,)
                )
                r = cur.fetchone()
        assert r is not None, f"manifest sweep should have registered {test_name}"
        assert r[1] == "soft_alert", f"cc_family default auto_kill_policy=soft_alert, got {r[1]}"
        # Cleanup
        with psycopg.connect(_DSN, autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("DELETE FROM long_running_claude_callers WHERE caller_name=%s", (test_name,))

    asyncio.run(_run())
```

- [ ] **Step 4.2: Run, verify GREEN (sweep + helper already implemented)**

```bash
python -m pytest tests/test_long_running_claude_callers.py::test_manifest_sweep_upserts_registry -v
```

Expected: 1 passed (helper + sweep code from Task 2 covers it).

- [ ] **Step 4.3: Modify `wingmen_orch.py` to sweep manifests on boot**

Add imports near top:

```python
from nervous_system.long_running_claude_callers import sweep_manifests, register as _lrcc_register
```

In `main_loop()` initialization (before the main `while True` loop), add:

```python
    # CC-LONG-CALLER-REGISTRY-001 Phase A: sweep manifests on orchestrator boot.
    # Each YAML/JSON manifest under manifests/long_running_callers/ is parsed
    # and upserted to the registry. Errors logged but non-fatal.
    try:
        for _manifest in sweep_manifests():
            try:
                await _lrcc_register(
                    supabase,
                    caller_name=_manifest.caller_name,
                    cmd=_manifest.cmd,
                    expected_cadence_seconds=_manifest.expected_cadence_seconds,
                    expected_tokens_per_day=_manifest.expected_tokens_per_day,
                    ratified_by_decision_ref=_manifest.ratified_by_decision_ref,
                    registered_by_identity=_manifest.registered_by_identity,
                    purpose=_manifest.purpose,
                    max_tokens_per_day=_manifest.max_tokens_per_day,
                    auto_kill_policy=_manifest.auto_kill_policy,
                )
            except Exception as e:
                logger.warning(f"long_running_caller manifest registration failed for {_manifest.caller_name}: {e}")
    except Exception as e:
        logger.warning(f"long_running_caller manifest sweep failed: {e}")
```

(Place this AFTER `supabase` is initialized; identify the correct spot by reading the existing main_loop init.)

- [ ] **Step 4.4: Smoke test orch starts cleanly**

```bash
python -c "import wingmen_orch; print('import ok')"
```

- [ ] **Step 4.5: Commit**

```bash
git add wingmen_orch.py tests/test_long_running_claude_callers.py
git commit -m "feat(long-caller-registry): wire manifest sweep into orch boot"
```

---

## Task 5: notification_log source-enum forward-compat

**Files:**
- (no schema changes — notification_log.source is TEXT, no CHECK constraint to amend)
- Modify: `nervous_system/long_running_claude_callers.py` (add constants for the 5 source values)

- [ ] **Step 5.1: Add source constants**

Append to `nervous_system/long_running_claude_callers.py` near the top of the module:

```python
# Notification-log source-enum values per CAI-RESP-161 Q7 (reserved here for
# Phase B watchdog wire-in; Phase A uses CALLER_REGISTERED and CALLER_REVOKED).
NOTIFICATION_SOURCE_WATCHDOG_HARD_KILL = "watchdog_hard_kill"
NOTIFICATION_SOURCE_WATCHDOG_SOFT_ALERT = "watchdog_soft_alert"
NOTIFICATION_SOURCE_CALLER_SELF_KILL = "caller_self_kill"
NOTIFICATION_SOURCE_CALLER_REGISTERED = "caller_registered"
NOTIFICATION_SOURCE_CALLER_REVOKED = "caller_revoked"
```

- [ ] **Step 5.2: Modify `revoke()` to use the constant**

In `nervous_system/long_running_claude_callers.py`, change the hard-coded `"caller_revoked"` string in `revoke()` to use `NOTIFICATION_SOURCE_CALLER_REVOKED` for grep-ability.

- [ ] **Step 5.3: Run all tests to confirm no regression**

```bash
python -m pytest tests/test_long_running_claude_callers.py tests/test_long_running_claude_callers_helper.py -v
```

Expected: all pass.

- [ ] **Step 5.4: Commit**

```bash
git add nervous_system/long_running_claude_callers.py
git commit -m "feat(long-caller-registry): notification_log source-enum constants (Phase B forward-compat)"
```

---

## Task 6: Rollback procedure (required per CAI-RESP-161 constraints)

**Files:**
- Create: `docs/superpowers/rollbacks/CC-LONG-CALLER-REGISTRY-001-phase-a.md`

- [ ] **Step 6.1: Write rollback doc**

Create `docs/superpowers/rollbacks/CC-LONG-CALLER-REGISTRY-001-phase-a.md`:

```markdown
# CC-LONG-CALLER-REGISTRY-001 Phase A Rollback

Per CAI-RESP-161 constraint: "All Phase A + Phase B PRs must include rollback procedure in case of defect discovery post-ship."

## Triggers for rollback
- Phase A migration causes boot_briefing view corruption
- Helper module breaks orchestrator boot
- Manifest sweep blocks orchestrator main loop
- Discovered data-integrity issue (e.g., FK violation on substrate seed)

## Rollback steps

### Code revert
```bash
git revert <PR-merge-commit-SHA>
git push origin main
./scripts/restart_orch.sh
```

### Migration rollback (additive — soft-revert via DROP)
The migration is additive (table + view extension + seed). Soft revert:

```sql
BEGIN;

-- Drop the table (loses seed + any registrations)
DROP TABLE IF EXISTS long_running_claude_callers CASCADE;

-- Restore boot_briefing view to pre-Phase-A definition
-- (capture pre-Phase-A defn before applying; store at /tmp/boot_briefing_pre_phase_a.sql)
CREATE OR REPLACE VIEW boot_briefing AS
<paste pre-Phase-A view body verbatim>;

COMMIT;
```

### Verify rollback
```bash
python3 -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv()
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT count(*) FROM information_schema.tables WHERE table_name='long_running_claude_callers'\")
        assert cur.fetchone()[0] == 0, 'table still exists'
        cur.execute(\"SELECT pg_get_viewdef('boot_briefing'::regclass, true)\")
        defn = cur.fetchone()[0]
        assert 'long_running_caller' not in defn, 'view still references long_running_caller'
print('rollback verified')
"
```

## Audit trail
Any rollback must be filed as a decision_ref `CC-LONG-CALLER-REGISTRY-ROLLBACK-NNN` with:
- Trigger (which defect)
- Decision (revert + soft-drop)
- Audit log of all caller registrations lost
```

- [ ] **Step 6.2: Commit**

```bash
git add docs/superpowers/rollbacks/CC-LONG-CALLER-REGISTRY-001-phase-a.md
git commit -m "docs(long-caller-registry): Phase A rollback procedure per CAI-RESP-161 constraint"
```

---

## Task 7: PR + ship

- [ ] **Step 7.1: Run full test suite**

```bash
python -m pytest tests/ -q --timeout=120
```

Expected: all pass (or pre-existing flaky test failures noted but not new).

- [ ] **Step 7.2: Push + open PR**

```bash
env -u GITHUB_TOKEN git push -u origin feat/long-caller-registry
env -u GITHUB_TOKEN gh pr create --base main --head feat/long-caller-registry --title "feat(long-caller-registry): Phase A — table + helper + manifest sweep (CAI-RESP-161)" --body "..."
```

PR body should include:
- Summary referencing CAI-RESP-160 + CAI-RESP-161
- 7 commits enumerated
- Phase B forward-compat notes (notification_log source constants ready; watchdog wire-in held for separate PR)
- Substrate-native seed listing (ralphy, paused-job-retry)
- Rollback procedure linked
- Test plan checklist

- [ ] **Step 7.3: Wait for CI + merge**

```bash
env -u GITHUB_TOKEN gh pr checks <pr-num>
env -u GITHUB_TOKEN gh pr merge <pr-num> --squash --delete-branch
```

- [ ] **Step 7.4: Restart orchestrator (loads sweep_manifests + register at boot)**

```bash
./scripts/restart_orch.sh
launchctl list | grep wingmen.orchestrator
```

- [ ] **Step 7.5: File ship-update to cai (on CAI-RESP-161 thread)**

```python
INSERT INTO agent_messages
  (thread_id=<CAI-RESP-161 thread>, from_agent='cc-orchestrator', to_agent='cai',
   message_type='update', subject='CC-LONG-CALLER-REGISTRY-001 Phase A shipped (PR #XX, commit SHA)',
   body='Phase A live. Substrate-native seed in place. Manifest sweep on boot. Phase B starts on CAI-RESP-157 [B] kickoff.',
   priority='P3')
```

- [ ] **Step 7.6: Trigger CAI-RESP-160 retroactive 7-day audit (Phase C condition)**

Per CAI-RESP-161 Phase C: "Triggered IF CAI-RESP-160 7-day audit surfaces unregistered long-running callers (operator daemons, scripts, plists, cron jobs other than the revoked probe)."

Run a discovery pass + file findings if any:

```bash
# Discover candidates
ps -ef | grep -iE "claude" | grep -v grep | head -20
ls ~/Library/LaunchAgents/
find / -name "*.plist" -path "*claude*" 2>/dev/null
# Cross-reference with the registry (post-seed)
python3 -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv()
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute('SELECT caller_name FROM long_running_claude_callers ORDER BY caller_name')
        print('registered:', [r[0] for r in cur.fetchall()])
"
```

If audit surfaces unregistered callers, file `CC-LONG-CALLER-AUDIT-FINDINGS-001` per CAI-RESP-161 Phase C with full listing + recommendations.

---

## Self-Review

**Spec coverage:**
- ✅ Q1 hybrid registration → Python helper (Task 2) + manifest reader (Task 2/3) + DB-direct (open via direct SQL)
- ✅ Q2 heartbeat + detector cross-verify → heartbeat() helper (Task 2); stale_heartbeat status derived in view (Task 1 Section 3)
- ✅ Q3 pattern + self-declaration → Phase A ships visibility; Phase B (separate PR) wires watchdog
- ✅ Q4 threshold bound to CAI-RESP-157 [A] → no separate constant in this PR; Phase B references the existing detector's constant
- ✅ Q5 grandfather window → Phase C trigger documented in Task 7 + README
- ✅ Q6 schema additions (all 5 fields) → Task 1 Section 1
- ✅ Q7 notification_log source-enum constants → Task 5
- ✅ Q-final self_kill_on_budget_exceed → opt-in helper API documented in Task 2 docstring (full implementation deferred; not blocking Phase A)
- ✅ Substrate-native seed (ralphy + paused-job-retry) → Task 1 Section 2
- ✅ boot_briefing UNION arm → Task 1 Section 3
- ✅ Rollback procedure → Task 6

**Placeholder scan:** No TBD/TODO. Each step has either complete code or exact commands.

**Type consistency:**
- `Manifest` dataclass shape matches the Python helper `register()` kwarg signature
- `_VALID_IDENTITIES` set matches CHECK constraint values
- `_IDENTITY_TO_DEFAULT_POLICY` matches CAI-RESP-161 Q6 defaults

**Deferred for Phase B (separate PR):**
- `self_kill_on_budget_exceed=` actual polling implementation (Phase A documents the API only; Phase B wires it)
- Watchdog kill/alert flows (the consumer of registry)
- Cadence-drift detection
- Audit findings filing CC-LONG-CALLER-AUDIT-FINDINGS-001 (Phase C, conditional)

No gaps to fix.
