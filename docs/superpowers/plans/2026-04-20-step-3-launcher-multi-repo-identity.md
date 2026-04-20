# Step 3 — Launcher Revision (Multi-Repo + Dual-Identity + Opus 4.7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revise `scripts/launch_dangerous_cc.sh` to compose msgs 315 (multi-repo scope), 317 (auto-ID auto-pick within a registered family), and 324 (Opus 4.7 default) into a single launcher path, with a dual-identity convention (sub-tag for per-instance `agent_status` + GUC, base for FK-enforced `agent_messages.from_agent`) that defers first-class sub-identity promotion to Step 4 (BUG-024 Phase 1).

**Architecture:**
- New Python helper `scripts/lib/auto_agent_id.py` owns: pwd→base-family mapping (fail-fast ABORT on unrecognized), advisory-lock sub-tag allocation with stale-slot reclaim, and overlap-warning scan of active siblings. Pure functions are unit-tested; the one DB-touching function is integration-tested against the real Supabase project in a SAVEPOINT/ROLLBACK harness (same pattern as Step 2's `verify_governance_hygiene_batch.py`).
- `scripts/launch_dangerous_cc.sh` is edited surgically: new CLI `--repo` arg, call into helper to produce `CC_AGENT_ID` (sub-tag) + `CC_BASE_AGENT_ID` (base) + `SCOPE_REPOS`, all three exported for child processes. ARCH-035 `agent_status` UPSERT uses sub-tag + GUC. All `agent_messages.from_agent` write sites (3 today) switch to base. Exit trap split along the same axis.
- Zero migrations. No schema changes. No new tables.

**Tech Stack:** Python 3.9 (orchestrator venv), psycopg (direct PG for GUC semantics), pytest + unittest.mock, bash 5, Supabase, Claude Code CLI.

**Self-surgery meta:** this session (cc-ihsanos-3) edits the launcher code that spawns cc-ihsanos-N. Edits take effect on NEXT launch, not this session. Task 11 smoke verifies the first next-launch reads the revised launcher correctly. If cc-ihsanos-3 itself exits before Task 12 closes, verify the next cc-ihsanos boot picks up the delta cleanly.

**Worktree-naming convention (delta-v2 L1-B1 + CAI msg 407):** directories created as git worktrees MUST use one of two naming forms so `strip_worktree_suffix` can distinguish them from canonical repo names:
- uppercase-initial token after `-` or `.`: e.g. `orchestrator-LEDGER`, `orchestrator-HOTFIX`, `orchestrator.QURBAN`
- lowercase `wt` prefix after `-` or `.`: e.g. `orchestrator.wt-qurban`, `ihsanos.wt-abc123`

Lowercase suffixes (`orchestrator-hotfix`) are treated as canonical repo names, not worktrees — they will `UnknownRepoError` on pwd resolution. Canonical hyphenated repo names (`hifz-companion`, `ai-scholar`, `cosem-tdu`) are preserved by this convention since they start with lowercase.

**Deferred to Step 4 (BUG-024 Phase 1) — NOT in scope here:**
- Promoting sub-tags to first-class `agents.id` rows (currently FK blocks this).
- `agents.last_heartbeat` semantics redesign — today all cc-ihsanos-N stomp the same `agents.cc-ihsanos` heartbeat row. Acceptable noise for Step 3; cleaner split lands in Phase 1 where `agent_status.last_heartbeat` becomes per-instance truth.
- RLS on `agent_status` writes (ARCH-034 activation).

---

## Scope & Context

**Parent thread:** GOVERNANCE-CLEANUP-001 (msg 339 onwards).
**Inputs:**
- msg 315 — multi-repo session scope (cd + `--repo` + scope_repos propagation)
- msg 317 — auto-identity within a family (no more manual `CC_AGENT_ID=cc-ihsanos-2` env)
- msg 324 — Opus 4.7 as the hardcoded default model with env override
- msg 394 (cc-ihsanos-3 → cai) — pre-plan Qs with leans
- msg 395 (cai → cc-ihsanos-3) — approved all 3 leans + added: mapping-table constraint, exit-trap preservation, dual-identity formalization

**Out of scope (deferred to Step 4):**
- Making sub-tags first-class `agents.id` rows (currently the `agents` FK blocks this, which is BUG-024 Phase 1's remit).
- RLS on `agent_status` writes (ARCH-034 activation).
- Per-repo STATUS.md pointer in `boot_briefing` (msg 315 item B — filed as separate follow-up task once Step 3 ships).

**Invariants this plan must preserve:**
- ARCH-035 GUC tripwire `enforce_agent_status_identity` at `supabase/migrations/20260419_arch035_three_channel_taxonomy.sql:129` compares `NEW.agent_id` to `app.current_agent_id` GUC. The GUC MUST equal the sub-tag (not the base) for the UPSERT to pass.
- `agent_messages.from_agent` has FK to `agents.id` (6 registered rows today: `cai`, `cc-web`, `cc-scholar`, `cc-ihsanos`, `musa`, `broadcast`). Any `from_agent` write must use a base that matches a registered row — else 23503 FK violation.
- The existing EXIT trap in `launch_dangerous_cc.sh` flips `agent_status.status='offline'` on SIGTERM/Ctrl-C/clean exit. The revision must keep this behavior, and use the sub-tag variable (not a hardcoded base).
- `build_launch_context` is called at L88 with hardcoded `cd ~/wingmen/orchestrator` — that stays (the context builder has to run from orchestrator), but the `--agent` arg now receives the sub-tag.

---

## File Structure

**New files:**
- `scripts/lib/__init__.py` — empty, enables `scripts.lib.*` package import.
- `scripts/lib/auto_agent_id.py` — helper module. Public surface (delta-v2):
  - `UnknownRepoError`, `LockTimeoutError` — exception classes.
  - `load_family_map(dsn: str) -> dict[str, str]` — reads `agents.repo_scope` (NOT `scope_repos`), strips `wingmen-` prefix for canonical keys, returns `{repo_canonical: base_agent_id}`. Raises on duplicate claim. Called at launcher startup; 4-family live state (cc-ihsanos / cc-scholar / cc-web / cc-cosem per CAI-AGENTS-001).
  - `strip_worktree_suffix(segment: str) -> str` — strips uppercase-initial post-dash/post-dot suffixes (e.g. `orchestrator-LEDGER` → `orchestrator`, `orchestrator.wt-qurban` → `orchestrator`). Preserves lowercase inner hyphens (`hifz-companion` unchanged). Regex: uppercase-start token after `-` or `.`.
  - `resolve_base_agent_id(pwd: str, family_map: dict[str, str]) -> str` — impure (calls git), pure given fixed map. Uses `git rev-parse --show-toplevel` first, falls back to pwd walk. Applies `strip_worktree_suffix` at each step. Raises `UnknownRepoError` on no match.
  - `pick_sub_tag(base: str, active: list[str]) -> str` — pure; scans a given active list for next free N.
  - `allocate_sub_tag_and_register(base, dsn, repo, stale_cutoff_minutes=30) -> AllocResult` — acquires advisory lock via `pg_try_advisory_xact_lock` with 5s retry loop, scans `agent_status` for active siblings, picks next N, UPSERTs with GUC, all one TX. Raises `LockTimeoutError` with `pg_locks` diagnostic on timeout.
  - `scan_overlap_siblings(base, scope_repo, dsn, exclude_sub_tag, stale_cutoff_minutes=30) -> list[tuple[str, int]]` — returns `(agent_id, heartbeat_age_seconds)` tuples, excluding self.
  - `main()` — CLI entrypoint: `--pwd/--repo/--dsn/--stale-minutes`. Loads family map itself (checkpoint lean per delta msg 398 — CLI owns map, bash-side stays pure). Emits JSON `{"sub_tag", "base", "siblings", "overlap_warnings": [[agent, age_s], ...]}` on stdout. Exits 1 on `UnknownRepoError` or `LockTimeoutError` with human-readable stderr.
- `tests/test_auto_agent_id.py` — pytest covering:
  - `resolve_base_agent_id` — all 6 mapped prefixes, absolute vs home-expanded paths, unrecognized path raises, case preservation.
  - `pick_sub_tag` — empty active list → `-1`, contiguous `-1/-2` → `-3`, gap `-1/-3` → `-2`, foreign-family entries in list are ignored, duplicate entries are deduped.
  - `allocate_sub_tag_and_register` (integration) — SAVEPOINT-rolled, asserts row lands with correct GUC, advisory lock serializes 2 concurrent pickers, stale row (heartbeat > cutoff) is reclaimed.
  - `scan_overlap_siblings` (integration) — SAVEPOINT-rolled, returns only rows whose `scope_repos` intersects, excludes self.

**Modified files:**
- `scripts/launch_dangerous_cc.sh` — surgical edits detailed per-task below. Summary of sites:
  - L14-21: header comment block (update env-var docs)
  - L23-43: CLI arg parse + helper invocation + dual-identity assignment
  - L67-74: header print (show sub-tag + base + repo)
  - L88: `build_launch_context --agent` now uses sub-tag
  - L100-146: ARCH-035 `agent_status` UPSERT — replaced by call to helper (which does its own UPSERT with scope_repos + GUC). This block becomes a pass-through check.
  - L150-167: heartbeat loop — `agents.last_heartbeat` update uses base (unchanged today, but becomes explicit `$BASE_AGENT_ID`).
  - L197-322: `_verify_vercel_deploy` — `agent_messages` blocker inserts at L282 + L314 switch from `$AGENT_ID` to `$BASE_AGENT_ID`.
  - L325-441: `_handle_exit` — `agent_status` offline flip uses sub-tag + GUC; `agents.status` + `agent_messages` session digest insert use base.
  - L443-477: launch block — hardcode `--model claude-opus-4-7` with `$MODEL` env override, log resolved model.

---

## Task Sequencing

10 tasks, TDD-first, one file per task-group. Each task commits at end. Final adversarial-review filing to CAI mirrors the Step 2 msg 381 shape.

---

### Task 1: Skeleton (package + empty module + test file)

**Files:**
- Create: `scripts/lib/__init__.py`
- Create: `scripts/lib/auto_agent_id.py`
- Create: `tests/test_auto_agent_id.py`

- [ ] **Step 1: Create the package init**

```bash
mkdir -p scripts/lib
```

Write `scripts/lib/__init__.py`:

```python
"""Launcher-side helpers. Kept separate from scripts/ top-level so pytest
can import `scripts.lib.auto_agent_id` cleanly without tripping on scripts/
that invoke sys.exit at import time."""
```

- [ ] **Step 2: Create the helper module stub**

Write `scripts/lib/auto_agent_id.py`:

```python
"""Launcher auto-identity + repo family resolution.

Composes msgs 315/317/324 per GOVERNANCE-CLEANUP-001 Step 3.
Dual-identity convention: sub-tag (per-instance, agent_status + GUC) +
base (family, agent_messages.from_agent FK-enforced).
"""
from __future__ import annotations


class UnknownRepoError(ValueError):
    """Raised when pwd does not map to a registered agent family.
    Fail-fast per CAI msg 395 Q2 constraint — never silent-fallback."""
```

- [ ] **Step 3: Create the test stub**

Write `tests/test_auto_agent_id.py`:

```python
"""Tests for scripts.lib.auto_agent_id — GOVERNANCE-CLEANUP-001 Step 3."""
import pytest
from scripts.lib import auto_agent_id
```

- [ ] **Step 4: Confirm module imports clean**

Run: `.venv/bin/python -c "from scripts.lib import auto_agent_id; print(auto_agent_id.UnknownRepoError)"`
Expected: `<class 'scripts.lib.auto_agent_id.UnknownRepoError'>`

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/__init__.py scripts/lib/auto_agent_id.py tests/test_auto_agent_id.py
git commit -m "chore(step-3): scaffold scripts/lib/auto_agent_id module"
```

---

### Task 1.5: `load_family_map` — data-driven map from `agents.repo_scope` (delta-v2 L1-B2)

**Files:**
- Modify: `scripts/lib/auto_agent_id.py`
- Modify: `tests/test_auto_agent_id.py`

CAI msg 397 flagged hardcoded-map drift; msg 403 applied a live agents-table split (cc-cosem family created). Both issues collapse if the map is loaded from `agents.repo_scope` at each launcher boot. Column name is `repo_scope` (verified live, NOT `scope_repos`).

Live state at delta-v2 time:
  - cc-ihsanos: [ihsanos, wingmen-orchestrator]
  - cc-scholar: [ai-scholar, hifz-companion]
  - cc-web:     [wordpress-sites, dookana]
  - cc-cosem:   [cosem-tdu, cosem-adcda]

Canonicalization rule: strip `wingmen-` prefix so `wingmen-orchestrator` keys as `orchestrator` — matches the filesystem basename.

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_auto_agent_id.py`:

```python
import os
from dotenv import load_dotenv

load_dotenv()
DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")

pytestmark_integration = pytest.mark.skipif(
    not DSN,
    reason="DATABASE_URL not set — skipping Supabase integration tests",
)


@pytestmark_integration
class TestLoadFamilyMap:
    def test_returns_all_four_cc_families_canonicalized(self):
        m = auto_agent_id.load_family_map(DSN)
        # 4 families live (post CAI-AGENTS-001 split).
        assert m["ihsanos"] == "cc-ihsanos"
        assert m["orchestrator"] == "cc-ihsanos"  # 'wingmen-' stripped
        assert m["ai-scholar"] == "cc-scholar"
        assert m["hifz-companion"] == "cc-scholar"
        assert m["dookana"] == "cc-web"
        assert m["wordpress-sites"] == "cc-web"
        assert m["cosem-tdu"] == "cc-cosem"
        assert m["cosem-adcda"] == "cc-cosem"

    def test_duplicate_claim_raises(self):
        # Can't easily test in integration without mutating agents table.
        # Unit-style test with monkeypatched psycopg instead.
        import types
        fake_rows = [("cc-a", ["x"]), ("cc-b", ["x"])]

        class _FakeCur:
            def __enter__(self_): return self_
            def __exit__(self_, *a): pass
            def execute(self_, *a, **k): pass
            def fetchall(self_): return fake_rows
        class _FakeConn:
            def __enter__(self_): return self_
            def __exit__(self_, *a): pass
            def cursor(self_): return _FakeCur()

        import psycopg
        orig = psycopg.connect
        psycopg.connect = lambda *a, **k: _FakeConn()
        try:
            with pytest.raises(ValueError, match="claimed by both"):
                auto_agent_id.load_family_map("dummy-dsn")
        finally:
            psycopg.connect = orig
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py::TestLoadFamilyMap -v`
Expected: FAIL with `AttributeError: module 'scripts.lib.auto_agent_id' has no attribute 'load_family_map'`.

- [ ] **Step 3: Implement `load_family_map`**

Append to `scripts/lib/auto_agent_id.py`:

```python
def load_family_map(dsn: str) -> dict[str, str]:
    """Build canonical repo-name → base-agent-id map from agents.repo_scope.

    Canonicalization: strip 'wingmen-' prefix so 'wingmen-orchestrator'
    matches filesystem basename 'orchestrator'. Replaces any hardcoded map.
    Raises ValueError if two agent rows claim the same canonical repo
    (indicates agents table corruption — fail loud, not silent-last-wins).

    Called once per launcher boot. Zero drift risk — if agents table changes,
    next launcher picks up the new map automatically.
    """
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, repo_scope FROM agents WHERE id LIKE 'cc-%'"
            )
            rows = cur.fetchall()

    out: dict[str, str] = {}
    for agent_id, repo_list in rows:
        for raw in (repo_list or []):
            canon = raw[len("wingmen-"):] if raw.startswith("wingmen-") else raw
            existing = out.get(canon)
            if existing is not None and existing != agent_id:
                raise ValueError(
                    f"repo {canon!r} claimed by both {existing} and {agent_id} "
                    f"in agents.repo_scope — fix the agents table before launching"
                )
            out[canon] = agent_id
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py::TestLoadFamilyMap -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/auto_agent_id.py tests/test_auto_agent_id.py
git commit -m "feat(step-3): load_family_map — data-driven from agents.repo_scope with wingmen- strip"
```

---

### Task 2: `resolve_base_agent_id` + `strip_worktree_suffix` — pwd→family with worktree handling (delta-v2 L1-B1)

**Files:**
- Modify: `scripts/lib/auto_agent_id.py`
- Modify: `tests/test_auto_agent_id.py`

Delta-v2 rewrite: `resolve_base_agent_id` takes the family_map produced by Task 1.5 as a parameter (no hardcoded constant). First tries `git rev-parse --show-toplevel` for clean worktree handling, falls back to pwd-component walk. At each candidate basename applies `strip_worktree_suffix` which strips uppercase-initial post-dash/post-dot tokens like `-LEDGER` or `.wt-qurban` without touching lowercase-hyphen names like `hifz-companion`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auto_agent_id.py`:

```python
import os

# Fixture-style map matching live agents table at delta-v2 time.
FAKE_MAP = {
    "ihsanos": "cc-ihsanos",
    "orchestrator": "cc-ihsanos",
    "ai-scholar": "cc-scholar",
    "hifz-companion": "cc-scholar",
    "dookana": "cc-web",
    "wordpress-sites": "cc-web",
    "cosem-tdu": "cc-cosem",
    "cosem-adcda": "cc-cosem",
}


class TestStripWorktreeSuffix:
    def test_dash_uppercase_stripped(self):
        assert auto_agent_id.strip_worktree_suffix("orchestrator-LEDGER") == "orchestrator"

    def test_dot_wt_stripped(self):
        assert auto_agent_id.strip_worktree_suffix("orchestrator.wt-qurban") == "orchestrator"

    def test_dash_lowercase_preserved(self):
        # This is a legit repo name, not a worktree suffix.
        assert auto_agent_id.strip_worktree_suffix("hifz-companion") == "hifz-companion"

    def test_dash_lowercase_multi_preserved(self):
        assert auto_agent_id.strip_worktree_suffix("cosem-tdu") == "cosem-tdu"

    def test_no_suffix_unchanged(self):
        assert auto_agent_id.strip_worktree_suffix("orchestrator") == "orchestrator"


class TestResolveBaseAgentId:
    def test_orchestrator_maps_to_cc_ihsanos(self, monkeypatch):
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/orchestrator")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/orchestrator", FAKE_MAP
        ) == "cc-ihsanos"

    def test_orchestrator_worktree_LEDGER_maps(self, monkeypatch):
        # Worktree: git rev-parse --show-toplevel returns the worktree path.
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/orchestrator-LEDGER")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/orchestrator-LEDGER", FAKE_MAP
        ) == "cc-ihsanos"

    def test_orchestrator_worktree_dot_wt_maps(self, monkeypatch):
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/orchestrator.wt-qurban")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/orchestrator.wt-qurban", FAKE_MAP
        ) == "cc-ihsanos"

    def test_hifz_companion_hyphen_preserved(self, monkeypatch):
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/projects/hifz-companion")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/projects/hifz-companion", FAKE_MAP
        ) == "cc-scholar"

    def test_cosem_tdu_maps_to_cc_cosem(self, monkeypatch):
        # New family post-CAI-AGENTS-001.
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/projects/cosem-tdu")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/projects/cosem-tdu", FAKE_MAP
        ) == "cc-cosem"

    def test_subdirectory_falls_back_to_walk(self, monkeypatch):
        # User in dookana/src/components — git-toplevel resolves, basename dookana.
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/projects/dookana")
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/projects/dookana/src/components", FAKE_MAP
        ) == "cc-web"

    def test_no_git_walks_pwd_components(self, monkeypatch):
        # Fallback when outside a git repo.
        monkeypatch.setattr(auto_agent_id, "_git_toplevel", lambda pwd: None)
        assert auto_agent_id.resolve_base_agent_id(
            "/Users/sheikhmusa/wingmen/projects/dookana/src", FAKE_MAP
        ) == "cc-web"

    def test_unrecognized_raises(self, monkeypatch):
        monkeypatch.setattr(auto_agent_id, "_git_toplevel",
                            lambda pwd: "/Users/sheikhmusa/wingmen/projects/unregistered-repo")
        with pytest.raises(auto_agent_id.UnknownRepoError):
            auto_agent_id.resolve_base_agent_id(
                "/Users/sheikhmusa/wingmen/projects/unregistered-repo", FAKE_MAP
            )

    def test_outside_wingmen_raises(self, monkeypatch):
        monkeypatch.setattr(auto_agent_id, "_git_toplevel", lambda pwd: None)
        with pytest.raises(auto_agent_id.UnknownRepoError):
            auto_agent_id.resolve_base_agent_id("/tmp/foo", FAKE_MAP)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py::TestStripWorktreeSuffix tests/test_auto_agent_id.py::TestResolveBaseAgentId -v`
Expected: All FAIL with `AttributeError` on `strip_worktree_suffix`, `_git_toplevel`, or `resolve_base_agent_id`.

- [ ] **Step 3: Implement `strip_worktree_suffix`, `_git_toplevel`, and `resolve_base_agent_id`**

Append to `scripts/lib/auto_agent_id.py`:

```python
import re
import subprocess
from pathlib import Path

