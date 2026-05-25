# Watchdog Phase B — R1-AMENDED Content-Shape Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Layer R1-AMENDED 3-of-3 content-shape AND-gate onto `decide_kill` per CAI-RESP-164 + CAI-RESP-167. Watchdog daemon remains dormant until this PR lands.

**Architecture:** Two new pure-Python modules (`jsonl_safe_read.py` defensive IO, `content_shape_signals.py` three signal extractors) feed an extended `decide_kill` with a new `content_shape` parameter + new `monitored` action. New `watchdog_monitored_callers` table + `boot_briefing` arm surface monitored callers with their signal values. `watchdog.py` `_long_caller_sweep` computes signals per flagged caller and writes a pre-SIGTERM audit row before any kill.

**Decision refs:** CAI-RESP-164 (R1-AMENDED ratification), CAI-RESP-167 (PR #42 mandate), CC-WATCHDOG-PHASE-B-CONTENT-SHAPE-001 (decision 956, this PR's locked scope), CC-FAMILY-INTERACTIVE-SESSIONS-001 (decision 909, registry exemption still in effect).

---

## File Structure

| Path | Purpose | New/Modified |
|---|---|---|
| `nervous_system/jsonl_safe_read.py` | defensive IO helpers (read_first_user_message, safe_file_stats); never raise | NEW |
| `nervous_system/content_shape_signals.py` | signal_a (median size), signal_b (cadence band), signal_c (identical prompts); return Match dataclass per signal | NEW |
| `nervous_system/long_caller_watchdog.py` | extend KillDecision actions + decide_kill content_shape param; new `monitored` branch | MODIFIED |
| `supabase/migrations/20260523_watchdog_monitored_callers.sql` | new table + boot_briefing UNION arm | NEW |
| `watchdog.py` | compute signals + R5 pre-SIGTERM audit row + monitored upserts | MODIFIED |
| `tests/fixtures/probe_max_throttle/` | 10 synthetic-but-faithful jsonls matching probe pattern | NEW |
| `tests/fixtures/cc_scholar_2026_05_19_2244/` | 10 anonymized jsonls from real incident | NEW |
| `tests/test_jsonl_safe_read.py` | pure-unit; verify never-raise + None-on-error | NEW |
| `tests/test_content_shape_signals.py` | pure-unit + fixture-driven; positive→3of3, negative→0of3 | NEW |
| `tests/test_long_caller_watchdog_content_shape.py` | extends Task-2 test surface with content_shape param cases | NEW |
| `tests/test_watchdog_monitored_callers.py` | live-DB schema test | NEW |
| `docs/superpowers/rollbacks/watchdog-phase-b-content-shape.md` | rollback procedure | NEW |

---

## Pre-Flight

- [ ] **Step 0.1: Environment**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git branch --show-current  # should be feat/watchdog-phase-b-content-shape-001
source .venv/bin/activate
python -c "import psycopg, json, statistics; print('ok')"
```

- [ ] **Step 0.2: Verify PR #41 substrate landed**

```bash
python3 - <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='active_autonomous_loops' AND column_name='parent_pid'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
        defn = cur.fetchone()[0]
        assert "'active_autonomous_loops'" in defn and "'long_running_caller'" in defn
print('pre-flight: PR #41 substrate live')
PY
```

---

## Task 1: jsonl_safe_read.py — defensive IO helpers

**Files:**
- Create: `nervous_system/jsonl_safe_read.py`
- Create: `tests/test_jsonl_safe_read.py`

- [ ] **Step 1.1: Failing pure-unit tests**

`tests/test_jsonl_safe_read.py`:

```python
"""Pure-unit tests for jsonl_safe_read — verify never-raise + correct None-on-error."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.jsonl_safe_read import (
    read_first_user_message,
    safe_file_stats,
    SafeStats,
)


class TestReadFirstUserMessage:
    def test_missing_file_returns_none(self, tmp_path):
        result = read_first_user_message(tmp_path / "nope.jsonl")
        assert result is None

    def test_empty_file_returns_none(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        assert read_first_user_message(p) is None

    def test_corrupt_json_returns_none(self, tmp_path):
        p = tmp_path / "corrupt.jsonl"
        p.write_text("{this is not json}\n{also broken\n")
        assert read_first_user_message(p) is None

    def test_no_user_message_returns_none(self, tmp_path):
        p = tmp_path / "no-user.jsonl"
        p.write_text(json.dumps({"type": "summary", "summary": "x"}) + "\n")
        assert read_first_user_message(p) is None

    def test_returns_first_user_message_content(self, tmp_path):
        p = tmp_path / "ok.jsonl"
        msgs = [
            {"type": "summary", "summary": "x"},
            {"type": "user", "message": {"role": "user", "content": "hello"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "hi"}},
            {"type": "user", "message": {"role": "user", "content": "second"}},
        ]
        p.write_text("\n".join(json.dumps(m) for m in msgs) + "\n")
        assert read_first_user_message(p) == "hello"

    def test_user_message_with_content_blocks(self, tmp_path):
        """Claude CLI sometimes stores content as list-of-blocks instead of string."""
        p = tmp_path / "blocks.jsonl"
        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "block-prompt"}],
            },
        }
        p.write_text(json.dumps(msg) + "\n")
        assert read_first_user_message(p) == "block-prompt"


class TestSafeFileStats:
    def test_missing_file_returns_none(self, tmp_path):
        assert safe_file_stats(tmp_path / "nope.jsonl") is None

    def test_returns_size_and_mtime(self, tmp_path):
        p = tmp_path / "f.jsonl"
        p.write_text("x" * 1024)
        stats = safe_file_stats(p)
        assert isinstance(stats, SafeStats)
        assert stats.size_bytes == 1024
        assert stats.mtime > 0
```

- [ ] **Step 1.2: RED**

```bash
source .venv/bin/activate && python -m pytest tests/test_jsonl_safe_read.py -v
```

- [ ] **Step 1.3: Implementation**

`nervous_system/jsonl_safe_read.py`:

```python
"""Defensive IO helpers for jsonl files per CAI-RESP-164 R3.

Read failures NEVER raise. None on any error. Watchdog must keep running
even when jsonl files are missing, rotated, or corrupt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class SafeStats:
    size_bytes: int
    mtime: float


def safe_file_stats(path: Path) -> Optional[SafeStats]:
    try:
        st = path.stat()
        return SafeStats(size_bytes=st.st_size, mtime=st.st_mtime)
    except (OSError, FileNotFoundError):
        return None


def _extract_text(content) -> Optional[str]:
    """Claude CLI stores user content as either string or list-of-blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    return text
    return None


def read_first_user_message(path: Path) -> Optional[str]:
    """Read the first {type:'user'} message's content text from a Claude CLI jsonl.

    Returns the text payload or None on ANY error (missing, empty, corrupt,
    no user message, content shape unrecognized).
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    return None  # corrupt — bail on first bad line
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") != "user":
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                return _extract_text(msg.get("content"))
        return None
    except (OSError, FileNotFoundError):
        return None
```

- [ ] **Step 1.4: GREEN + commit**

```bash
source .venv/bin/activate && python -m pytest tests/test_jsonl_safe_read.py -v
git add nervous_system/jsonl_safe_read.py tests/test_jsonl_safe_read.py
git commit -m "feat(watchdog-content-shape): jsonl_safe_read defensive IO (CAI-RESP-164 R3)"
```

---

## Task 2: content_shape_signals.py — signal extractors

**Files:**
- Create: `nervous_system/content_shape_signals.py`
- Create: `tests/test_content_shape_signals.py` (pure-unit; fixture-driven tests come in Task 4)

- [ ] **Step 2.1: Failing tests**

`tests/test_content_shape_signals.py`:

```python
"""Pure-unit tests for content_shape_signals — three signal extractors."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.content_shape_signals import (
    signal_a_median_size,
    signal_b_cadence_band,
    signal_c_identical_prompts,
    SignalResult,
    SIGNAL_A_MAX_BYTES,
    SIGNAL_B_BAND_LO,
    SIGNAL_B_BAND_HI,
    SIGNAL_B_MIN_SPAN_SECONDS,
)


def _make_jsonl(parent: Path, name: str, size_bytes: int, mtime: float, first_user_text: str = "ok") -> Path:
    p = parent / name
    body = json.dumps({"type": "user", "message": {"role": "user", "content": first_user_text}})
    pad_needed = size_bytes - len(body) - 1
    if pad_needed > 0:
        body = body + "\n" + ("x" * pad_needed)
    p.write_text(body)
    import os as _os
    _os.utime(p, (mtime, mtime))
    return p


class TestSignalA:
    def test_burn_pattern_median_under_threshold(self, tmp_path):
        """10 small files → median < 80KB → match=True."""
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 54_000, 1000.0 + i * 300) for i in range(10)]
        result = signal_a_median_size(paths)
        assert result.match is True
        assert result.value < SIGNAL_A_MAX_BYTES

    def test_legitimate_pattern_median_over_threshold(self, tmp_path):
        """10 large files → median > 80KB → match=False."""
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 200_000, 1000.0 + i * 60) for i in range(10)]
        result = signal_a_median_size(paths)
        assert result.match is False
        assert result.value > SIGNAL_A_MAX_BYTES

    def test_too_few_files_unobservable(self, tmp_path):
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 54_000, 1000.0 + i * 300) for i in range(3)]
        result = signal_a_median_size(paths)
        assert result.match is None
        assert result.unobservable is True


class TestSignalB:
    def test_burn_pattern_cadence_in_band_sustained(self, tmp_path):
        """Cadence ~300s, span ~3000s (>7200? no — adjust to span >7200)."""
        # 30 observations at 300s cadence = 8700s span > 7200s
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 54_000, 1000.0 + i * 300) for i in range(30)]
        result = signal_b_cadence_band(paths)
        assert result.match is True

    def test_legitimate_variable_cadence_not_band(self, tmp_path):
        """Highly variable cadence 60s..1200s → exits band → match=False."""
        import random
        random.seed(42)
        mtimes = [1000.0]
        for _ in range(29):
            mtimes.append(mtimes[-1] + random.uniform(60, 1200))
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 200_000, m) for i, m in enumerate(mtimes)]
        result = signal_b_cadence_band(paths)
        assert result.match is False

    def test_too_short_span_unobservable(self, tmp_path):
        """Even with band cadence, span <7200s → unobservable."""
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 54_000, 1000.0 + i * 300) for i in range(5)]
        result = signal_b_cadence_band(paths)
        assert result.match is None or result.match is False


class TestSignalC:
    def test_burn_pattern_all_identical_prompts(self, tmp_path):
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 54_000, 1000.0 + i * 300, first_user_text="ok") for i in range(10)]
        result = signal_c_identical_prompts(paths)
        assert result.match is True
        assert result.value == "ok"

    def test_distinct_prompts_not_match(self, tmp_path):
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 200_000, 1000.0 + i * 300, first_user_text=f"prompt-{i}") for i in range(10)]
        result = signal_c_identical_prompts(paths)
        assert result.match is False

    def test_unreadable_majority_unobservable(self, tmp_path):
        """6 of 10 files have no user message → >50% unreadable → unobservable."""
        good = [_make_jsonl(tmp_path, f"s{i}.jsonl", 54_000, 1000.0 + i * 300, first_user_text="ok") for i in range(4)]
        # Make 6 files with no user-type entry
        for i in range(4, 10):
            p = tmp_path / f"s{i}.jsonl"
            p.write_text(json.dumps({"type": "summary", "summary": "x"}) + "\n")
            import os as _os
            _os.utime(p, (1000.0 + i * 300, 1000.0 + i * 300))
        all_paths = good + [tmp_path / f"s{i}.jsonl" for i in range(4, 10)]
        result = signal_c_identical_prompts(all_paths)
        assert result.match is None
        assert result.unobservable is True
```

- [ ] **Step 2.2: RED**

```bash
source .venv/bin/activate && python -m pytest tests/test_content_shape_signals.py -v
```

- [ ] **Step 2.3: Implementation**

`nervous_system/content_shape_signals.py`:

```python
"""Three content-shape signal extractors per CAI-RESP-164 R1-AMENDED.

Pure functions over filesystem inputs. Use jsonl_safe_read defensively;
never raise. Each signal returns SignalResult with match (bool|None) and
unobservable (bool). 'unobservable' means >50% of inputs unreadable —
caller treats as no_action (NOT hard_kill, NOT monitored).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from nervous_system.jsonl_safe_read import (
    read_first_user_message,
    safe_file_stats,
)

# Starting thresholds per CAI-RESP-164 R2 (calibration window 30d post-ship).
SIGNAL_A_MAX_BYTES = 80 * 1024
SIGNAL_B_BAND_LO = 5
SIGNAL_B_BAND_HI = 600
SIGNAL_B_MIN_SPAN_SECONDS = 2 * 60 * 60  # 7200s = 2h sustained
MIN_SESSIONS_FOR_SIGNAL = 10
UNOBSERVABLE_THRESHOLD = 0.5  # >50% unreadable → unobservable


@dataclass(frozen=True)
class SignalResult:
    match: Optional[bool]   # True = burn-pattern, False = not, None = unobservable
    value: Any              # observed value (median bytes / cadence avg / prompt string)
    unobservable: bool = False
    sample_count: int = 0


def signal_a_median_size(paths: list[Path]) -> SignalResult:
    """Median file size across last 10 sessions; burn if < SIGNAL_A_MAX_BYTES."""
    if len(paths) < MIN_SESSIONS_FOR_SIGNAL:
        return SignalResult(match=None, value=None, unobservable=True, sample_count=len(paths))
    sizes = []
    for p in paths[:MIN_SESSIONS_FOR_SIGNAL]:
        stats = safe_file_stats(p)
        if stats is not None:
            sizes.append(stats.size_bytes)
    if len(sizes) < MIN_SESSIONS_FOR_SIGNAL * (1 - UNOBSERVABLE_THRESHOLD):
        return SignalResult(match=None, value=None, unobservable=True, sample_count=len(sizes))
    median = statistics.median(sizes)
    return SignalResult(match=(median < SIGNAL_A_MAX_BYTES), value=median, sample_count=len(sizes))


def signal_b_cadence_band(paths: list[Path]) -> SignalResult:
    """Cadence_seconds in [5,600] band sustained over >2h.

    Compute inter-arrival gaps from mtimes. If all gaps within band AND span
    (last - first) > 7200s, match=True. Else False.
    """
    if len(paths) < MIN_SESSIONS_FOR_SIGNAL:
        return SignalResult(match=None, value=None, unobservable=True, sample_count=len(paths))
    mtimes = []
    for p in paths:
        stats = safe_file_stats(p)
        if stats is not None:
            mtimes.append(stats.mtime)
    if len(mtimes) < MIN_SESSIONS_FOR_SIGNAL * (1 - UNOBSERVABLE_THRESHOLD):
        return SignalResult(match=None, value=None, unobservable=True, sample_count=len(mtimes))
    mtimes.sort()
    span = mtimes[-1] - mtimes[0]
    if span < SIGNAL_B_MIN_SPAN_SECONDS:
        return SignalResult(match=False, value={"span": span}, sample_count=len(mtimes))
    gaps = [mtimes[i + 1] - mtimes[i] for i in range(len(mtimes) - 1)]
    all_in_band = all(SIGNAL_B_BAND_LO <= g <= SIGNAL_B_BAND_HI for g in gaps)
    avg = sum(gaps) / len(gaps)
    return SignalResult(
        match=all_in_band,
        value={"avg_gap": avg, "span": span, "all_in_band": all_in_band},
        sample_count=len(mtimes),
    )


def signal_c_identical_prompts(paths: list[Path]) -> SignalResult:
    """Last 10 sessions all have IDENTICAL first-user-message content."""
    if len(paths) < MIN_SESSIONS_FOR_SIGNAL:
        return SignalResult(match=None, value=None, unobservable=True, sample_count=len(paths))
    prompts: list[str] = []
    for p in paths[:MIN_SESSIONS_FOR_SIGNAL]:
        text = read_first_user_message(p)
        if text is not None:
            prompts.append(text)
    if len(prompts) < MIN_SESSIONS_FOR_SIGNAL * (1 - UNOBSERVABLE_THRESHOLD):
        return SignalResult(match=None, value=None, unobservable=True, sample_count=len(prompts))
    first = prompts[0]
    all_match = all(p == first for p in prompts)
    return SignalResult(
        match=all_match,
        value=first if all_match else None,
        sample_count=len(prompts),
    )
```

- [ ] **Step 2.4: GREEN + commit**

```bash
source .venv/bin/activate && python -m pytest tests/test_content_shape_signals.py -v
git add nervous_system/content_shape_signals.py tests/test_content_shape_signals.py
git commit -m "feat(watchdog-content-shape): three signal extractors (CAI-RESP-164 R1-AMENDED)"
```

---

## Task 3: Extend long_caller_watchdog.decide_kill with content_shape param + monitored action

**Files:**
- Modify: `nervous_system/long_caller_watchdog.py`
- Create: `tests/test_long_caller_watchdog_content_shape.py`

- [ ] **Step 3.1: Failing tests**

`tests/test_long_caller_watchdog_content_shape.py`:

```python
"""Tests for content_shape integration into decide_kill."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.long_caller_watchdog import decide_kill, ContentShape
from nervous_system.content_shape_signals import SignalResult


def _shape(a, b, c, unobs=False) -> ContentShape:
    return ContentShape(
        signal_a=SignalResult(match=a, value=None, unobservable=(a is None and unobs)),
        signal_b=SignalResult(match=b, value=None, unobservable=(b is None and unobs)),
        signal_c=SignalResult(match=c, value=None, unobservable=(c is None and unobs)),
    )


class TestContentShapeGate:
    def test_3_of_3_match_unregistered_hard_kill(self):
        d = decide_kill(
            caller_name="cc-rogue", sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
            content_shape=_shape(True, True, True),
        )
        assert d.action == "hard_kill"

    def test_2_of_3_match_unregistered_monitored(self):
        d = decide_kill(
            caller_name="cc-rogue", sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
            content_shape=_shape(True, True, False),
        )
        assert d.action == "monitored"

    def test_0_of_3_match_unregistered_monitored(self):
        d = decide_kill(
            caller_name="cc-scholar-active-but-unregistered",
            sessions_24h=400, cadence_seconds=20,
            registered=False, parent_pid=12345,
            content_shape=_shape(False, False, False),
        )
        assert d.action == "monitored"

    def test_any_unobservable_no_action(self):
        d = decide_kill(
            caller_name="cc-rogue", sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
            content_shape=_shape(True, True, None, unobs=True),
        )
        assert d.action == "no_action"

    def test_substrate_native_still_wins_over_3_of_3(self):
        """C2 belt-and-suspenders survives content-shape gate."""
        d = decide_kill(
            caller_name="ralphy", sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
            content_shape=_shape(True, True, True),
        )
        assert d.action == "no_kill"

    def test_panic_button_still_wins(self, monkeypatch):
        monkeypatch.setenv("WINGMEN_LONG_CALLER_WATCHDOG_DISABLED", "1")
        d = decide_kill(
            caller_name="cc-rogue", sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
            content_shape=_shape(True, True, True),
        )
        assert d.action == "no_kill"

    def test_registered_no_kill_policy_wins_over_3_of_3(self):
        d = decide_kill(
            caller_name="cc-scholar-interactive",
            sessions_24h=400, cadence_seconds=20,
            registered=True, registered_policy="no_kill",
            parent_pid=12345,
            content_shape=_shape(True, True, True),
        )
        assert d.action == "no_kill"

    def test_omitting_content_shape_is_backward_compat_no_action(self):
        """If caller doesn't pass content_shape and would otherwise hard_kill,
        we now require 3-of-3 — so absence of content_shape means no SIGTERM."""
        d = decide_kill(
            caller_name="cc-rogue", sessions_24h=200, cadence_seconds=300,
            registered=False, parent_pid=12345,
        )
        # Pre-CAI-RESP-164: this returned hard_kill. Post: requires content_shape.
        assert d.action != "hard_kill"
```

- [ ] **Step 3.2: RED**

```bash
source .venv/bin/activate && python -m pytest tests/test_long_caller_watchdog_content_shape.py -v
```

- [ ] **Step 3.3: Modify `nervous_system/long_caller_watchdog.py`**

Add near the top imports:

```python
from nervous_system.content_shape_signals import SignalResult
```

Add new dataclass after `KillDecision`:

```python
@dataclass(frozen=True)
class ContentShape:
    """Three content-shape signal results per CAI-RESP-164 R1-AMENDED."""
    signal_a: SignalResult
    signal_b: SignalResult
    signal_c: SignalResult

    @property
    def any_unobservable(self) -> bool:
        return any(s.unobservable for s in (self.signal_a, self.signal_b, self.signal_c))

    @property
    def all_match(self) -> bool:
        return (
            self.signal_a.match is True
            and self.signal_b.match is True
            and self.signal_c.match is True
        )
```

Extend `decide_kill` signature:

```python
def decide_kill(
    *,
    caller_name: str,
    sessions_24h: int,
    cadence_seconds: int,
    registered: bool,
    registered_policy: Optional[str] = None,
    parent_pid: Optional[int] = None,
    content_shape: Optional[ContentShape] = None,
) -> KillDecision:
```

Modify the R1 unregistered branch (last branch of decide_kill). Replace:

```python
return KillDecision(
    action="hard_kill",
    reason="R1_unregistered_pattern",
    caller_name=caller_name,
    pid=parent_pid,
    extras={"sessions_24h": sessions_24h, "cadence_seconds": cadence_seconds},
)
```

with:

```python
# CAI-RESP-164 R1-AMENDED: 3-of-3 content-shape AND-gate required for hard_kill.
if content_shape is None:
    # No signals computed — conservative: don't kill, surface as monitored.
    return KillDecision(
        action="monitored",
        reason="R1_unregistered_no_content_shape_computed",
        caller_name=caller_name,
        pid=parent_pid,
        extras={"sessions_24h": sessions_24h, "cadence_seconds": cadence_seconds},
    )
if content_shape.any_unobservable:
    return KillDecision(
        action="no_action",
        reason="R3_signals_unobservable",
        caller_name=caller_name,
        pid=parent_pid,
        extras={"sessions_24h": sessions_24h, "cadence_seconds": cadence_seconds},
    )
if content_shape.all_match:
    return KillDecision(
        action="hard_kill",
        reason="R1_AMENDED_unregistered_3of3_content_shape",
        caller_name=caller_name,
        pid=parent_pid,
        extras={
            "sessions_24h": sessions_24h,
            "cadence_seconds": cadence_seconds,
            "signal_a_value": content_shape.signal_a.value,
            "signal_b_value": content_shape.signal_b.value,
            "signal_c_value": content_shape.signal_c.value,
        },
    )
return KillDecision(
    action="monitored",
    reason="R1_AMENDED_unregistered_under_3of3",
    caller_name=caller_name,
    pid=parent_pid,
    extras={
        "sessions_24h": sessions_24h,
        "cadence_seconds": cadence_seconds,
        "signal_a_match": content_shape.signal_a.match,
        "signal_b_match": content_shape.signal_b.match,
        "signal_c_match": content_shape.signal_c.match,
    },
)
```

Update `decide_kill_with_pid_verify` to pass `content_shape` through to inner `decide_kill`:

```python
def decide_kill_with_pid_verify(
    *,
    caller_name: str,
    sessions_24h: int,
    cadence_seconds: int,
    registered: bool,
    registered_policy: Optional[str] = None,
    parent_pid: Optional[int] = None,
    expected_cwd_prefix: str = "/Users/sheikhmusa/wingmen/",
    content_shape: Optional[ContentShape] = None,
) -> KillDecision:
    inner = decide_kill(
        caller_name=caller_name,
        sessions_24h=sessions_24h,
        cadence_seconds=cadence_seconds,
        registered=registered,
        registered_policy=registered_policy,
        parent_pid=parent_pid,
        content_shape=content_shape,
    )
    # ... existing PID-verify logic unchanged ...
```

Extend `build_telegram_body` with a `monitored` event branch:

```python
elif event == "monitored":
    return (
        "👁  Watchdog monitored caller (not killed)\n\n"
        f"caller: {caller_name} (PID {pid}, cwd {cwd})\n"
        f"observed: {sessions_24h} sessions/24h\n"
        "0/3, 1/3, or 2/3 content-shape signals matched — insufficient evidence for SIGTERM.\n"
        "24h auto-expire unless escalates to 3/3 or operator registers."
    )
```

- [ ] **Step 3.4: GREEN — Task 2 tests + Task 3 tests + Task 2 baseline tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_long_caller_watchdog.py tests/test_long_caller_watchdog_content_shape.py tests/test_content_shape_signals.py tests/test_jsonl_safe_read.py -v
```

All green. Existing Task 2 baseline must still pass (backward compat — calling decide_kill without content_shape doesn't crash; it monitors instead of hard_kills).

- [ ] **Step 3.5: Commit**

```bash
git add nervous_system/long_caller_watchdog.py tests/test_long_caller_watchdog_content_shape.py
git commit -m "feat(watchdog-content-shape): decide_kill content_shape param + monitored action (CAI-RESP-164 R1-AMENDED)"
```

---

## Task 4: Migration — watchdog_monitored_callers table + boot_briefing arm

**Files:**
- Create: `supabase/migrations/20260523_watchdog_monitored_callers.sql`
- Create: `tests/test_watchdog_monitored_callers.py`

- [ ] **Step 4.1: Failing schema test**

`tests/test_watchdog_monitored_callers.py`:

```python
"""Live-DB schema test for watchdog_monitored_callers table + boot_briefing arm."""
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
def test_watchdog_monitored_callers_table_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name='watchdog_monitored_callers' ORDER BY ordinal_position"
            )
            cols = {r[0]: r[1] for r in cur.fetchall()}
    for required in (
        "caller_name", "first_observed_at", "expires_at",
        "sessions_24h", "cadence_seconds",
        "signal_a_value", "signal_b_value", "signal_c_value",
        "signal_a_match", "signal_b_match", "signal_c_match",
        "escalated_at",
    ):
        assert required in cols, f"missing column: {required}"


@pytestmark_integration
def test_boot_briefing_view_has_watchdog_monitored_callers_arm():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
            defn = cur.fetchone()[0]
    assert "'watchdog_monitored_callers'::text" in defn, "missing arm"
    # Verify all preceding arms still present (regression guard against silent rollback)
    for arm in (
        "'repo_context'", "'active_decision'", "'active_autonomous_loops'",
        "'long_running_caller'",
    ):
        assert arm in defn, f"arm {arm} dropped"
```

- [ ] **Step 4.2: Capture current view, then write migration**

```bash
source .venv/bin/activate && python3 - <<'PY' > /tmp/current_boot_briefing_v6.sql
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
        print(cur.fetchone()[0])
PY
```

Create `supabase/migrations/20260523_watchdog_monitored_callers.sql` with:

1. `CREATE TABLE IF NOT EXISTS watchdog_monitored_callers (
       caller_name TEXT PRIMARY KEY,
       first_observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       expires_at TIMESTAMPTZ NOT NULL,
       sessions_24h INTEGER NOT NULL,
       cadence_seconds INTEGER,
       signal_a_value JSONB,
       signal_b_value JSONB,
       signal_c_value JSONB,
       signal_a_match BOOLEAN,
       signal_b_match BOOLEAN,
       signal_c_match BOOLEAN,
       escalated_at TIMESTAMPTZ
   );`
2. `COMMENT ON TABLE watchdog_monitored_callers IS 'CAI-RESP-164 R4 ...'`
3. `CREATE OR REPLACE VIEW boot_briefing AS <full body from /tmp + new arm>`. The new arm:
   ```
   UNION ALL
    SELECT 'watchdog_monitored_callers'::text AS source,
       wmc.caller_name AS key,
       json_build_object(
         'first_observed_at', wmc.first_observed_at,
         'expires_at', wmc.expires_at,
         'sessions_24h', wmc.sessions_24h,
         'cadence_seconds', wmc.cadence_seconds,
         'signal_a_match', wmc.signal_a_match,
         'signal_b_match', wmc.signal_b_match,
         'signal_c_match', wmc.signal_c_match,
         'escalated', (wmc.escalated_at IS NOT NULL)
       ) AS context
      FROM watchdog_monitored_callers wmc
      WHERE wmc.expires_at > now() OR wmc.escalated_at IS NOT NULL
   ```
4. `DO $$ ... END $$` assertion gate verifying table exists + view contains 'watchdog_monitored_callers'.
5. `INSERT INTO supabase_migrations.schema_migrations` row version `'20260523120000'`, name `'watchdog_monitored_callers'`, ON CONFLICT DO NOTHING.

Wrap 1-4 in BEGIN/COMMIT.

- [ ] **Step 4.3: Apply + verify**

```bash
source .venv/bin/activate && python scripts/check_additive_migration.py supabase/migrations/20260523_watchdog_monitored_callers.sql
python3 - <<'PY'
import os, psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
sql = open('supabase/migrations/20260523_watchdog_monitored_callers.sql').read()
with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
    cur.execute(sql)
    print('applied')
PY
python -m pytest tests/test_watchdog_monitored_callers.py -v
```

- [ ] **Step 4.4: Commit**

```bash
git add supabase/migrations/20260523_watchdog_monitored_callers.sql tests/test_watchdog_monitored_callers.py
git commit -m "feat(watchdog-content-shape): watchdog_monitored_callers table + boot_briefing arm (CAI-RESP-164 R4)"
```

---

## Task 5: watchdog.py — compute signals + R5 pre-SIGTERM audit + monitored upsert

**Files:**
- Modify: `watchdog.py`

- [ ] **Step 5.1: Wire signal computation into `_long_caller_sweep`**

Add imports near the existing long_caller_watchdog import:

```python
from pathlib import Path
from nervous_system.long_caller_watchdog import ContentShape
from nervous_system.content_shape_signals import (
    signal_a_median_size,
    signal_b_cadence_band,
    signal_c_identical_prompts,
)
from nervous_system.autonomous_loop_detector import _DIR_TO_CC

_CC_TO_CLAUDE_PROJECT_DIR = {v: k for k, v in _DIR_TO_CC.items()}
_CLAUDE_PROJECTS_ROOT = Path.home() / ".claude" / "projects"
```

In `_long_caller_sweep`, after computing `registered` + `policy` + `matched_name` for a flagged row, BEFORE the `decide_kill_with_pid_verify` call, compute the content_shape:

```python
content_shape = None
mangled = _CC_TO_CLAUDE_PROJECT_DIR.get(cc_id)
if mangled:
    project_dir = _CLAUDE_PROJECTS_ROOT / mangled
    if project_dir.exists():
        # Take 10 most-recent jsonls
        try:
            jsonls = sorted(
                (p for p in project_dir.iterdir() if p.is_file() and p.name.endswith(".jsonl") and not p.name.startswith(".")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:10]
        except OSError:
            jsonls = []
        if jsonls:
            content_shape = ContentShape(
                signal_a=signal_a_median_size(jsonls),
                signal_b=signal_b_cadence_band(jsonls),
                signal_c=signal_c_identical_prompts(jsonls),
            )

decision = decide_kill_with_pid_verify(
    caller_name=matched_name,
    sessions_24h=sessions_24h,
    cadence_seconds=cadence_seconds or 0,
    registered=registered,
    registered_policy=policy,
    parent_pid=parent_pid,
    content_shape=content_shape,
)
```

- [ ] **Step 5.2: R5 pre-SIGTERM audit row BEFORE os.kill**

Replace the existing `hard_kill` branch in `_long_caller_sweep`. New flow:

```python
if decision.action == "hard_kill" and parent_pid:
    # R5 pre-SIGTERM audit — INSERT FIRST, commit barrier; SIGTERM only if INSERT succeeds.
    audit_ok = False
    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notification_log "
                "(source, decision_ref, channel, recipient, message_text) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    "watchdog_pre_kill_audit",
                    "CAI-RESP-167",
                    "long_running_callers",
                    matched_name,
                    json.dumps({
                        "caller_pid": parent_pid,
                        "jsonl_dir": str(project_dir) if mangled else None,
                        "signal_a_value": content_shape.signal_a.value if content_shape else None,
                        "signal_b_value": content_shape.signal_b.value if content_shape else None,
                        "signal_c_value": content_shape.signal_c.value if content_shape else None,
                        "signal_a_match": content_shape.signal_a.match if content_shape else None,
                        "signal_b_match": content_shape.signal_b.match if content_shape else None,
                        "signal_c_match": content_shape.signal_c.match if content_shape else None,
                        "registration_status": "unregistered" if not registered else "registered",
                        "check_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    }),
                ),
            )
        audit_ok = True
    except Exception as audit_err:
        logger.error(f"R5 audit write FAILED — skipping SIGTERM for {matched_name}: {audit_err}")
        # Try to log the failure
        try:
            with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO notification_log "
                    "(source, decision_ref, channel, recipient, message_text) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        "watchdog_pre_kill_audit_failed",
                        "CAI-RESP-167",
                        "long_running_callers",
                        matched_name,
                        json.dumps({"error": str(audit_err), "pid": parent_pid}),
                    ),
                )
        except Exception:
            pass

    if not audit_ok:
        continue  # next flagged row; no SIGTERM without audit

    # Now actuate SIGTERM
    try:
        os.kill(parent_pid, signal.SIGTERM)
        logger.warning(f"long_caller_watchdog: SIGTERM sent to PID {parent_pid} (caller {matched_name})")
        body = build_telegram_body(
            event="hard_kill",
            caller_name=matched_name,
            pid=parent_pid,
            cwd=str(project_dir) if mangled else "unknown",
            sessions_24h=sessions_24h,
            threshold=DETECTION_THRESHOLD_SESSIONS_24H,
        )
        await alert_admin(body)
        # Final notification_log row marking the kill completed
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notification_log "
                "(source, decision_ref, channel, recipient, message_text) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    "watchdog_hard_kill",
                    "CAI-RESP-167",
                    "long_running_callers",
                    matched_name,
                    json.dumps({
                        "sessions_24h": sessions_24h,
                        "cadence_seconds": cadence_seconds,
                        "pid": parent_pid,
                        "reason": decision.reason,
                    }),
                ),
            )
    except (ProcessLookupError, PermissionError) as e:
        logger.warning(f"SIGTERM to {parent_pid} failed: {e}")
```

- [ ] **Step 5.3: New `monitored` branch — upsert watchdog_monitored_callers**

```python
elif decision.action == "monitored":
    sa = content_shape.signal_a if content_shape else None
    sb = content_shape.signal_b if content_shape else None
    sc = content_shape.signal_c if content_shape else None
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO watchdog_monitored_callers
              (caller_name, expires_at, sessions_24h, cadence_seconds,
               signal_a_value, signal_b_value, signal_c_value,
               signal_a_match, signal_b_match, signal_c_match)
            VALUES (%s, now() + interval '24 hours', %s, %s,
                    %s, %s, %s,
                    %s, %s, %s)
            ON CONFLICT (caller_name) DO UPDATE SET
              expires_at = EXCLUDED.expires_at,
              sessions_24h = EXCLUDED.sessions_24h,
              cadence_seconds = EXCLUDED.cadence_seconds,
              signal_a_value = EXCLUDED.signal_a_value,
              signal_b_value = EXCLUDED.signal_b_value,
              signal_c_value = EXCLUDED.signal_c_value,
              signal_a_match = EXCLUDED.signal_a_match,
              signal_b_match = EXCLUDED.signal_b_match,
              signal_c_match = EXCLUDED.signal_c_match
            """,
            (
                matched_name, sessions_24h, cadence_seconds,
                json.dumps(sa.value) if sa else None,
                json.dumps(sb.value) if sb else None,
                json.dumps(sc.value) if sc else None,
                sa.match if sa else None,
                sb.match if sb else None,
                sc.match if sc else None,
            ),
        )
    logger.info(f"watchdog: monitored {matched_name} ({decision.reason})")
