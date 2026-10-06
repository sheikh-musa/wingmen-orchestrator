# Fable substrate audit — 2026-10-06 — synthesis

Coordinator: orch-console (Nazim, Fable 5.1). Trigger: Musa op#26589/26590 ("audit the rest of the substrate… spin up subagents if required"), after op#26577-26579 ("snail's pace… does not reflect our substrate's potential velocity").
Method: five parallel read-only forks (A regression vs the 09-30 audit, B velocity + inert mechanisms, C operator surface, D accretion/CI/launchd/secrets, E client-lane quality), each with its own report in this directory. No DB writes, no bus posts, no sends, no git actions by any fork. Findings labelled verified unless stated.

## One-paragraph verdict

The substrate is not slow at deciding; it is slow at **knowing**. Decisions land in under a minute at the console and within ten minutes at the slowest lane, yet ~157 reply-required rows were truly dropped in seven days (about 140 of them P0/P1), 871 asks sit "open" with ~550 already judged noise, the dispatch queue carried rows nobody was building for days, and 96% of lane status rows read "working" whether or not anything happens. Six safety mechanisms exist but run in observe or dead-signal mode against live demand (the autoscaler read depth 0 beside 9 unclaimed rows; client-ask chases 147 due, 0 sent; a client waited 41 h behind a net that logs "WOULD page"). Eleven of the sixteen defects that reached a gate or a client this week were verification *claims* that were never measurements. The fixes are mostly small and mechanical, and almost all of them subtract or arm something that already exists rather than add a mechanism.

## The six findings that matter (cross-cutting)