# Matches trailing worktree-style suffix: -UPPERCASE... or .UPPERCASE... or
# .wt-anything. Lowercase inner hyphens (hifz-companion) do NOT match since
# the second group requires an uppercase start or the literal 'wt'.
_WORKTREE_SUFFIX_RE = re.compile(r"[-.](?:[A-Z][\w-]*|wt[\w-]*)$")


def strip_worktree_suffix(segment: str) -> str:
    """Strip trailing worktree-style suffix from a path segment.

    'orchestrator-LEDGER' → 'orchestrator'
    'orchestrator.wt-qurban' → 'orchestrator'
    'hifz-companion' → 'hifz-companion' (lowercase hyphen, no match)

    CONVENTION (delta-v2 + CAI msg 407): worktree suffixes MUST use either:
        (a) uppercase-initial token:  `-FEATURE`, `-LEDGER`, `-HOTFIX`
        (b) dot-wt prefix form:       `.wt-qurban`, `.wt-abc123`
    Lowercase suffixes (e.g. `orchestrator-hotfix`) are treated as CANONICAL
    repo names, NOT worktrees — they will UnknownRepoError on pwd resolution.
    This trades one convention rule for zero ambiguity between `hifz-companion`
    (canonical, no strip) and `orchestrator-LEDGER` (worktree, stripped).
    """
    return _WORKTREE_SUFFIX_RE.sub("", segment)


def _git_toplevel(pwd: str) -> str | None:
    """Return `git rev-parse --show-toplevel` for pwd, or None if not in a repo."""
    try:
        r = subprocess.run(
            ["git", "-C", pwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def resolve_base_agent_id(pwd: str, family_map: dict[str, str]) -> str:
    """Map an absolute pwd to a registered agents.id base family.

    Algorithm:
      1. Try `git rev-parse --show-toplevel` — get worktree/repo root path.
      2. Take basename, apply strip_worktree_suffix, look up in family_map.
      3. If miss OR no git toplevel: walk pwd components bottom-up, apply
         strip_worktree_suffix at each, first hit wins.
      4. Fail-fast UnknownRepoError if nothing matches.
    """
    # Step 1+2: git toplevel
    toplevel = _git_toplevel(pwd)
    if toplevel:
        basename = strip_worktree_suffix(Path(toplevel).name)
        if basename in family_map:
            return family_map[basename]

    # Step 3: fallback — walk pwd components bottom-up
    for part in reversed(Path(pwd).parts):
        if not part or part == "/":
            continue
        canon = strip_worktree_suffix(part)
        if canon in family_map:
            return family_map[canon]

    raise UnknownRepoError(
        f"pwd {pwd!r} (toplevel={toplevel!r}) is not a registered agent family. "
        f"Known: {sorted(family_map.keys())}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py::TestStripWorktreeSuffix tests/test_auto_agent_id.py::TestResolveBaseAgentId -v`
Expected: 14 PASS (5 strip + 9 resolve).

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/auto_agent_id.py tests/test_auto_agent_id.py
git commit -m "feat(step-3): resolve_base_agent_id with worktree-strip + data-driven map"
```

---

### Task 3: `pick_sub_tag` — pure N-picker

**Files:**
- Modify: `scripts/lib/auto_agent_id.py`
- Modify: `tests/test_auto_agent_id.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auto_agent_id.py`:

```python
class TestPickSubTag:
    def test_empty_active_picks_one(self):
        assert auto_agent_id.pick_sub_tag("cc-ihsanos", []) == "cc-ihsanos-1"

    def test_contiguous_picks_next(self):
        assert auto_agent_id.pick_sub_tag(
            "cc-ihsanos", ["cc-ihsanos-1", "cc-ihsanos-2"]
        ) == "cc-ihsanos-3"

    def test_gap_fills_first_gap(self):
        assert auto_agent_id.pick_sub_tag(
            "cc-ihsanos", ["cc-ihsanos-1", "cc-ihsanos-3"]
        ) == "cc-ihsanos-2"

    def test_foreign_family_ignored(self):
        assert auto_agent_id.pick_sub_tag(
            "cc-ihsanos",
            ["cc-web-1", "cc-scholar-5", "cc-ihsanos-1"],
        ) == "cc-ihsanos-2"

    def test_duplicate_active_deduped(self):
        assert auto_agent_id.pick_sub_tag(
            "cc-ihsanos", ["cc-ihsanos-1", "cc-ihsanos-1"]
        ) == "cc-ihsanos-2"

    def test_base_matching_entry_ignored(self):
        # "cc-ihsanos" (no -N suffix) is the legacy base row, not a sub-tag
        assert auto_agent_id.pick_sub_tag(
            "cc-ihsanos", ["cc-ihsanos", "cc-ihsanos-1"]
        ) == "cc-ihsanos-2"

    def test_non_integer_suffix_ignored(self):
        # Robust to unexpected suffixes like "cc-ihsanos-test"
        assert auto_agent_id.pick_sub_tag(
            "cc-ihsanos", ["cc-ihsanos-test", "cc-ihsanos-1"]
        ) == "cc-ihsanos-2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py::TestPickSubTag -v`
Expected: 7 FAIL with `AttributeError: ... no attribute 'pick_sub_tag'`.

- [ ] **Step 3: Implement `pick_sub_tag`**

Append to `scripts/lib/auto_agent_id.py`:

```python
def pick_sub_tag(base: str, active: list[str]) -> str:
    """Pick the smallest positive integer N such that f'{base}-{N}' is not in active.

    Pure function — no DB. Callers feed it a scanned active list. Ignores
    entries that don't parse as '{base}-<int>' (e.g. legacy bare base, or
    experimental suffixes).
    """
    taken: set[int] = set()
    prefix = f"{base}-"
    for entry in active:
        if not entry.startswith(prefix):
            continue
        suffix = entry[len(prefix):]
        if not suffix.isdigit():
            continue
        taken.add(int(suffix))

    n = 1
    while n in taken:
        n += 1
    return f"{base}-{n}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py::TestPickSubTag -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/auto_agent_id.py tests/test_auto_agent_id.py
git commit -m "feat(step-3): pick_sub_tag pure N-picker (handles gaps, dedupes, ignores foreign)"
```

---

### Task 4: `allocate_sub_tag_and_register` — integration (advisory lock + UPSERT in one TX)

**Files:**
- Modify: `scripts/lib/auto_agent_id.py`
- Modify: `tests/test_auto_agent_id.py`

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/test_auto_agent_id.py`:

```python
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")

pytestmark_integration = pytest.mark.skipif(
    not DSN,
    reason="DATABASE_URL not set — skipping Supabase integration tests",
)


@pytestmark_integration
class TestAllocateSubTagAndRegister:
    """SAVEPOINT-rolled integration tests against the real Supabase project.
    Mirrors verify_governance_hygiene_batch.py SAVEPOINT/ROLLBACK harness."""

    def _fresh_conn(self):
        import psycopg
        return psycopg.connect(DSN, autocommit=False)

    def test_empty_family_allocates_one(self):
        # Roll in SAVEPOINT so we don't pollute real agent_status.
        import psycopg
        with self._fresh_conn() as setup_conn:
            with setup_conn.cursor() as cur:
                cur.execute("SAVEPOINT test_alloc")
                # Delete any sub-tagged rows in test family so we start clean.
                cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                            ("cc-test-family",))
                cur.execute(
                    "DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'"
                )
            setup_conn.commit()

        try:
            result = auto_agent_id.allocate_sub_tag_and_register(
                base="cc-test-family",
                dsn=DSN,
                repo="orchestrator",
            )
            assert result.sub_tag == "cc-test-family-1"
            assert result.siblings == []

            # Verify row landed
            with self._fresh_conn() as verify_conn:
                with verify_conn.cursor() as cur:
                    cur.execute(
                        "SELECT status, current_task, scope_repos "
                        "FROM agent_status WHERE agent_id = %s",
                        (result.sub_tag,),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    assert row[0] == "working"
                    assert row[1] == "session-launch"
                    assert row[2] == ["orchestrator"]
        finally:
            # Cleanup
            with self._fresh_conn() as clean_conn:
                with clean_conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                                ("cc-test-family-1",))
                    cur.execute(
                        "DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'"
                    )
                clean_conn.commit()

    def test_stale_row_is_reclaimed(self):
        # Insert a row with heartbeat 2 hours old — allocator should skip it
        # (and its N becomes available).
        with self._fresh_conn() as setup_conn:
            with setup_conn.cursor() as cur:
                cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                            ("cc-test-family-1",))
                cur.execute(
                    "INSERT INTO agent_status "
                    "(agent_id, status, last_heartbeat, updated_at) "
                    "VALUES (%s, 'working', now() - interval '2 hours', now() - interval '2 hours') "
                    "ON CONFLICT (agent_id) DO UPDATE SET "
                    "last_heartbeat = EXCLUDED.last_heartbeat",
                    ("cc-test-family-1",),
                )
            setup_conn.commit()

        try:
            # N=1 is stale, so allocator should reclaim it (pick N=1 again).
            result = auto_agent_id.allocate_sub_tag_and_register(
                base="cc-test-family",
                dsn=DSN,
                repo="orchestrator",
            )
            assert result.sub_tag == "cc-test-family-1"
            # The previously-stale row should now have fresh heartbeat (UPSERT overwrote).
        finally:
            with self._fresh_conn() as clean_conn:
                with clean_conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                                ("cc-test-family-1",))
                    cur.execute(
                        "DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'"
                    )
                clean_conn.commit()

    def test_active_sibling_bumps_n(self):
        # Pre-populate with a fresh sibling; new allocation should pick N=2.
        with self._fresh_conn() as setup_conn:
            with setup_conn.cursor() as cur:
                cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                            ("cc-test-family-1",))
                cur.execute(
                    "INSERT INTO agent_status "
                    "(agent_id, status, last_heartbeat, updated_at) "
                    "VALUES (%s, 'working', now(), now()) "
                    "ON CONFLICT (agent_id) DO UPDATE SET "
                    "last_heartbeat = now()",
                    ("cc-test-family-1",),
                )
            setup_conn.commit()

        try:
            result = auto_agent_id.allocate_sub_tag_and_register(
                base="cc-test-family",
                dsn=DSN,
                repo="orchestrator",
            )
            assert result.sub_tag == "cc-test-family-2"
            assert "cc-test-family-1" in result.siblings
        finally:
            with self._fresh_conn() as clean_conn:
                with clean_conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'"
                    )
                clean_conn.commit()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py::TestAllocateSubTagAndRegister -v`
Expected: 3 FAIL with `AttributeError: ... no attribute 'allocate_sub_tag_and_register'`.

- [ ] **Step 3: Implement `allocate_sub_tag_and_register` + `AllocResult` + `LockTimeoutError` (delta-v2 L2)**

Append to `scripts/lib/auto_agent_id.py`:

```python
import time
from dataclasses import dataclass


