# cc-quality review — set-pointer routing fix (fleet-default clobber, GAP-B follow-up)

**Verdict: ✅ PASS** — correctly fixes the operator's twice-tripped fleet-default clobber, well-tested (130 passed incl. substantive repro), does not over-block, and holds display==apply==boot with the GAP-B resolver. 1 LOW non-blocking observation.

- **Reviewer:** cc-quality (Head of Quality) — cc-fleet-health SRE-verified; this is the independent content gate before orch-console signs + deploy.
- **Request:** bus id 20573 (P2), review_request.
- **Scope:** app.py-only (+ tests). Worktree `/Users/sheikhmusa/wingmen/orch-setpointer-wt`, branch `fable/setpointer-routing`, commits `41a0980` (L1) + `c463cf0` (L2) off `bd68502` (live tip).
- **app.py diff SHA-256 (bd68502..c463cf0):** `9a6feda95e412a90738201a26792571af684846f5e54bace1f72b27b287afbdb`
- **Reviewed (UTC):** 2026-08-13T23:23:21Z
- **Method:** verify-not-assert — read both commits, executed the shipped routing functions on representative sessions (incl. the operator's exact lane), confirmed the switch-token scope claim, and independently re-ran the full test suite.
- **Deploy note (from requester, confirmed sensible):** `deploy_console.sh`'s content-hash gate is keyed on the STATIC bundle only; this is app.py-only (no static, no sw.js bump), so the gate won't auto-force a review — hence this explicit request. Verdict saved here (backend-review), not a console-deploy content-hash dir.

---

## The bug

A per-lane console token pick on a WORKER lane wrote the SHARED fleet default `.lane_default_token` (because `_handle_set_pointer` used `_token_pointer_name`, which maps a worker lane → `.lane_default_token`). The operator tripped it **twice** — a pick on `irsyad-import` rewrote the all-lanes default → 13 lanes went off-account. Billing/routing-critical.

## The fix (two layers, both present in c463cf0)

- **L1 (41a0980) — refuse the clobber:** `_handle_set_pointer` token branch returns 400 + audits (`…:blocked-fleet-default`) if the resolved write pointer is `.lane_default_token`. Standalone-shippable "stop the bleeding".
- **L2 (c463cf0) — route correctly:** new `_token_write_pointer_name(session)` sends a worker lane's per-lane WRITE to its per-GROUP pointer `.group_default_token.<family>` (never the fleet default); singletons keep their own pointer; None for `.env` bodies / no-family. Read paths (display, apply-dry-run, armed apply) switch to new `_effective_token_pointer_name(session)` which honors the per-group tier (tier 3 if the group file exists, else fleet default) so display==apply==boot. `_HELD_LANES` guard added to set-pointer. The L1 fleet-default refusal is KEPT as defense-in-depth.

## Verification

| Item | Result | Evidence |
|---|---|---|
| Worker write no longer clobbers fleet default | ✅ | Executed shipped `_token_write_pointer_name`: `irsyad-import`/`cosem-exams`/`cc-irsyad-1`/`random-lane` → `.group_default_token.<family>`; **never** `.lane_default_token` (asserted True). `irsyad-import` → `.group_default_token.irsyad`. |
| Operator's exact lane safe | ✅ | `irsyad-import` is also in `_HELD_LANES = {"irsyad-import"}` → the held-guard refuses it first; and its would-be write routes to the group pointer regardless. Double protection. |
| Singletons unaffected | ✅ | `nazim` → `.nazim_default_token`; `cai`/`fleet-health` → None (off `.env`). |
| Fleet-default refusal (defense-in-depth) | ✅ | With current mappings `_token_write_pointer_name` can't return `.lane_default_token` for a worker (group pointer) or None (caught earlier), so the `ptr == ".lane_default_token"` guard is belt-and-suspenders — refuses LOUD + audited if a future mapping regressed. |
| display==apply==boot | ✅ | `_enrich_token_pointers`, `apply-dry-run`, `_resolve_armed_apply` all read via `_effective_token_pointer_name` (group-if-exists) — matches the lane_token_resolver tiers the lane boots on. Closes the latent GAP-B gap where a per-lane write (group) wouldn't reflect in the read (fleet default). |
| No over-block of the legit fleet path | ✅ | `test_set_pointer_guard_does_not_block_bulk_switch_all` passes; switch-all/switch-group untouched by the diff. |
| switch-token scope correction | ✅ | `_handle_switch_token` writes NO pointer (shells `switch_lane_token.sh` only) — confirmed by reading it; no spurious guard added there. `flip_fleet.sh` remains the sole legit fleet-default writer. |
| Tests | ✅ | `pytest tests/console tests/test_lane_token_resolver.py` → **130 passed** (matches the claim). The operator-repro test `test_set_pointer_worker_lane_does_not_clobber_fleet_default` is SUBSTANTIVE — asserts `not fleet.exists()` after the pick (not hollow). `…_clear_does_not_touch_fleet_default`, `…_routes_to_group`, `…_held_lane_refused`, `…_guard_does_not_block_bulk_switch_all` all green. |

## Finding (LOW · non-blocking · consistency)

`_effective_token_pointer_name` gates its tier-3 (group) on `(_REPO_ROOT / grp).exists()`, whereas the canonical `lane_token_resolver` gates tier-3 on **readable AND non-forbidden-fp**. For a group file that exists but points at an unreadable/forbidden token, the resolver falls through to the fleet default (boot) while `_effective_token_pointer_name` reports the group tier (display/apply) — a display-vs-boot split, exactly the class this work exists to prevent. **Cannot occur via the console** (set-group-pointer / switch-group only write registry-validated, readable, non-forbidden tokens), so it needs a hand-written invalid group file — and even then boot stays SAFE (the resolver refuses the forbidden token → fleet default; only the label misreads). Same class as the GAP-B `~`-hand-write caveat. Recommend `_effective_token_pointer_name` mirror the resolver's tier-3 predicate (readable + non-forbidden-fp), or consult the resolver, so the two implementations of the tier precedence can't drift. Not a ship blocker.

## Bottom line

The fix does exactly what it should: a per-lane token pick on a worker lane can no longer rewrite the shared fleet default (routed to the per-group pointer; refused loud if it ever resolved to the fleet default; held lanes refused), the read paths stay consistent with what the lane boots on, and the legitimate bulk/fleet paths are untouched. Repro is a real RED→GREEN with a substantive assertion; 130 tests green. **Ships.** The one finding is a defensive consistency nit (hand-write-only, boot stays safe) — fold into a follow-up.

— cc-quality
