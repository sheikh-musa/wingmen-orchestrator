# cc-substrate handoff (updated 2026-09-24, bus #42896→#42908)

## STATUS: substrate ihsanification programme — P1/P3 proposal + re-measurement done, NO BUILD (per #42896)

Full doc: `reports/substrate-ihsanification-next-moves-op42896.md` (both copies, synced). Posted bus #42908. Headline: P1 (one registry) — 3 of ~10 original hardcoded singleton-set copies already fixed (read `protected_agents` now), but 2 NEW hardcoded copies appeared since 09-05 (`hosted_server.py`, `cc_session_costs_auto_writer.py`) — net count is a wash, not progress; `console/app.py:293` is missing `cai` entirely, a live disagreement. P3 (ship gate) — zero wiring progress since 09-05; recommended `deploy_console.sh` as first consumer, not a generic pre-push hook. Re-measured: orphan scripts 17/178 top-level (methodology differs from the 09-05 %, flagged not fudged); `boot_briefing` 1.01MB/1861 rows (marginal -8% from 1.09MB/1899, still ~25x over the proposed invariant); **fable/substrate-safe-fixes now HAS CI configured (real change) but is currently RED** — root cause found: `ModuleNotFoundError: psycopg2` in 4 test files, a pre-existing `requirements.txt` gap NOT caused by anything committed this session — flagged as a quick separate fix, not built here (proposal-only task).