class LockTimeoutError(RuntimeError):
    """Raised when pg_try_advisory_xact_lock fails to acquire within retry budget.

    Delta-v2 L2: `pg_advisory_xact_lock` blocks indefinitely if another
    launcher's TX wedges. We prefer a bounded wait + pg_locks diagnostic so
    the operator sees who holds the key rather than hanging forever.
    """


@dataclass(frozen=True)
class AllocResult:
    sub_tag: str
    siblings: list[str]  # active siblings *before* this allocation


def allocate_sub_tag_and_register(
    base: str,
    dsn: str,
    repo: str,
    stale_cutoff_minutes: int = 30,
) -> AllocResult:
    """Atomically: acquire global advisory lock, scan active siblings of `base`,
    pick next-free N, UPSERT agent_status for the picked sub-tag with GUC set.

    The lock is held across scan + UPSERT so two concurrent launchers cannot
    pick the same N — one commits, the other sees the fresh row on rescan.

    Lock strategy (delta-v2 L2): `pg_try_advisory_xact_lock` with 500ms poll
    for up to 5s. On timeout, query pg_locks for the holder and raise
    LockTimeoutError with the diagnostic attached.

    Args:
        base: registered agents.id family (e.g. 'cc-ihsanos').
        dsn: Postgres connection string with the GUC-capable user.
        repo: repo name for scope_repos (single-element array for now).
        stale_cutoff_minutes: rows whose last_heartbeat is older than this
            count as reclaimable (their N is considered free).

    Returns:
        AllocResult(sub_tag, siblings_seen_before_alloc).

    Raises:
        LockTimeoutError: advisory lock not acquired within 5s.
    """
    import psycopg

    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            # Bounded lock acquisition: 10 × 500ms = 5s ceiling.
            acquired = False
            for _ in range(10):
                cur.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
                    ("cc-agent-id-alloc",),
                )
                if cur.fetchone()[0]:
                    acquired = True
                    break
                time.sleep(0.5)
            if not acquired:
                # Diagnostic: who holds our key?
                cur.execute(
                    """
                    SELECT pid, granted, query_start, state
                      FROM pg_locks l
                      JOIN pg_stat_activity a ON a.pid = l.pid
                     WHERE locktype = 'advisory'
                       AND objid = hashtext('cc-agent-id-alloc')::bigint
                    """
                )
                holders = cur.fetchall()
                raise LockTimeoutError(
                    f"advisory lock 'cc-agent-id-alloc' held >5s. "
                    f"Holders: {holders}"
                )

            cur.execute(
                """
                SELECT agent_id FROM agent_status
                 WHERE agent_id LIKE %s
                   AND last_heartbeat > now() - (%s * interval '1 minute')
                   AND status != 'offline'
                """,
                (f"{base}-%", stale_cutoff_minutes),
            )
            siblings = [r[0] for r in cur.fetchall()]
            sub_tag = pick_sub_tag(base, siblings)

            # Still inside TX with lock held — UPSERT agent_status.
            # GUC must equal NEW.agent_id (sub-tag) per ARCH-035 trigger.
            cur.execute(
                "SELECT set_config('app.current_agent_id', %s, true)",
                (sub_tag,),
            )
            cur.execute(
                """
                INSERT INTO agent_status
                  (agent_id, status, current_task, scope_repos, last_heartbeat, updated_at)
                VALUES (%s, 'working', 'session-launch', ARRAY[%s]::text[], now(), now())
                ON CONFLICT (agent_id) DO UPDATE SET
                  status = 'working',
                  current_task = 'session-launch',
                  scope_repos = ARRAY[%s]::text[],
                  last_heartbeat = now(),
                  updated_at = now()
                """,
                (sub_tag, repo, repo),
            )
        conn.commit()  # releases advisory lock

    return AllocResult(sub_tag=sub_tag, siblings=siblings)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py::TestAllocateSubTagAndRegister -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/auto_agent_id.py tests/test_auto_agent_id.py
