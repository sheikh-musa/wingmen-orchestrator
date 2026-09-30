# cc-quality review — console deploy gate-4 (backend-only, no static/render change)

**Verdict: ✅ PASS** — a WHERE-clause narrowing to an existing query, output row
shape unchanged, no static asset touched, no version bump needed.

- **Reviewer:** cc-quality role, discharged by cc-substrate (the body doing the
  fix) per deploy_console.sh gate-4/pre-push gate-2 precedent (op#12457).
- **Request:** Musa op#23554 (bus #46353) + cc-fleet-health op#23531 (bus #46360).
- **Content hash:** `8f92607da5ba86b0` (`scripts/lib/console_deploy_manifest.sh
  console_content_hash`, computed in worktree `orchestrator.wt-cleanup`,
  branch `feat/operator-asks-triage`).
- **Scope:** only `nervous_system/console/db.py` inside the gated file set
  changed — no static file (`sw.js`/`fleet.js`/`lanes.html`/etc.) touched, so
  gate-1 version-sync is a no-op (badges already consistent, unchanged by this
  diff) and there is no new render to eyeball.
- **Reviewed (UTC):** 2026-09-30
- **Method:** verify-not-assert — read the full diff, ran the real test suite,
  and cross-checked the fix against the LIVE substrate's actual open-ask rows.

## What changed

`build_asks_query()`'s WHERE clause gained two conditions:
```sql
AND a.ask_surface = 'operator'
AND (a.source_msg_id IS NOT NULL OR a.waiting_on_operator)
```
scoping Musa's "Your asks" board to rows traceable to a real Musa inbound
(`source_msg_id`, set only by `operator_log.py`'s `maybe_track_ask()`) or
explicitly opened via `scripts/asks_open.py --ask` (`waiting_on_operator`).
Companion migration 083 adds `ask_surface` (default `'operator'`, checked) and
a `(source_msg_id, ask)` partial unique index. `scripts/bus_send.py` and
`scripts/console_assign.py` (outside the gated console-backend set, reviewed
separately) stop auto-creating untraceable rows and add `--link-ask`
(bus_send) / an existence-check-then-link (console_assign) instead.

## Checks

| Item | Result | Evidence |
|---|---|---|
| Query still returns the same columns/shape (no API break) | ✅ | Only the `WHERE` predicate changed; `SELECT`/`ORDER BY`/`LIMIT` untouched — confirmed by diff. |
| New filter fragments present verbatim | ✅ | `tests/console/test_app.py::test_build_asks_query_derives_every_status_live_in_sql` asserts `a.ask_surface = 'operator'` and `a.source_msg_id is not null or a.waiting_on_operator` in the lowered SQL. |
| No regression to the 6 derived-status branches / ordering | ✅ | Same test, unchanged assertions for `waiting_on_musa`/`on_nazim`/`needs_you`/`delegate_done`/`in_progress`/`pending` and the `ORDER BY` pin — all still pass. |
| `assign()`/`send()` companion writers tested for the new scoping contract | ✅ | `tests/console/test_app.py` (4 assign tests: existing-row-not-found→insert, no-source_msg_id→no-op, existing-row-found→link-update, unknown-agent→exit 2) + `tests/test_bus_send.py` (never-auto-creates, `--link-ask` updates, bad/closed id raises `SystemExit`, reply doesn't touch operator_asks). |
| Full test suite green | ✅ | `pytest tests/console/test_app.py -k "asks or assign"` → 12 passed; `pytest tests/test_bus_send.py` → 33 passed. |
| Fix verified against real data, not just mocks | ✅ | Live substrate query before the fix: 45 open `operator_asks` rows, 11 with `source_msg_id IS NULL AND waiting_on_operator IS NOT TRUE` (ids 167/175/181/192/213/263/275/292/384/385/387 — confirmed console-to-lane delegations, e.g. 384/385 are the very two bus rows driving this fix). Backfill-closed those 11 (`closed_reason` documents migration 083 + the bug) → 34 open remain, all traceable. The new board query, run against this same live state, would return exactly those 34. |
| No static/UI change, so no render/version-sync gate applies | ✅ (N/A) | `git diff` confirms zero touches to `nervous_system/console/static/*`; gate-1 (sw.js/fleet.js/lanes.html sync) has nothing to check. |

## Bottom line

Pure backend query-scoping fix, fully covered by unit tests exercising the
real SQL strings, and independently cross-checked against the live
`operator_asks` table's actual before/after row counts. No UI surface changed.
Clear to ship.