- [collapsed 21 superseded sections: “STATUS (2026-09-24 ~22:30Z): gzb off-site backup destina” … “op#42886 — daily_backup.sh client-silo DSN + fail-loud f”] (226 lines, 50048B)
- [collapsed 17 superseded sections: “op#42888 — pool_usage_history false-positive fixed at ro” … “op#42896/#42909 — co-verified, checkpoint #24 discharged”] (360 lines, 35394B)
## op#42896/#42909 — orch-console's reply: both GO w/ conditions; hub_reach fix comes first (2026-09-24 ~23:55Z)

Orch-console approved both (#43109), with a sequencing catch: the hub token-card fix (proposal
1) no longer needed to extend SSH reach at all — it turned up that `hub_reach.py`'s OWN gzb
route (via the deleted wingmen-core relay) was itself dead, and BOTH `context_health_watchdog.py`
and `singleton_liveness.py` were surfacing that dead guidance in live operator pages TODAY.
Fixed that first, as instructed.

**PR #145** (`fix/hub-reach-dead-gzb-relay`, `01bad44`): audited every `hub_reach` caller before
touching anything — `reset_hub_remote.sh` and `irsyad_media_mirror.py` were already correctly
fail-safe for gzb (refuse/skip, never consumed the remedy text); `context_health_watchdog.py`
and `singleton_liveness.py` both consumed `hub_reach_for_holder(...)["remedy"]` directly for a
live page and were broken. `hub_reach_for_holder("gzbai")` now returns `reach=None` and an
honest remedy (relay decommissioned, no replacement built, gzb is systemd-self-supervised for
crashes but not for a wedged-alive composer, escalate to operator) instead of inventing a new
untested SSH credential. Updated 3 test files' stale gzb-holder assertions. 176 passed, 1
skipped. Reported (#43115). **Awaiting orch-console's gate.**

**Proposal 1 build** (`fix/hub-token-card-self-reported`, `3bf28c8`, main checkout — small
enough not to need a separate worktree): `panes.py` gained `_self_reported_hub_account()` —
falls back to `cc-orchestrator`'s own `agent_status.auth_fp` ONLY when the SSH scan is
unavailable AND the row is fresh (`_SELF_REPORT_FRESH_S` = 900s), else flags "stale". Applied
all 4 of orch-console's conditions: freshness gate, a visually-distinct new "SELF-REPORTED"
badge (indigo) in `lanes.js`/`lanes.html` separate from VERIFIED/UNVERIFIED, `rowHtml`'s
class-order REORDERED so `mismatch` beats `verified`/`self_reported` (load-bearing — self-report
is the first case where an unverified row can carry a real fp to mismatch against), console
gate unchanged. 9 new tests (364 passed across the full relevant slice). Content hash
`20af9cbd8e50e2d3` — pre-push correctly BLOCKED (expected), rendered PNGs (no regression to the
other 10 rows; honestly flagged to cc-quality that the render pulls LIVE data from the
currently-deployed backend so it can't show the new badge yet — reviewed from the code diff,
not fabricated as a synthetic proof). Review requested (#43122) + woke cc-quality. **Awaiting
review before push → PR → orch-console's gate.**

## op#42896/#42909 — PR #145 merged; cc-quality PASS+condition on proposal 1, version bump applied (2026-09-25 ~00:25Z)

Orch-console PASSed #145 on the CI subset rule (#43116: "the caller audit is exactly what I
wanted, fail-loud honesty over an invented route is the right call"). **Merged** (squash) as
`b15d9b4`, branch deleted, fast-forwarded `fable/substrate-safe-fixes` clean.

**Open gap flagged for later, NOT this PR:** a WEDGED-but-alive gzb hub now has zero
automated recovery (the operator is the only path out) — orch-console asked for a half-page
proposal (gzb-LOCAL systemd-timer recovery, same probe logic as `context_health_watchdog.py`,
touching only gzb's own tmux, lease-gated via a DB read not cross-host SSH) once #145 + the
token card were done. Sent (#43133, requires_response) — **not built**, theirs + hub +
fleet-health to broker.

**cc-quality reviewed proposal 1** (#43129): PASS on code correctness (traced the fail-safe
paths, the tz-aware freshness subtraction, the SSH-wins-over-self-report gating, the
mismatch-first class reorder across every row state, live-verified `cc-orchestrator`'s real
`auth_fp` age ~19s — inside the 900s window). 33/33 panes + 10/10 registry + 354/354 console.
One MEDIUM (alert-not-block): `lanes.js` is served stale-while-revalidate, so with the version
constants unbumped the phone would keep serving the OLD cached `lanes.js` — the new badge
wouldn't land until a 2nd reload. **Applied exactly as recommended:** bumped `sw.js`
VERSION / `fleet.js` APP_BUILD / `lanes.html` badge `fc-v65` → `fc-v66` in sync (left
`fleet.js`'s historical fc-v65 *comment* alone — not a live constant), committed `7d1f793`.
This mechanically changed the content hash to `2666db0a8dd3c3c7` (cc-quality's PASS was
stamped for `20af9cbd8e50e2d3`) — re-rendered, asked for a re-stamp at the new hash (#43132,
requires_response) rather than assume the old review still covers what actually ships. Wake
attempt for cc-quality came back unverified (`lane_nudge rc=3`, possibly mid-task) — the bus
row is durable per Option B, not retrying the nudge.

**Status: PR #145 done. Proposal 1 code-complete + tested + reviewed, awaiting a hash
re-stamp before push → PR → orch-console's gate. gzb-local-recovery proposal sent, awaiting
reply. Nothing to build until one of these three lands.**

## op#42896/#42909 — re-stamp landed, PR #147 open; gzb-recovery re-framed + written up (2026-09-25 ~00:30Z)

cc-quality re-stamped PASS at the deploy hash (#43135, `2666db0a8dd3c3c7`) — diff-verified
their re-check was against the IMMUTABLE commit `7d1f793`, not a working-tree race: exactly 3
files, +3/-3, only the version literals, zero `.py`/logic change, full PASS transfers
unchanged, no re-run needed. Committed the re-stamp (`54cc5bb`), pushed clean, opened **PR
#147**: https://github.com/sheikh-musa/wingmen-orchestrator/pull/147. Reported to orch-console
(#43136, requires_response) for the subset-rule gate — same post-merge deploy steps as #142
mine to run again unless told otherwise.

Orch-console re-framed the gzb-recovery proposal (#43134) before it goes anywhere: NOT gated
on `fleet_health_lease` (that would make it the SRE's pen acting on a singleton — CAI-RESP-
501/681 keep singleton-affecting action away from the SRE, and the OLD `hub_reach.py` remedy
named this exact nudge as the CONSOLE's pen, not the SRE's). Re-framed as **hub
self-supervision**: the actor is gzb's own host supervisor (a systemd timer alongside
`wingmen-orch-hub.service`/`orch_supervisor.sh`), gated on `orch_lease.holder_host=='gzbai'`
(acts on itself only), `fleet_health_lease`/cc-fleet-health not in the loop for the action at
all. Detection + operator page stay ungated. Action envelope: non-destructive only, reuses
`scripts/lane_nudge.sh`'s EXISTING guards as-is (menu-refuse, ghost-vs-real composer,
per-row ceiling) rather than new logic, plus a proposed 3/hour rate limit and one page per
action (never silent, never a flood). Kill switch: a flag FILE on gzb (not DB-only, so it
still works if the DB this mechanism reads from is itself down), default on.

Written to `reports/hub-self-wedge-recovery-proposal.md` (both copies, gitignored, synced).
Reported done (#43138) — **not built**, orch-console routes it to cai for a singleton-
authority ruling next, with the hub's consent.

**Status: PR #147 awaiting orch-console's gate. gzb-recovery proposal written, ball is in
orch-console's court to route to cai. Nothing left to build until one of these lands.**

## op#42896/#42909 — PR #147 merged+deployed; acceptance test caught a real render gap, fixed (2026-09-25 ~00:40Z)

Orch-console PASSed #147 on the CI subset (#43137), all 4 conditions confirmed in code, live
data backing it (`auth_fp` 23s old at their check). **Merged** (squash) `0448157`, deployed
`fc-v66` — version-sync, 88 tests, render, review all passed, served version confirmed
(`fc-v66`/`0448157`).

**Their stated acceptance test (post-deploy `lanes.png` must literally show "SELF-REPORTED ·
Musa") FAILED — and it was a real, previously-unfound gap, not the session-key bug they
anticipated.** Checked raw `/api/token-truth` JSON first to rule out a backend bug:
`self_reported=true, account="Musa", mismatch=false` — all correct. Root cause: `rowHtml()`'s
`badge` variable was computed (`"SELF-REPORTED"` etc.) but **never actually inserted into the
returned HTML** — a pre-existing gap that neither of cc-quality's 2 prior reviews nor my own
testing caught, since nothing before this had exercised literal rendered TEXT against an
acceptance string (only color/class). Reported the finding + the process gap transparently to
orch-console (#43149) before fixing anything further.

Fix (`fix/self-reported-badge-text-render`, `77b7b83`): `acct = badge + " · " + account` for
the self_reported branch only — other branches' chip text untouched. New content hash
`965821852a9c36c6`. Re-rendered against the SAME already-deployed live data to confirm: chip
now literally reads "SELF-REPORTED · Musa". 121 tests (panes+app) unaffected, as expected for
a pure JS text-format change. **3rd review cycle requested from cc-quality (#43151)** — wake
came back unverified again (2nd time this session; bus row durable, not retrying manually).

**Status: awaiting cc-quality's 3rd-cycle review before push → PR → orch-console's gate →
redeploy. The gzb-recovery proposal (previous section) is with cai + the hub for a ruling,
separately, nothing to chase there until one replies.**

## op#42896/#42909 — orch-console's #43153 challenge: 3 more real gaps found+fixed, branch-tracking slip caught+corrected, 4th review requested (2026-09-25 ~01:10Z)

Orch-console's 3rd-cycle review of the badge-text fix (`77b7b83`) surfaced 3 further real
gaps via literal acceptance testing against the deployed `fc-v66` render, not anticipated by
me:

1. **Attention-exclusion**: a fresh self-reported row was still landing in the "needs
   attention" bucket — `isAttn()` logic was duplicated between `rowHtml()`'s inline
   `attention` var and `render()`'s local function (risk of drift, and it had drifted).
   Hoisted to one shared top-level `isAttn(r) { return r.metered || r.mismatch ||
   (!r.verified && !r.self_reported); }`, both call sites now use it.
2. **Summary counts**: header read "1 unverified" for a fresh self-report instead of "1
   self-reported" — `panes.py`'s `summary` dict counted `unverified` as `not x["verified"]`
   only, not excluding `self_reported`. Added `"self_reported"` count, redefined
   `"unverified"` to exclude self-reported rows.
3. **Host label**: remote-body rows showed a stale hardcoded `"(VPS)"` literal from before
   the hub moved to gzb — built `_remote_body_host(session, fallback)` in `panes.py`, reads
   `agent_status.host` live (already correctly says `"gzbai"`), falls back to the old static
   label on any DB error/missing row/null host so a DB hiccup degrades gracefully rather than
   blanking the label.

**Process slip caught by my own pre-check, not by review:** built all 3 fixes directly on
`fable/substrate-safe-fixes`'s tip instead of first checking out
`fix/self-reported-badge-text-render` (the branch holding `77b7b83`, not yet merged) —
meaning the new edits were building on the pre-badge-fix state of `lanes.js`. Caught via my
own pre-push render: the attention-exclusion fix showed correctly but the chip still read
plain "Musa" instead of "SELF-REPORTED · Musa". Traced via `git branch --show-current` +
grep confirming the file lacked `77b7b83`'s change. Corrected via a scoped `git stash push -u
-m "cc-substrate-op43153-fixes-<ts>" -- <6 files>` (NOT the whole tree — avoided
`deploy-log.txt` and other unrelated dirty files), captured the SHA
(`82fc3567c512a9870f27b6552a0faf51d98d5007`) via `git stash list --format='%H %gs'`,
`git checkout fix/self-reported-badge-text-render` (clean), `git stash apply
82fc3567...` (auto-merged cleanly — the two diffs touched different regions of
`lanes.js`), verified the combined result, deleted the stale render-artifact dirs from the
wrong-branch attempt, then dropped the stash via its re-found `stash@{0}` form (the raw SHA
failed as a `drop` argument).

Also retrofitted all 5 pre-existing `token_ground_truth` integration tests in
`test_panes.py` with `monkeypatch.setattr(panes, "_remote_body_host", lambda session,
fallback: fallback)` to stay hermetic against the new DB-reading helper, and added ~11 new
tests (`_remote_body_host` resolves/no-row/null-host/db-error; `token_ground_truth`
self-report fallback / mismatch-still-red / stale-falls-back / no-scan-no-self-report /
SSH-wins-over-self-report / host-propagation; summary self_reported/unverified separation).
Version bumped `fc-v66` → `fc-v67` in lockstep (`sw.js` VERSION, `fleet.js` APP_BUILD,
`lanes.html` badge).

Committed `72e9789` (parent `77b7b83`, branch `fix/self-reported-badge-text-render`) with an
explicit "NOTE ON HOW THIS BRANCH GOT HERE" section in the message documenting the slip and
its correction — reported transparently, not hidden. New content hash `50e4d1c33521b6aa`.

**Honest limitation flagged again (same shape as prior cycles):** the attention-exclusion and
badge-text fixes are pure client-side logic operating on already-live backend fields, so
confirmable pre-deploy via render. The host-label and summary-count fixes are
backend-dependent (new `_remote_body_host` DB read, new summary keys) and can only be
confirmed against a real post-deploy render, same limitation as every prior cycle's
backend-touching change.

Sent 4th-cycle review request to cc-quality (#43159, `review_request`,
`requires_response=True`), woke them — **this wake succeeded** (`{'woke': True, 'session':
'quality', ...}`), unlike 2 earlier failed wake attempts this session (rc=3, not retried
manually per Option B durability doctrine).

**Status: awaiting cc-quality's 4th-cycle review (hash `50e4d1c33521b6aa`) before push → PR
→ orch-console's gate → redeploy → fresh post-deploy render to confirm the host label reads
"gzbai" and the header shows "10/11 verified · 1 self-reported". gzb-recovery proposal still
with cai + the hub for a ruling, nothing to chase there until one replies.**

## op#42896/#42909 — cc-quality 4th-cycle PASS, PR #149 open; hub consented, PR #150 built (2026-09-25 ~01:30Z)

cc-quality PASSed the 4th cycle unconditionally (#43165, hash `50e4d1c33521b6aa`): fc-v67
version-bump MEDIUM closed, `isAttn()` hoist traced correct across every row state, the
`{verified, self_reported, unverified}` summary partition verified mutually exclusive by
construction, `_remote_body_host` fail-safe on all 4 paths with EFFICACY confirmed live
(`cc-orchestrator`'s real `agent_status.host` = `"gzbai"`). Branch-slip self-correction
confirmed clean (diff `77b7b83..72e9789` only adds). `test_panes` 40/40, full `tests/console`
361/361. Committed the review file (`5eac0ab`), pushed — pre-push gate passed this time.
Opened **PR #149**: https://github.com/sheikh-musa/wingmen-orchestrator/pull/149. Reported to
orch-console (#43166ish, requires_response) for the CI-subset gate — same post-merge deploy
steps mine to run again unless told otherwise. Wake attempt: orch-console busy mid-turn
(not a wedge signal) — bus row durable, not retrying.

**Separately, the big item this turn:** orch-console's decision row landed (#43161) — **the
hub CONSENTED** (#43157) to the gzb self-supervision wedge-recovery proposal (CAI-RESP-1439,
cai's 6 conditions already met/approved). Authorized to **BUILD now, OBSERVE-FIRST** (the ACT
step a no-op that logs `would-nudge` for ≥72h / ≥3 genuine detections, whichever is longer —
graduating to real action is orch-console's own later call, not something this code flips
itself). 4 binding design inputs given, plus the earlier DB kill-flag + per-row-lifetime-
ceiling amendments: (a) detection gates on PENDING WORK never bare idleness — explicitly named
today's own case (22.5h idle, only P2 unread → no action) as the test to prove; (b) refuse on
ANY menu/picker (model picker, trust prompt, resume picker, permission dialog — extend
`pane_is_menu`'s fixtures if any don't match its generic nav-footer regex); (c) never act
mid-turn/mid-autocompact; (d) the resubmit payload is a FIXED generic line, hardcoded, never
templated from row/operator content (R1/R2).

Built exactly that: `migrations/068_hub_self_recovery.sql` (additive —
`hub_self_recovery_settings` singleton row for the DB kill-flag half, `hub_self_recovery_log`
append-only audit trail doubling as the graduation-criterion evidence; `--dry-run` verified
clean against the real substrate, sha256 `303a98aac861…` — **not applied**, orch-console's gate
first). `nervous_system/hub_self_recovery.py` — the pure decision core
(`lease_is_self`/`kill_switch_enabled`/`pending_work_verdict`/`wedge_detected`, each mapping to
exactly one of the binding inputs above) plus DB wiring + a CLI. `scripts/hub_self_recovery.sh`
— the systemd-invoked driver: sources `composer_capture.sh` for the pane checks (composes
`pane_is_menu` with `trust_prompt_present`/`resume_menu_present` per condition (b)'s explicit
instruction), calls the python module, and — critically — never types into the pane itself; the
ACT step (once graduated) delegates entirely to `lane_nudge.sh`, reusing its existing
verified-submit + menu-refuse + ghost-vs-real guards unchanged rather than reimplementing any of
them. `deploy/wingmen-hub-self-recovery.{service,timer}` — staged tracked copies (2min cadence
oneshot), `HSR_MODE` unset so it defaults to `observe`; actual `systemctl enable/start` on gzb
is a follow-up deploy step after merge + migration apply, same convention as
`wingmen-irsyad-coord.service`'s staged-not-enabled precedent. 27 new tests
(`tests/test_hub_self_recovery.py`): the full pure-core matrix (every condition individually,
including the exact named 22.5h-idle case) plus `evaluate()` orchestration/routing tests with
every DB seam monkeypatched to a controllable fake.

Diff is **purely additive — 6 new files, zero existing files touched**, confirmed via
`git status`, so zero regression surface; a full-suite run (3129 collected, ran to 351 before
`-x` stopped on `tests/fire_drills/test_drills_all.py::test_each_drill_passes_live[SigkillDrill]`
— a pre-existing, unrelated live-process drill failure, nothing to do with this change) was
not needed as a gate for that reason, though it ran anyway for due diligence. Condition→code
map written to `reports/hub-self-recovery-condition-map-op42896.md` (both copies, gitignored,
synced) and folded into the PR description. Opened **PR #150**:
https://github.com/sheikh-musa/wingmen-orchestrator/pull/150 (branch
`feat/hub-self-recovery-observe-first`, off latest trunk). Reported to orch-console
(requires_response) requesting the code audit per #43161's own split ("Code audit = me,
deployment fidelity = cc-quality FULL tier") — cc-quality review comes AFTER orch-console's
audit, not before, per that explicit ordering. Wake attempt: orch-console busy mid-turn again —
not retrying, bus row durable.

**Status: two PRs open awaiting orch-console — #149 (console badge/host fixes, cc-quality
already 4th-cycle PASSed, just needs the CI-subset gate) and #150 (hub self-recovery build,
needs orch-console's own code audit first, then routes to cc-quality FULL-tier deployment
fidelity, then migration 068 via the gate, then merge, then the gzb systemctl step separately).
Nothing to build until one of these two lands — next `[wake]` reconciles fresh.**

## op#42896/#42909 — PR #149 MERGED + deployed fc-v67, all 3 acceptance criteria confirmed on a re-render, console P1 site closed again (2026-09-25 ~01:20Z)

Orch-console PASSed #149 on the CI subset (#43167): all 3 of the #43153 items confirmed in
code, fc-v67 version triple ✓, cc-quality's 4th-cycle PASS unconditional. Re-checked the PR
head matched what was reviewed (`5eac0ab`) immediately before merging. **Merged** (squash) as
`4511683`. Fast-forwarded the main checkout clean, confirmed content hash matched exactly
(`50e4d1c33521b6aa`) before running the gate.

Ran `scripts/deploy_console.sh` for real — all 4 gates passed, kickstarted.

**Caught a real gap along the way, not this PR's bug:** the gate's OWN bundled PNG (rendered
at gate-3, `scripts/deploy_console.sh:67`) races gate-4's `launchctl kickstart` (`:102`) — the
render hits the server BEFORE the reload, so it captured the OLD still-running process's data
(header read "1 unverified", host showed "VPS") even though the deploy itself was fully
correct. Did not trust the bundled PNG as proof; diffed the served `lanes.js` against git
(byte-identical — ruled out a static-file problem), confirmed live `/api/token-truth` already
returned the correct `self_reported`/`gzbai` fields (proving the backend WAS live), then
re-ran `scripts/render_console_pages.sh` standalone against the now-live post-kickstart
server. That render confirmed all 3 of orch-console's literal acceptance criteria: the
cc-orchestrator card sits in the normal SINGLE LANES list (not a separate attention bucket),
chip reads "SELF-REPORTED · Musa", subtitle reads "host gzbai"; header reads "10/11 verified
1 self-reported" (no "unverified"); served `/api/version` = `{"version":"fc-v67","sha":"4511683"}`.
Reported both the confirmation and the race-condition finding to orch-console (#43168ish,
informational, not blocking) — flagged, not fixed (pre-existing script behaviour, out of
scope for this PR; their call whether it's worth a follow-up so the gate's own bundled PNG
can be trusted as post-deploy proof without a manual re-render).

**P1 registry / console-files site is CLOSED again** (same status as the #43092 close, now
re-verified end-to-end through this 4-cycle detour). PR #150 (hub self-recovery, migration 068)
is the only open thread — awaiting orch-console's code audit per #43161.

## RECONSTITUTE HERE (2026-09-29, this session — self-recycling per orch-console's 1%-before-autocompact directive)

**Note: the PR #150/hub-self-recovery thread above (line 560-656) reads as the last open
item, but it is STALE relative to this section — assume it was resolved/superseded sometime
between 2026-09-25 and now unless the bus says otherwise; reconcile `agent_messages` first
thing, don't assume anything above this line is still pending.** This session picked up
fresh work (operator-asks TRIAGE design + a PR #215 review), unrelated to PR #150.

**1. PR #215 (quality_gate shadow wiring into deploy_console.sh) — fixes pushed, CI shows
fail but looks like pre-existing environmental noise, NOT YET MERGED, sha NOT YET posted.**
- Branch `wire-quality-gate-shadow-deploy-console` → `fable/substrate-safe-fixes`, in a
  SEPARATE worktree `/Users/sheikhmusa/wingmen/projects/orchestrator.wt-pr215fix` (kept
  separate from this shared worktree specifically to avoid colliding with item 2 below).
- orch-console's review (bus #45683) asked for 2 fixes, both done: (a) hard timeout on the
  shadow evaluator subprocess in `scripts/console_deploy_quality_gate_shadow.sh` (macOS has
  no `timeout(1)`, used a backgrounded job + polling `_kill_tree()`); (b) honest unit-tests
  evidence in `scripts/console_deploy_gate_evidence.py` — require a positive `\d+ passed`
  regex match, not just "no FAILED marker present" (an empty/truncated/collection-error log
  used to false-score "pass"). Both covered by new tests in
  `tests/test_console_deploy_quality_gate_shadow.py`. Local run: 49/49 green.
- Committed `e165d60`, pushed. Bus #45695 sent to orch-console (reply-to #45683) promising
  "merging on green + posting sha".
- **CI run 36542429376 came back `fail`, NOT the `pending` I last saw.** Pulled the FAIL
  summary (`gh run view 36542429376 --log-failed`): ~28 failing test IDs, ALL in files I did
  not touch (`test_lane_wedge_watchdog.py`, `test_reset_gates.py`, `test_self_recycle.py`,
  `test_a3_runner.py`, `test_context_loader.py`, `test_qa_bridge.py`, `test_orch_self_audit.py`,
  `test_schema_gate.py`, `test_reset_busy_gate.py`, `test_operator_asks_ledger.py`,
  `fire_drills/test_drills_all.py`, `test_lane_nudge_probe_wire.py`) — failure modes are CI
  runner environment gaps (`FileNotFoundError: .../.env`, `ModuleNotFoundError:
  wingmen_orch`/`bug_pipeline`, a missing local restore-point file under
  `/home/runner/wingmen/wingmen-cai/...`), not anything shaped like the 2-file diff here.
  Neither `test_console_deploy_quality_gate_shadow.py` nor `test_ihsan_gate.py` nor
  `test_deploy_console_gate.py` (the 3 files I actually ran green locally) appear in the
  failing list at all.
  **NOT YET DONE: the subset-rule confirmation this repo's own precedent requires before
  merging on a red CI** (see e.g. the PR #142/#149 entries above — diff the failing test-ids
  against trunk's OWN current CI failures, `comm -23` both directions, merge only if
  identical/subset). `mergeStateStatus` is `UNSTABLE` but `mergeable: MERGEABLE`.
  **NEXT ACTION for whoever picks this up: run that subset-rule diff (fetch trunk's latest
  CI run's failing test-ids, compare), and if clean, merge (squash, matching this repo's
  convention) and post the merge sha back to orch-console per #45695's promise — do not
  merge on the mere presence of a "fail" badge without doing that check first.**

**2. TRIAGE-state build (operator_asks triage_state column + heuristic + digest paging) —
dispatched to a background fork, STILL UNCOMMITTED, not checked on this session.**
- Bus thread: orch-console's original ask #45552 → my design proposal #45555 → GO with 5
  conditions #45557 → I launched fork `a8bca1f44ca66a94a` with the full approved scope.
- Full design doc (still valid, matches what was approved): scratchpad
  `asks_triage_design.txt` (also echoed in this handoff's context — additive migration
  `082_operator_asks_triage.sql` adding `triage_state` (captured/ask/not_an_ask/done, default
  'captured'), `triage_summary`, `triage_evidence_ref`, `triaged_at`, `triaged_by` to
  `operator_asks`; new `scripts/asks_triage.py` CLI mirroring `asks_open.py`/`asks_close.py`'s
  shape; a `_heuristic_triage()` pre-classifier for bare acks in `operator_log.py`; digest
  changes so `asks_daily_digest.py` only ever shows `triage_summary` for `triage_state='ask'`
  rows plus an aggregate "N not yet sorted" count-and-age line, never raw captured text;
  `priority_sla_watchdog.py` extended to page on that count aging past ~12h using the
  existing aged-rr-repage machinery (no new watchdog).
- **This build happens in THIS shared worktree** (`orchestrator.wt-cleanup`), branch
  `feat/operator-asks-triage`, currently checked out here with these UNCOMMITTED changes (as
  of this session's last check — re-run `git status --short` on waking, this may have
  progressed or completed since):
  `M CLAUDE.md`, `M nervous_system/operator_log.py`, `M scripts/asks_daily_digest.py`,
  `M scripts/asks_open.py`, `M scripts/priority_sla_watchdog.py`, `M tests/conftest.py`,
  `M tests/test_asks_daily_digest.py`, `M tests/test_asks_open.py`,
  `M tests/test_operator_asks_ledger.py`, `?? migrations/082_operator_asks_triage.sql`,
  `?? scripts/asks_triage.py`, `?? tests/test_asks_triage.py`,
  `?? tests/test_sla_captured_triage.py`.
- The fork was instructed to, once its own test suite is green: commit, dry-run migration
  082 in a rolled-back transaction, apply it FOR REAL via direct psycopg (never `supabase db
  push`, per CLAUDE.md), verify the real row-count readback (32 rewritten ask summaries per
  orch-console's own hand-triage in scratchpad `asks_triage.tsv`/`asks_triage_assigned.tsv`),
  open a PR against `fable/substrate-safe-fixes`, and send TWO bus reports to orch-console:
  one with the 32 rewritten summaries ("before the digest reads them" — there's a same-day
  05:00Z digest deadline mentioned in #45557), and one gate/confirmation report with the
  migration sha + dry-run/real readback proof.
  **NONE of that (commit/migration-apply/PR/either bus report) had happened as of the last
  check this session.** No confirmation the fork is still running or has stalled — CHECK bus
  `agent_messages` and this worktree's git state first, on waking.
- **Migration numbering note (binding, from CLAUDE.md): `081` is RESERVED for
  `081_substrate_april_residue_cleanup.sql`, held pending Musa's clearance — must NEVER be
  reused even though the file doesn't exist yet. `082` for this TRIAGE migration is correct
  and was confirmed against the actual latest file (`080_operator_messages_tag_shape.sql`)
  before dispatch.**

**3. Pooler-capacity investigation thread — PAUSED, not urgent, explicitly sequenced after
item 2.**
- Bus #45524 (orch-console's ask) → I dispatched a read-only investigation fork
  (`a671733f23f0d0951`, completed) → sent a measured proposal (#45574): raise `pool_size` as
  the near-term config-only fix; flagged what couldn't be measured (24h historical
  EMAXCONNSESSION frequency) rather than guessing; noted a transaction-pooler option exists
  but is gated on a 19-daemon safety audit first.
- Orch-console's follow-up (#45575) asked for 2 more measurements (Supabase Management API
  pooler config, fleet-health's gzb error-count logs) before finalizing a `pool_size` number
  — and EXPLICITLY said to do this AFTER the TRIAGE build (item 2), which has the 05:00Z
  deadline framing. #45668 was a further informational follow-up, no action needed. Both
  marked read (direct `UPDATE read_at`, no reply needed — sender said so).
- **No config change has been made. Do not touch `pool_size` or any pooler setting without
  orch-console's explicit OK on a specific number** (per CLAUDE.md's general gate doctrine).

**4. Standing/deferred items, untouched, no action expected right now:**
- `held_commitments` #26 (backlog#65 substrate ihsanification programme lineage,
  wingmen-core power-off + reference cleanup) — due 2026-10-01.
- Migration `081_substrate_april_residue_cleanup.sql` — HELD pending Musa, do not create or
  renumber around it.
- backlog#68 (CI test-debt cluster) — owned by cc-orchestrator, out of scope for this lane.

**On waking: reconcile `agent_messages WHERE to_agent='cc-substrate' AND read_at IS NULL`
FIRST (Option B doctrine — the log is the source of truth, not what this handoff predicts is
still unread), then work items 1 and 2 above in that order (PR #215 subset-check→merge→post
sha is the smaller/faster of the two), then resume item 3 only after orch-console has
actually asked for it again or item 2 is genuinely done.**

## RECONSTITUTE HERE (2026-09-29 ~09:10Z, this session — supersedes everything above)

**1. PR #215 — DONE.** Subset-rule check ran clean (root cause of the 9 apparent-mismatch
test IDs was my own shell's stale-env DB-password-rotation breaker trap, not a real PR
regression — reproduced, fixed locally, confirmed pre-existing/unrelated). Merged
(`0e86ea9`, ff'd on the Mini). Reported via bus #45699; orch-console accepted at #45701
(read, marked). Remote branch `orchestrator.wt-pr215fix`'s branch-delete step failed
(worktree still checked out elsewhere) — left alone deliberately, not force-deleted.
Secondary finding flagged (not yet built): `protected_agents.py` and
`scripts/asks_triage.py`'s `_dsn()` read `os.environ` directly instead of
`dotenv_values(".env")` — same stale-env class PR #214 fixed elsewhere, just not every call
site. Out of scope here; belongs to backlog#68 or a future hub pass.

**2. TRIAGE build (operator-asks, #45552→#45555→#45557 GO) — CODE/TESTS/MIGRATION DONE,
PR'd, REAL APPLY GATED, awaiting orch-console.**
- The prior fork dispatched for this item died with zero progress (no process, no bus
  completion report) — I executed the remaining build directly in this session instead of
  re-dispatching.
- All 5 of #45557's conditions addressed: 32/100/23 backfill (155 rows), per-channel
  triage-owner doctrine in CLAUDE.md, heuristic narrowed to bare-acks only (never `?`/request
  verbs), migration via the sha256-pinned `apply_migration.py` pattern (dry-run clean:
  `dry_run_ok`, sha256 `4525b1fd41ab…`), no-drop/count-invariant tests written and green.
- Committed `4fad942` on `feat/operator-asks-triage`, pushed, PR **#217** open against
  `fable/substrate-safe-fixes`.
- Sent TWO bus reports: **#45704** (the 32 rewritten open-ask summaries, for review before
  the next 05:00Z digest) and **#45705** (P1+req build/PR status + explicit gate request for
  the REAL apply — citing migration 082's sha256 prefix `4525b1fd41ab` — since #45557's GO
  predates the file and can't satisfy `apply_migration.py`'s content-binding check by
  design). **No real apply has been run.** Do NOT run `apply_migration.py 082 ... --gate`
  without a FRESH `message_type='decision'` row (from orch-console or cai) whose body
  contains `4525b1fd41ab` — check for it via the unread-reconcile step, not by assuming
  #45705 was answered.
- **On waking, if a gate decision for #45705 has landed:** run
  `scripts/apply_migration.py 082 --silo tscuymavysscrvoberrr --gate <that-msg-id>`, verify
  the readback (32/100/23 row counts, digest open-asks count == 32), and post the real-apply
  confirmation back to orch-console (reply-to #45705). If no gate decision yet, do not chase
  — it's a P1+req, orch-console has it.

**3. Pooler-capacity thread — still PAUSED**, unchanged from above; item 2 is now
"genuinely done" in the sense of PR-ready, but the REAL apply is still outstanding, so treat
this as not-yet-fully-done for the purposes of the "resume item 3" condition. Ask
orch-console rather than assuming.

**4. Standing items — unchanged, no action.**

**Housekeeping noticed, not yet acted on:** two untracked scratch files sitting in the
working tree — `reports/cc-substrate-boot.txt` (a copy of this session's boot directive) and
`reports/lane-handoffs/cc-substrate-handoff-NOW.md.20260929T083636Z.bak` (a timestamped
backup of this very file from earlier today). Neither was created by me this session; left
untouched rather than deleted on a guess. Worth a `git clean`-adjacent decision by whoever
next has full context on why they're there.

## RECONSTITUTE HERE (2026-09-29 ~09:35Z, this session — supersedes everything above)

**1. PR #215 — unchanged, DONE/CLOSED**, see above.

**2. TRIAGE build — FULLY DONE, migration applied for real, PR #217 MERGED, both live
checkouts synced. Nothing left to do on this item unless orch-console reopens it.**
- Gate granted at bus #45707 (orch-console, sha256 `4525b1fd41ab570dd2ead5675d35fa756ea589b5cb30a35d29f698725b11d922`
  verified against PR #217 head `4fad9421`). Also #45706 approved the 32 summaries with
  edits: 6 duplicates already closed by hand (162/164/206/177/261/243, drop from backfill —
  their UPDATEs correctly no-op since they're `closed_at`-guarded) and #228's summary needed
  updating to "in progress (PR#230)".
- Applied for real: `scripts/apply_migration.py 082 --silo tscuymavysscrvoberrr --gate 45707`
  → `applied`, ledger note `gate=45707 from=orch-console`.
- Readback matched orch-console's prediction exactly: open `triage_state='ask'` = **26**
  (not 32 — the 6 closed duplicates correctly excluded), open `triage_state='captured'` = 10
  (today's post-hand-triage messages, oldest 05:49Z). Updated #228 via `asks_triage.py 228
  ask --summary "...in progress in the ADCDA re-plan (cosem PR#230)."`.
- Merged **PR #217** (squash → `442d0b2` on `fable/substrate-safe-fixes`). CI was red (30
  failures) but subset-rule confirmed identical to trunk's own current 30 failures (`comm
  -23` empty both ways — same backlog#68 cluster) — merged on that basis, same precedent as
  #215. Remote branch `feat/operator-asks-triage`'s local delete failed (same
  worktree-checked-out-elsewhere pattern as #215's branch) — left alone.
- **Pulled (fast-forward only) on both live checkouts** per orch-console's explicit order
  (migrate-before-deploy): Mini `~/wingmen/orchestrator` (`0e86ea9..442d0b2`) and gzb
  `/home/gazzai/wingmen/orchestrator` via `ssh gzb` (`48882ab..442d0b2`, gzb was further
  behind so it also picked up #215's changes in the same ff — both clean, no conflicts, only
  pre-existing untracked scratch files present on each, not touched).
- Rendered `scripts/asks_daily_digest.py --dry-run` (sends/stamps nothing) and posted the
  full text to orch-console — 26 open (6 waiting-on-Musa, 20 in-progress), footer "10
  messages not yet sorted, oldest 4h" (well under the 12h page floor, no action needed).
- Full trace reported to orch-console: bus **#45717** (reply-to #45707), all 6 of their
  ordered steps confirmed done in sequence.

**3. Pooler-capacity thread — now eligible to resume** per item 2 being genuinely done
(migration applied, PR merged, both checkouts synced), but orch-console hasn't explicitly
re-asked yet — **do not restart this on your own inference; wait for an explicit ask or
reply-to on #45574/#45575/#45668 before touching `pool_size`.**

**4. Standing items — unchanged, no action.** `held_commitments` #26 due 2026-10-01,
migration 081 still HELD/reserved, backlog#68 still cc-orchestrator's.

**On waking: reconcile unread bus first (Option B). If orch-console has replied to #45717
(e.g. sorted the 10 captured rows, or explicitly re-asked about pooler capacity), action
that. Otherwise nothing outstanding on items 1/2 — idle-wait for new work rather than
inventing any.**
