# Nothing Goes Dark — Phase 2 (operator auto-push) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a quiet-but-open operator-facing item (a `held_commitment` or `operator_ask`) force a re-nudge of its owning agent automatically, with no new standalone watchdog and no reliance on any agent remembering to push on its own.

**Architecture:** Extend the existing `priority_sla_watchdog.py` daemon with one more dead-man's-switch net (same shape as its `client_asks_chase`/`captured_triage` nets already in the file), reusing the exact overdue-detection logic the live "nothing goes dark" status board already ships and tests — extracted into a shared module first so the pulled view (what Musa sees if he looks) and the pushed nudge (what fires if he doesn't) can never disagree. No new daemon, no new schema.

**Where design part 2 lives:** the design doc's part 2 ("the push rides the mandatory turn-start reconcile") is deliberately NOT a new code hook in this plan. That reconcile is today agent-discipline-enforced, not substrate-enforced (same admitted limit as `docs/governance/inbox-check-directive.md`'s cold-start inbox check) — building a second, parallel enforcement path for it would itself be "another watchdog watching dogs." Instead, this net's nudge (Task 3) IS the forcing function: it wakes the owning agent via the fleet's normal bus-doorbell path and its body explicitly names the action owed ("post a one-line status to the operator now"), so the agent's own next turn-start reconcile is what actually sends the update — exactly the part-2→part-3 relationship the design doc itself describes ("that nudge is a turn → the reconcile fires the update").

**Tech Stack:** Python 3.9, psycopg (direct, never `supabase db push`), pytest, the fleet's existing tmux/bus nudge primitives.