git commit -m "feat(step-3): allocate_sub_tag_and_register — advisory-lock + UPSERT in one TX"
```

---

### Task 5: `scan_overlap_siblings` — soft overlap warning

**Files:**
- Modify: `scripts/lib/auto_agent_id.py`
- Modify: `tests/test_auto_agent_id.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auto_agent_id.py`:

```python
@pytestmark_integration
class TestScanOverlapSiblings:
    def _fresh_conn(self):
        import psycopg
        return psycopg.connect(DSN, autocommit=False)

    def test_returns_overlapping_active_sibling(self):
        # Seed two rows in cc-test-family: -1 scopes orchestrator, -2 scopes orchestrator too.
        with self._fresh_conn() as setup_conn:
            with setup_conn.cursor() as cur:
                for n, scope in [(1, "orchestrator"), (2, "orchestrator")]:
                    cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                                (f"cc-test-family-{n}",))
                    cur.execute(
                        "INSERT INTO agent_status "
                        "(agent_id, status, scope_repos, last_heartbeat, updated_at) "
                        "VALUES (%s, 'working', ARRAY[%s]::text[], now(), now()) "
                        "ON CONFLICT (agent_id) DO UPDATE SET "
                        "scope_repos = EXCLUDED.scope_repos, "
                        "last_heartbeat = now()",
                        (f"cc-test-family-{n}", scope),
                    )
            setup_conn.commit()

        try:
            overlaps = auto_agent_id.scan_overlap_siblings(
                base="cc-test-family",
                scope_repo="orchestrator",
                dsn=DSN,
                exclude_sub_tag="cc-test-family-2",
            )
            # delta-v2: return type is list[tuple[str, int]] — (agent_id, heartbeat_age_s).
            # Shape check + membership check by first element.
            assert all(isinstance(t, tuple) and len(t) == 2 for t in overlaps)
            assert all(isinstance(t[0], str) and isinstance(t[1], int) for t in overlaps)
            agent_ids = [t[0] for t in overlaps]
            assert "cc-test-family-1" in agent_ids
            assert "cc-test-family-2" not in agent_ids  # excluded self
            # heartbeat just-inserted → age should be tiny (< 60s).
            age_1 = next(age for (aid, age) in overlaps if aid == "cc-test-family-1")
            assert 0 <= age_1 < 60
        finally:
            with self._fresh_conn() as clean_conn:
                with clean_conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'"
                    )
                clean_conn.commit()

    def test_non_overlapping_scope_excluded(self):
        with self._fresh_conn() as setup_conn:
            with setup_conn.cursor() as cur:
                cur.execute("SELECT set_config('app.current_agent_id', %s, true)",
                            ("cc-test-family-1",))
                cur.execute(
                    "INSERT INTO agent_status "
                    "(agent_id, status, scope_repos, last_heartbeat, updated_at) "
                    "VALUES (%s, 'working', ARRAY['dookana']::text[], now(), now()) "
                    "ON CONFLICT (agent_id) DO UPDATE SET "
                    "scope_repos = EXCLUDED.scope_repos, last_heartbeat = now()",
                    ("cc-test-family-1",),
                )
            setup_conn.commit()

        try:
            overlaps = auto_agent_id.scan_overlap_siblings(
                base="cc-test-family",
                scope_repo="orchestrator",  # different
                dsn=DSN,
                exclude_sub_tag="cc-test-family-2",
            )
            assert overlaps == []
        finally:
            with self._fresh_conn() as clean_conn:
                with clean_conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'"
                    )
                clean_conn.commit()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py::TestScanOverlapSiblings -v`
Expected: 2 FAIL with `AttributeError: ... no attribute 'scan_overlap_siblings'`.

- [ ] **Step 3: Implement `scan_overlap_siblings`**

Append to `scripts/lib/auto_agent_id.py`:

```python
def scan_overlap_siblings(
    base: str,
    scope_repo: str,
    dsn: str,
    exclude_sub_tag: str,
    stale_cutoff_minutes: int = 30,
) -> list[tuple[str, int]]:
    """Return `(agent_id, heartbeat_age_seconds)` for active sub-tags in
    `base` family whose scope_repos contains `scope_repo`, excluding
    `exclude_sub_tag` (the caller itself).

    Soft-warning helper per CAI msg 395 Q3-C — prints a pre-launch overlap
    notice if 2+ CCs in the same family are about to edit the same repo.
    Read-only, no lock, no UPSERT.

    Delta-v2 non-load-bearing #4: include heartbeat age so operator sees
    actionable recency at a glance (e.g. `cc-ihsanos-2 (3s ago)` vs
    `cc-ihsanos-5 (847s ago)`).
    """
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT agent_id,
                       EXTRACT(EPOCH FROM (now() - last_heartbeat))::int AS age_s
                  FROM agent_status
                 WHERE agent_id LIKE %s
                   AND agent_id != %s
                   AND last_heartbeat > now() - (%s * interval '1 minute')
                   AND status != 'offline'
                   AND %s = ANY(scope_repos)
                """,
                (f"{base}-%", exclude_sub_tag, stale_cutoff_minutes, scope_repo),
            )
            return [(r[0], int(r[1])) for r in cur.fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py::TestScanOverlapSiblings -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/auto_agent_id.py tests/test_auto_agent_id.py
git commit -m "feat(step-3): scan_overlap_siblings — soft warn on same-scope family overlap"
```

---

### Task 6: CLI entrypoint (`python -m scripts.lib.auto_agent_id`)

**Files:**
- Modify: `scripts/lib/auto_agent_id.py`
- Modify: `tests/test_auto_agent_id.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auto_agent_id.py`:

```python
import json
import subprocess
import sys


class TestCliEntrypoint:
    def test_unrecognized_repo_exits_1_with_clear_error(self):
        # Invoke the module as a subprocess — verify exit code + stderr.
        result = subprocess.run(
            [sys.executable, "-m", "scripts.lib.auto_agent_id",
             "--pwd", "/tmp/foo",
             "--repo", "unknown",
             "--dsn", "postgres://invalid"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "UnknownRepoError" in result.stderr or "not a registered" in result.stderr

    @pytestmark_integration
    def test_recognized_repo_emits_json(self):
        # Clean state
        import psycopg
        with psycopg.connect(DSN, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'")

        try:
            # Use a real repo path (orchestrator itself)
            env = {**os.environ, "DATABASE_URL": DSN}
            result = subprocess.run(
                [sys.executable, "-m", "scripts.lib.auto_agent_id",
                 "--pwd", str(os.path.expanduser("~/wingmen/orchestrator")),
                 "--repo", "orchestrator",
                 "--dsn", DSN,
                 "--base-override", "cc-test-family"],  # override for test
                capture_output=True, text=True, env=env,
            )
            assert result.returncode == 0, result.stderr
            payload = json.loads(result.stdout)
            assert payload["base"] == "cc-test-family"
            assert payload["sub_tag"] == "cc-test-family-1"
            assert isinstance(payload["siblings"], list)
            assert isinstance(payload["overlap_warnings"], list)
        finally:
            with psycopg.connect(DSN, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py::TestCliEntrypoint -v`
Expected: FAIL with `No module named scripts.lib.auto_agent_id.__main__` OR `argparse` failure — no `main()` defined yet.

- [ ] **Step 3: Implement the CLI `main()` + `__main__` guard**

Append to `scripts/lib/auto_agent_id.py`:

```python
def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: emit JSON {sub_tag, base, siblings, overlap_warnings}.

    Invoked by scripts/launch_dangerous_cc.sh. Exits 1 on UnknownRepoError
    or LockTimeoutError with human-readable stderr.

    Delta-v2 checkpoint: CLI loads the family map itself (not launcher-side).
    Keeps bash pure — shell ships only --pwd/--repo/--dsn and the CLI owns
    the map-shape surface.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m scripts.lib.auto_agent_id",
        description="Allocate a sub-tag agent identity within a registered family.",
    )
    parser.add_argument("--pwd", required=True, help="caller working directory")
    parser.add_argument("--repo", required=True, help="repo name for scope_repos")
    parser.add_argument("--dsn", required=True, help="Postgres DSN")
    parser.add_argument("--stale-minutes", type=int, default=30)
    parser.add_argument(
        "--base-override",
        default=None,
        help="skip pwd→family resolution, use this base (test hook only)",
    )
    args = parser.parse_args(argv)

    try:
        # Data-driven map (delta-v2 L1-B2): load from agents.repo_scope each boot.
        # Drift from hardcoded constants is structurally impossible.
        family_map = load_family_map(args.dsn)
        base = args.base_override or resolve_base_agent_id(args.pwd, family_map)
    except UnknownRepoError as e:
        sys.stderr.write(f"UnknownRepoError: {e}\n")
        return 1

    try:
        result = allocate_sub_tag_and_register(
            base=base,
            dsn=args.dsn,
            repo=args.repo,
            stale_cutoff_minutes=args.stale_minutes,
        )
    except LockTimeoutError as e:
        sys.stderr.write(f"LockTimeoutError: {e}\n")
        return 1

    overlaps = scan_overlap_siblings(
        base=base,
        scope_repo=args.repo,
        dsn=args.dsn,
        exclude_sub_tag=result.sub_tag,
        stale_cutoff_minutes=args.stale_minutes,
    )

    json.dump(
        {
            "sub_tag": result.sub_tag,
            "base": base,
            "siblings": list(result.siblings),
            # list of [agent_id, heartbeat_age_s] pairs (JSON-serialised tuples).
            "overlap_warnings": [[aid, age] for (aid, age) in overlaps],
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py::TestCliEntrypoint -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/auto_agent_id.py tests/test_auto_agent_id.py
git commit -m "feat(step-3): CLI entrypoint emits JSON {sub_tag, base, siblings, overlap_warnings}"
```

---

### Task 7: Launcher edit — CLI args + helper invocation + dual-identity exports

**Files:**
- Modify: `scripts/launch_dangerous_cc.sh` (lines 14-21, 23-43)

- [ ] **Step 1: Update the header comment block**

Edit `scripts/launch_dangerous_cc.sh:14-21`. Replace:

```bash
# Usage:
#   ./scripts/launch_dangerous_cc.sh
#   CC_AGENT_ID=cc-web ./scripts/launch_dangerous_cc.sh
#   ./scripts/launch_dangerous_cc.sh -- --resume <session-id>
#
# Environment:
#   CC_AGENT_ID  — agent id to boot as (default: cc-ihsanos)
#   CC_REPO      — repo name for cc_work_sessions row (default: autodetect from git)
```

With:

```bash
# Usage:
#   ./scripts/launch_dangerous_cc.sh
#   ./scripts/launch_dangerous_cc.sh --repo orchestrator
#   ./scripts/launch_dangerous_cc.sh -- --resume <session-id>
#
# Environment:
#   CC_REPO      — repo name override (default: caller pwd's git toplevel basename)
#   MODEL        — claude model override (default: claude-opus-4-7)
#
# Identity (GOVERNANCE-CLEANUP-001 Step 3, composes msgs 315/317/324):
#   Base family (CC_BASE_AGENT_ID) resolved from pwd → data-driven family map
#   built from agents.repo_scope at launch time (delta-v2: no hardcoded
#   constant; worktrees handled via git-toplevel + suffix-strip). Unrecognized
#   pwd = fail-fast ABORT. Sub-tag (CC_AGENT_ID) allocated via bounded
#   pg_try_advisory_xact_lock (5s retry) + scan of agent_status, picks the
#   smallest free N in the family. GUC + agent_status = sub-tag.
#   agent_messages.from_agent = base (FK requires a registered agents.id row).
#   Sub-identity promotion to first-class FK is Step 4 (BUG-024 Phase 1).
```

- [ ] **Step 2: Parse `--repo` arg + resolve repo + call helper**

Edit `scripts/launch_dangerous_cc.sh:23-43`. Replace:

```bash
set -uo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCH_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PY="$ORCH_DIR/.venv/bin/python3"
CALLER_DIR="$(pwd)"

AGENT_ID="${CC_AGENT_ID:-cc-ihsanos}"

# Auto-detect repo name from caller's git directory, or use fallback
REPO_NAME="${CC_REPO:-}"
if [ -z "$REPO_NAME" ]; then
    REPO_NAME="$(git -C "$CALLER_DIR" rev-parse --show-toplevel 2>/dev/null | xargs basename 2>/dev/null || echo "unknown")"
fi

SESSION_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SESSION_START_EPOCH="$(date -u +%s)"
HEARTBEAT_PID=""
REMINDER_PID=""
```

With:

```bash
set -uo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCH_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PY="$ORCH_DIR/.venv/bin/python3"
CALLER_DIR="$(pwd)"

# ── CLI arg parse (Step 3: --repo override, -- passthrough) ──────────────────

REPO_OVERRIDE=""
CLAUDE_PASSTHROUGH=()
PASS_THROUGH=false
for arg in "$@"; do
    if $PASS_THROUGH; then
        CLAUDE_PASSTHROUGH+=("$arg")
        continue
    fi
    case "$arg" in
        --repo=*)
            REPO_OVERRIDE="${arg#--repo=}"
            ;;
        --repo)
            # Placeholder — next arg is the value. Handled after loop.
            ;;
        --)
            PASS_THROUGH=true
            ;;
        *)
            ;;
    esac
done
# Second pass for space-separated --repo <value>
set -- "$@"
while [ $# -gt 0 ]; do
    case "$1" in
        --repo)
            shift
            REPO_OVERRIDE="$1"
            ;;
    esac
    shift || true
done

# Repo name: --repo flag > CC_REPO env > pwd git basename
REPO_NAME="${REPO_OVERRIDE:-${CC_REPO:-}}"
if [ -z "$REPO_NAME" ]; then
    REPO_NAME="$(git -C "$CALLER_DIR" rev-parse --show-toplevel 2>/dev/null | xargs basename 2>/dev/null || echo "unknown")"
fi

SESSION_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SESSION_START_EPOCH="$(date -u +%s)"
HEARTBEAT_PID=""
REMINDER_PID=""

# ── Step 3: dual-identity resolution via scripts/lib/auto_agent_id ───────────

# Load DATABASE_URL from .env for the helper call.
# shellcheck disable=SC1091
set -a; . "$ORCH_DIR/.env" 2>/dev/null || true; set +a
DSN="${DATABASE_URL:-${SUPABASE_DB_URL:-}}"
if [ -z "$DSN" ]; then
    echo -e "\033[31mERROR: DATABASE_URL not set — cannot allocate agent identity\033[0m" >&2
    echo "       Add DATABASE_URL=postgres://... to $ORCH_DIR/.env" >&2
    exit 1
fi

ALLOC_JSON="$(cd "$ORCH_DIR" && "$VENV_PY" -m scripts.lib.auto_agent_id \
    --pwd "$CALLER_DIR" \
    --repo "$REPO_NAME" \
    --dsn "$DSN" 2>/tmp/cc_alloc_err.log)" || {
    echo -e "\033[31mERROR: identity allocation failed\033[0m" >&2
    cat /tmp/cc_alloc_err.log >&2
    exit 1
}

CC_AGENT_ID="$(echo "$ALLOC_JSON" | "$VENV_PY" -c 'import sys,json;print(json.load(sys.stdin)["sub_tag"])')"
CC_BASE_AGENT_ID="$(echo "$ALLOC_JSON" | "$VENV_PY" -c 'import sys,json;print(json.load(sys.stdin)["base"])')"
# Delta-v2 non-load-bearing #4: overlap_warnings is list of [aid, age_s]
# pairs. Format each as "aid (Ns ago)" for operator-readable output.
OVERLAP_WARNINGS="$(echo "$ALLOC_JSON" | "$VENV_PY" -c 'import sys,json;print(", ".join(f"{a} ({s}s ago)" for a,s in json.load(sys.stdin)["overlap_warnings"]))')"

export CC_AGENT_ID
export CC_BASE_AGENT_ID
export SCOPE_REPO="$REPO_NAME"

# Legacy alias: some blocks below still reference $AGENT_ID. Retain a local
# only for readability; every write site specifies sub-tag vs base explicitly.
AGENT_ID="$CC_AGENT_ID"
BASE_AGENT_ID="$CC_BASE_AGENT_ID"
```

- [ ] **Step 3: Verify bash parses cleanly (syntax-only)**

Run: `bash -n scripts/launch_dangerous_cc.sh`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/launch_dangerous_cc.sh
git commit -m "feat(step-3): launcher --repo arg + dual-identity allocation via auto_agent_id helper"
```

---

### Task 8: Launcher edit — header print, `build_launch_context`, agent_status block, heartbeat loop

**Files:**
- Modify: `scripts/launch_dangerous_cc.sh` (lines 67-74, 86-98, 100-146, 148-167)

- [ ] **Step 1: Update header print (lines 67-74)**

Replace:

```bash
echo -e "${BOLD}${TEAL}╔══════════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${TEAL}║   WINGMEN AGENT BOOT — ARCH-022 Layer 2                             ║${RESET}"
echo -e "${BOLD}${TEAL}╚══════════════════════════════════════════════════════════════════════╝${RESET}"
echo -e "${DIM}Agent: ${AGENT_ID}  |  Repo: ${REPO_NAME}  |  Started: ${SESSION_START}${RESET}"
```

With:

```bash
echo -e "${BOLD}${TEAL}╔══════════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${TEAL}║   WINGMEN AGENT BOOT — ARCH-022 Layer 2 + Step 3 multi-repo         ║${RESET}"
echo -e "${BOLD}${TEAL}╚══════════════════════════════════════════════════════════════════════╝${RESET}"
echo -e "${DIM}Sub-tag: ${CC_AGENT_ID}  |  Base: ${CC_BASE_AGENT_ID}  |  Repo: ${REPO_NAME}${RESET}"
echo -e "${DIM}Started: ${SESSION_START}  |  pwd: ${CALLER_DIR}${RESET}"
if [ -n "$OVERLAP_WARNINGS" ]; then
    echo -e "${AMBER}${BOLD}⚠ OVERLAP: family siblings in ${REPO_NAME}: ${OVERLAP_WARNINGS}${RESET}"
    echo -e "${AMBER}  Coordinate scope or split work before editing shared paths.${RESET}"
fi
```

- [ ] **Step 2: `build_launch_context` — keep the cd-to-orchestrator, pass BASE (lines 86-98)**

Replace:

```bash
echo -e "${BOLD}▶ Building session context...${RESET}"
# Stdout = context block (captured). Stderr = diagnostics (shown on terminal).
LAUNCH_CONTEXT="$(cd ~/wingmen/orchestrator && "$VENV_PY" -m scripts.build_launch_context --agent "$AGENT_ID")" || {
    echo -e "${AMBER}⚠ build_launch_context failed. Continuing without injected context.${RESET}"
    LAUNCH_CONTEXT=""
}
```

With:

```bash
echo -e "${BOLD}▶ Building session context for ${CC_BASE_AGENT_ID}...${RESET}"
# Stdout = context block (captured). Stderr = diagnostics (shown on terminal).
#
# Delta-v2 L3-A1 fix: pass CC_BASE_AGENT_ID, NOT CC_AGENT_ID. The context
# builder is per-FAMILY, not per-instance:
#   - scripts/build_launch_context.py L57 — agent_context.eq('agent_id', base)
#   - scripts/build_launch_context.py L111 — inbox filter to_agent.eq.{base}
#   - scripts/build_launch_context.py L187-189 — agents.update(...).eq('id', base)
# Passing the sub-tag would land an empty agent_context row + hidden inbox
# (sibling filter would match literal 'cc-ihsanos-3' while all inbox rows are
# addressed to base 'cc-ihsanos' with '[cc-ihsanos-3]' tagged in body).
LAUNCH_CONTEXT="$(cd "$ORCH_DIR" && "$VENV_PY" -m scripts.build_launch_context --agent "$CC_BASE_AGENT_ID")" || {
    echo -e "${AMBER}⚠ build_launch_context failed. Continuing without injected context.${RESET}"
    LAUNCH_CONTEXT=""
}
```

- [ ] **Step 3: Replace the ARCH-035 UPSERT block (lines 100-146) with a no-op pass-through**

The UPSERT has already happened inside the helper call in Task 7. Replace the entire block with:

```bash
# ── 2.5 ARCH-035 — agent_status already UPSERTed by auto_agent_id helper ─────
# The helper acquired the advisory lock, scanned siblings, picked sub_tag,
# and UPSERTed agent_status(sub_tag, status=working, current_task=session-launch,
# scope_repos=[REPO_NAME]) all in one TX with GUC=sub_tag. Nothing to do here.

echo -e "${BOLD}▶ agent_status registered: ${CC_AGENT_ID} (scope_repos=[${REPO_NAME}])${RESET}"
echo ""
```

- [ ] **Step 4: Heartbeat loop — pin to base for `agents` table, and also bump `agent_status` sub-tag (lines 148-167)**

Replace:

```bash
_heartbeat_loop() {
    while true; do
        sleep 300  # every 5 minutes
        "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
from supabase import create_client
from datetime import datetime, timezone
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
sb.table('agents').update({'last_heartbeat': datetime.now(timezone.utc).isoformat()}).eq('id', '$AGENT_ID').execute()
" 2>/dev/null || true
    done
}

_heartbeat_loop &
HEARTBEAT_PID=$!
```

With:

```bash
_heartbeat_loop() {
    # Two heartbeats on a 5-minute cadence:
    #   1. agents.last_heartbeat (base id, legacy agents table)
    #   2. agent_status.last_heartbeat (sub-tag, ARCH-035 with GUC)
    # Both are best-effort; if the worker misses a beat, stale_agents view
    # (15-min threshold) catches it.
    while true; do
        sleep 300
        "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
from supabase import create_client
from datetime import datetime, timezone
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
now_iso = datetime.now(timezone.utc).isoformat()
# agents table — base id (FK-enforced)
sb.table('agents').update({'last_heartbeat': now_iso}).eq('id', '$BASE_AGENT_ID').execute()
" 2>/dev/null || true
        # agent_status heartbeat needs psycopg (GUC).
        "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
try:
    import psycopg
except ImportError:
    sys.exit(0)
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
if not dsn:
    sys.exit(0)
try:
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(\"SELECT set_config('app.current_agent_id', %s, true)\", ('$AGENT_ID',))
            cur.execute(\"UPDATE agent_status SET last_heartbeat=now(), updated_at=now() WHERE agent_id=%s\", ('$AGENT_ID',))
        conn.commit()
except Exception:
    pass
" 2>/dev/null || true
    done
}

_heartbeat_loop &
HEARTBEAT_PID=$!
```

- [ ] **Step 5: Verify syntax**

Run: `bash -n scripts/launch_dangerous_cc.sh`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/launch_dangerous_cc.sh
git commit -m "feat(step-3): launcher header/context/agent_status/heartbeat wired to dual identity"
```

---

### Task 9: Launcher edit — Vercel verify blocker inserts, exit trap (agent_status sub-tag, agent_messages base)

**Files:**
- Modify: `scripts/launch_dangerous_cc.sh` (lines 282-289, 313-320, 380-438)

- [ ] **Step 1: Vercel deploy FAIL blocker insert (line 282)**

Edit the insert at lines 281-289. Change `'from_agent': '$AGENT_ID'` to `'from_agent': '$BASE_AGENT_ID'`. The whole insert becomes:

```bash
sb.table('agent_messages').insert({
    'from_agent': '$BASE_AGENT_ID',
    'to_agent': 'cai',
    'message_type': 'blocker',
    'subject': 'DEPLOY FAILED — ${REPO_NAME} commit ${commit_sha:0:8} [${CC_AGENT_ID}]',
    'body': 'Vercel deployment reached ERROR state.\nCommit: ${commit_sha}\nBuild log: ${build_log_url}\nAction required: read build log, fix, push again.\n\nPosted by sub-tag: ${CC_AGENT_ID}',
    'requires_response': True,
}).execute()
```

- [ ] **Step 2: Vercel deploy TIMEOUT blocker insert (line 313)**

Edit the insert at lines 313-320. Change `'from_agent': '$AGENT_ID'` to `'from_agent': '$BASE_AGENT_ID'`. The whole insert becomes:

```bash
sb.table('agent_messages').insert({
    'from_agent': '$BASE_AGENT_ID',
    'to_agent': 'cai',
    'message_type': 'blocker',
    'subject': 'DEPLOY TIMEOUT — ${REPO_NAME} commit ${commit_sha:0:8} [${CC_AGENT_ID}]',
    'body': 'Vercel deployment did not reach READY within 5 minutes.\nCommit: ${commit_sha}\nCheck Vercel dashboard for build status.\n\nPosted by sub-tag: ${CC_AGENT_ID}',
    'requires_response': True,
}).execute()
```

- [ ] **Step 3: Exit trap — agent_status offline uses sub-tag + GUC (lines 380-412)**

This block ALREADY uses `$AGENT_ID`. After Task 7, `$AGENT_ID` == sub-tag, so the behavior is correct as-is. Add a clarifying comment. Replace the block header comment:

```bash
    # ARCH-035: flip agent_status to offline (psycopg direct for GUC).
    # Survives clean exit + SIGTERM (trap fires). Does NOT survive kill -9 —
    # stale_agents view catches that via 15-min heartbeat threshold.
```

With:

```bash
    # ARCH-035: flip agent_status to offline for THIS sub-tag ($CC_AGENT_ID,
    # aliased as $AGENT_ID above). psycopg direct is mandatory for GUC
    # semantics (SET LOCAL + UPDATE must be one transaction for the trigger
    # to see the GUC match). Survives clean exit + SIGTERM (trap fires).
    # Does NOT survive kill -9 — stale_agents view catches that via 15-min
    # heartbeat threshold.
```

(The inner SQL is unchanged — `$AGENT_ID` is already the sub-tag.)

- [ ] **Step 4: Exit trap — agent_messages session digest + agents.status use base (lines 421-438)**

Replace:

```bash
    "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
sb.table('agent_messages').insert({
    'from_agent': '$AGENT_ID',
    'to_agent': 'cai',
    'message_type': 'update',
    'subject': '$subject',
    'body': 'Session ended. Outcome: $outcome. Duration: ${duration_seconds}s. Repo: $REPO_NAME. Exit code: $exit_code.',
    'requires_response': False,
}).execute()
# Flip agent status to idle
sb.table('agents').update({'status': 'idle', 'current_task': None}).eq('id', '$AGENT_ID').execute()
" 2>/dev/null || true
```

With:

```bash
    "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
# agent_messages.from_agent uses BASE (FK-enforced). Sub-tag goes in subject.
sb.table('agent_messages').insert({
    'from_agent': '$BASE_AGENT_ID',
    'to_agent': 'cai',
    'message_type': 'update',
    'subject': '$subject [$CC_AGENT_ID]',
    'body': 'Session ended. Sub-tag: $CC_AGENT_ID. Outcome: $outcome. Duration: ${duration_seconds}s. Repo: $REPO_NAME. Exit code: $exit_code.',
    'requires_response': False,
}).execute()
# Flip BASE family status to idle (legacy agents table). Sub-tag agent_status
# was flipped to offline in the psycopg block above.
sb.table('agents').update({'status': 'idle', 'current_task': None}).eq('id', '$BASE_AGENT_ID').execute()
" 2>/dev/null || true
```

- [ ] **Step 5: Verify syntax**

Run: `bash -n scripts/launch_dangerous_cc.sh`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/launch_dangerous_cc.sh
git commit -m "feat(step-3): launcher vercel blockers + exit trap split base/sub-tag correctly"
```

---

### Task 10: Launcher edit — Opus 4.7 default with env override, final claude invocation

**Files:**
- Modify: `scripts/launch_dangerous_cc.sh` (lines 443-477)

- [ ] **Step 1: Resolve model + log it**

Add (directly after the `# ── 6. Launch claude` comment, before the existing echoes):

```bash
# ── 6. Launch claude (Step 3: Opus 4.7 default + MODEL env override) ─────────
#
# Delta-v2 non-load-bearing #3 — model precedence order (LAST argv --model
# wins, because `claude` uses last-wins flag parsing):
#   1. CLAUDE_PASSTHROUGH --model foo   — highest priority (operator escape
#                                         hatch via `-- --model foo` on the
#                                         launcher command line).
#   2. MODEL env var                    — resolves RESOLVED_MODEL, applied as
#                                         the first --model flag on argv.
#   3. hardcoded default claude-opus-4-7 — falls through when MODEL unset.
# Sequencing: resolve RESOLVED_MODEL from (MODEL env || default) → append
# `--model $RESOLVED_MODEL` to the claude call → append "${CLAUDE_PASSTHROUGH[@]}"
# AFTER it, so a passthrough --model overrides by coming later on argv.

RESOLVED_MODEL="${MODEL:-claude-opus-4-7}"
echo -e "${BOLD}${TEAL}▶ Resolved model: ${RESOLVED_MODEL}${RESET}"
if [ "$RESOLVED_MODEL" != "claude-opus-4-7" ]; then
    echo -e "${AMBER}  (override via MODEL env var — default is claude-opus-4-7)${RESET}"
fi

# Also stamp resolved model into current_task so CAI can observe model drift
# across sessions via agent_status.current_task sampling.
"$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
try:
    import psycopg
except ImportError:
    sys.exit(0)
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
if not dsn:
    sys.exit(0)
try:
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(\"SELECT set_config('app.current_agent_id', %s, true)\", ('$CC_AGENT_ID',))
            cur.execute(
                \"UPDATE agent_status SET current_task = %s, updated_at=now() WHERE agent_id = %s\",
                ('session-launch model=$RESOLVED_MODEL repo=$REPO_NAME', '$CC_AGENT_ID'),
            )
        conn.commit()
except Exception:
    pass
" 2>/dev/null || true
```

- [ ] **Step 2: Pass `--model` to the final `claude` invocation**

Find the existing final `claude` call (currently line 477):

```bash
claude --dangerously-skip-permissions "${CLAUDE_ARGS[@]+"${CLAUDE_ARGS[@]}"}"
```

Replace with:

```bash
# CLAUDE_ARGS was built in Task 7's arg-parse loop as CLAUDE_PASSTHROUGH;
# the old local shadowing loop at lines 454-464 is obsolete.
claude --dangerously-skip-permissions --model "$RESOLVED_MODEL" "${CLAUDE_PASSTHROUGH[@]+"${CLAUDE_PASSTHROUGH[@]}"}"
```

Then DELETE the now-redundant local arg-parse loop at (formerly) lines 454-464:

```bash
# Pass any extra args after -- to claude
CLAUDE_ARGS=()
PASS_THROUGH=false
for arg in "$@"; do
    if [ "$arg" = "--" ]; then
        PASS_THROUGH=true
        continue
    fi
    if $PASS_THROUGH; then
        CLAUDE_ARGS+=("$arg")
    fi
done
```

(This was duplicated from Task 7's top-level parse. Keep only the top-level `CLAUDE_PASSTHROUGH` array.)

- [ ] **Step 3: Verify syntax**

Run: `bash -n scripts/launch_dangerous_cc.sh`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/launch_dangerous_cc.sh
git commit -m "feat(step-3): launcher hardcodes --model claude-opus-4-7 with MODEL env override"
```

---

### Task 11: Integration smoke — dry-launch + verify agent_status state

**Files:**
- No new files; runs the real launcher up to the `claude` call in a throwaway fashion.

- [ ] **Step 1: Clean any stale test-family rows**

Run:

```bash
source .venv/bin/activate
python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv()
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(\"DELETE FROM agent_status WHERE agent_id LIKE 'cc-test-family-%'\")
print('OK')
"
```

Expected: `OK`

- [ ] **Step 2: Dry-invoke the helper directly from orchestrator pwd**

Run:

```bash
cd ~/wingmen/orchestrator
.venv/bin/python -m scripts.lib.auto_agent_id \
    --pwd "$(pwd)" \
    --repo orchestrator \
    --dsn "$(grep -E '^DATABASE_URL=|^SUPABASE_DB_URL=' .env | head -1 | cut -d= -f2-)"
```

Expected: JSON of shape `{"sub_tag": "cc-ihsanos-N", "base": "cc-ihsanos", "siblings": [...], "overlap_warnings": [...]}` on stdout, exit 0.

Record the allocated `sub_tag` (e.g. `cc-ihsanos-4`) — you will clean it up in Step 5.

- [ ] **Step 3: Verify the row landed with correct shape**

Run:

```bash
.venv/bin/python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv()
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT agent_id, status, current_task, scope_repos FROM agent_status WHERE agent_id LIKE 'cc-ihsanos-%' ORDER BY last_heartbeat DESC LIMIT 1\")
        print(cur.fetchone())
"
```

Expected: `('cc-ihsanos-N', 'working', 'session-launch', ['orchestrator'])` where N matches Step 2.

- [ ] **Step 4: Verify the bash launcher survives `bash -n`**

Run: `bash -n scripts/launch_dangerous_cc.sh`
Expected: no output, exit 0.

- [ ] **Step 4.5: Verify both `--repo` argument forms parse (delta-v2 non-load-bearing #2)**

Run (space-separated form):

```bash
cd ~/wingmen/orchestrator
.venv/bin/python -m scripts.lib.auto_agent_id \
    --pwd "$(pwd)" \
    --repo orchestrator \
    --dsn "$(grep -E '^DATABASE_URL=|^SUPABASE_DB_URL=' .env | head -1 | cut -d= -f2-)" | "$VENV_PY" -c 'import sys,json;d=json.load(sys.stdin);print(d["sub_tag"], d["base"])'
```

Expected: `cc-ihsanos-<N1> cc-ihsanos` on stdout, exit 0.

Run (equals-separated form):

```bash
cd ~/wingmen/orchestrator
.venv/bin/python -m scripts.lib.auto_agent_id \
    --pwd="$(pwd)" \
    --repo=orchestrator \
    --dsn="$(grep -E '^DATABASE_URL=|^SUPABASE_DB_URL=' .env | head -1 | cut -d= -f2-)" | "$VENV_PY" -c 'import sys,json;d=json.load(sys.stdin);print(d["sub_tag"], d["base"])'
```

Expected: `cc-ihsanos-<N2> cc-ihsanos` on stdout, exit 0. `argparse` accepts
both forms natively; this only asserts we don't regress launcher parse logic
across the two conventions.

Both N1 and N2 are additional test rows to clean up in Step 5.

- [ ] **Step 5: Clean up the smoke-test row**

Run:

```bash
.venv/bin/python -c "
import os, psycopg
from dotenv import load_dotenv
load_dotenv()
dsn = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
sub_tags = ['cc-ihsanos-N']  # <-- REPLACE with N from Step 2, plus N1+N2 from Step 4.5
with psycopg.connect(dsn, autocommit=True) as c:
    with c.cursor() as cur:
        for sub_tag in sub_tags:
            cur.execute(\"SELECT set_config('app.current_agent_id', %s, true)\", (sub_tag,))
            cur.execute(\"UPDATE agent_status SET status='offline', updated_at=now() WHERE agent_id=%s\", (sub_tag,))
            print(f'Flipped {sub_tag} → offline')
"
```

Expected: `Flipped cc-ihsanos-N → offline`

- [ ] **Step 6: Run the full pytest suite to confirm no regressions**

Run: `.venv/bin/pytest tests/test_auto_agent_id.py tests/test_agent_messages_poll.py -v`
Expected: All Task 2-6 tests PASS. `test_agent_messages_poll.py` may show the 2 pre-existing `"claude.ai" in text` failures — those are out-of-scope (pre-date Step 3).

- [ ] **Step 7: No commit (smoke only — no code changes in this task)**

---

### Task 12: STATUS.md + work_outputs + adversarial review filing

**Files:**
- Modify: `STATUS.md`
- Insert: `work_outputs` row via Supabase (linked to a tracking job)
- Post: agent_message to cai with the same shape as Step 2 msg 381

- [ ] **Step 1: Update STATUS.md**

Replace the top block (currently the GOVERNANCE-CLEANUP-001 Step 2 entry) with a Step 3 entry. Preserve the commit hashes (to be filled after Task 10 commits are in the log). Shape:

```markdown
# Orchestrator Status

## Currently Shipping

**GOVERNANCE-CLEANUP-001 Step 3 — launcher multi-repo + dual-identity + Opus 4.7**
- Commits: <c1> (helper + tests), <c2> (launcher header/args), <c3> (launcher body + trap), <c4> (model default)
- Composes msgs 315 (multi-repo scope) / 317 (auto-identity) / 324 (Opus 4.7 default).
- New helper: `scripts/lib/auto_agent_id.py` — pwd→family map with fail-fast ABORT,
  advisory-lock sub-tag allocation with stale-reclaim, soft overlap warning.
- Launcher: `scripts/launch_dangerous_cc.sh` — `--repo` arg, dual-identity exports
  (`CC_AGENT_ID` sub-tag, `CC_BASE_AGENT_ID` base), `--model claude-opus-4-7` default
  with `MODEL` env override, exit trap split along identity axis.
- 21/21 unit + integration tests PASS. Smoke from orchestrator pwd PASS.
- Sub-identity promotion to first-class FK deferred to Step 4 (BUG-024 Phase 1).

## Previously Completed

[... previous Step 2 block demoted here ...]
```

- [ ] **Step 2: Insert a tracking job row**

Same pattern as Step 2 — `work_outputs.job_id` is NOT NULL.

```bash
.venv/bin/python -c "
import os
from supabase import create_client
from dotenv import load_dotenv
load_dotenv()
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
r = sb.table('jobs').insert({
    'repo': 'orchestrator',
    'description': 'GOVERNANCE-CLEANUP-001 Step 3 — launcher multi-repo + dual-identity + Opus 4.7',
    'status': 'completed',
    'priority': 2,
}).execute()
print('job id:', r.data[0]['id'])
"
```

Record the `job_id`.

- [ ] **Step 3: Insert the work_output**

```bash
.venv/bin/python -c "
import os
from supabase import create_client
from dotenv import load_dotenv
load_dotenv()
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
sb.table('work_outputs').insert({
    'job_id': <JOB_ID>,
    'repo': 'orchestrator',
    'task_ref': 'GOVERNANCE-CLEANUP-001-step-3',
    'output_type': 'implementation',
    'content': 'Launcher revision shipped. <c1>/<c2>/<c3>/<c4> commits. 21/21 pytest PASS. Smoke PASS. Dual-identity convention live — CC_AGENT_ID (sub-tag) + CC_BASE_AGENT_ID (base). Opus 4.7 hardcoded default with MODEL override. Step 4 (BUG-024 Phase 1) unblocked.',
}).execute()
print('work_outputs inserted')
"
```

- [ ] **Step 4: File adversarial review request to CAI**

Post one message to cai (same shape as Step 2 msg 381 — name the delta set, invite attack on the 3 load-bearing surfaces):

```bash
.venv/bin/python -c "
import os
from supabase import create_client
from dotenv import load_dotenv
load_dotenv()
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
sb.table('agent_messages').insert({
    'from_agent': 'cc-ihsanos',
    'to_agent': 'cai',
    'message_type': 'review_request',
    'subject': 'GOVERNANCE-CLEANUP-001 Step 3 SHIPPED — launcher revision ready for adversarial review [cc-ihsanos-3]',
    'body': (
        'Step 3 ready for your adversarial review before I flip the jobs row to implemented.\n\n'
        'Commits: <c1>/<c2>/<c3>/<c4>. 21/21 unit+integration PASS. Smoke from orchestrator PASS.\n\n'
        'Load-bearing surfaces for your attack:\n'
        '  L1. pwd→family mapping fail-fast: any pwd not in the data-driven family map (loaded from agents.repo_scope) aborts before claude starts; worktree suffixes are stripped via git-toplevel + regex.\n'
        '      Attack: can a symlink, worktree-rename, or race between git-toplevel + resolve_base_agent_id bypass this? Can a repo_scope write between load_family_map and allocate_sub_tag_and_register mis-route the allocation?\n'
        '  L2. advisory-lock + UPSERT atomicity: scan + pick + UPSERT are one TX with pg_try_advisory_xact_lock (5s retry + pg_locks diagnostic on timeout).\n'
        '      Attack: can two launchers starting within <1s double-allocate the same N? Can a stuck holder bypass the timeout?\n'
        '  L3. exit trap identity split: agent_status flip uses sub-tag ($AGENT_ID, now = sub-tag); \n'
        '      agent_messages inserts + agents.status update use $BASE_AGENT_ID.\n'
        '      Attack: any write site I missed? Any path where $AGENT_ID is still used for a BASE-expecting write?\n\n'
        'If clean: reply ack and I ship to main. If you find issues: I address in a single follow-up delta same shape as msg 381.\n\n'
        'Thread: GOVERNANCE-CLEANUP-001.'
    ),
    'requires_response': True,
}).execute()
print('filed for adversarial review')
"
```

- [ ] **Step 5: Commit STATUS.md and summary**

```bash
git add STATUS.md
git commit -m "chore: STATUS.md — GOVERNANCE-CLEANUP-001 Step 3 shipped"
```

Do NOT push until CAI acks the review.

---

## Self-Review

**1. Spec coverage:**
- msg 315 (multi-repo scope) → Tasks 7 (`--repo` arg + pwd resolution), 8 (scope_repos in agent_status), 11 (smoke verifies scope_repos lands + both `--repo` forms).
- msg 317 (auto-identity) → Tasks 1.5 (`load_family_map` data-driven), 2 (mapping + worktree strip), 3 (pick_sub_tag), 4 (allocate), 6 (CLI), 7 (launcher calls CLI).
- msg 324 (Opus 4.7 default) → Task 10 (hardcoded `--model claude-opus-4-7` + `MODEL` env override + current_task stamp + precedence order comment).
- CAI msg 395 Q2 mapping-table constraint → Task 1.5 (data-driven from `agents.repo_scope`) + Task 2 (`resolve_base_agent_id` with fail-fast `UnknownRepoError`).
- CAI msg 395 exit-trap preservation → Task 9 Steps 3-4 (comment-clarifies agent_status uses sub-tag; agent_messages uses base).
- CAI msg 395 Q3-C overlap warning → Task 5 (`scan_overlap_siblings`, now returns `list[tuple[str, int]]` with heartbeat age) + Task 8 Step 1 (header print formatted "aid (Ns ago)").
- Non-load-bearing confirmations (advisory lock namespace, 30min stale-cutoff, --repo + pwd both supported) → Tasks 4 + 7.

**Delta-v2 (CAI msg 397) coverage:**
- L1-B1 (worktree-suffix) → Task 2 (`strip_worktree_suffix` + `_git_toplevel` + `resolve_base_agent_id(pwd, family_map)`).
- L1-B2 (map drift) → Task 1.5 (`load_family_map` from `agents.repo_scope`, wingmen- prefix stripped; raises `ValueError` on duplicate claim).
- L2 (advisory-lock hang) → Task 4 (`pg_try_advisory_xact_lock` + 5s retry + `LockTimeoutError` + `pg_locks` diagnostic).
- L3-A1 (build_launch_context write-site) → Task 8 Step 2 (passes `$CC_BASE_AGENT_ID`, not `$CC_AGENT_ID`).
- L3-A2 (agents.last_heartbeat double-write) → deferred to Step 4 (BUG-024 Phase 1) — documented in header "Deferred to Step 4" block.
- Non-load-bearing #2 (both `--repo` forms) → Task 11 Step 4.5.
- Non-load-bearing #3 (MODEL order) → Task 10 Step 1 comment.
- Non-load-bearing #4 (heartbeat-age in overlap warning) → Task 5 return type + Task 7 bash formatter.
- Non-load-bearing #5 (self-surgery preamble) → plan header.
- Checkpoint "CLI owns map" → Task 6 `main()` calls `load_family_map(args.dsn)` itself.
- CAI-AGENTS-001 (4-family split) → Task 1.5 tests cover cc-cosem family; data-driven map handles structurally.

**2. Placeholder scan:**
- Task 12 uses `<c1>/<c2>/<c3>/<c4>` and `<JOB_ID>` — these are explicit "fill at commit time" markers, not plan placeholders. Acceptable because the hashes don't exist until Tasks 7-10 land.
- Step 5 of Task 11 has a literal `cc-ihsanos-N` to replace with the actual N from Step 2 — explicit instruction.
- No other TBDs.

**3. Type consistency:**
- `load_family_map(dsn: str) -> dict[str, str]` — defined in Task 1.5, called by `main()` in Task 6. Consistent.
- `resolve_base_agent_id(pwd: str, family_map: dict[str, str]) -> str` — delta-v2 signature with map param. Used in Task 2, called by `main()` in Task 6 with `family_map=load_family_map(args.dsn)`. Consistent.
- `strip_worktree_suffix(segment: str) -> str` — defined in Task 2, used inside `resolve_base_agent_id`. Consistent.
- `_git_toplevel(pwd: str) -> str | None` — defined in Task 2, used inside `resolve_base_agent_id`. Consistent.
- `pick_sub_tag(base: str, active: list[str]) -> str` — used in Task 3, called by `allocate_sub_tag_and_register` in Task 4. Consistent.
- `AllocResult(sub_tag, siblings)` — defined in Task 4, returned by `allocate_sub_tag_and_register`, consumed by `main()` in Task 6. Consistent.
- `LockTimeoutError(RuntimeError)` — defined in Task 4, caught by `main()` in Task 6 (returns exit 1 with stderr message). Consistent.
- `scan_overlap_siblings(base, scope_repo, dsn, exclude_sub_tag, stale_cutoff_minutes=30) -> list[tuple[str, int]]` — delta-v2 return type. Task 5 signature matches Task 6 call site; Task 7 bash unpacker formats `(aid, age)` tuples. Consistent.
- Bash variable names `CC_AGENT_ID` (sub-tag), `CC_BASE_AGENT_ID` (base), `OVERLAP_WARNINGS` — introduced in Task 7, referenced in Tasks 8/9/10 consistently. Task 8 Step 2 passes `$CC_BASE_AGENT_ID` to `build_launch_context` (delta-v2 L3-A1 fix). Local aliases `AGENT_ID`/`BASE_AGENT_ID` mirror sub-tag/base 1:1.

No issues found — plan is internally consistent across delta-v2 surfaces.

---

## Step 3.5 — CAI-RESP-053 Integration (Tasks 13–17)

**Source:** CAI review of Step 3 (msg 408 → our delta msg 413 → CAI ack msg 416).

**Scope:** 1 blocker (B1), 3 ship-gates (G1–G3), 4 amendments (A1–A4), consolidated into 5 sequenced tasks. 2 items (D1/D2) are scheduled — not implemented here — but documented below.

**CAI-confirmed defaults (msg 416):**
1. A2 framing → **asymmetric fail-loud**: `agents.last_heartbeat` write fails loud to tailable log; `agent_status.last_heartbeat` (psycopg+GUC) stays best-effort (stale_agents 15-min backstop covers it).
2. G3 ceiling → **20 sub-tags per base**.
3. B1 CHECK → `sub_tag IS NULL OR sub_tag LIKE from_agent || '-%'` (LIKE form, not numeric-N regex — numeric enforcement stays in `pick_sub_tag`).
4. G1 → **overwrite** existing plan file, append new Step 3.5 section inline (git preserves the three source commits 797565e + 77b6111 + 7be7519).

**CAI sub-amendment (msg 416):** Log files introduced in A2/A4 must be size-bounded. `/tmp/cc_heartbeat_err.log` → 10MB cap (check-and-truncate on each write). `/tmp/cc_lock_timeout_<UTC-iso>.log` → keep newest 20, unlink older via glob+sort.

**Deferred (scheduled, NOT in Step 3.5):**
- **D1 = Step 4 = BUG-024 Phase 1** — sub-identity (`cc-ihsanos-N`) promoted to first-class `agents.id` FK. Collapses the dual write pattern into a single identity surface. Committed-date: TBD after Step 3.5 ships.
- **D2 = Step 5 = BUG-027** — exit-trap janitor cron (exit trap doesn't survive `kill -9`). Committed-date: TBD after Step 4.

### Task 13: B1 — agent_messages.sub_tag column + CHECK + partial index

**Files:**
- Create: `supabase/migrations/20260420_agent_messages_sub_tag.sql`
- Create: `tests/test_agent_messages_sub_tag_migration.py`

**Why:** Sub-tag is currently string-encoded into `subject`/`body` (introduced in Task 9 of Step 3). String-encoding is opaque to queries, un-indexable, and has no schema-level impersonation protection. Structural column is cheaper to query and the CHECK constraint structurally blocks cross-family impersonation (a row claiming `from_agent='cc-ihsanos'` with `sub_tag='cc-scholar-1'` gets rejected by the DB, not a downstream parser).

**Schema:**
- `sub_tag TEXT NULL` (nullable: CAI writes, orchestrator background jobs, legacy rows all leave NULL).
- `CHECK (sub_tag IS NULL OR sub_tag LIKE from_agent || '-%')` — named `agent_messages_sub_tag_family_prefix_chk`.
- Partial index `idx_agent_messages_sub_tag ON agent_messages (from_agent, sub_tag) WHERE sub_tag IS NOT NULL`.

- [ ] **Step 1: Write the failing test file first**

```python
# tests/test_agent_messages_sub_tag_migration.py
"""B1 migration: agent_messages.sub_tag column, CHECK constraint, partial index."""
import os
import pytest
import psycopg
from pathlib import Path

MIGRATION_PATH = Path(__file__).parent.parent / "supabase/migrations/20260420_agent_messages_sub_tag.sql"


def _dsn():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set — integration test")
    return dsn


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), f"migration file missing: {MIGRATION_PATH}"


def test_sub_tag_column_present():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'agent_messages' AND column_name = 'sub_tag'
            """
        )
        row = cur.fetchone()
        assert row is not None, "sub_tag column not found"
        assert row[0] == "text"
        assert row[1] == "YES"  # nullable


