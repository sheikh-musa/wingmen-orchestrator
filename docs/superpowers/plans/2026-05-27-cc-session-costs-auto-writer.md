# cc_session_costs Auto-Writer (M1 Transcript-Tail Parser) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Restore per-CC-family token visibility by scanning `~/.claude/projects/<repo>/*.jsonl` files, parsing `usage` fields from assistant messages, and upserting into `cc_session_costs`. Operator-request trigger fired 2026-05-27.

**Architecture:** Pure-Python sweep module (`nervous_system/cc_session_costs_auto_writer.py`) wired into orch main loop at 10-min cadence. Re-uses `jsonl_safe_read` (PR #42) for defensive IO and `_DIR_TO_CC` (PR #36) for caller attribution. Additive schema extension adds `cache_creation_input_tokens` + `cache_read_input_tokens` columns for proper cost modeling. One-time operator-invokable backfill script for historical jsonls.

**Decision refs:** CC-LONG-CALLER-AUTO-TOKEN-TRACK-001 (parent decision 908, parking-lot), CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME (decision 964, this PR's locked scope), CAI-RESP-154 (cc_session_costs Phase A, manual-write).

**R2 enforcement REMAINS DEFERRED** — this is visibility only.

---

## File Structure

| Path | Purpose | New/Modified |
|---|---|---|
| `supabase/migrations/20260527_cc_session_costs_cache_tokens.sql` | ADD cache_creation_input_tokens + cache_read_input_tokens columns | NEW |
| `nervous_system/cc_session_costs_auto_writer.py` | sweep + parse + upsert | NEW |
| `wingmen_orch.py` | 10min-gated call into auto-writer | MODIFIED |
| `scripts/cc_session_costs_backfill.py` | operator-invokable historical backfill | NEW |
| `tests/test_cc_session_costs_schema_cache_tokens.py` | live-DB schema test | NEW |
| `tests/test_cc_session_costs_auto_writer.py` | pure-unit tests | NEW |

---

## Pre-Flight

- [ ] **Step 0.1: Environment + dependencies confirmed**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git branch --show-current  # feat/cc-session-costs-auto-writer
source .venv/bin/activate
python -c "import psycopg, json, statistics; from nervous_system.jsonl_safe_read import safe_file_stats; from nervous_system.autonomous_loop_detector import _DIR_TO_CC; print('ok')"
```

- [ ] **Step 0.2: Verify current state**

```bash
python3 - <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
    cur.execute("SELECT count(*) FROM cc_session_costs WHERE started_at > now() - interval '7 days'")
    assert cur.fetchone()[0] == 0, "expected empty cc_session_costs for clean baseline"
print("pre-flight: cc_session_costs baseline confirmed empty")
PY
```

---

## Task 1: Schema extension — cache token columns

**Files:**
- Create: `supabase/migrations/20260527_cc_session_costs_cache_tokens.sql`
- Create: `tests/test_cc_session_costs_schema_cache_tokens.py`

- [ ] **Step 1.1: Schema test (RED)**

`tests/test_cc_session_costs_schema_cache_tokens.py`:

```python
"""Live-DB schema test for cc_session_costs cache token columns."""
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
pytestmark_integration = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")


@pytestmark_integration
def test_cache_creation_input_tokens_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT data_type, column_default FROM information_schema.columns "
            "WHERE table_name='cc_session_costs' AND column_name='cache_creation_input_tokens'"
        )
        r = cur.fetchone()
    assert r is not None, "cache_creation_input_tokens missing"
    assert r[0] == "integer"


@pytestmark_integration
def test_cache_read_input_tokens_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT data_type, column_default FROM information_schema.columns "
            "WHERE table_name='cc_session_costs' AND column_name='cache_read_input_tokens'"
        )
        r = cur.fetchone()
    assert r is not None, "cache_read_input_tokens missing"
    assert r[0] == "integer"
```

Verify RED: `python -m pytest tests/test_cc_session_costs_schema_cache_tokens.py -v`

- [ ] **Step 1.2: Migration**

`supabase/migrations/20260527_cc_session_costs_cache_tokens.sql`:

```sql
-- CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME: extend cc_session_costs with
-- cache-token columns for proper cost modeling. Cache-read is much cheaper
-- than fresh-input; existing summed input_tokens conflates them.
--
-- Additive. Idempotent. Pre-apply per CAI-RESP-102.

BEGIN;

ALTER TABLE cc_session_costs
    ADD COLUMN IF NOT EXISTS cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0;

