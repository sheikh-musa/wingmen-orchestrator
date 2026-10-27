# cc-quality review — console content `e126968d8934a49d` (fc-v86 rev3, badge == /asks hero)

- **Reviewer:** cc-quality (Head of Quality). Ran on claude-opus-4-8.
- **Target:** content hash `e126968d8934a49d` = lane tip `df586fc` (feature `65ef515` fc-v86 rev3 + render evidence `df586fc`), worktree `orchestrator.wt-console-revamp`. Tree quiesced (clean working set).
- **Supersedes:** my rev2 verdict for `c99a135457e956b8` (committed verbatim for the audit trail) per cc-console-revamp bus **#78416**.
- **Request:** bus 78416 (orch-console GO is gated on this verdict).
- **Scope:** engineering correctness, security/token-gating, derivation correctness, NO-DROP. Carries the two MEDIUM alerts from my rev2 verdict (now fixed) + the orch-console 78374 copy blocker. **Not** the aesthetic eyeball (orch-console's per 77019).
- **Date:** 2026-10-28.

## Verdict: CODE **PASS** — cleared to deploy. Both prior MEDIUM alerts fixed and verified; no open findings.

Independently recomputed the content hash and re-ran the gate-critical + targeted fix suites on the quiesced tree. The two MEDIUM alerts from my rev2 verdict are resolved in the exact shape I asked for, and each is locked by a test that fails on the old behavior.

---

## What I verified (evidence)

**Content hash — PASS.** Recomputed via the manifest seam: `e126968d8934a49d` ✓ matches. Working tree clean (no dirty gated paths).

**FINDING 1 (rev2 MEDIUM) — FIXED & verified.** The badge no longer undercounts the `/asks` Needs-Musa hero. There is now **ONE** derivation, `db.needs_musa(row)` = `bool(waiting_on_operator OR needs_operator_decision)`, applied at all three count sites over the **same** row source (`fetch_fleet_asks` = `build_fleet_asks_query`, the hero's own rows):
- `/api/asks-board` `counts.needs_musa` → `sum(1 for r in open_rows if db.needs_musa(r))`.
- `/api/asks-waiting` → row source switched from `fetch_asks()` to `fetch_fleet_asks()`, counted with `db.needs_musa`.
- `/api/fleet` `pulse.asks_waiting` → new guarded executor future `f_fleet_asks = ex.submit(db.fetch_fleet_asks)`, counted with `db.needs_musa` (replaces the old `build_asks_query` status filter).

So the badge equals the hero **by construction** (same predicate, same rows), not by coincidence. The client hero (`asks.js needsMusa`) mirrors the identical predicate and renders `open.filter(needsMusa)`, so the visible hero set == the count == the badge. Locked by `test_asks_badge_and_hero_share_one_needs_musa_derivation`, which seeds `needs_operator_decision`-only rows (the 11-vs-33 defect shape) **plus a deliberately disagreeing `fetch_asks` stub** and asserts `asks-waiting == counts.needs_musa == pulse.asks_waiting == 3` — the seed is the divergence, the stub cannot be the proof. **Ran it: PASS.** (`asks_open/asks_needs/asks_review` still derive from `fetch_asks`/`your_asks`, so those historical pulse keys are unchanged; `fetch_asks` and `fetch_fleet_asks` both run in parallel in the threadpool — bounded, same guard shape as every other signal.)

**FINDING 2 (rev2 MEDIUM, out-of-hash `scripts/asks_triage.py`) — FIXED & verified.** The `--answers` CLI exit code now hangs on the **PRIMARY** triage rowcount (`n = cur.rowcount` captured right after the primary UPDATE, `return n`). The answers-close fires only `if answers is not None and n == 1 and not target_already_closed` — a triage that touched nothing cannot be the evidence an ask was answered, and an already-closed target is skipped (idempotent). `target_already_closed` is read in the pre-check (`SELECT closed_at`). `main()` prints `ok` on both close paths. Locked by two tests that drive `main()` end-to-end:
- `test_answers_cli_on_already_closed_target_exits_zero` — my exact wet-proved failure case (fresh answer row, already-closed target): **now exits 0** (was exit 2 "0 rows updated"). **Ran it: PASS.**
- `test_answers_primary_miss_exits_2_and_leaves_target_open` — primary miss (typo'd item id): exits 2 AND the target stays open (close did not fire). **Ran it: PASS.**

**orch-console 78374 copy blocker — FIXED.** `fleet.html` drops the "(collapsed · also powers Retask)" parenthetical → plain "Delegate an ask".

**NO-DROP — still PASS (unchanged from rev2).** Confirm/dismiss on `/asks` review rows (ported swipe, same `POST /api/ask-close`), composer survives collapsed, three top-bar links preserved. No new server surface; `/api/asks-waiting` remains below the `_authed()` bearer gate.

**Tests — PASS (re-run on the quiesced `df586fc` tree).** Gate-critical `test_app.py` + `test_db.py` + `test_status_board.py` + `test_asks_triage.py` = **174 passed** (+3 vs rev2's 171). Node harness **12/12**. Consistent with the lane's 5713-pytest / 12-node superset claim.

---

## Not covered by me
- Aesthetic eyeball incl. the "sheet closed on load" claim and the live-shaped 390 render shots (`fleet` / `lanes` / `asks`, hero 33 == badge 33): orch-console's per 77019. The structural DOM asserts (11 structural + 22 flag-only rows) are in `render-harness.py`; the visual sign-off is not mine.
- The full 5713-pytest superset (prod guard refuses a prod-DSN run) — I re-ran the gate-critical subset (174) + the 12 node suites + the 3 targeted fix tests.

## Bottom line
CODE **PASS** for `e126968d8934a49d` — **cleared to deploy**. Both MEDIUM alerts from my rev2 verdict are fixed in the specified shape and verified by tests I ran (each fails on the old behavior); the 78374 copy blocker is resolved. No open findings from me. Aesthetic sign-off remains orch-console's.

Ran on claude-opus-4-8.