def test_check_rejects_cross_family_impersonation():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, sub_tag)
                VALUES ('cc-ihsanos', 'cai', 'update', 't', 'b', 'cc-scholar-1')
                """
            )


def test_check_accepts_matching_family():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, sub_tag)
            VALUES ('cc-ihsanos', 'cai', 'update', 'b1-test-accept', 'b', 'cc-ihsanos-99')
            RETURNING id
            """
        )
        mid = cur.fetchone()[0]
        cur.execute("DELETE FROM agent_messages WHERE id = %s", (mid,))


def test_check_accepts_null():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body)
            VALUES ('cai', 'cc-ihsanos', 'update', 'b1-test-null', 'b')
            RETURNING id, sub_tag
            """
        )
        mid, sub_tag = cur.fetchone()
        assert sub_tag is None
        cur.execute("DELETE FROM agent_messages WHERE id = %s", (mid,))


def test_partial_index_exists():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
             WHERE tablename = 'agent_messages' AND indexname = 'idx_agent_messages_sub_tag'
            """
        )
        row = cur.fetchone()
        assert row is not None, "partial index missing"
        assert "sub_tag IS NOT NULL" in row[0]


def test_migration_idempotent():
    sql = MIGRATION_PATH.read_text()
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql)  # should succeed even if already applied
        cur.execute(sql)  # running twice = no-op
