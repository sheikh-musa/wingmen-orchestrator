# Fable audit 2026-10-06 — Fork E: client-lane quality & gate adherence (last 7 days)

Auditor: orch-console fork E (Fable 5.1). READ-ONLY: substrate reads (agent_messages, operator_messages, bot_channels, data_provenance), repo/report files, lane CLAUDE.md/AGENTS.md. No irsyad repo/silo access (console guard — irsyad evidence is bus-only). No PII quoted; bus/message ids only. Window: 2026-09-29 18:00Z → 2026-10-06 19:00Z.

Labels: **verified** = read in the row/file itself; **inferred** = pattern/heuristic count (regex over bodies/subjects; collisions possible, stated where relevant).

---

## 1. Defects that reached the console gate or the client (declared done/verified → found wrong)

| # | Lane | What was declared | What was wrong | Who caught it, where | What the lane's own gate should have been | Smallest mechanical check that would have caught it |
|---|------|-------------------|----------------|----------------------|-------------------------------------------|------------------------------------------------------|
| D1 | cc-cosem-platform | DROP-2 C2 + C3 Overview "fact gates ALL PASS, text-layer asserts green" (#54400, #54497) | Client-facing Period-Accounting sentence printed `undefinedp` ×4 (HR/BSM/SS14-1/OBS) in BOTH Overview workbooks; present in C2 AND C3; also passed the console's own C2 verify-render (#54403). **verified** (sharedStrings count 4/4 in C2 and C3; fixed in C3.1 #54503) | console verify-render of C3 (#54499) | output-tree text assert | **Post-render assert over the RENDERED files**: refuse on `undefined|null|NaN|\[object` substrings in xlsx sharedStrings / docx document.xml / PDF text. Lane added it in C3.1 — must be a shared library step, not a per-generator habit. |
| D2 | cc-cosem-platform | C3 render "clean" | First C3 render wrote `op#26543` (fleet bus id) into all 4 client docs; the mechanical vocab scan covers `src/` only, not the reports tree. Caught and self-fixed before delivery (#54497 disclosure). **verified** | lane self-catch | same as D1 | Same output-tree scanner, with the fleet-vocabulary regex (`op#\d+`, `#\d{5}`, `cc-[a-z]+`, `CAI-`, `orch-console`, `Nazim`, `wet-proof`, `silo`, `lane`) — run on every artifact that leaves for a client (Drive, TG, PDF). |
| D3 | cc-cosem-platform | "FIF/ESB docx BYTE-IDENTICAL to C3" (#54503) | File md5s differed (zip/docProps metadata); only `word/document.xml` text was identical. Lane measured text equality and reported bytes. **verified** (md5 diff + document.xml diff = ∅) | console (#54506) | state what you measured | Evidence-pack schema: every "identical" claim must name the comparator (`sha256(file)` vs `text(document.xml)`), with both numbers. A `bus_send --evidence-type` field, or a reviewer checklist line. |
| D4 | cc-cosem-platform | C3.1 Overview verified; applied render "ready" | G3 Period-Accounting line read **block slack −3p** (false "G3 short 3 periods"); double-counted the 32p concurrent theory. Present in C3/C3.1 that the console verified; neither text assert nor eyeball caught it — arithmetic. Lane caught it on its own PDF pass (#54601). **verified** | lane self-catch, pre-Drive | arithmetic identity assert | **Accounting identity assert**: for every group, `AVAILABLE == required_placed + filler + reserve + slack (+ concurrent)` and `slack >= 0` unless explicitly flagged, evaluated on the numbers that are rendered (not on internal state). G2's −11p is still rendered today (lane calls it "blunt, not wrong") — same assert would force an explicit label. |
| D5 | cc-cosem-platform (joining deck, 10-05/06) | "pixel-proven / 300dpi transcription" AR slide | AR text not rendering at all, EN bullets bidi-wrong (#53771); earlier "AR render" claim hallucinated per handoff NOW-OPUS-18 ("the lane's pixel-proven claim was a hallucination"). **verified** via console messages #53740/#53771/#53812 | console rendered the PDF itself | render-and-read before claiming | "Rendered" claims must ship the PNG/PDF path + a `pdftotext` character-class count (Arabic glyph count > 0 for AR pages) — a number the console can check without re-rendering. |
| D6 | cc-cosem-adcda (via console) | "~5 violations will remain after the 9 moves" | 8 remained; the estimate ignored 2 trailing-edge new violations + the 2-cycle rendering as two rows (#54504 §"8 not 5"). The CONSOLE made the estimate from the lane's second-pass numbers; the lane then had to reconcile. **verified** | Musa ("its 8 now", op#26558) | don't estimate to the client | Console-side: never relay a derived number to the operator that the lane has not itself computed and labelled. Lane-side: the reconcile dry-run should output the **post-move violation list**, not just the pre-move one. |
| D7 | cc-cosem-adcda | Renumber dialog "Swap with the other trainee holding this id" would resolve 525↔524 | Swap refused `already-exists` with swap ticked (Musa op#26564). Root cause: collection-wide uniqueness check across archived prior-cohort docs (#54588) — a model error in the safety check, present since the panel shipped (#54422 "panel LIVE"). **verified** | Musa, live in the panel | wet-proof with real-shaped data | Staging wet-proof seeded with the real cohort's **shape** (archived prior-batch docs sharing ids) — the 24/24 wet-proof used a fake batch sharing nothing with real data (#54394), so it could not hit this class. Rule: wet-proof fixtures must include every holder class the prod read shows exists. |
| D8 | cc-cosem-adcda | Renumber dialog UX | Refusal shown in small red text under an unticked box; stale refusal persists after the mode changes to Swap (#54490 D1/D2). **verified** (screenshots op#26553/26562) | Musa ("shit ui") | render gate on the real failure path | Component test + PNG of **each refusal state** as part of the PR render pack (the pack covered happy paths only). |
| D9 | cc-cosem-adcda | "#54305 Hamood runbook: re-upload dedupes silently" | Re-upload THROWS `Group 7 already has 99+` before dedupe (#54341 §3 corrects #54305). **verified** | lane self-correction | run the path before describing it | A runbook step for the client must be executed once against staging (or dry-run) before it is written; "would" is not evidence. |
| D10 | cc-cosem-exams | PR#293 skill_assessments policy "applied" | Live `TO public` mis-apply via raw DSN (dropped `to authenticated`) on the gated production silo — DDL-gate violation (#54170, #53763). Caught by cc-storefront FULL. **verified** | cc-storefront (#54199) → console hold | gated apply path only | `apply_migration.py --gate` is the only DDL path for a store classified REAL/MIXED (data_truth); a PreToolUse hook (now PR#314) refuses raw DDL against PRODUCTION_SILOS — ensure the cosem demo silo is in that list. |
| D11 | cc-cosem-exams | item-8 report "fixed" | Blank for its target user (Test Coordinator) under RLS; green for admin only (#54156). **verified** | console render-review (per-role PNGs) | render as the target role | Render pack must include the **target role's** view for every role-scoped screen; "admin sees it" is not the acceptance. |
| D12 | cc-irsyad-coord | "q#136 class checklist not worked" (#54521) | Already shipped 10-05 (PR#939); a builder was started on it and stopped (#54557). **verified** | lane self-correction | ledger read before claiming idle | `coord_dispatch_queue.done_at` must be set at ship time (merge hook / deploy script stamps the row); a row that is "done" in the bus but open in the queue is a ledger lie the queue-age pager will keep raising. |
| D13 | cc-irsyad-coord | "FAS blocked on Shuq since 10-02" (#54521) | Shuq answered 10-04/05; the PR#933 pack was never revised (#54579). Client would have been re-asked in tonight's PDF. **verified** | lane self-correction | requirements register as source of truth | Every "blocked on client" row must cite the operator_messages id it is waiting on; the asks-ledger/register answer-link closes it automatically (`operator_asks` reply auto-close exists for the operator channel — extend it to client channels). |
| D14 | cc-irsyad-coord / builder | PR#984 "behavior unchanged" | Feature itself adds a more-permissive child route (#53894). **verified** | console | enumerate, don't assert | Route-matrix enumeration test (all child ⊄ parent cases listed) as a required check for middleware PRs. |
| D15 | cc-irsyad-2 | "runners are DOWN" (#52769) | Runners were online (#52845 retraction). **verified** | lane self-correction | probe before asserting | Cite the probe output (gh api runner list) in the claim. |
| D16 | cc-orchestrator (hub) | "coord_dispatch_queue was empty — not an actuator bug" (#54583) | 9 unclaimed rows existed while the probe logged depth=0 (#54592). **verified** (substrate query) | console | query before asserting | Any "the table is empty" claim ships the SQL + count + connection identity. |

**Pattern across D1–D16 (verified):** 11 of 16 are *claims of verification that were not measurements*: text-only equality called byte-identical, src-only scans called "clean", happy-path wet-proofs called "every failure path", "would" runbooks, estimates relayed as counts. The mechanical fixes are all **output-side asserts** (check the artifact that leaves, not the code that made it) and **evidence-pack schemas** (name the comparator and the probe). Lane self-catches (D2, D4, D9, D12, D13, D15) are the healthy half: 61 self-correction subjects in 7 days (coord 39, platform 23, adcda 17, exams 10 — inferred, regex on subjects).

---

## 2. Gate adherence per lane (7 days)

Method (inferred, heuristic): for every PR the lane itself declared MERGED/LIVE in a subject, did the 7-day bus show (a) a cc-quality request/verdict naming that PR, (b) a cc-storefront request/verdict (FULL tier for money/PII), (c) render PNGs mentioned with that PR, (d) a console verify/eyeball message naming it. PR numbers were attributed by **sender**; cross-repo number collisions are possible (e.g. exams and adcda both have PRs in the 270–293 range) — treat ±2 as noise. Client messages are covered in §5.

| Lane | PRs declared shipped | cc-quality | cc-storefront | PNGs before gate | console verify | Notes / misses (verified rows) |
|------|---:|---:|---:|---:|---:|------|
| cc-cosem-adcda | 31 | 10 (32%) | 1 (3%) | 20 (65%) | 2 (6%) | Real minors' PII app. Only PR#274 and PR#305 carried FULL-tier involvement; PR#302/303/304 (roster writes) shipped on cc-quality only or on console code-read (#54408 "approved in substance"). D7/D8 came from this lane. |
| cc-cosem-exams | 14 | 11 (79%) | 1 (7%) | 10 (71%) | 5 (36%) | Best discipline on quality + PNGs; the one FULL (PR#293) was the one that caught D10. PR#192/217/248 (early window) had none. |
| cc-cosem-platform | 16 | 5 (31%) | 0 | 9 (56%) | 5 (31%) | Client-facing DOCS are its product; console verify-render covered the Drive drops (29 console render mentions, highest), but code PRs went in with 31% cc-quality. D1–D5 all here. |
| cc-cosem-tdu-coord | 13 | 4 (31%) | 0 | 1 (8%) | 0 | 12 of 13 merges with no PNG and no console verify; 9 with no review at all (#47386–#47445 cluster on 10-01). TDU = real staff/asset data per registry. |
| cc-irsyad-coord | 28 | 3 (11%) | 13 (46%) | 8 (29%) | 0 | Money path: cc-storefront FULL is the required tier and is used on roughly half; cc-quality rarely (coord routes to storefront instead). 15 merges show neither reviewer in a subject naming the PR (#46056, #47555–#47936 cluster, #50153, #51068, #51590). Console does not render irsyad (guard) — 0 by design. |
| cc-oeh / angullia / coffeemedia / second-brain / mamadah / finance / shipforge | ≤ 2 each | n/a | n/a | — | — | Too few merges in-window to score; coffeemedia QA pass had PNGs (#54479); oeh ran 2 cc-quality requests on 20 verdicts received (reviews mostly pushed by console). |

**data_truth.py classify citations (verified):** required on P1 data-security posts by `bus_send.py` (it WARNS, does not refuse). In 7 days the WARNING fired on console posts #54487, #54513, #54527, #54593; the registry (`data_provenance`) has 8 rows: 4 stores + ywrpttpxwfcoodovxhsr (store + 2 orgs) + goumlyne org. **UNCLASSIFIED (fail-safe treat-as-real) for every other client ref tried:** cosem, cosem-adcda, cosem-adcda-cb6d9 (the real-cohort Firebase project), cosem-platform, cosem-exams, cosem-tdu, oeh, angullia, coffeemedia, second-brain, mamadah, finance. So the citation that `bus_send` asks for cannot be produced for 12 of 13 client refs — the warning is structurally unsatisfiable and is being ignored.

---

## 3. CLAUDE.md drift vs `templates/lane_claude_template.md`

Template sections: *Your job · How work reaches you (bus doorbell, read both base+instance, `bus_send.py` only, never hand-INSERT) · Self-recycling (absolute path) · Hard rules (no secrets/client data in bus; never open a client file; never tell a client you can't read it)*.

| Lane repo | File | Lines / mtime | Doorbell section | Residency | Render gate | Verify-not-assert | PII/name-free | data_truth | bus_send | Verdict (verified by grep) |
|---|---|---|---|---|---|---|---|---|---|---|
| cosem-adcda-hotfix (live lane) | CLAUDE.md | 252 / 2026-10-07 | **none** | 2 (hard rule: never log into real org) | **none** | 3 | 1 | 0 | **none** | App-developer doc, not a lane doc: no bus/doorbell, no render gate, no bus_send, no self-recycle path (the #49853 "rediscovered 4×" problem is exactly this). |
| cosem-adcda (base) | CLAUDE.md | 233 / **2026-04-04** | none | 0 | none | 3 | 0 | 0 | none | Stale (April). |
| cosem-port-lane, cosem-exams-lane | CLAUDE.md → `@AGENTS.md` (120 lines, 09-29) | — | 2 | 5 | 3 | **0** | 1 | 0 | **0** | AGENTS.md has residency + render + "no op# in client text" (2 hits) but no `bus_send` discipline, no self-recycle, no verify-not-assert. |
| cosem-platform (base) | → `@AGENTS.md` 100 lines / 2026-07-23 | — | **0** | 5 | 3 | 0 | 1 | 0 | 0 | No doorbell section at all; July. |
| cosem-tdu | CLAUDE.md | 331 / **2026-04-04** | none | 0 | 2 | 2 | 0 | 0 | none | Stale app doc; real staff data lane. |
| angullia, coffeemedia, second-brain | CLAUDE.md | 19–28 lines / 09-29..10-04 | yes (5–6) | 0 | 0 | 0 | 0 | 0 | yes | Template-seeded; **angullia still names a retired script** (`tg_bridge|mark_handled_through` 1 hit). No render gate although all three ship UI. |
| oeh | CLAUDE.md | 61 / 09-30 | yes | 0 | 5 | 2 | 1 | 0 | 0 | Best of the Tier-B set; 2 retired-script mentions. |
| mamadah | CLAUDE.md | 55 / 10-01 | yes | 1 | 0 | 0 | 0 | 0 | yes | wingmen-personal store (REAL) — residency line present, render absent. |

**Finding (verified):** no lane doc references `data_truth.py` or the review matrix (which tier when); none of the four cosem docs has a self-recycle path; the two lanes handling REAL gov PII (cosem-adcda, cosem-tdu) run on April app docs with zero fleet sections. `templates/lane_claude_template.md` itself lacks: render gate (PNG before claiming "fixed"), the comparator rule (D3), the output-tree vocab/placeholder scan (D1/D2), and a "cite the probe" rule (D15/D16).

---

## 4. Residency / PII guards — code vs promise

| Guard | Exists in code? | Scope | Gap (verified) |
|---|---|---|---|
| `console_irsyad_guard.py` (PreToolUse) | yes | console body only; irsyad silos + ihsanos repo | No analogue for cosem: nothing stops a console tool call against `cosem-adcda-cb6d9` prod or `ywrpttpxwfcoodovxhsr`. |
| `client_media_read_guard.py` | yes | client media reads | not evaluated here |
| `secrets_transcript_guard.py` / `secrets_output_scanner.py` | yes, launcher-enforced (PR#313) | all lanes | Blocked this audit's own grep on a memory file (correct). |
| `lane_db_residency_guard` + CAI-1225 L2 assert in `launch_dangerous_cc.sh` | yes (lines 115–126) | lane env DSNs at launch | Detect-only (1308) + enforcing assert (1225) — good; does not cover Firebase SA keys (the global `GOOGLE_APPLICATION_CREDENTIALS` = cosem-adcda SA is inherited by EVERY lane, incl. irsyad and personal — known memory, still true today). |
| `bus_send.py` P1 data-security WARN → `data_truth classify` cite | yes | bus posts | Warning only; 12/13 client refs UNCLASSIFIED (see §2) → unsatisfiable → ignored. |
| `data_provenance` registry | 8 rows | 4 stores, 1 cosem store+2 orgs, 1 irsyad org | Missing: cosem-adcda-cb6d9 (Firebase, REAL minors' PII), tdu-tools-prod (REAL staff), tdu-tools-staging, wingmen-personal orgs, every Tier-B client. The registry doc lists the Firebase projects (registry rows 58–62) but `data_truth` cannot see them. |
| Bus-body PII | **no guard** | — | 7-day regex scan (inferred, counts only): Emirates-ID-shaped strings in **8 rows** (orch-console #53370 #54293; cc-cosem-adcda #54305 #54436; cc-quality #48373 #51553 #51583; cc-cosem-exams #51517); NRIC-shaped in 4 rows (coord↔console #50906/#50907/#53872/#53876); Arabic 3-token names in 17 rows (console 7, adcda 3, platform 3, …); emails: domains mostly system/test (`cosem.demo`, `*.test`, `noreply.github`, `gserviceaccount`) but 21 `gmail.com` + 6 `gazzabyte.sg` + 2 `irsyad.edu.sg` hits = real people's addresses in bus bodies. **The console is the largest single emitter** (relaying operator text verbatim into lane briefs, e.g. #54293 carried a real Emirates ID + full name from op#26456). |

---

## 5. Client comms quality (7 days, `operator_messages` outbound by tag)

All client sends in the window went through sanctioned send scripts (each logs an `operator_messages` row with `--tag <channel>`; `_tg_chunked_send.py` dedupes). Delivered/total:

| Channel (tag) | Sent | Delivered | Sender lane | Internal-vocabulary leaks (verified by reading the row) |
|---|---:|---:|---|---|
| gazzabyte-irsyad (Shuq/Wan) | 283 | 283 | cc-irsyad-coord via `irsyad_support_send.sh` | 0 pattern hits. Clean. |
| cosem-caai (Ray/Musa/Arqam) | 170 (+21 drafts held, 1 cleared) | 170 | cc-cosem-platform / console via `caai_send.sh`, `reviewer_send.sh`; **draft gate** (`cosem-caai-draft`, undelivered until cleared) is working | 0 hits. |
| oeh (Sya) | 88 | 87 | cc-oeh via `oeh_send.sh` | 0 real leaks (1 false positive, #25239). |
| cosem-exams (Hariz group) | 83 | 83 | cc-cosem-exams via `reviewer_send.sh` | **3 leaks**: #26235, #26255 cite `PR#283/#284/#285` to the client; **#26535 cites operator-message ids "(#26511)", "(#26513)"** to Hariz. Hariz is a developer-adjacent client so PR# is borderline; bus/op ids are not. |
| mamadah | 47 | 47 | cc-mamadah | 0 |
| angullia (Rhaihan) | 25 | 25 | cc-angullia | 0 |
| cosem-tdu (Fazlie) | 22 | 22 | cc-cosem-tdu-coord | 0 |
| coffeemedia | 17 | 17 | cc-coffeemedia | **1 leak**: #24611 told the client "I'm the Coffee Media lane (running on GLM for your trial)" — fleet/model internals to a client. |
| cai-channel | 5 | 5 | cai → Musa | CAI ids (operator channel, acceptable) |

Volume to the OPERATOR on `nazim-console`: 983 outbound in 7 days (~140/day; ~1 every 10 min while awake) — not a client channel, but relevant to "literate comms": over-messaging risk noted for fork C.

---

## Ranked fixes — mechanical checks, enforce-in-code (owner · effort)

1. **Output-tree artifact scanner as a shared library step** (`scripts/lib/client_artifact_scan.py`): refuses any file bound for a client (Drive upload, TG send_file, PDF/xlsx/docx in reports/) containing `undefined|null|NaN|\[object`, fleet vocabulary (`op#\d+`, bare `#\d{5}`, `cc-[a-z]+`, `CAI-`, `orch-console|Nazim`, `wet-proof|silo|lane|bus row`), or `PROPOSED` when the label says applied. Wire into `reviewer_send_file.sh`, `irsyad_support_send_file.sh`, `tg_send_file.sh`, and the Drive writer the platform lane uses. Evidence: D1, D2, #26535, #24611. Owner: cc-substrate · 3h.
2. **Accounting identity asserts in the schedule generators** (platform): rendered numbers must satisfy the per-group identity and `slack >= 0 || labelled`; refuse to render otherwise. Evidence: D4 (−3p shipped through two verifies), G2 −11p still live. Owner: cc-cosem-platform · 2h.
3. **Evidence-pack schema for `bus_send.py --type review_request|update` with "identical/verified/clean" words**: require `--evidence` naming the comparator (`sha256:file`, `text:document.xml`, `probe:<sql|cmd>` + count). The console's reviewer checklist refuses packs without it. Evidence: D3, D15, D16. Owner: cc-substrate (bus_send) + console · 2h.
4. **Register every client store in `data_provenance` and make the `bus_send` P1 WARN a refusal only once the ref IS registered**: add cosem-adcda-cb6d9 (REAL, minors), tdu-tools-prod (REAL), tdu-tools-staging, wingmen-personal orgs, Tier-B stores. Until then the warning is noise (fired 4× on console posts today, satisfiable 0×). Owner: cc-substrate + console gate per row · 2h.
5. **Bus-body PII redactor** in `bus_send.py` and in `operator_log` relay paths: hash Emirates-ID / NRIC / 3-token Arabic-name / non-system email patterns before insert (keep a `--allow-pii <reason>` escape that logs). Evidence: 8 Emirates-ID rows, 4 NRIC rows, 29 real-address rows in 7 days, console as top emitter. Owner: cc-substrate · 3h.
6. **Wet-proof fixtures must mirror prod holder classes**: the staging runner reads a name-free **shape census** from prod (counts of archived/removed/cross-batch/unlinked holders per id) and seeds each class; a wet-proof that covers fewer classes than the census fails. Evidence: D7 (24/24 pass, then first real swap refused). Owner: cc-cosem-adcda · 4h.
7. **Render pack = every refusal state + every target role**: PR template requires PNGs for each error branch of a dialog and each RLS role of a screen; `reviewer` refuses "happy path only". Evidence: D8, D11. Owner: cc-quality template + lane CLAUDE/AGENTS.md · 1h + adoption.
8. **Queue/ledger truth at ship time**: merge/deploy scripts stamp `coord_dispatch_queue.done_at` (and the matching backlog row) from the PR body's `q#` / row id; "blocked on client" rows must carry the `operator_messages` id they wait on and auto-close when a reply lands on that channel. Evidence: D12, D13, the 9 rows unclaimed for days. Owner: cc-irsyad-coord + cc-substrate · 3h.

Also (doc, not code): re-seed the cosem-adcda-hotfix / cosem-platform / cosem-tdu lane docs from the template (they are April/July app docs) and extend the template with render-gate, comparator-rule and cite-the-probe lines; remove the retired-script mentions in angullia/oeh. Owner: console via `seed_lane_claude_md.py` · 1h.

## Could not measure
irsyad repo-side gate adherence (console guard; bus-only). Whether cosem-tdu merges had reviews outside the 7-day window. Telegram-server copies of leaked ids. Which Tier-B lanes rendered PNGs when fewer than 3 merges exist.
