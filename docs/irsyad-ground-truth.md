# IRSYAD GROUND-TRUTH (canonical — every irsyad agent reads on boot)

**Purpose:** the load-bearing facts about irsyad/Gazzabyte that agents keep re-deriving or forgetting as the code scales. This is the ONE authoritative source. **When any fact here is corrected, update it HERE the moment it's corrected** — don't just fix it in one conversation. Owner: Nazim (orch-console). Created 2026-08-22 (op#15919, Musa: "how do we prevent this forgetting").

## 🔎 INDEX — grep THIS first (keyword → answer / section)
Search here before re-deriving or guessing. `grep -i "<keyword>"` this file.
- **who is / people / niffira / shuq / wan / kryptoh / director / gender** → 2 directors, both male; @NiffiraQuhs IS Shuq (no separate "Niffira") → *Client & people*
- **operator / governance / who approves / gate / cai** → Gazzabyte drives direct (CAI-1288); no per-feature gate; floor only → *Governance model*
- **receipt access / letter access / preparer / front-line** → FULL (CAI-1283), NOT scoped → *Product facts*
- **merge / dedup / duplicate / merge tool / merge_persons** → tool LIVE (mig221); backend dedup done (1,678); org_admin-only execute → *Product facts / Gotchas / Build state*
- **subadmin / Elly / Salsabiila** → admin minus 3 blocks; new role + isAdminEquivalent(); awaiting client dual-control answer → *Product facts / Build state*
- **zakat / asnaf / riqab** → 4 Asnaf line (CAI-1285/1242) → *Product facts / Build state*
- **minors / students / exclusion / sch_students** → anti-join by construction (CAI-1199), fail-closed (CAI-1222) → *Product facts*
- **silo / goumlyne / database / prod / project ref** → GOUMLYNE_DATABASE_URL / IHSANOS_PROD_DATABASE_URL → *Data & migrations*
- **migration / mig / apply / supabase push** → gh api + psql -f; never `supabase db push` prod → *Data & migrations*
- **recycle / context / bloat / lane full** → auto-recycle being promoted from detect-only → *Gotchas / Build state*
- **is X built / shipped / live / status** → *Build state* table below

## Client & people
- **Client = GAZZABYTE** — builds Madrasah Irsyad's tabung/donation system. Data silo = **goumlyne** `goumlynecruxrlmzlntp` (writes: `GOUMLYNE_DATABASE_URL`).
- The Telegram group (chat `-5330147776`) has **EXACTLY 2 people**, both Gazzabyte **directors**, both **MALE (he/him)**:
  1. **Wan** — `@wan_toh` — TG id `661212242` — display "Kryptoh".
  2. **Shuq (Ariffin)** — `@NiffiraQuhs` — TG id `605271890` — display "Quhs Niffira".
     ⚠️ **"Niffira" is NOT a separate person.** `@NiffiraQuhs` IS Shuq (handle ≈ "Shuq Ariffin" scrambled; he asked to be called Shuq; once trolled "I'm Wan"). Do NOT treat Niffira as a third contact.
- Musa (owner/operator, `@haikusmesh`) is in the group too.
- **Identity source of truth: `chat_members` table** (keyed by TG user_id, with a curated `known_label`).

## Governance model (post CAI-1288, 2026-08-22)
- **GAZZABYTE = operator for irsyad dev.** Their word goes DIRECT via **cc-irsyad-coord** (owns client voice) + **Nazim** (console). **No per-feature cai/Musa approval gate.**
- cai STOOD DOWN on per-feature irsyad governance; does a full assessment AFTER a build settles.
- **FLOOR still holds** (engineering-defaults; breach → Nazim heads-up Musa, NOT a gate): minors-PII/amanah · money-path auditability · no false donor/IRAS claims · auditable fund flows · no vendor-lock-in / data-portability.
- Base `cc-irsyad` has **NO client access** — client relays go to **cc-irsyad-coord**.
- Keep Gazzabyte **proactively + comprehensively updated** on all build state (op#15912) — don't wait to be asked.

## Load-bearing product facts (correct-in-place if these change)
- **Front-line receipt/letter access = FULL** (CAI-1283 — restored after client rejected scoped; op 15842 "she should see ALL... her job to send to donors"). NOT scoped.
- **Merge People tool = working** (mig221 fix). Stays available for client ad-hoc use.
- Merge-EXECUTE = **org_admin only** (four-eyes). Front-line manual-propose + admin-confirm = CAI-1274.
- **Minors-exclusion by construction** on ALL donor-aggregation (sch_students anti-join, CAI-1199); **fail-closed on error** (CAI-1222).
- **Subadmin** (Elly + Salsabiila) = admin **minus 3 blocks**: (1) no admin-create/grant, (2) no approve-after-money-counted, (3) submit-only/no-unsubmit. Model = NEW `subadmin` role string + one `isAdminEquivalent()` helper. Open Q with Gazzabyte: block-2 "cannot approve" = never-endorse-any vs can't-endorse-own.
- **Zakat receipt line** = render all 4 Asnaf (CAI-1285/CAI-1242 Riqab substantiated upstream).

## Data & migrations
- Silo writes: `psql`/psycopg + `GOUMLYNE_DATABASE_URL` (prod ceayj = `IHSANOS_PROD_DATABASE_URL`).
- Migration apply: `gh api repos/sheikh-musa/ihsanos/contents/<path>?ref=<sha>` → `psql -v ON_ERROR_STOP=1 -f` (the mig's OWN BEGIN..COMMIT is right for apply).
- **Never `supabase db push` against prod** (strips view arms — CC-SUBSTRATE-VIEW-INTEGRITY-001).
- `merge_persons(survivor, loser, org, actor, field_choices, merge_id, audit_payload)`: requires `auth.uid()`==actor AND actor is org_admin. **Our service org_admin = `orchestrator@ihsanos.com` (`ca2a4a9c-f745-40c3-9536-7a021fd42bb9`)** — use it, NOT a client admin. Set `request.jwt.claim.sub`=actor per-tx. Reparents donations (+all person-FKs); loser soft-deleted; reversible via `reverse_merge`.

## Known gotchas / recent
- **Donor dedup (2026-08-22):** the tabung-history import (`irsyad_tabung_hist_2020_2026`) created ONE person per DONATION, not per donor. Cleaned: **1,678 merges** (all reversible), active donors 3,186 → 1,508, $0 lost. 5 ambiguous names (Puan Liza, Suraya Mohd Yussof, Puan Fatimah, Mariah, Subaidah) left for client manual merge.
- **Context/recycle:** `auto_recycle_on_bloat` is/was DETECT-ONLY (Stage 0) — flags bloat, "fires nothing", pages a human. Being promoted to actually recycle idle-bloated lanes (SRE).
- No NRIC/DOB on goumlyne persons; address/phone/email sparse.
- **Residency (2026-08-22):** `post_journal_atomic` (atomic tabung journal-posting flow) is **goumlyne-ONLY** — the function is ABSENT on ceayj/BAPA (verified at source; base tabung tables exist on ceayj but not this function). So mig218/#426 and #424's residency text claiming "both silos" is the DOC being wrong → fix to goumlyne-only (benign, not a backfill gap) UNLESS product says BAPA should run the atomic-journal/Qurban flow (then it's a money-path residency provisioning call → cai). By contrast `void_donation`/`unvoid_donation` (mig202, hardened by mig219/#427) exist on BOTH silos → mig219 residency = both silos, correct.

## BUILD STATE — what's built vs not (keep CURRENT; update the moment status changes)
Status legend: **SPECCED** (agreed, not built) · **BUILDING** · **DARK** (merged, behind a flag / not user-visible) · **LIVE** (deployed, user-visible) · **VERIFIED** (LIVE + confirmed working).

| Feature | Status | Ref | Where / note |
|---|---|---|---|
| Merge People tool (person merge) | **VERIFIED** | mig221 | client using it; org_admin-only execute |
| Donor dedup cleanup (backend, tabung-hist) | **VERIFIED** | op#15902/15908 | 1,678 merges, reversible, $0 lost; 5 ambiguous left for client manual |
| Front-line receipt/letter access | **LIVE (FULL)** | CAI-1283 | org_role_permissions; #437 download link render |
| Minors-exclusion (donor aggregation) | **LIVE** | CAI-1199/1222 | sch_students anti-join, fail-closed |
| Zakat asnaf receipt line (4 categories) | **LIVE but DORMANT** | CAI-1285, #443 | flag flipped + deployed both domains; goumlyne has 0 zakat donations so line renders on nothing yet (appears going forward) |
| Subadmin role (Elly + Salsabiila) | **SPECCED, FULL-HOLD** (no code) | CAI-1288 | shape A confirmed (admin-by-default + 3 blocks); block-B (dual-control) OPEN; awaiting Musa full-reqs |
| Front-line merge propose / admin-confirm | **SPECCED** (approved, not built) | CAI-1274 | reuses CAI-1219 flag arch |
| Stale-test fix (tabung deny-cells) | **MERGED** (#441, 281411f4) | CAI-1247 | e2e module-matrix expectations landed |
| mig214 / mig215 (KK field-minimize / JWT-gate) | applied both silos; **#421 MERGED; #419 pending** | CAI-1286/1287 | #419 blocked on tabung-synthtest runner (actions-runner-ihsanos-2 hangs; fix = runs-on pin to wingmen-core-runner) |
| mig218 (post_journal_atomic actor-bind) | **APPLIED+VERIFIED live goumlyne; #426 MERGED** | CAI-RESP-1289 | **goumlyne-ONLY** (post_journal_atomic absent on ceayj); #424/#426 "both silos" doc is WRONG → fix doc |
| mig219 (#427, void/unvoid fund_raised symmetry) | wet-proven; **apply window ~23:15Z 08-22, BOTH silos** | CAI-1270 | touches void_donation/unvoid_donation (NOT post_journal_atomic); ceayj HAS both → both-silos correct; re-confirm 0 voided both silos at close |

*(Definitive build state = merged PRs + live deploys + applied migrations; this table is the human-readable index into that — if it and the code disagree, the CODE wins and this table is stale → fix it.)*

## Discipline
Every irsyad agent (console, coord, builders) reads this on boot. **Correct any wrong fact HERE immediately** — the whole point is to stop re-forgetting as the code scales. Grep the INDEX first; trust the CODE over this doc if they conflict, then fix the doc.