```

- [ ] **Step 2: Run the failing test**

Run: `pytest tests/test_agent_messages_sub_tag_migration.py -v`
Expected: all tests FAIL (migration file does not yet exist / column not present).

- [ ] **Step 3: Write the migration**

```sql
-- supabase/migrations/20260420_agent_messages_sub_tag.sql
-- B1 (CAI-RESP-053): replace string-encoded sub-tag with structural column.
-- Idempotent — safe to re-run.

ALTER TABLE agent_messages
    ADD COLUMN IF NOT EXISTS sub_tag TEXT NULL;

-- Structural impersonation guard: sub_tag must be an N-suffix of from_agent.
-- NULL is permitted (CAI writes, background jobs, legacy rows).
ALTER TABLE agent_messages
    DROP CONSTRAINT IF EXISTS agent_messages_sub_tag_family_prefix_chk;
ALTER TABLE agent_messages
    ADD CONSTRAINT agent_messages_sub_tag_family_prefix_chk
    CHECK (sub_tag IS NULL OR sub_tag LIKE from_agent || '-%');

CREATE INDEX IF NOT EXISTS idx_agent_messages_sub_tag
    ON agent_messages (from_agent, sub_tag)
    WHERE sub_tag IS NOT NULL;

COMMENT ON COLUMN agent_messages.sub_tag IS
  'Sub-identity (e.g. cc-ihsanos-3) of the CC session that wrote this row. '
  'NULL for CAI, orchestrator background jobs, and legacy rows. '
  'CHECK constraint enforces structural family match with from_agent.';