**Spec:** `reports/nothing-goes-dark-design-20261026/design.md` (live checkout, uncommitted — not yet in this repo's git history) + the Phase-1 implementation it supersedes in part, `nervous_system/console/db.py` lines ~1382-1583 (bus #77019). Read both; this plan documents where Phase 1 deliberately departed from the design doc's original part 1 and why that departure is the right foundation for Phase 2, not a gap to re-fix.

## Global Constraints

- **Operator-facing only** (orch-console bus #86475): act only on rows whose derived `waiting_party == "operator"`. A `client-channel` ask or a non-operator `channel_tag` commitment is filtered OUT, deferred to Phase 3. Test this boundary explicitly — it is the scope line orch-console drew.
- **"Reuse, do not build a new watchdog"** (bus #86475, design doc part 3): the new logic lives in `scripts/priority_sla_watchdog.py` as one more net, wired into its existing `main()` scan loop, its existing state file, its existing `renudge()`/escalation plumbing. No new launchd job, no new daemon file.
- **One overdue definition, shared** (design doc part 5 + its own Phase-1 comment: "Phase 2 (fleet-health) can lift the SQL below into a view verbatim if it wants it"): the push's "is this item dark?" check must be the IDENTICAL function the pulled status board uses — not a re-derived, possibly-diverging copy.
- **Dead-agent fallback = the EXISTING liveness escalation** (design doc part 4): when a chase doesn't clear, escalate via the same internal-then-operator ladder (`escalate_internally` / `send_page`) already in the file — no new escalation path.
- **Anti-false-fire, the STRONG oracle** (design doc open questions, citing the 2026-10-26 near-miss, bus #76991/#76993/#76998): gate on `_recipient_active_turn` (the `pane_busy.sh` oracle — catches the extended-thinking spinner, not just "esc to interrupt"), the same guard the main violation loop uses at `scripts/priority_sla_watchdog.py:1991-1998` — NOT the weaker "esc to interrupt" substring check inside `_tmux_countonly` that the existing chase nets implicitly rely on today.
- **Observe-first, arm later** (house pattern for every net already in this file — e.g. `SLA_CLIENT_ASKS_CHASE_ENABLED`): ship force-dry via the existing `_gated_dry()` helper behind a new `SLA_PARTY_UPDATE_CHASE_ENABLED` flag. Nothing pages or nudges live until orch-console reviews a dry-run scan and arms it.
- **Never `supabase db push`**; this plan adds **no migration** — see Task 1's rationale for why the schema-columns half of the original design doc (part 1) is deliberately not being resurrected.
- **Coordinate the watchdog touch with cc-fleet-health** (bus #86475) — they own `scripts/priority_sla_watchdog.py`. This plan is the artifact to hand them for review before merge; they are mid-flight on other PRs today (bus #86469-86478), so treat their review as a dependency to schedule, not a blocker to starting the build.

## Review Focus

- A `held_commitment` already `status='fired'` (the commitment-sweeper's own breach escalation) must NOT also get chased by this new net — that would be a second, uncoordinated escalation of the same event. Exclude `overdue_reason == "fired"` rows; the sweeper already owns them.
- A row whose derived `waiting_party` is `"client"` must never appear in this net's target list, even transiently — test the exact boundary, not just the happy path.
- The owning agent is mid-turn on real work (the near-miss class, #76991) — must be suppressed via `_recipient_active_turn`, not nudged; un-suppresses automatically once idle.
- A row with no prior update at all (`last_update_at IS NULL` → `derive_overdue` returns `(True, "quiet")`) must still produce a valid chase target, not crash on a `None`.
- Once chased, a row must not be re-chased every ~90s scan (dead-man's-switch backoff), and must drop out of the target set the instant it's closed/discharged/cancelled — both already enforced by the shared SQL's `WHERE` clause, but the backoff half needs its own test, same as `test_within_cadence_of_last_chase_is_suppressed` for the existing asks-chase net.

---

## Task 1: Extract the shared "is this item dark?" module

**Files:**
- Create: `scripts/lib/nothing_goes_dark.py`
- Modify: `nervous_system/console/db.py:1393-1578` (remove the moved definitions; import them back)
- Modify: `tests/console/test_status_board.py` (import path only — no behavior change)
- Test: `tests/test_nothing_goes_dark.py` (the moved unit tests, same assertions)

**Interfaces:**
- Produces, for Task 2 to consume:
  - `STATUS_CADENCE_S: dict[str, int]` (`{"operator": 86400, "client": 43200}`)
  - `waiting_party_for_ask(ask_surface: str | None) -> str`
  - `waiting_party_for_commitment(channel_tag: str | None) -> tuple[str, str | None]`
  - `derive_overdue(due_at, last_update_at, waiting_party: str, now=None) -> tuple[bool, str | None]`
  - `annotate_status_rows(rows: list[dict], now=None) -> list[dict]` (adds `waiting_party`, `client_name`, `overdue`, `overdue_reason`, `source`; sorts darkest-first)
  - `build_open_items_query() -> tuple[str, list]` (renamed from `build_status_board_query` — same SQL verbatim; the name drops the "board" framing since a second, non-board consumer now exists)

- [ ] **Step 1: Create `scripts/lib/nothing_goes_dark.py`** with the functions and constant listed above, moved byte-for-byte from `nervous_system/console/db.py` (just the rename `build_status_board_query` → `build_open_items_query`). Keep every existing docstring/comment — they are the design rationale, not boilerplate.

- [ ] **Step 2: Update `nervous_system/console/db.py`** — delete the moved definitions, add `from scripts.lib.nothing_goes_dark import (STATUS_CADENCE_S, waiting_party_for_ask, waiting_party_for_commitment, derive_overdue, annotate_status_rows, build_open_items_query as build_status_board_query)`. `fetch_status_board()` keeps its existing name/behavior unchanged — this is a pure refactor from the console's point of view.

- [ ] **Step 3: Move the unit tests.** Relocate every test in `tests/console/test_status_board.py` that targets a moved function (`test_ask_waiting_party_by_surface` through `test_annotate_fired_commitment_is_always_overdue` — 10 tests per the current file) into `tests/test_nothing_goes_dark.py`, updating only the import line. Leave in `tests/console/test_status_board.py` only whatever still exercises `db.py`-specific wiring (if anything remains after the move, otherwise the file may end up empty of unit tests and that's correct — the console-specific integration test, if one exists for `fetch_status_board()`/the HTTP endpoint, stays).

- [ ] **Step 4: Run both test files.**

Run: `.venv/bin/python3 -m pytest tests/test_nothing_goes_dark.py tests/console/test_status_board.py -v`
Expected: same pass count as before the move (10 relocated + whatever remained), zero failures, zero import errors.

- [ ] **Step 5: Run the full test suite once** to catch any other importer of the moved names.

Run: `.venv/bin/python3 -m pytest -q`
Expected: no new failures vs. the pre-change baseline.

- [ ] **Step 6: Commit.**

```bash
git add scripts/lib/nothing_goes_dark.py nervous_system/console/db.py tests/test_nothing_goes_dark.py tests/console/test_status_board.py
git commit -m "refactor: extract nothing-goes-dark overdue derivation to scripts/lib (op#34349/34354)"
```

---

## Task 2: The party-update-chase net (detection + pure target selection)

**Files:**
- Modify: `scripts/priority_sla_watchdog.py` (new section, placed alongside the other chase nets — after the `captured_triage` block, ~line 2287)
- Test: `tests/test_sla_party_update_chase.py`

**Interfaces:**
- Consumes: `scripts.lib.nothing_goes_dark.{build_open_items_query, annotate_status_rows}` (Task 1); `_recipient_active_turn`, `all_lane_map`, existing `state`/`now` plumbing already in this file.
- Produces, for Task 3 to wire in:
  - `_fetch_party_update_open_items(conn) -> list[dict]` — runs `build_open_items_query()`, passes through `annotate_status_rows`, filters to `waiting_party == "operator" and overdue and overdue_reason != "fired"`.
  - `party_update_chase_targets(rows, *, now, chase_state, active_turn_check) -> list[dict]` — PURE. Dead-man's-switch per row-id (same shape as `client_chase_targets`): a row already chased within `PARTY_UPDATE_CHASE_EVERY_MIN` of its last chase is skipped; `active_turn_check(owner_agent) -> bool` (injected, not called directly, so the pure function stays unit-testable without tmux) suppresses a mid-turn owner.
  - Two new config constants: `PARTY_UPDATE_CHASE_EVERY_MIN` (default 240, env `SLA_PARTY_UPDATE_CHASE_EVERY_MIN`), `MAX_PARTY_UPDATE_CHASE_PER_RUN` (default 3, env `SLA_PARTY_UPDATE_MAX_CHASE_PER_RUN`) — same `_envint` pattern as every other constant in this file.

- [ ] **Step 1: Write the failing tests** in `tests/test_sla_party_update_chase.py`, mirroring `tests/test_sla_asks_chase.py`'s structure:

```python
def test_client_waiting_party_is_never_a_target():
    rows = [dict(id=1, kind="commitment", waiting_party="client",
                 overdue=True, overdue_reason="quiet", owner="cc-irsyad-coord")]
    assert party_update_chase_targets(rows, now=NOW, chase_state={},
                                       active_turn_check=lambda a: False) == []

def test_fired_commitment_is_excluded_sweeper_already_owns_it():
    rows = [dict(id=2, kind="commitment", waiting_party="operator",
                 overdue=True, overdue_reason="fired", owner="orch-console")]
    assert party_update_chase_targets(rows, now=NOW, chase_state={},
                                       active_turn_check=lambda a: False) == []

def test_quiet_operator_row_with_no_prior_update_is_a_target():
    rows = [dict(id=3, kind="ask", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-scholar")]
    out = party_update_chase_targets(rows, now=NOW, chase_state={},
                                      active_turn_check=lambda a: False)
    assert [t["id"] for t in out] == [3]

def test_active_turn_owner_is_suppressed():
    rows = [dict(id=4, kind="ask", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-irsyad-3")]
    out = party_update_chase_targets(rows, now=NOW, chase_state={},
                                      active_turn_check=lambda a: a == "cc-irsyad-3")
    assert out == []

def test_within_cadence_of_last_chase_is_suppressed():
    state = {"4": {"last_chase_ts": NOW - 60}}  # just chased, well under the 240m cadence
    rows = [dict(id=4, kind="ask", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-scholar")]
    assert party_update_chase_targets(rows, now=NOW, chase_state=state,
                                       active_turn_check=lambda a: False) == []

def test_at_cadence_boundary_second_chase_fires():
    state = {"4": {"last_chase_ts": NOW - PARTY_UPDATE_CHASE_EVERY_MIN * 60}}
    rows = [dict(id=4, kind="ask", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-scholar")]
    out = party_update_chase_targets(rows, now=NOW, chase_state=state,
                                      active_turn_check=lambda a: False)
    assert [t["id"] for t in out] == [4]

def test_chase_is_capped_per_scan():
    rows = [dict(id=i, kind="ask", waiting_party="operator", overdue=True,
                 overdue_reason="quiet", owner="cc-scholar") for i in range(5)]
    out = party_update_chase_targets(rows, now=NOW, chase_state={},
                                      active_turn_check=lambda a: False,
                                      max_targets=MAX_PARTY_UPDATE_CHASE_PER_RUN)
    assert len(out) == MAX_PARTY_UPDATE_CHASE_PER_RUN
```

(`NOW`, `PARTY_UPDATE_CHASE_EVERY_MIN`, `MAX_PARTY_UPDATE_CHASE_PER_RUN` imported from the watchdog module at the top of the test file, same as `test_sla_asks_chase.py` does for its own constants.)

- [ ] **Step 2: Run to verify every test fails** with `ImportError`/`NameError` (the functions don't exist yet).

Run: `.venv/bin/python3 -m pytest tests/test_sla_party_update_chase.py -v`
Expected: collection error or `NameError: party_update_chase_targets`.

- [ ] **Step 3: Implement** `_fetch_party_update_open_items`, `party_update_chase_targets`, and the two config constants in `scripts/priority_sla_watchdog.py`. `party_update_chase_targets` takes `max_targets` as a keyword (default `MAX_PARTY_UPDATE_CHASE_PER_RUN`), iterates rows in the SQL's existing darkest-first order (already sorted by `annotate_status_rows`, so the cap naturally keeps the quietest items), and for each surviving row stamps `chase_state[str(row_id)] = {"last_chase_ts": now}` only on the caller's later confirmation of a successful send — this function only SELECTS targets, it does not mutate `chase_state` itself (mirrors `client_chase_targets`'s pure/impure split; the stamping happens in Task 3's action function, matching `chase_client_asks`).

- [ ] **Step 4: Run tests to verify they pass.**

Run: `.venv/bin/python3 -m pytest tests/test_sla_party_update_chase.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit.**

```bash
git add scripts/priority_sla_watchdog.py tests/test_sla_party_update_chase.py
git commit -m "feat(sla-watchdog): detect operator-facing party-update-overdue items (op#34349/34354)"
```

---

## Task 3: The chase action, escalation reuse, and main()-loop wiring

**Files:**
- Modify: `scripts/priority_sla_watchdog.py` (action function + `main()` wiring, mirrors the `client_asks_chase`/`captured_triage` blocks at ~lines 2240-2287)
- Test: `tests/test_sla_party_update_chase.py` (append)

**Interfaces:**
- Consumes: Task 2's `_fetch_party_update_open_items`, `party_update_chase_targets`; existing `renudge`, `escalate_internally`, `send_page`, `already_paged_on_bus`, `_gated_dry`, `all_lane_map`.
- Produces: `chase_party_update(targets, *, dry, now, chase_state, send_chase, max_chases) -> tuple[int, list]` (sent-count, sent-list — same return shape as `chase_client_asks`), `_send_party_update_chase(conn, owner, target) -> bool` (the real send, count-only nudge text distinct from the generic SLA-violation line — see Step 3), and the `main()` wiring block.

- [ ] **Step 1: Write the failing tests.**

```python
def test_chase_dry_run_sends_nothing_and_stamps_nothing():
    calls = []
    n, sent = chase_party_update(
        [dict(id=5, owner="cc-scholar", title="x")], dry=True, now=NOW,
        chase_state={}, send_chase=lambda o, t: calls.append((o, t)) or True,
        max_chases=3)
    assert n == 0 and calls == [] and sent == []

def test_chase_stamps_state_only_on_successful_send():
    state = {}
    n, sent = chase_party_update(
        [dict(id=6, owner="cc-scholar", title="x")], dry=False, now=NOW,
        chase_state=state, send_chase=lambda o, t: False, max_chases=3)
    assert n == 0 and "6" not in state

def test_chase_targets_owner_never_operator_or_console_fallback():
    seen = []
    chase_party_update(
        [dict(id=7, owner="cc-scholar", title="x")], dry=False, now=NOW,
        chase_state={}, send_chase=lambda o, t: seen.append(o) or True, max_chases=3)
    assert seen == ["cc-scholar"]

def test_party_update_chase_message_type_is_constraint_valid():
    # same guard as test_asks_chase_message_type_is_constraint_valid —
    # the bus message_type used by _send_party_update_chase must be one of
    # the agent_messages_message_type_check allowed values, never 'escalation'
    import inspect
    src = inspect.getsource(w._send_party_update_chase)
    assert "mtype=\"blocker\"" in src or "mtype='blocker'" in src
```

- [ ] **Step 2: Run to verify failure.**

Run: `.venv/bin/python3 -m pytest tests/test_sla_party_update_chase.py -v -k chase_action_not_yet_defined_placeholder`
(adjust `-k` to the new test names) — Expected: `NameError`.

- [ ] **Step 3: Implement `chase_party_update`** (mirrors `chase_client_asks`'s loop/cap/stamp shape exactly) and **`_send_party_update_chase(conn, owner, target)`**. The nudge text must name the SPECIFIC overdue item (not the generic "N priority bus messages" line from `_countonly_line`) so the nudged agent's turn-start reconcile knows a status push — not an inbox drain — is owed:

  - For a reachable lane (`owner in lanes`): route through `renudge`'s existing `lane_nudge.sh` path is NOT reused verbatim here, because that path sends `_countonly_line`'s fixed text. Instead call `bus_send.send(from_agent="sla-watchdog", to=owner, mtype="blocker", subject=f"[party-update-overdue] {target['kind']}#{target['id']} has been quiet — operator is owed a status", body=f"{target['title'][:200]!r} (owner={owner}) has had no update to the operator in over {STATUS_CADENCE_S['operator']//3600}h. Post a one-line status to the operator now (bus or nazim_send.sh per channel ownership), then it clears on the next status-board read.", priority="P2", req=True)` — this both reaches the owner via the fleet's normal wake path (the bus doorbell, same mechanism every other agent-to-agent nudge in this fleet already uses — no new transport) and leaves the durable audit trail every other net in this file requires.
  - The active-turn check happens in `party_update_chase_targets` (Task 2), via the injected `active_turn_check` callable `main()` wires to `_recipient_active_turn` — NOT inside `_send_party_update_chase`. This keeps the selection step (what to chase) pure and testable without tmux, and the send step (`_send_party_update_chase`) dumb: by the time it is called, the target has already cleared the active-turn gate, so it only composes and sends.
  - On an owner that is a `HUB_SESSIONS` key or `"cai"` or `"musa"`: same unreachable-owner handling as `renudge()` already does — log and skip (an operator-facing item should essentially never be owned by one of these, but fail safely, never crash the scan).

- [ ] **Step 4: Wire into `main()`**, directly after the `captured_triage` block (~line 2287), following the exact same pattern:

```python
party_update_dry = _gated_dry(dry, "SLA_PARTY_UPDATE_CHASE_ENABLED")
try:
    party_chase_state = state.setdefault("party_update_chase", {})
    party_targets = party_update_chase_targets(
        _fetch_party_update_open_items(conn), now=now, chase_state=party_chase_state,
        active_turn_check=lambda a: _recipient_active_turn(all_lanes.get(a, [])),
        max_targets=MAX_PARTY_UPDATE_CHASE_PER_RUN)
    if party_targets:
        n, sent_list = chase_party_update(
            party_targets, dry=party_update_dry, now=now, chase_state=party_chase_state,
            send_chase=lambda owner, t: _send_party_update_chase(conn, owner, t),
            max_chases=MAX_PARTY_UPDATE_CHASE_PER_RUN)
        for t in party_targets:
            actions.append(f"{'[DRY] ' if party_update_dry else ''}PARTY-UPDATE-CHASE "
                           f"{t['kind']}#{t['id']} -> owner {t.get('owner')}")
        log(f"party-update-chase [{'DRY/observe' if party_update_dry else 'ARMED'}] "
            f"sent={0 if party_update_dry else n}/{len(party_targets)}")
except Exception as e:
    log(f"party-update-chase ERROR: {e!r}")
```

- [ ] **Step 5: Run the full watchdog test module.**

Run: `.venv/bin/python3 -m pytest tests/test_sla_party_update_chase.py tests/test_priority_sla_watchdog.py tests/test_sla_asks_chase.py -v`
Expected: all PASS, no regression in the untouched nets.

- [ ] **Step 6: Extend `self_test()`** (the in-memory, no-DB offline exercise at the bottom of the file) with one deterministic check for `party_update_chase_targets` (mirrors the existing `_session_active_turn`/`_recipient_active_turn` checks already there at ~line 2389).

- [ ] **Step 7: Commit.**

```bash
git add scripts/priority_sla_watchdog.py tests/test_sla_party_update_chase.py
git commit -m "feat(sla-watchdog): wire party-update-chase into the scan loop, observe-first (op#34349/34354)"
```

---

## Task 4: End-to-end proof

No new files — this task is a verification runbook, executed and its real output pasted into the PR description and the bus report to orch-console.

- [ ] **Step 1: Dry-run against the real substrate, today's actual open items.**

Run: `.venv/bin/python3 scripts/priority_sla_watchdog.py --dry-run`
Confirm in the output: the new `PARTY-UPDATE-CHASE` line(s) (if any exist today) name real `held_commitments`/`operator_asks` ids, correctly exclude any `client`-party or `fired` row, and match what `/api/status-board` already shows as overdue for those same ids (cross-check via `nervous_system.console.db.fetch_status_board()` or the live console page) — this is the proof the push and the pull never disagree.

- [ ] **Step 2: Synthetic quiet-item proof, isolated.** Using a disposable `is_test`-flagged (or otherwise clearly tagged, e.g. `source_ref='nothing-goes-dark-e2e-proof'`) `held_commitments` row inserted with `updated_at` far in the past and `channel_tag` in the operator set: run `party_update_chase_targets` + `chase_party_update` against it in `dry=True`, confirm it is selected and the composed nudge text is correct; then in `dry=False` against a scratch/local DB (never the real substrate for the live-send half — same discipline as every migration-apply drill in this repo), confirm the bus row is actually sent with the right subject/body/priority, then delete the synthetic row.

- [ ] **Step 3: One real, orch-console-approved live-armed drill.** This is the actual "Musa gets an update without asking" proof and it is **not fully boundable by this plan alone** — it requires (a) `SLA_PARTY_UPDATE_CHASE_ENABLED=1` armed on the live watchdog (orch-console's call, same gate every other net in this file uses), and (b) a real quiet operator-facing item existing at drill time, or `hc#637` itself deliberately left to go quiet as its own test (the design doc's own "apply the rule to itself" clause). Propose this to orch-console as the arm-and-watch step once Tasks 1-3 are merged; report the first real fire (or the first all-clear scan showing none are currently overdue) back on the bus #86475/hc#637 thread.

---

## Timeline (honest)

- **Tasks 1-3 (code + unit tests + Step 1-2 proof, dry-run only):** today, 2026-10-31 — this is bounded, mechanical, and closely mirrors three nets already in the file.
- **cc-fleet-health review** (they own this file; bus shows them mid-flight on PR #643/#729 right now): scheduled as soon as they're free today; not assumed instant.
- **Task 4 Step 3 (live-armed drill):** gated on orch-console's explicit arm decision, same as `SLA_CLIENT_ASKS_CHASE_ENABLED` was — cannot honestly commit to a time I don't control. Proposing arm-and-watch starting as soon as Tasks 1-3 are merged and reviewed, realistically **today or tomorrow (Sun 1 Nov)**, not held to a fixed clock.
- **`hc#637` (due 2026-10-31 16:00Z):** code ships today; the commitment should be updated to reflect "engine built, observe-first, armed pending orch-console go" rather than "fully live" by 16:00Z — flagging this gap now rather than silently missing it.
