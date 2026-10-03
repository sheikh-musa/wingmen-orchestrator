# cc-quality review — content hash `1c37eaf8f7c50848`

PR: `fix/console-dock-float` (Musa op#25251, two reported bugs: dock floats
mid-screen; "Your asks" shows a suspicious "100 open").

Reviewer: `code-review` skill (high effort), run against the diff at
`/Users/sheikhmusa/wingmen/orchestrator.wt-console-dock-fix` vs
`origin/fable/substrate-safe-fixes`. The live fleet `cc-quality` lane was not
reachable from this sandboxed task context, so the repo's `code-review` skill
stood in as the independent reviewer — noted here for transparency; orch-console
may want the fleet `cc-quality` lane to re-review before merge.

## Scope reviewed

- `nervous_system/console/db.py` — `build_asks_query()` triage_state filter
- `nervous_system/console/static/fleet.html` — `overscroll-behavior-y` fix + version bump
- `nervous_system/console/static/fleet.js` — version bump only
- `nervous_system/console/static/lanes.html` — version bump (+ follow-up fix below)
- `nervous_system/console/static/sw.js` — version bump only
- `tests/console/test_app.py` — new/updated assertions

## Findings

### 1. HIGH CONFIDENCE (fixed) — `triage_state='ask'` filter silently orphans console-assigned asks

The new `AND a.triage_state = 'ask'` filter in `build_asks_query()` is correct
per migration 082 doctrine, but `scripts/console_assign.py` — the console's
own "+ ask" / assign feature, which creates exactly the kind of
Musa-source-traced, deliberate ask this board exists to show — never stamped
`triage_state` on insert (or on its retry/re-delegate UPDATE path), so those
rows default to `'captured'` and, with no promotion mechanism, would have been
silently and permanently excluded from "Your asks" going forward. This
mirrors the precedent already set in `scripts/asks_open.py`, which *was*
updated at migration-082 time to stamp `triage_state='ask'` for the same
"already-summarized, never a raw capture" category.

**Fix applied:** `scripts/console_assign.py` now stamps
`triage_state='ask', triage_summary=<ask text>, triaged_at=now(),
triaged_by='console_assign'` on both the fresh-INSERT path and the
existing-row re-delegate UPDATE path. Tests
`test_console_assign_stamps_link_row_in_same_transaction` and
`test_console_assign_links_existing_row_instead_of_duplicating` updated to
assert this.

### 2. HIGH CONFIDENCE (fixed) — the WKWebView overscroll fix wasn't carried to lanes.html

`fleet.html` got `overscroll-behavior-y: none` to suppress the Telegram-iOS
WKWebView bug (TelegramMessenger/Telegram-iOS#1748) that drags
`position:fixed` elements off the true bottom during overscroll, but
`lanes.html` has its own `position:fixed` `.toast` and still only declared
`overflow-x:hidden`, even though its build badge was bumped in the same
deploy. An operator scrolling a long lane list in Telegram's in-app browser
could see the same float on `.toast`.

**Fix applied:** `lanes.html`'s `html,body` rule now also carries
`overscroll-behavior-y:none`. Verified no custom touch/scroll JS exists on
that page to interact with it. New test
`test_lanes_page_suppresses_overscroll_bounce` locks it in.

## Not flagged as blocking / left as-is

- `LIMIT 100` in `build_asks_query()` remains — with the `triage_state='ask'`
  filter the real count is 40 (verified live against the substrate DB), well
  under the cap, so it doesn't currently manifest. Flagged to orch-console as
  a known follow-up: `asks_open` is still `len(rows)` off a LIMIT-ed list, so
  if genuine actionable backlog ever exceeds 100 the badge would again
  silently under-report. Not fixed here to keep this PR to the two reported
  bugs.
- iOS/WebKit's own support for `overscroll-behavior` on the document-level
  scroller is historically inconsistent; there is no complete upstream fix
  (Telegram's own GitHub issue for this is open with none proposed). This
  change is the correct, zero-regression mitigation available from our side,
  not a claim that it fully eliminates the bug on every iOS version.

## Verification run

- `tests/console/` (Python): 408 passed, 0 failed.
- `tests/console/*.test.js` (Node, all 8 suites): all passed.
- Render gate: `fleet.png` + `lanes.png` rendered via Playwright (iPhone 13
  emulation) against this exact content, live `/api/fleet` + `/api/token-truth`
  data — both pages render without error, dock/toast pinned correctly in a
  standard (non-buggy) engine, "Your asks" count visible (the harness replays
  a pre-fetched API snapshot, so it still shows the server's live value at
  fetch time, not a re-run of the fixed SQL — the SQL fix itself is verified
  directly against the substrate DB and by `test_build_asks_query_derives_every_status_live_in_sql`).

## Verdict

PASS. Two real findings from independent review, both fixed and tested before
this review was written (this file documents the final, already-corrected
state — it is not a pre-fix snapshot).