```

- [ ] **Step 4: Apply the migration and run tests**

Run:
```bash
psql "$DATABASE_URL" -f supabase/migrations/20260420_agent_messages_sub_tag.sql
pytest tests/test_agent_messages_sub_tag_migration.py -v
```
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260420_agent_messages_sub_tag.sql \
        tests/test_agent_messages_sub_tag_migration.py
git commit -m "$(cat <<'EOF'
feat(db): B1 agent_messages.sub_tag column + CHECK + partial index

CAI-RESP-053 blocker B1. Replaces string-encoded sub-tag (Task 9 of
Step 3) with a structural column. CHECK `sub_tag LIKE from_agent || '-%'`
rejects cross-family impersonation at the DB, not a downstream parser.

Thread: GOVERNANCE-CLEANUP-001 Step 3.5.
EOF
)"
```

### Task 14: Update launcher insert sites — drop string encoding, populate sub_tag

**Files:**
- Modify: `scripts/launch_dangerous_cc.sh` — three inserts at L377, L409, L528.

**Why:** Task 13 adds the column; Task 14 switches the writers to populate it. String encoding (`[${CC_AGENT_ID}]` suffix on subject; `Sub-tag: ${CC_AGENT_ID}` in body) is deleted — it has no consumers (the string convention was introduced in Task 9 of this same Step 3 and never read downstream).

**Reconnaissance (already done in CAI-RESP-052 delta):**
- L377 — DEPLOY FAILED blocker.
- L409 — DEPLOY TIMEOUT blocker.
- L528 — session-end digest.
- `scripts/audit_mac_mini.py:374` is a background job not in the launcher chain (leaves `sub_tag=NULL`, correct). Out of scope.
- No other writers found via `grep -rn "agent_messages.*insert" scripts/`.

- [ ] **Step 1: Edit L377 block (DEPLOY FAILED)**

Old:
```python
sb.table('agent_messages').insert({
    'from_agent': '$BASE_AGENT_ID',
    'to_agent': 'cai',
    'message_type': 'blocker',
    'subject': 'DEPLOY FAILED — ${REPO_NAME} commit ${commit_sha:0:8} [${CC_AGENT_ID}]',
    'body': 'Vercel deployment reached ERROR state.\nCommit: ${commit_sha}\nBuild log: ${build_log_url}\nAction required: read build log, fix, push again.\n\nPosted by sub-tag: ${CC_AGENT_ID}',
    'requires_response': True,
}).execute()
```

New:
```python
sb.table('agent_messages').insert({
    'from_agent': '$BASE_AGENT_ID',
    'sub_tag': '$CC_AGENT_ID',
    'to_agent': 'cai',
    'message_type': 'blocker',
    'subject': 'DEPLOY FAILED — ${REPO_NAME} commit ${commit_sha:0:8}',
    'body': 'Vercel deployment reached ERROR state.\nCommit: ${commit_sha}\nBuild log: ${build_log_url}\nAction required: read build log, fix, push again.',
    'requires_response': True,
}).execute()
```

- [ ] **Step 2: Edit L409 block (DEPLOY TIMEOUT)**

Old subject `... [${CC_AGENT_ID}]` → drop suffix.
Old body `... \n\nPosted by sub-tag: ${CC_AGENT_ID}` → drop trailing line.
Add `'sub_tag': '$CC_AGENT_ID',` immediately after `'from_agent'` line.

- [ ] **Step 3: Edit L528 block (session-end digest)**

Old subject `'$subject [$CC_AGENT_ID]'` → `'$subject'`.
Old body `'Session ended. Sub-tag: $CC_AGENT_ID. Outcome: ...'` → `'Session ended. Outcome: ...'`.
Add `'sub_tag': '$CC_AGENT_ID',`.

- [ ] **Step 4: Verify no stale `[${CC_AGENT_ID}]` or `Sub-tag:` strings remain**

Run:
```bash
grep -nE '\[\$\{CC_AGENT_ID\}\]|Sub-tag:|Posted by sub-tag' scripts/launch_dangerous_cc.sh
```
Expected: no output. (Comments documenting the convention are allowed to stay; only the payload strings must go.)

- [ ] **Step 5: Smoke test — insert a blocker and confirm column populated**

Run (with DATABASE_URL set):
```bash
CC_AGENT_ID=cc-ihsanos-99 BASE_AGENT_ID=cc-ihsanos REPO_NAME=orchestrator \
  python -c "
import os
from dotenv import load_dotenv
load_dotenv('.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
r = sb.table('agent_messages').insert({
    'from_agent': 'cc-ihsanos', 'sub_tag': 'cc-ihsanos-99', 'to_agent': 'cai',
    'message_type': 'update', 'subject': 'b1 smoke', 'body': 'x',
    'requires_response': False,
}).execute()
print(r.data[0]['id'], r.data[0]['sub_tag'])
sb.table('agent_messages').delete().eq('id', r.data[0]['id']).execute()
"
```
Expected: output has `cc-ihsanos-99` as the second token.

- [ ] **Step 6: Commit**

```bash
git add scripts/launch_dangerous_cc.sh
git commit -m "$(cat <<'EOF'
feat(launcher): drop sub-tag string encoding, populate sub_tag column

CAI-RESP-053 B1 call-site updates. Three agent_messages inserts
(DEPLOY FAILED, DEPLOY TIMEOUT, session-end) now write sub_tag via
the new column. Subject/body string artefacts removed — no consumers.

Thread: GOVERNANCE-CLEANUP-001 Step 3.5.
EOF
)"
```

### Task 15: G3 MAX_SUB_TAGS + A1 lock-namespace registry

**Files:**
- Modify: `scripts/lib/auto_agent_id.py` — L19 (`_ALLOC_LOCK_KEY`), L210 (`pg_try_advisory_xact_lock`), L226 (`pg_locks` lookup), add module constants, add `NamespaceExhaustedError`, add guard at bottom of `pick_sub_tag`.
- Create: `docs/lock-namespace.md`
- Create/Modify: `tests/test_auto_agent_id.py` — add tests for exhaustion + no-supabase-py import guard.

**Why:**
- **G3**: `pick_sub_tag` as written will return `cc-ihsanos-21`, `cc-ihsanos-22`… forever. 20 concurrent sub-tags per base = 80 across 4 families. Anything at that level is runaway launchd/watchdog, not legitimate concurrency. Fail loud.
- **A1**: `hashtext('cc-agent-id-alloc')` collapses a string to one 32-bit int. Today only one call site uses advisory locks, so collision is theoretical; registering the integer now locks the namespace down before future use sites introduce a real collision. Switch to `pg_try_advisory_xact_lock(1001)` (bigint).
- **A3 guard**: `allocate_sub_tag_and_register` already uses psycopg end-to-end (verified in delta recon — L202–245). A regression-guard test keeps it that way.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auto_agent_id.py` (create if missing):

```python
# tests/test_auto_agent_id.py — Step 3.5 additions
"""G3 + A1 + A3 guards for auto_agent_id module."""
import ast
from pathlib import Path
import pytest

from scripts.lib.auto_agent_id import (
    pick_sub_tag,
    NamespaceExhaustedError,
    _MAX_SUB_TAGS_PER_BASE,
    _ALLOC_LOCK_ID,
)


def test_max_sub_tags_ceiling_is_20():
    assert _MAX_SUB_TAGS_PER_BASE == 20


def test_alloc_lock_id_is_registered_int():
    assert isinstance(_ALLOC_LOCK_ID, int)
    assert _ALLOC_LOCK_ID == 1001


def test_pick_sub_tag_raises_when_all_slots_taken():
    base = "cc-test-family"
    active = [f"{base}-{n}" for n in range(1, _MAX_SUB_TAGS_PER_BASE + 1)]
    with pytest.raises(NamespaceExhaustedError) as exc:
        pick_sub_tag(base, active)
    msg = str(exc.value)
    assert base in msg
    assert str(_MAX_SUB_TAGS_PER_BASE) in msg
    # The message must include the siblings list so the operator can spot the culprit.
    assert "cc-test-family-20" in msg


def test_pick_sub_tag_returns_first_free_below_ceiling():
    base = "cc-test-family"
    active = [f"{base}-{n}" for n in range(1, _MAX_SUB_TAGS_PER_BASE)]  # 1..19 taken
    assert pick_sub_tag(base, active) == f"{base}-{_MAX_SUB_TAGS_PER_BASE}"


def test_auto_agent_id_does_not_import_supabase_py():
    """A3 guard: allocate_sub_tag_and_register must stay on psycopg.
    supabase-py is PostgREST + pooled — incompatible with GUC."""
    module_src = Path("scripts/lib/auto_agent_id.py").read_text()
    tree = ast.parse(module_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "supabase" not in alias.name.lower(), f"found import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").lower()
            assert "supabase" not in mod, f"found from-import {node.module}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_auto_agent_id.py -v -k "max_sub_tags or alloc_lock_id or exhausted or first_free or supabase"`
Expected: ImportError or AssertionError — `_MAX_SUB_TAGS_PER_BASE`, `NamespaceExhaustedError`, `_ALLOC_LOCK_ID` not yet defined.

- [ ] **Step 3: Edit `scripts/lib/auto_agent_id.py` — top-of-module constants + exception**

Replace the block around line 19:

Old:
```python
_ALLOC_LOCK_KEY = "cc-agent-id-alloc"
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_INTERVAL_SECONDS = 0.5
_LOCK_RETRY_COUNT = 10  # 10 × 0.5s = 5s ceiling
```

New:
```python
# Advisory-lock registry (see docs/lock-namespace.md).
# Identity/scheduling range: 1000–1099. Reserve new keys there.
_ALLOC_LOCK_ID = 1001  # "cc-agent-id-alloc" — allocator critical section
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_INTERVAL_SECONDS = 0.5
_LOCK_RETRY_COUNT = 10  # 10 × 0.5s = 5s ceiling

# G3 (CAI-RESP-053): 20 concurrent sub-tags per base is pathology threshold.
# 4 families × 20 = 80 concurrent identities — anything approaching this is
# runaway launchd/watchdog, not legitimate concurrency. Fail loud.
_MAX_SUB_TAGS_PER_BASE = 20


class NamespaceExhaustedError(RuntimeError):
    """Raised when pick_sub_tag is asked to allocate past _MAX_SUB_TAGS_PER_BASE.
    Operator should investigate rogue launchd/watchdog rather than raising the cap."""
```

- [ ] **Step 4: Edit `pick_sub_tag` to enforce the ceiling**

Replace the final `n = 1 … return …` block:

Old:
```python
    n = 1
    while n in taken:
        n += 1
    return f"{base}-{n}"
```

New:
```python
    n = 1
    while n in taken:
        n += 1
    if n > _MAX_SUB_TAGS_PER_BASE:
        siblings = sorted(f"{base}-{k}" for k in taken)
        raise NamespaceExhaustedError(
            f"{base} exhausted ({_MAX_SUB_TAGS_PER_BASE} concurrent sub-tags). "
            f"Likely runaway launchd/watchdog. "
            f"Run `ps aux | grep launch_dangerous_cc` and cull. "
            f"Siblings: {siblings}"
        )
    return f"{base}-{n}"
```

- [ ] **Step 5: Swap hashtext(string) → bigint literal in the two advisory-lock call sites**

Replace L210 block:

Old:
```python
                cur.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
                    (_ALLOC_LOCK_KEY,),
                )
```

New:
```python
                cur.execute(
                    "SELECT pg_try_advisory_xact_lock(%s)",
                    (_ALLOC_LOCK_ID,),
                )
```

Replace L220–L228 diagnostic block (pg_locks lookup):

Old:
```python
                cur.execute(
                    """
                    SELECT pid, granted, query_start, state
                      FROM pg_locks l
                      JOIN pg_stat_activity a ON a.pid = l.pid
                     WHERE locktype = 'advisory'
                       AND objid = hashtext(%s)::bigint
                    """,
                    (_ALLOC_LOCK_KEY,),
                )
```

New:
```python
                cur.execute(
                    """
                    SELECT pid, granted, query_start, state
                      FROM pg_locks l
                      JOIN pg_stat_activity a ON a.pid = l.pid
                     WHERE locktype = 'advisory'
                       AND objid = %s
                    """,
                    (_ALLOC_LOCK_ID,),
                )
```

Replace the `raise LockTimeoutError(...)` message:

Old:
```python
                raise LockTimeoutError(
                    f"advisory lock {_ALLOC_LOCK_KEY!r} held >{_LOCK_TIMEOUT_SECONDS}s. "
                    f"Holders: {holders}"
                )
```

New:
```python
                raise LockTimeoutError(
                    f"advisory lock AGENT_ID_ALLOC (id={_ALLOC_LOCK_ID}) "
                    f"held >{_LOCK_TIMEOUT_SECONDS}s. Holders: {holders}"
                )
```

- [ ] **Step 6: Create `docs/lock-namespace.md`**

```markdown
# Advisory Lock Namespace Registry

Postgres advisory locks use a shared 64-bit integer key space across the
database. To prevent collisions, every advisory-lock key used in this
codebase MUST be registered here before use.

## Reserved ranges

| Range      | Purpose                       | Status  |
|------------|-------------------------------|---------|
| 1000–1099  | Identity & scheduling         | active  |
| 1100–1199  | Migrations & schema evolution | reserved|
| 1200–1299  | Build/deploy coordination     | reserved|
| 1300+      | Future use                    | reserved|

## Active registrations

| Key ID | Constant name     | Owner                                    | Purpose                                  |
|--------|-------------------|------------------------------------------|------------------------------------------|
| 1001   | AGENT_ID_ALLOC    | `scripts/lib/auto_agent_id.py`           | Sub-tag allocator critical section.      |

## Rules

1. Pick the next available integer in the appropriate range.
2. Add a row to the "Active registrations" table in the same commit
   that introduces the lock. A PR that adds a `pg_*_advisory_*_lock`
   call without a registry entry must be rejected in review.
3. Never use `hashtext('some-string')` — it's a 32-bit hash and the
   registry loses meaning. Always use an explicit bigint literal.
4. Document in the owner file:
   ```python
   # See docs/lock-namespace.md — registered as AGENT_ID_ALLOC.
   _ALLOC_LOCK_ID = 1001
   ```

## History

- **2026-04-20** — Registry created (CAI-RESP-053 A1). Migrated
  `auto_agent_id.py` from `hashtext('cc-agent-id-alloc')` to `1001`.
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_auto_agent_id.py -v`
Expected: PASS (including new G3/A1/A3 tests + pre-existing pick_sub_tag tests still green).

- [ ] **Step 8: Commit**

```bash
git add scripts/lib/auto_agent_id.py docs/lock-namespace.md tests/test_auto_agent_id.py
git commit -m "$(cat <<'EOF'
feat(identity): G3 MAX_SUB_TAGS ceiling + A1 lock-namespace registry

CAI-RESP-053:
- G3: pick_sub_tag raises NamespaceExhaustedError past 20 concurrent
  sub-tags per base. Prevents silent sprawl of runaway launchd.
- A1: advisory lock key is now a registered bigint (1001 = AGENT_ID_ALLOC)
  instead of hashtext('cc-agent-id-alloc'). docs/lock-namespace.md locks
  the integer namespace down before future use sites introduce collisions.
- A3 guard: regression test asserts auto_agent_id.py never imports
  supabase-py (PostgREST is GUC-incompatible).

Thread: GOVERNANCE-CLEANUP-001 Step 3.5.
EOF
)"
```