ALTER TABLE cc_session_costs
    ADD COLUMN IF NOT EXISTS cache_read_input_tokens INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN cc_session_costs.cache_creation_input_tokens IS
    'Per CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME: fresh tokens written to '
    'prompt cache (5m + 1h ephemeral). Distinct from input_tokens (uncached '
    'fresh input) for cost-model accuracy.';

COMMENT ON COLUMN cc_session_costs.cache_read_input_tokens IS
    'Per CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME: tokens served from '
    'prompt cache hits. Heavily discounted vs fresh input on Anthropic '
    'pricing — track separately for accurate cost attribution.';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name='cc_session_costs'
                      AND column_name='cache_creation_input_tokens') THEN
        RAISE EXCEPTION 'cache_creation_input_tokens missing after ADD COLUMN';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name='cc_session_costs'
                      AND column_name='cache_read_input_tokens') THEN
        RAISE EXCEPTION 'cache_read_input_tokens missing after ADD COLUMN';
    END IF;
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES ('20260527120000', 'cc_session_costs_cache_tokens', ARRAY[]::text[])
ON CONFLICT (version) DO NOTHING;
```

Apply:
```bash
python scripts/check_additive_migration.py supabase/migrations/20260527_cc_session_costs_cache_tokens.sql
python3 -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
sql = open('supabase/migrations/20260527_cc_session_costs_cache_tokens.sql').read()
with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
    cur.execute(sql); print('applied')