1. **The ledger lies, the gates don't.** Dropped RR rows ~157 (fleet-health 74, coord 39, hub 15, Tier-B 16); answered-but-unstamped 199; `operator_asks` 871 open (292 judged not-an-ask never closed, 254 untriaged); 9 dispatch rows unclaimed for 2–5 days while builders idled; `delegated_to`/`claimed_by` hold slugs unjoinable to the bus (358 rows). Every watchdog that reads these tables chases ghosts. (B §1,3; C §1)
2. **Built but inert, or reading a dead signal.** Autoscaler depth probe 0 vs 9 rows (hub diagnosing); `client-asks-chase` and `captured-triage` observe-only with 147/255 live items; `client-nodrop-watch` detect-only while cosem-tdu waited ~41 h; `hub_self_recovery` observe; `sre_lane_recycle` force-disarmed (798 refusals); pooler dead-man pages *through* the pooler it reports on and is still untracked; escalation ladders terminate at the console with no liveness fallback; `front_door.py` dead. One of six has an arming date. (A top-5; B §4)
3. **Claims are not measurements.** "Byte-identical" was text-identical; "clean" scans covered `src/` not the output tree; "24/24 wet-proof" covered zero of the holder classes the first real swap hit; a −3p accounting line shipped through two verifies (mine included) and a −11p is still live. Gate adherence on the two most sensitive lanes: cosem-adcda (real minors) cc-quality 32% / storefront FULL 3% / console verify 6%; irsyad-coord (money) 11% / 46% / 0. `data_truth classify` is UNCLASSIFIED for 12 of 13 client refs, so the P1 data-security warning cannot be satisfied and is ignored. Bus bodies carried 8 Emirates-ID, 4 NRIC and ~29 real-email rows in 7 days; the console was the top emitter. (E §1-4)
4. **The operator surface is loud.** 1.4 replies per inbound, 60% over 600 chars, 16 typed questions/day to Musa, 35 auto-acks/day arriving *after* the real reply, 2–4 uncoalesced nudges per message, a 794 KB handoff of 185 stacked blocks truncated to 24 KB on boot (stale blocks contradicted fresh facts twice today), `bot_channels` marks the busiest channel disabled, and the one-tap receive path is built with nothing sending buttons. (C)
5. **Accretion and drift.** 47 loaded-but-untracked launchd jobs (incl. live singletons), 23 dead installed plists, 15 tracked-not-loaded, `pooler_breaker_snapshot.py` running from an untracked file; 27 Telegram senders of which 10 bypass the core and 4 log; DSN accessor used by 10 of 135 readers; 11 `.env.bak*` holding the pre-rotation password; 70 transcripts with DSN shapes; `console-ngrok` sources the whole `.env` into ngrok; `main` unprotected. Orphan code is 22 files / ~2.1k lines (the 09-30 "28%" does not reproduce). CI is healthy: 4,319 passed, 27/30 green, median 9.6 min. (A §4-5; D)
6. **Capacity is the box, not the headcount.** gzbai: 4 cores, load ~10, two runners both busy with five jobs queued, six lane processes, ~8 headless Chromiums, and our own `secrets_output_scanner` hook at ~95% CPU. Honest spare heavy-build concurrency: 0. The ihsanos heavy-build label is pinned to runner-1 only. (fleet-health #54639; B §5)

## What landed since 09-30 (so we don't re-fix)

Landed 5 / partial 4 / open 3 / regressed 1 of the 12 findings; fix-order 4 / 3 / 3. Landed and verified: gzb host identity + fresh `orch_lease`; backstop sweep host-scoped and gzb sweep armed; operator_log body roles; secrets hooks + hash sweep; cc-substrate liveness row; disk at 21%. Regressed: `.env.bak*` 8 → 11. (A scorecard)

## Programme (ranked; owner · effort · when)

**Phase 0 — this week, mostly arming what exists**
1. Stamp-or-die on RR rows: `bus_send.py` requires `--reply-to` when answering an RR; nightly sweeper auto-stamps from same-thread evidence and pages the recipient for truly dropped P0/P1. Hub (`bus_send`) + fleet-health (sweeper) · 1 day. [B1]
2. Arm `client-asks-chase`, `captured-triage`, `client-nodrop-watch` (observe → act, rate-limited), fix the pooler pager (save-before-page, spool, bot-API flush), add `console_alive()` fallback to the hub in the three escalators, raise the P1 window ≥300. fleet-health · 1 day. [B3, A1-3]
3. Autoscaler: depth probe root cause, MAX_LANES 4, claim loop accepts `[supervised]` build-only rows, reaper honours console/coord HOLD, stale docstring. Hub → cc-substrate (in flight, evidence 20:45Z). [B4]
4. Output-tree artifact scanner shared library (placeholders, fleet vocabulary, stale PROPOSED label) wired into every client send/upload path; accounting identity asserts in the schedule generators (`slack >= 0 || labelled`). cc-substrate 3 h; cc-cosem-platform 2 h. [E1, E2]
5. Operator surface: suppress auto-ack on nazim-console (15 min); nudge coalescing with priority in the count line (2 h); bounded 8 KB handoff with archive (console, 2 h); 600-char reply budget with a soft gate (1 h). fleet-health / console. [C2, C5, C6, C7]
6. `secrets_output_scanner` perf (precompiled, capped, benchmarked) · cc-substrate · half day; heavy-build CI on GitHub-hosted 4-core runners pending Musa's click, bigger VPS as durable fix · fleet-health prepares, Musa decides. [#54639, #54642]

**Phase 1 — next two weeks, subtract and unify**
7. One-tap decisions: `nazim_send.sh --choices` inline keyboard + callback writes the inbound row and closes the ask; chase ladder for WAITING-ON-MUSA with buttons. Console tooling · 3–4 h + 2 h. [C1, C4]
8. Ledger hygiene: nightly close of `not_an_ask`, auto-triage captured >24 h, close-requires-evidence, agent-id-only `delegated_to`/`claimed_by` (CHECK + backfill), ship-time stamping of queue rows from the PR body. cc-substrate + coord · 2 h + 3 h. [C3, B6, E8]
9. Evidence-pack schema: any `review_request|update` that says identical/verified/clean must carry `--evidence` naming the comparator or probe; console checklist refuses packs without it; render packs cover every refusal state and target role. cc-substrate + cc-quality template · 3 h. [E3, E7]
10. Register every client store in `data_provenance` (cosem-adcda-cb6d9 REAL/minors, tdu-tools-prod, staging, Tier-B); bus-body PII redactor with a logged escape. cc-substrate · 5 h. [E4, E5]
11. Launchd = repo, one way (install/diff script, CI parity test, retire 23 dead plists, commit the dead-man); one sender core with mandatory logging + redaction (27 → 1 + wrappers); finish the DSN accessor (lint blocks new `psycopg.connect`); protect `main`; `console-ngrok` passes only its token. fleet-health 1 day; hub 1 day; cc-substrate 1–2 days + 2 h. [D1-4, A4-5]
12. Real utilisation signal (lanes stamp idle on empty inbox + no tool activity; console shows it; idle-with-work pages on it); arming calendar — one `held_commitments` row per inert mechanism with soak criteria and date. fleet-health 1 day; console 1 h. [B5, B7]

**Phase 2 — hygiene**
13. Delete the dead 13 (~1.7k lines); CLI registry test; skipped-test inventory gated like `ci_known_failing`; secrets purge (keep 2 backups, load hash sweep, scrub the 70 transcripts or rotate); re-seed cosem-adcda / cosem-platform / cosem-tdu lane docs from the template and extend the template with render-gate, comparator and cite-the-probe rules. cc-substrate / fleet-health / console · ~1.5 days total. [D5-8, E doc]

## What I own personally (console)
Bounded handoff (today); reply budget and fewer typed questions (now); arming-calendar rows (tonight); PII in my own verbatim relays — hash before posting (now); evidence checklist at my gate (now). Two of this week's shipped defects passed my own verify; the comparator rule applies to me first.

## Could not measure
irsyad repo-side adherence (console guard; bus evidence only); live process env of other bodies for stale DSNs; whether `read-parked` is armed; Telegram-server copies of leaked ids; exposed-key rotation status (operator-side).
