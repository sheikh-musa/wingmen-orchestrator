# cc-quality review — console content hash `9ace6cd6a1ebcaf4`

**Verdict: PASS** (code sign-off for deploy_console.sh Gate-4 / orch-console's #43066 gate)

- **Reviewer:** cc-quality (Head of Quality), model `claude-opus-4-8` (`.quality_model` carve-out confirmed at source; auditor function authorized).
- **Date:** 2026-09-25
- **Request:** cc-substrate bus #43077 (bundled, per orch-console #43066) — ONE content hash, ONE review, two console changes.
- **Scope:** backend `.py` only — no static-asset changes, version-sync gate unaffected. Renders (fleet.html/lanes.html) are unaffected by these changes and are produced by the gate script at merge time (item 3 of #43066); not in this code review's scope.

## Identifiers verified (at source, in the worktree)

| Field | Requested | Verified |
|---|---|---|
| Worktree | `/Users/sheikhmusa/wingmen/projects/orchestrator.wt-cleanup` | ✓ present |
| Branch | `fix/console-lane-action-protected-migration` | ✓ checked out |
| HEAD SHA | `468d09410acd4ea2f5f05e4a111a7256b02241a2` | ✓ exact |
| Working tree | — | ✓ clean (no uncommitted drift) |
| Content hash | `9ace6cd6a1ebcaf4` | ✓ recomputed via `scripts/lib/console_deploy_manifest.sh console_content_hash` |
| Base | — | ✓ `main` is an ancestor of HEAD |

Actual review set = the two tip commits only: `bb4508f` (app.py, hosted_server.py, **protected_agents.py** — the shared accessor is *added* here and was reviewed) and `468d094` (panes.py).

---

## CHANGE 1 — `bb4508f`: migrate console protected-identity sets onto `console_protected_identities()`

`app.py::_LANE_ACTION_PROTECTED` and `hosted_server.py::_PROTECTED` (whose own
comment said "mirrors app.py" — the exact hand-maintained-duplication anti-pattern
`protected_agents.py` exists to end) now both read
`nervous_system.protected_agents.console_protected_identities()` =
`protected_tmux_sessions() | protected_agent_ids() | {"hub"}`.

**Primary risk audited: loss of protection.** This is a deny gate; a dropped
member = a protected body becomes bootable/wind-downable from the phone. Verified
empirically (not from the commit's assertion), computing the full sets in the
worktree venv and running a residual-escape check (any OLD member missing from NEW):

| Set | n | OLD⊆NEW? |
|---|---|---|
| OLD_APP (live `lane_winddown.SINGLETONS` ∪ literal) | 12 | — |
| OLD_HOSTED (12-member literal) | 12 | — |
| NEW_LIVE (`console_protected_identities()`, DATABASE_URL) | 15 | **YES** vs both |
| NEW_FLOOR (fail-safe, forced DB failure) | 15 | **YES** vs both |

- No member lost in **either** the healthy path **or** the DB-down fail-safe floor.
  Gains: `cc-finance`, `cc-storefront`, `nazim-console` (the last is the one
  genuinely-new net protection; the two `cc-*` were already caught by the adjacent
  `session.startswith("cc-")` check).
- Fail-safe floor confirmed sourced from the static fallbacks
  (`_FALLBACK_PROTECTED_SESSIONS` + `_FALLBACK_PROTECTED`); dead-man staleness can
  only ever make protection *more* conservative.
- **"never raises" contract closed** (per fail-closed callee-contract rule): forcing
  a bad DSN AND unsetting `DATABASE_URL` (KeyError path) both return the 15-member
  floor without raising — so hosted_server's `try/except` fallback branch
  (`console_protected_identities(dsn=hosted_view._dsn())` → bare
  `console_protected_identities()`) is safe even on the VPS where only
  `CONSOLE_DB_URL` is set.
- **Usage confirmed deny-only:** `_LANE_ACTION_PROTECTED` (app.py:2339) and
  `_PROTECTED` (hosted_server.py:487, 524) are used solely as `if session in … →
  403`. A superset can only refuse more — no new code path for the added members.
  The `!= "nazim"` pointer-set carve-out (hosted_server:524) is preserved;
  `nazim-console` now refused there is the intended new protection.

## CHANGE 2 — `468d094`: panes.py token-truth scan resolves hub host via `hub_reach`

The decommissioned wingmen-core IP literal is no longer the blind default. New
`_resolve_hub_ssh_target()`: `CONSOLE_HUB_SSH` override wins; else resolve
`orch_lease.holder_host` via `hub_reach`; the hardcoded `root@91.107.235.77` is now
**gated** behind `info["known"] and info["host"] == "wingmen-core"`, else `None` →
existing UNVERIFIED path.

- **Callee contracts verified** (not grepped): `hub_reach_for_holder(None)` and
  `('gzbai')` → `None` target → UNVERIFIED (no crash); `('wingmen-core')` → the
  gated IP. Matches the author's live-verify (current holder gzbai → None/UNVERIFIED
  instead of a dead SSH).
- No dangling `_REMOTE_HUB_HOST` reference remains anywhere (no NameError risk).
- Connection is inside the 45s TTL cache and uses a `with` context manager
  (connect_timeout=5) — closes properly, not re-opened per poll.

---

## Tests (run by me at this HEAD — not taken on the author's word)

- `tests/test_protected_agents_registry.py` — **10 passed**
- `tests/console/test_panes.py` — **24 passed**
- `tests/console` (full) — **345 passed** (asyncio feed-fixture teardown lines are
  benign; exit 0)

## Findings

None blocking. No P0/P1/P2. Both changes are strict, verified improvements
(security-conservative superset migration + removal of a dead-host blind SSH).

**Signed off for the gate. Clear to push + open the PR to fable/substrate-safe-fixes.**

— cc-quality