### Task 16: A2 asymmetric heartbeat fail-loud + A4 LockTimeout diagnostic flush

**Files:**
- Modify: `scripts/launch_dangerous_cc.sh` — heartbeat loop at L217–L260.
- Modify: `scripts/lib/auto_agent_id.py` — `allocate_sub_tag_and_register` LockTimeoutError path (inside the `if not acquired:` block).

**Why:**
- **A2 asymmetric (CAI-confirmed)**: `agents.last_heartbeat` feeds `stale_agents` view + telegram `/status` + operator dashboards. If it silently stops, operator goes blind. `agent_status.last_heartbeat` (psycopg+GUC) has its own 15-min view-based backstop → stays best-effort. Drop `|| true` on the supabase-py path; redirect stderr to a dedicated log.
- **A4**: `LockTimeoutError` today prints holders to stderr only; if the launcher is run under launchd, stderr may already be redirected or swallowed. A dedicated diagnostic file (`/tmp/cc_lock_timeout_<UTC-iso>.log`) + fsync before raise gives the operator a forensic trail even after the shell exits.
- **Size caps (CAI sub-amendment)**: unbounded `/tmp/` growth is a known launcher pathology. Both logs get caps.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_launcher_logs.py`:

```python
"""Tests for launcher heartbeat error log and LockTimeout diagnostic flush."""
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
import pytest


LAUNCHER = Path("scripts/launch_dangerous_cc.sh")
HELPER = Path("scripts/lib/auto_agent_id.py")


def test_launcher_heartbeat_agents_path_does_not_swallow_stderr():
    """The supabase-py heartbeat (agents table) must fail loud, not `|| true`."""
    src = LAUNCHER.read_text()
    # Find the `sb.table('agents').update(...).execute()` heartbeat block
    # and assert the surrounding shell wrapper does NOT have `|| true`.
    # Crude but effective: the agents-table heartbeat should be followed by
    # `2>>/tmp/cc_heartbeat_err.log` (append redirect), not `2>/dev/null || true`.
    m = re.search(
        r"sb\.table\('agents'\)\.update\(\{'last_heartbeat'[\s\S]*?execute\(\)\n\"\n(\s*)([^\n]+)",
        src,
    )
    assert m, "could not locate agents heartbeat block"
    tail = m.group(2)
    assert "/tmp/cc_heartbeat_err.log" in tail, f"expected redirect to heartbeat err log, got {tail!r}"
    assert "|| true" not in tail, f"agents heartbeat must fail loud, got {tail!r}"


def test_launcher_heartbeat_cap_check_present():
    """Heartbeat log must be size-capped (10MB) to prevent /tmp sprawl."""
    src = LAUNCHER.read_text()
    assert "10485760" in src or "10*1024*1024" in src or "10 * 1024 * 1024" in src, \
        "expected 10MB cap constant in launcher"
    assert "cc_heartbeat_err.log" in src


def test_lock_timeout_flush_path_present():
    """allocate_sub_tag_and_register writes a diagnostic file before raising."""
    src = HELPER.read_text()
    assert "cc_lock_timeout_" in src, "expected LockTimeout diagnostic filename pattern"
    assert "fsync" in src, "expected fsync on the diagnostic file handle"
    # Newest-20 retention via glob + sort + unlink
    assert "/tmp/cc_lock_timeout_" in src
    assert re.search(r"glob.*cc_lock_timeout", src), "expected glob for retention pruning"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_launcher_logs.py -v`
Expected: all 3 tests FAIL (current code has `|| true` and no diagnostic file).

- [ ] **Step 3: Edit heartbeat loop in `scripts/launch_dangerous_cc.sh` (A2)**

Replace L223–L236 (the `while true` body, agents-table write):

Old:
```bash
    while true; do
        sleep 300
        "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
from supabase import create_client
from datetime import datetime, timezone
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
now_iso = datetime.now(timezone.utc).isoformat()
# agents table — base id (FK-enforced)
sb.table('agents').update({'last_heartbeat': now_iso}).eq('id', '$BASE_AGENT_ID').execute()
" 2>/dev/null || true
```

New:
```bash
    local HB_ERR_LOG="/tmp/cc_heartbeat_err.log"
    local HB_ERR_CAP=10485760  # 10 MB cap (CAI-RESP-053 sub-amendment)
    while true; do
        sleep 300
        # A2 (CAI-RESP-053): agents.last_heartbeat feeds stale_agents view +
        # telegram /status + operator dashboards. FAIL LOUD — no `|| true`.
        # Errors append to a tailable log; size-capped to HB_ERR_CAP bytes.
        if [ -f "$HB_ERR_LOG" ] && [ "$(wc -c < "$HB_ERR_LOG")" -gt "$HB_ERR_CAP" ]; then
            : > "$HB_ERR_LOG"   # truncate once cap exceeded
            echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [heartbeat-log] truncated (cap=$HB_ERR_CAP B)" >> "$HB_ERR_LOG"
        fi
        "$VENV_PY" -c "
import os, sys
sys.path.insert(0, '$ORCH_DIR')
from dotenv import load_dotenv
load_dotenv('$ORCH_DIR/.env')
from supabase import create_client
from datetime import datetime, timezone
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
now_iso = datetime.now(timezone.utc).isoformat()
# agents table — base id (FK-enforced)
sb.table('agents').update({'last_heartbeat': now_iso}).eq('id', '$BASE_AGENT_ID').execute()
" 2>>"$HB_ERR_LOG"
```

(The `agent_status` psycopg block below stays best-effort — no change to its `2>/dev/null || true`. The `stale_agents` 15-min backstop covers silent failure there.)

- [ ] **Step 4: Edit `allocate_sub_tag_and_register` LockTimeout flush (A4)**

In `scripts/lib/auto_agent_id.py`, inside the `if not acquired:` block, BEFORE `raise LockTimeoutError(...)`, insert:

```python
                # A4 (CAI-RESP-053): flush diagnostic to disk before raising.
                # Launcher stderr may be redirected by launchd; a dedicated
                # file gives the operator a forensic trail.
                import datetime as _dt
                import glob as _glob
                import os as _os
                import traceback as _traceback
                _iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                _diag_path = f"/tmp/cc_lock_timeout_{_iso}.log"
                try:
                    with open(_diag_path, "a") as _fh:
                        _fh.write(f"timestamp: {_iso}\n")
                        _fh.write(f"base: {base}\n")
                        _fh.write(f"repo: {repo}\n")
                        _fh.write(f"lock_id: {_ALLOC_LOCK_ID}\n")
                        _fh.write(f"timeout_seconds: {_LOCK_TIMEOUT_SECONDS}\n")
                        _fh.write(f"holders: {holders}\n")
                        _fh.write("stack:\n")
                        _fh.write("".join(_traceback.format_stack()))
                        _fh.flush()
                        _os.fsync(_fh.fileno())
                except OSError:
                    pass  # disk full / permission — swallow; primary signal is the raise

                # Retention: keep newest 20 diagnostic files, unlink the rest.
                try:
                    _existing = sorted(_glob.glob("/tmp/cc_lock_timeout_*.log"))
                    for _old in _existing[:-20]:
                        try:
                            _os.unlink(_old)
                        except OSError:
                            pass
                except OSError:
                    pass
```

- [ ] **Step 5: Launcher also tails the most-recent diagnostic on allocator failure**

In `scripts/launch_dangerous_cc.sh`, find the existing `cc_alloc_err.log` tail (from Task 7). Immediately after it, append:

```bash
    # A4 (CAI-RESP-053): surface the most-recent LockTimeout diagnostic.
    RECENT_LOCK_DIAG=$(ls -t /tmp/cc_lock_timeout_*.log 2>/dev/null | head -n 1 || true)
    if [ -n "$RECENT_LOCK_DIAG" ] && [ -f "$RECENT_LOCK_DIAG" ]; then
        echo -e "${AMBER}  Most recent lock-timeout diagnostic: ${RECENT_LOCK_DIAG}${RESET}"
        tail -n 20 "$RECENT_LOCK_DIAG" | sed 's/^/    /'
    fi
```

(Exact location: inside the failure branch that already cats `/tmp/cc_alloc_err.log` — co-locate for consistency.)

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_launcher_logs.py tests/test_auto_agent_id.py -v`
Expected: PASS.

- [ ] **Step 7: Smoke — simulate a failed agents heartbeat and confirm the log grows**

Run:
```bash
# Simulate by writing a deliberately-invalid payload via the same redirect pattern.
: > /tmp/cc_heartbeat_err.log
python -c "
import os
from dotenv import load_dotenv
load_dotenv('.env')
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
# Invalid id → FK violation → stderr
try:
    sb.table('agents').update({'last_heartbeat': 'not-a-timestamp'}).eq('id', 'does-not-exist').execute()
except Exception as e:
    import sys; print(e, file=sys.stderr)
" 2>>/tmp/cc_heartbeat_err.log
wc -c /tmp/cc_heartbeat_err.log   # expect > 0
```

- [ ] **Step 8: Commit**

```bash
git add scripts/launch_dangerous_cc.sh scripts/lib/auto_agent_id.py tests/test_launcher_logs.py
git commit -m "$(cat <<'EOF'
feat(launcher): A2 asymmetric heartbeat fail-loud + A4 lock-timeout diagnostic flush

CAI-RESP-053:
- A2: agents.last_heartbeat feeds dashboards + stale_agents view +
  telegram /status. Drop `|| true`, redirect stderr to /tmp/cc_heartbeat_err.log
  (10 MB cap). agent_status heartbeat stays best-effort per stale_agents backstop.
- A4: on LockTimeoutError, fsync full diagnostic (base, repo, lock id,
  holders, stack) to /tmp/cc_lock_timeout_<UTC-iso>.log BEFORE raising.
  Retention: newest 20 files; older pruned.
- Launcher also tails the most-recent diagnostic on allocator failure.

Thread: GOVERNANCE-CLEANUP-001 Step 3.5.
EOF
)"
```

### Task 17: G2 regression-check artifact + D1/D2 in STATUS.md

**Files:**
- Modify: `STATUS.md` — Next Steps block.
- Create: `reports/step-3.5/g2-regression-check.txt` — git-log artifact.

**Why:**
- **G1**: the plan file is *this* file. Appending Step 3.5 inline (which you're reading now) IS the consolidation. No further action needed in Task 17 for G1.
- **G2**: CAI wants the pre-existing test attribution (`TestFormatTelegram` + `TestPollAgentMessages` — the 2 failing tests) reproducible from the record alone. A frozen git-log artifact in-repo satisfies this without relying on run-time git state.
- **D1/D2**: STATUS.md "Next Steps" block needs explicit pointers so the next session knows Step 4 = BUG-024 Phase 1, Step 5 = BUG-027 janitor. Committed-date placeholders only; actual dates set when those steps start.

- [ ] **Step 1: Generate the G2 artifact**

Run:
```bash
mkdir -p reports/step-3.5
{
  echo "# G2: Pre-existing test regression check"
  echo "# Generated for CAI-RESP-053 G2 audit trail."
  echo "# Command:"
  echo "#   git log --oneline e0e26d6^..HEAD -- tests/test_agent_messages_poll.py scripts/agent_messages_poll.py"
  echo "# Context:"
  echo "#   e0e26d6 is the first commit of Step 3 (Task 1)."
  echo "#   The 2 failing tests in the Step 3 review were TestFormatTelegram and"
  echo "#   TestPollAgentMessages — both assert 'claude.ai' in text. Empty output"
  echo "#   below confirms Step 3 did NOT touch either file; failures are pre-existing."
  echo ""
  echo "## git log output:"
  git log --oneline e0e26d6^..HEAD -- tests/test_agent_messages_poll.py scripts/agent_messages_poll.py || true
  echo ""
  echo "## test file HEAD SHAs at the time of Step 3 review:"
  git log -1 --format='%H %ad %s' -- tests/test_agent_messages_poll.py || true
  git log -1 --format='%H %ad %s' -- scripts/agent_messages_poll.py || true
} > reports/step-3.5/g2-regression-check.txt
cat reports/step-3.5/g2-regression-check.txt
```

Expected: the "git log output" section is empty (no Step 3 commits touched those files); the file SHAs are older than `e0e26d6`.

- [ ] **Step 2: Edit `STATUS.md` Next Steps block**

Locate the "Next steps" block (top of file, under the Step 3 shipped entry). Append:

```markdown
### Deferred from Step 3.5 (CAI-RESP-053)

- **Step 4 (D1)**: BUG-024 Phase 1 — promote sub-identity (`cc-ihsanos-N`)
  to first-class `agents.id` FK. Collapses the current dual-identity split
  (base in `agents`, sub-tag in `agent_status` under GUC) into a single
  FK-coherent surface. Every new write site between now and Phase 1 is a
  BUG-024 re-introduction risk. Committed-date: TBD after Step 3.5 ships.

- **Step 5 (D2)**: BUG-027 — exit-trap janitor cron. Exit trap doesn't
  survive `kill -9`, so stale `agent_status` rows can linger past the
  `stale_agents` view's 15-min threshold. Cron-based janitor flips rows
  with `last_heartbeat < now() - interval '30 minutes'` to `offline`.
  Committed-date: TBD after Step 4.
```

- [ ] **Step 3: Commit**

```bash
git add STATUS.md reports/step-3.5/g2-regression-check.txt
git commit -m "$(cat <<'EOF'
chore: Step 3.5 — G2 regression-check artifact + D1/D2 in STATUS.md

CAI-RESP-053 close-out:
- G2: frozen git-log artifact confirms the 2 pre-existing test failures
  (TestFormatTelegram, TestPollAgentMessages) are not Step 3 regressions.
- D1: STATUS.md Next Steps pointer to Step 4 = BUG-024 Phase 1.
- D2: STATUS.md Next Steps pointer to Step 5 = BUG-027 janitor cron.

Thread: GOVERNANCE-CLEANUP-001 Step 3.5.
EOF
)"
```

### Step 3.5 Close-Out

After Task 17 commits:

1. **File review_request to CAI** — same shape as msg 408; subject `Step 3.5 shipped — CAI-RESP-053 integration ready for adversarial review [cc-ihsanos-3]`; body covers B1/G1/G2/G3/A1/A2/A3/A4 status + D1/D2 deferred.
2. **On CAI ack** → push `origin/main`, close job #111, close GOVERNANCE-CLEANUP-001 thread.
3. **On CAI counter** → draft delta, cycle (same pattern as this Step 3.5 itself).

**Estimate:** 2–2.5h end-to-end.

---