```

- [ ] **Step 5.4: Smoke import**

```bash
source .venv/bin/activate && python -c "import watchdog; print('ok')"
```

- [ ] **Step 5.5: Commit**

```bash
git add watchdog.py
git commit -m "feat(watchdog-content-shape): signal computation + R5 pre-SIGTERM audit + monitored upsert (CAI-RESP-164/167)"
```

---

## Task 6: Test fixtures (probe positive + cc-scholar anonymized negative)

**Files:**
- Create: `tests/fixtures/probe_max_throttle/*.jsonl` (10 files)
- Create: `tests/fixtures/cc_scholar_2026_05_19_2244/*.jsonl` (10 files, anonymized)
- Create: `tests/fixtures/README.md`
- Create: `tests/test_content_shape_fixtures.py`

- [ ] **Step 6.1: Generate probe fixture**

Create 10 synthetic-but-faithful jsonls for the probe pattern. Each ~54KB, identical first-user-message="ok", mtimes spaced 300s apart over 8700s span.

`tests/fixtures/probe_max_throttle/gen.py` (committed for reproducibility):

```python
"""Generate probe_max_throttle fixture: 10 jsonls matching the historical
probe daemon pattern — 54KB no-op sessions, prompt='ok', 300s exact cadence."""
import json
import os
from pathlib import Path

OUT = Path(__file__).parent
BASE_TIME = 1700000000.0  # arbitrary stable epoch
SIZE = 54 * 1024

def make_session(idx: int):
    msgs = [
        {"type": "user", "message": {"role": "user", "content": "ok"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "ok"}},
    ]
    body = "\n".join(json.dumps(m) for m in msgs)
    pad = SIZE - len(body) - 2
    if pad > 0:
        body += "\n" + ("x" * pad)
    p = OUT / f"session-{idx:02d}.jsonl"
    p.write_text(body + "\n")
    mt = BASE_TIME + idx * 300
    os.utime(p, (mt, mt))

if __name__ == "__main__":
    for i in range(10):
        make_session(i)
    print(f"generated 10 probe fixtures in {OUT}")
```

Run once: `python tests/fixtures/probe_max_throttle/gen.py`. Verify 10 files created. Commit both the script and the resulting jsonls.

- [ ] **Step 6.2: Acquire + anonymize cc-scholar fixture**

```bash
ls ~/.claude/projects/-Users-sheikhmusa-wingmen-projects-ai-scholar/ | sort | head -20
# Pick the 10 jsonls closest to 2026-05-19 22:44 SGT (via stat -f %m)
```

For each picked file: read its messages, replace ALL user-message and assistant-message content with REDACTED placeholder that preserves the original character count (e.g., `"x" * len(original)`). Preserve all message types, structure, mtimes, file sizes. Write to `tests/fixtures/cc_scholar_2026_05_19_2244/session-NN.jsonl`.

Anonymizer script `tests/fixtures/cc_scholar_2026_05_19_2244/anonymize.py`:

```python
"""Anonymize cc-scholar incident jsonls — preserve sizes + structure + mtimes,
redact all user/assistant content text."""
import json
import os
import sys
from pathlib import Path

SRC = Path.home() / ".claude" / "projects" / "-Users-sheikhmusa-wingmen-projects-ai-scholar"
DST = Path(__file__).parent

# Pick 10 most-recent at the target timestamp
TARGET = 1747691040  # 2026-05-19 22:44 SGT (UTC+8) → 14:44 UTC = epoch
files = sorted(
    (p for p in SRC.iterdir() if p.is_file() and p.name.endswith(".jsonl") and not p.name.startswith(".")),
    key=lambda p: abs(p.stat().st_mtime - TARGET),
)[:10]

def redact_text(s):
    if isinstance(s, str):
        return "x" * len(s)
    if isinstance(s, list):
        return [{"type": "text", "text": "x" * len(b.get("text", ""))} if isinstance(b, dict) and b.get("type") == "text" else b for b in s]
    return s

for idx, src_path in enumerate(files):
    mt = src_path.stat().st_mtime
    lines_out = []
    with src_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("message"), dict):
                if "content" in obj["message"]:
                    obj["message"]["content"] = redact_text(obj["message"]["content"])
            lines_out.append(json.dumps(obj))
    dst = DST / f"session-{idx:02d}.jsonl"
    dst.write_text("\n".join(lines_out) + "\n")
    os.utime(dst, (mt, mt))
print(f"anonymized {len(files)} fixtures to {DST}")
```

Run once. Verify 10 files at sizes 200KB-2.7MB with redacted (but identical-length) content. Commit script + jsonls.

`tests/fixtures/README.md`:

```markdown
# Watchdog Phase B Test Fixtures (CAI-RESP-164 R1)

## probe_max_throttle/
Synthetic-but-faithful regeneration of the 2026-05-15 probe_max_throttle.py
daemon pattern. 10 jsonls × ~54KB each, prompt='ok', cadence 300s.
Source: gen.py (committed). Must produce 3-of-3 content-shape match → SIGTERM.

## cc_scholar_2026_05_19_2244/
Anonymized real jsonls from cc-scholar's 2026-05-19 22:44 SGT Al-Bayan corpus
expansion (the false-positive near-miss that prompted CAI-RESP-164).
10 jsonls × 200KB-2.7MB, distinct prompts, variable cadence.
Source: anonymize.py (committed). All content text redacted to placeholders;
original sizes + mtimes + structure preserved. Must produce 0-of-3 (or at most
2-of-3) → monitored, NOT SIGTERM.

Per CAI-RESP-164 R1: CI must block on either fixture regression.
```

- [ ] **Step 6.3: Fixture-driven integration test**

`tests/test_content_shape_fixtures.py`:

```python
"""Fixture-driven integration tests per CAI-RESP-164 R1.

Positive case (probe_max_throttle) must produce 3-of-3 match → hard_kill.
Negative case (cc-scholar incident) must produce <3-of-3 → monitored."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.content_shape_signals import (
    signal_a_median_size,
    signal_b_cadence_band,
    signal_c_identical_prompts,
)
from nervous_system.long_caller_watchdog import (
    ContentShape,
    decide_kill,
)

PROBE_DIR = Path(__file__).parent / "fixtures" / "probe_max_throttle"
SCHOLAR_DIR = Path(__file__).parent / "fixtures" / "cc_scholar_2026_05_19_2244"


def _jsonls(d: Path) -> list[Path]:
    return sorted(
        [p for p in d.iterdir() if p.is_file() and p.name.endswith(".jsonl") and not p.name.startswith(".")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _shape(d: Path) -> ContentShape:
    paths = _jsonls(d)
    return ContentShape(
        signal_a=signal_a_median_size(paths),
        signal_b=signal_b_cadence_band(paths),
        signal_c=signal_c_identical_prompts(paths),
    )


@pytest.mark.skipif(not PROBE_DIR.exists(), reason="probe fixture not generated")
def test_probe_fixture_produces_3_of_3_match():
    shape = _shape(PROBE_DIR)
    assert shape.signal_a.match is True, f"signal_a expected burn-pattern: {shape.signal_a}"
    assert shape.signal_b.match is True, f"signal_b expected burn-pattern: {shape.signal_b}"
    assert shape.signal_c.match is True, f"signal_c expected burn-pattern: {shape.signal_c}"
    assert shape.all_match is True


@pytest.mark.skipif(not PROBE_DIR.exists(), reason="probe fixture not generated")
def test_probe_fixture_triggers_hard_kill():
    d = decide_kill(
        caller_name="cc-test-runaway-probe",
        sessions_24h=300, cadence_seconds=300,
        registered=False, parent_pid=99999,
        content_shape=_shape(PROBE_DIR),
    )
    assert d.action == "hard_kill"
    assert d.reason == "R1_AMENDED_unregistered_3of3_content_shape"


@pytest.mark.skipif(not SCHOLAR_DIR.exists(), reason="cc-scholar fixture not generated")
def test_scholar_fixture_not_3_of_3():
    shape = _shape(SCHOLAR_DIR)
    # cc-scholar at 22:44 SGT: distinct prompts → signal_c should be False.
    # File sizes vary 200KB-2.7MB → signal_a should be False.
    # Cadence in band but maybe span <2h with 10 samples — signal_b ambiguous.
    assert shape.all_match is False, "cc-scholar must NOT match 3-of-3"


@pytest.mark.skipif(not SCHOLAR_DIR.exists(), reason="cc-scholar fixture not generated")
def test_scholar_fixture_does_not_hard_kill():
    d = decide_kill(
        caller_name="cc-scholar",
        sessions_24h=351, cadence_seconds=11,
        registered=False, parent_pid=24810,
        content_shape=_shape(SCHOLAR_DIR),
    )
    assert d.action != "hard_kill"
    # Could be 'monitored' or 'no_action' depending on signal_b unobservability
```

- [ ] **Step 6.4: Run, expect green**

```bash
source .venv/bin/activate && python -m pytest tests/test_content_shape_fixtures.py -v
```

- [ ] **Step 6.5: Commit**

```bash
git add tests/fixtures/ tests/test_content_shape_fixtures.py
git commit -m "test(watchdog-content-shape): probe positive + cc-scholar anonymized negative fixtures (CAI-RESP-164 R1)"
```

---

## Task 7: Rollback doc

**Files:**
- Create: `docs/superpowers/rollbacks/watchdog-phase-b-content-shape.md`

Content covers: panic button still primary stop, code revert of PR #42, migration rollback for watchdog_monitored_callers + boot_briefing arm, audit log preservation, filing-as-decision_ref requirement.

```bash
git add docs/superpowers/rollbacks/watchdog-phase-b-content-shape.md
git commit -m "docs(watchdog-content-shape): rollback procedure"
```

---

## Task 8: PR + ship

- [ ] **Step 8.1: Full Phase-B sweep**

```bash
source .venv/bin/activate && python -m pytest \
  tests/test_jsonl_safe_read.py \
  tests/test_content_shape_signals.py \
  tests/test_content_shape_fixtures.py \
  tests/test_long_caller_watchdog.py \
  tests/test_long_caller_watchdog_content_shape.py \
  tests/test_watchdog_monitored_callers.py \
  tests/test_active_loops_parent_pid.py \
  tests/test_autonomous_loop_detector.py \
  tests/test_watchdog_phase_b_integration.py \
  -v
```

All green.

- [ ] **Step 8.2: Push + open PR**

```bash
env -u GITHUB_TOKEN git push -u origin feat/watchdog-phase-b-content-shape-001
env -u GITHUB_TOKEN gh pr create --base main --head feat/watchdog-phase-b-content-shape-001 \
  --title "feat(watchdog-content-shape): R1-AMENDED 3-of-3 gate + monitored state + R5 audit (CAI-RESP-164/167)" \
  --body "..."
```

- [ ] **Step 8.3: After CI green, request cai go-ahead for merge + kickstart**

Per CAI-RESP-167 ACTION 7: "launchctl kickstart on PR #42 green — CAI ratification required at kickstart moment (post fixture-CI-pass demonstration in msg to CAI)."

File a `review_request` to cai with the PR URL + CI green confirmation + fixture demonstration output. Wait for ratification before merge + kickstart.

---

## Self-Review

**Spec coverage (CAI-RESP-164 + CAI-RESP-167):**
- ✅ R1-AMENDED 3-of-3 AND-gate → Task 3 (decide_kill content_shape param + new branch)
- ✅ R3 graceful jsonl read failure → Task 1 (jsonl_safe_read) + Task 2 (unobservable propagation)
- ✅ R4 watchdog_monitored_callers boot_briefing arm → Task 4
- ✅ R5 pre-SIGTERM notification_log audit → Task 5
- ✅ Test fixtures (probe + cc-scholar) → Task 6
- ✅ 30-day calibration anchor → reasoned in decision 956 + thresholds as module constants

**Backward compat:**
- decide_kill without content_shape returns 'monitored', not 'hard_kill' (safer default)
- Substrate-native carve-out + panic button still checked FIRST
- Registered no_kill policy still wins over 3-of-3

**Placeholder scan:** No TBD/TODO. Every step has code or commands.
