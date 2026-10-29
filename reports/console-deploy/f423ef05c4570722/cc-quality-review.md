# cc-quality Gate-4 review — console-revamp follow-up (fc-v91)

- **Content hash (frozen):** `f423ef05c4570722`
- **Verdict:** ✅ SIGNED OFF — clear to deploy (one documented residual + a cross-lane dependency, neither a deploy-blocker)
- **Reviewer:** cc-quality (Head of Quality) — ran on claude-opus-4-8
- **Date:** 2026-10-29
- **Bus:** reply to cc-console-revamp #81289 ("FROZEN: content f423ef05c4570722"); follow-up to fc-v90 @13adaa6d
- **Branch/commits:** lane/console-revamp-followups @1c01957 (+97dd0a7)

## Hash discipline
`console_content_hash` computed twice a few seconds apart and again at sign-off:
`f423ef05c4570722` every time — tree frozen. fc-v90→fc-v91 confirmed bumped
across the gated files (spans + `APP_BUILD`).

## The three changes — verified at source

**1. backlog #141 — render_console_playwright.py full-page capture (render-only, pro-honesty).**
Before the `full_page` shot, if `#main` exists its internal scroll is neutralized
(`overflow: visible`, `height: auto`) so the whole board is captured instead of one
viewport. `#main` has `overflow:auto`, so `document.body.scrollHeight` never grew and
`full_page` silently truncated the repo-grouped Ask→live rows further down. No-op for
fleet/lanes/asks (no `#main`). Touches only screenshot tooling — no data/honesty path —
and it makes the capture *more* honest (no silent truncation). Author's measurement
(builds 1170×20136 now complete; fleet unchanged 1170×5613) is consistent with the diff.

**2. builds.js groupDuplicateDeliveries — card collapse (honest, no-drop, render-only).**
Collapses rows with an IDENTICAL summary line AND identical `delivered_at` (to the
microsecond: `key = line + "\u0000" + delivered_at`) into one card with an ×N badge.
Verified no information is lost or misstated:
- `pending` rows and `delivered_at == null` rows are pushed through untouched (never collapsed).
- single-key groups → `out.concat(items)` (preserved as-is, not dropped).
- multi-key groups → one `{collapsed:true, line, count}` card; `count` = true row count, surfaced in the ×N badge (nothing hidden).
- display only — server data/coverage counts untouched.
An identical microsecond `delivered_at` + identical summary is a genuine co-delivery
(one shipment resolving N operator_asks), so collapsing is more honest than N identical
cards that read as a mirror/dedupe bug. `node --test tests/console/builds_view.test.js`
→ all passed (new groupDuplicateDeliveries unit + end-to-end coverage).

**3. asks_triage.py — refuse --delivered/--delivered-at without a summary (fail-closed, correct for its target).**
`check_summary_before_delivery` raises ValueError (nothing written) unless
triage_summary/operator_summary already exists on the row OR --summary/--musa-summary is
supplied this call. Verified COMPLETE across both delivery paths: it is called once at a
single chokepoint (scripts/asks_triage.py:546) BEFORE the --delivered-at vs --delivered
branch (:548), so both are gated, and it fires before any write (no half-applied stamp).
`tests/test_asks_triage.py`: 75 passed (6 new). This correctly closes the operator_asks
#5690 root cause (a `done … --delivered-at` with NO summary ever supplied → now refused,
so no "summary pending" placeholder next to a delivered pill).

## Documented residual (NOT a deploy-blocker; being fixed in orchestrator PR#620)
`check_summary_before_delivery` passes the moment `--summary X` is supplied inline, without
verifying the summary actually persists. In this tree the 'done' branch still drops
`--summary` (its params omit summary; that predates PR#620), so
`asks_triage.py done <id> --summary X --delivered` passes the refuse-check yet writes
`triage_summary = NULL` → the /builds placeholder can reopen on that exact path. This is a
PRE-EXISTING 'done'-branch bug, not introduced by this PR, and is being fixed independently
by orchestrator PR#620 (makes 'done' store --summary; I reviewed it at #81295/#81316 and
asked that it also cover the 'not' branch + add per-branch tests). The #5690 case this PR
targets is fully fixed; the residual is narrow (requires inline --summary on a combined
done+deliver) and closes when PR#620 lands.

## Cross-lane dependency
Two lanes are editing scripts/asks_triage.py in parallel — this PR (adds
check_summary_before_delivery + call site) and PR#620 (makes 'done'/'not' persist
--summary). They are interdependent, not redundant: the refuse-check prevents delivery with
no summary; PR#620 ensures a supplied summary survives. They edit nearby regions, so the
second to merge must rebase. I'll re-check that the refuse-check + the done/not persistence
compose with no path where the check passes but the summary is dropped, once PR#620 lands.

## Tests — run, not asserted
- **Full `tests/console/`: 465 passed**, 3 warnings, 292.97s (my own run at this frozen
  hash; Gate-2 only runs test_app.py, so I run the whole suite per standing practice). Same
  count as the prior fc-v90 review.
- `node --test tests/console/builds_view.test.js`: all passed (1 file, 0 fail).
- `tests/test_asks_triage.py`: 75 passed (author's 6 new cases, consistent with the diff).

## Conclusion
All three changes are correct for their stated scope; #1 and #2 are display-side and
honesty-preserving, #3 fail-closed and complete across both delivery paths. The one residual
is pre-existing and has its own fix in flight (PR#620), and my gate is alert-not-block.
**Signed off — clear to deploy `f423ef05c4570722`.** Recommend landing PR#620 close behind
this so the summary-persistence loop is fully closed.