"
python -m pytest tests/test_cc_session_costs_schema_cache_tokens.py -v
```

- [ ] **Step 1.3: Commit**

```bash
git add supabase/migrations/20260527_cc_session_costs_cache_tokens.sql tests/test_cc_session_costs_schema_cache_tokens.py
git commit -m "feat(token-track): cc_session_costs cache_creation + cache_read columns (CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME)"
```

---

## Task 2: Auto-writer module

**Files:**
- Create: `nervous_system/cc_session_costs_auto_writer.py`
- Create: `tests/test_cc_session_costs_auto_writer.py`

- [ ] **Step 2.1: Failing tests**

`tests/test_cc_session_costs_auto_writer.py`:

```python
"""Pure-unit tests for cc_session_costs auto-writer."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.cc_session_costs_auto_writer import (
    parse_jsonl_usage,
    SessionTokens,
    sweep_projects_root,
)


def _make_jsonl(parent: Path, name: str, usages: list[dict], mtime: float | None = None) -> Path:
    """Write a jsonl with N assistant messages each carrying a usage block."""
    p = parent / name
    lines = []
    for u in usages:
        lines.append(json.dumps({"type": "user", "message": {"role": "user", "content": "x"}}))
        lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "x", "usage": u}}))
    p.write_text("\n".join(lines) + "\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


class TestParseJsonlUsage:
    def test_sums_input_output_across_assistant_messages(self, tmp_path):
        p = _make_jsonl(tmp_path, "sess.jsonl", [
            {"input_tokens": 100, "output_tokens": 50, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 1000},
            {"input_tokens": 10, "output_tokens": 30, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1500},
        ])
        result = parse_jsonl_usage(p)
        assert result.input_tokens == 110
        assert result.output_tokens == 80
        assert result.cache_creation_input_tokens == 200
        assert result.cache_read_input_tokens == 2500

    def test_missing_usage_fields_default_zero(self, tmp_path):
        p = _make_jsonl(tmp_path, "sess.jsonl", [
            {"input_tokens": 5, "output_tokens": 10},  # no cache fields
        ])
        result = parse_jsonl_usage(p)
        assert result.input_tokens == 5
        assert result.output_tokens == 10
        assert result.cache_creation_input_tokens == 0
        assert result.cache_read_input_tokens == 0

    def test_no_assistant_messages_returns_zeros(self, tmp_path):
        p = tmp_path / "user-only.jsonl"
        p.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
        result = parse_jsonl_usage(p)
        assert result.input_tokens == 0
        assert result.output_tokens == 0

    def test_corrupt_jsonl_returns_none(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text("not-json\n")
        result = parse_jsonl_usage(p)
        assert result is None

    def test_missing_file_returns_none(self, tmp_path):
        result = parse_jsonl_usage(tmp_path / "nope.jsonl")
        assert result is None


class TestSweepProjectsRoot:
    def test_attributes_unknown_dir_skipped(self, tmp_path):
        """Sweep must skip ~/.claude/projects/* directories not in _DIR_TO_CC."""
        unknown = tmp_path / "-some-random-dir"
        unknown.mkdir()
        (unknown / "sess.jsonl").write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "x"}}) + "\n")
        rows = sweep_projects_root(tmp_path, modified_since=0.0)
        assert rows == []

    def test_sweep_known_repo_emits_row(self, tmp_path):
        repo_dir = tmp_path / "-Users-sheikhmusa-wingmen-projects-ai-scholar"
        repo_dir.mkdir()
        _make_jsonl(repo_dir, "abc-123.jsonl", [
            {"input_tokens": 50, "output_tokens": 25, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 200},
        ])
        rows = sweep_projects_root(tmp_path, modified_since=0.0)
        assert len(rows) == 1
        row = rows[0]
        assert row["cc_identity"] == "cc-scholar"
        assert row["session_id"] == "abc-123"
        assert row["input_tokens"] == 50
        assert row["output_tokens"] == 25
        assert row["cache_creation_input_tokens"] == 100
        assert row["cache_read_input_tokens"] == 200

    def test_sweep_modified_since_filter_works(self, tmp_path):
        repo_dir = tmp_path / "-Users-sheikhmusa-wingmen-projects-ai-scholar"
        repo_dir.mkdir()
        # File mtime in the past
        _make_jsonl(repo_dir, "old.jsonl", [{"input_tokens": 1, "output_tokens": 1}], mtime=100.0)
        rows = sweep_projects_root(tmp_path, modified_since=1000.0)
        assert rows == []  # older than cutoff
```

Verify RED: `python -m pytest tests/test_cc_session_costs_auto_writer.py -v`

- [ ] **Step 2.2: Implementation**

`nervous_system/cc_session_costs_auto_writer.py`:

```python
"""cc_session_costs M1 transcript-tail parser per CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME.

Sweeps ~/.claude/projects/<mangled-repo>/*.jsonl. For each jsonl, parses
'usage' fields from assistant messages, sums them, returns SessionTokens.
sweep_projects_root attributes via _DIR_TO_CC and returns upsert-ready row dicts.

Pure-Python. No I/O at decision-time except the defensive jsonl_safe_read calls.
Caller wires it into orch main loop + commits the upsert batch.

R3 inheritance: all reads are defensive — missing/corrupt files return None.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from nervous_system.autonomous_loop_detector import _DIR_TO_CC
from nervous_system.jsonl_safe_read import safe_file_stats


@dataclass(frozen=True)
class SessionTokens:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


def parse_jsonl_usage(path: Path) -> Optional[SessionTokens]:
    """Stream-parse a Claude CLI jsonl and sum usage across assistant messages.

    Returns None on any error (missing, corrupt, unreadable). Returns
    SessionTokens(0,0,0,0) for a jsonl with no assistant messages.
    """
    try:
        in_t = out_t = cc_t = cr_t = 0
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    return None  # corrupt — bail
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") != "assistant":
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                in_t += int(usage.get("input_tokens") or 0)
                out_t += int(usage.get("output_tokens") or 0)
                cc_t += int(usage.get("cache_creation_input_tokens") or 0)
                cr_t += int(usage.get("cache_read_input_tokens") or 0)
        return SessionTokens(
            input_tokens=in_t,
            output_tokens=out_t,
            cache_creation_input_tokens=cc_t,
            cache_read_input_tokens=cr_t,
        )
    except (OSError, FileNotFoundError):
        return None


def sweep_projects_root(
    projects_root: Path,
    modified_since: float,
) -> list[dict[str, Any]]:
    """Walk projects_root/<mangled-repo>/*.jsonl, parse usage, return upsert rows.

    `modified_since`: epoch-seconds cutoff. Skip jsonls older than this. Used by
    the orch wrapper to do incremental sweeps (e.g., last 10min only).

    Each returned row is a dict ready for INSERT INTO cc_session_costs:
        cc_identity, session_id, input_tokens, output_tokens,
        cache_creation_input_tokens, cache_read_input_tokens, mtime
    """
    rows: list[dict[str, Any]] = []
    if not projects_root.exists():
        return rows
    try:
        repo_dirs = [d for d in projects_root.iterdir() if d.is_dir()]
    except OSError:
        return rows
    for repo_dir in repo_dirs:
        cc_identity = _DIR_TO_CC.get(repo_dir.name)
        if not cc_identity:
            continue
        try:
            jsonls = [
                p for p in repo_dir.iterdir()
                if p.is_file() and p.name.endswith(".jsonl") and not p.name.startswith(".")
            ]
        except OSError:
            continue
        for jsonl in jsonls:
            stats = safe_file_stats(jsonl)
            if stats is None:
                continue
            if stats.mtime < modified_since:
                continue
            tokens = parse_jsonl_usage(jsonl)
            if tokens is None:
                continue
            session_id = jsonl.stem  # filename without .jsonl
            rows.append({
                "cc_identity": cc_identity,
                "session_id": session_id,
                "input_tokens": tokens.input_tokens,
                "output_tokens": tokens.output_tokens,
                "cache_creation_input_tokens": tokens.cache_creation_input_tokens,
                "cache_read_input_tokens": tokens.cache_read_input_tokens,
                "mtime": stats.mtime,
            })
    return rows


def upsert_rows(dsn: str, rows: list[dict[str, Any]], source: str = "auto_writer_v1") -> int:
    """Upsert rows into cc_session_costs. Returns number of rows written.

    Conflict resolution: session_id is the natural key but the table doesn't
    currently have a UNIQUE constraint on it. We dedupe by session_id within
    the batch and UPDATE-on-conflict using a manual select-then-insert pattern.
    """
    if not rows:
        return 0
    import psycopg
    written = 0
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                SELECT id FROM cc_session_costs WHERE session_id = %s AND source = %s LIMIT 1
                """,
                (row["session_id"], source),
            )
            existing = cur.fetchone()
            from datetime import datetime, timezone
            started_at = datetime.fromtimestamp(row["mtime"], tz=timezone.utc)
            if existing:
                cur.execute(
                    """
                    UPDATE cc_session_costs SET
                      input_tokens = %s,
                      output_tokens = %s,
                      cache_creation_input_tokens = %s,
                      cache_read_input_tokens = %s,
                      ended_at = %s
                    WHERE id = %s
                    """,
                    (
                        row["input_tokens"],
                        row["output_tokens"],
                        row["cache_creation_input_tokens"],
                        row["cache_read_input_tokens"],
                        started_at,
                        existing[0],
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO cc_session_costs
                      (cc_identity, session_id, started_at, ended_at,
                       input_tokens, output_tokens,
                       cache_creation_input_tokens, cache_read_input_tokens,
                       source, has_per_message_detail)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false)
                    """,
                    (
                        row["cc_identity"], row["session_id"], started_at, started_at,
                        row["input_tokens"], row["output_tokens"],
                        row["cache_creation_input_tokens"], row["cache_read_input_tokens"],
                        source,
                    ),
                )
            written += 1
    return written
```

Verify GREEN: `python -m pytest tests/test_cc_session_costs_auto_writer.py -v`

- [ ] **Step 2.3: Commit**

```bash
git add nervous_system/cc_session_costs_auto_writer.py tests/test_cc_session_costs_auto_writer.py
git commit -m "feat(token-track): cc_session_costs auto-writer module (M1 transcript-tail parser)"
```

---

## Task 3: Orch main-loop wire-in

**File:**
- Modify: `wingmen_orch.py`

- [ ] **Step 3.1: Inspect current main-loop shape**

```bash
grep -n "main_loop\|while True\|asyncio.sleep" wingmen_orch.py | head -10
```

- [ ] **Step 3.2: Add gated sweep call**

Add near other module-level constants in `wingmen_orch.py`:

```python
from pathlib import Path
import time

_CC_SESSION_COSTS_SWEEP_INTERVAL = 600  # 10min per CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME
_cc_session_costs_last_sweep = 0.0
_CLAUDE_PROJECTS_ROOT = Path.home() / ".claude" / "projects"
```

Add helper function before main loop:

```python
async def _maybe_cc_session_costs_sweep() -> None:
    """Gated 10min sweep — parse recent jsonls, upsert usage into cc_session_costs."""
    global _cc_session_costs_last_sweep
    now = time.time()
    if now - _cc_session_costs_last_sweep < _CC_SESSION_COSTS_SWEEP_INTERVAL:
        return
    cutoff = _cc_session_costs_last_sweep or (now - 3600)  # first sweep covers last hour
    _cc_session_costs_last_sweep = now
    try:
        from nervous_system.cc_session_costs_auto_writer import sweep_projects_root, upsert_rows
        rows = sweep_projects_root(_CLAUDE_PROJECTS_ROOT, modified_since=cutoff)
        if rows:
            dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
            if dsn:
                written = upsert_rows(dsn, rows)
                logger.info(f"cc_session_costs auto-writer: {written} rows upserted")
    except Exception as e:
        logger.error(f"cc_session_costs auto-writer failed: {e}")
```

Wire into the main loop's `while True:` body (locate the existing tick loop and add `await _maybe_cc_session_costs_sweep()` at a sensible insertion point).

- [ ] **Step 3.3: Smoke import + commit**

```bash
python -c "import wingmen_orch; print('ok')"
git add wingmen_orch.py
git commit -m "feat(token-track): wire auto-writer into orch main loop at 10min cadence"
```

---

## Task 4: Operator backfill script

**File:**
- Create: `scripts/cc_session_costs_backfill.py`

```python
"""One-shot backfill of cc_session_costs from historical jsonls.

Operator-invokable per CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME [E].

Usage:
    python scripts/cc_session_costs_backfill.py             # last 7 days
    python scripts/cc_session_costs_backfill.py --days 30   # custom window
    python scripts/cc_session_costs_backfill.py --since-mtime 1700000000

Idempotent: UPSERT on session_id (re-runs update existing rows in place).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.cc_session_costs_auto_writer import sweep_projects_root, upsert_rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--since-mtime", type=float, default=None,
                   help="Override: epoch-seconds cutoff (otherwise computed from --days)")
    p.add_argument("--projects-root", type=str, default=str(Path.home() / ".claude" / "projects"))
    args = p.parse_args()

    import time as _t
    cutoff = args.since_mtime if args.since_mtime is not None else (_t.time() - args.days * 86400)

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1

    print(f"scanning {args.projects_root} for jsonls modified since epoch {cutoff:.0f}...")
    rows = sweep_projects_root(Path(args.projects_root), modified_since=cutoff)
    print(f"  found {len(rows)} session rows to upsert")
    if not rows:
        return 0

    # Summary by cc_identity
    from collections import defaultdict
    summary = defaultdict(lambda: {"sessions": 0, "input": 0, "output": 0, "cache_create": 0, "cache_read": 0})
    for r in rows:
        s = summary[r["cc_identity"]]
        s["sessions"] += 1
        s["input"] += r["input_tokens"]
        s["output"] += r["output_tokens"]
        s["cache_create"] += r["cache_creation_input_tokens"]
        s["cache_read"] += r["cache_read_input_tokens"]
    print("\nper-CC summary (pre-upsert):")
    for cc, s in sorted(summary.items()):
        total = s["input"] + s["output"] + s["cache_create"] + s["cache_read"]
        print(f"  {cc}: sessions={s['sessions']} in={s['input']:,} out={s['output']:,} "
              f"cache_create={s['cache_create']:,} cache_read={s['cache_read']:,} total={total:,}")

    written = upsert_rows(dsn, rows, source="backfill_v1")
    print(f"\nupserted {written} rows (source=backfill_v1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Commit:
```bash
git add scripts/cc_session_costs_backfill.py
git commit -m "feat(token-track): operator-invokable backfill script for historical jsonls"
```

---

## Task 5: Smoke test + PR + ship

- [ ] **Step 5.1: Full sweep**

```bash
source .venv/bin/activate && python -m pytest tests/test_jsonl_safe_read.py tests/test_cc_session_costs_auto_writer.py tests/test_cc_session_costs_schema_cache_tokens.py -v
```

- [ ] **Step 5.2: Live backfill smoke (manual, operator-confirmed)**

```bash
python scripts/cc_session_costs_backfill.py --days 1
```

Operator inspects the per-CC summary to verify reasonable numbers before scaling up. If summary looks right, the real `--days 7` backfill can run as a follow-up.

- [ ] **Step 5.3: Push + PR**

```bash
env -u GITHUB_TOKEN git push -u origin feat/cc-session-costs-auto-writer
env -u GITHUB_TOKEN gh pr create --base main --head feat/cc-session-costs-auto-writer --title "feat(token-track): cc_session_costs auto-writer + cache columns (CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME)" --body "..."
```

---

## Self-Review

- ✅ Schema additive: ADD COLUMN IF NOT EXISTS + NOT NULL DEFAULT 0
- ✅ Boot_briefing untouched (cc_session_costs arm already exists, query unchanged)
- ✅ R2 enforcement REMAINS DEFERRED — visibility-only PR
- ✅ Defensive IO via jsonl_safe_read inheritance — never raises
- ✅ Source-tag distinction ('auto_writer_v1' vs 'script' vs 'backfill_v1') for forensic forward-compat
- ✅ Idempotent upserts via session_id+source key (no UNIQUE constraint needed; SELECT-then-INSERT pattern)
- ✅ Orch wire-in failure-isolated via try/except (informational data must not block orch primary work)
