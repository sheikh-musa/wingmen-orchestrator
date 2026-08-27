# CAI-949 — donor-import authorisation redraft: source facts

Task: cai #22737 (CAI-RESP-949). Establish F1-F4 at source, redraft template to R1-R8, route to cai BEFORE any client send. CAI-928 ingest block stays.

## F1 — Gazzabyte↔Irsyad Data Processing Agreement exists?
- **No DPA artifact found in code/docs** (grep of ihsanos/docs + orchestrator/docs: only unrelated orchestrator planning docs matched, no client DPA).
- Existence is an operator/business fact, not a codebase fact. **Default: obligations INLINE (R6) + retention/termination INLINE (R5).** If operator confirms a real DPA exists, the doc can reference it and stay short.

## F2 — Hosting region  [RESOLVED: SG-only, both at-rest AND compute — VERIFIED at running system]
- **Data-at-rest = Singapore.** goumlyne DB host = `aws-1-ap-southeast-1.pooler.supabase.com` (Supabase ap-southeast-1).
- **Compute = Singapore (sin1).** VERIFIED first-hand: `curl -D- https://irsyad.ihsanos.com/api/health` → `x-vercel-id: sin1::sin1::...` (function EXECUTES in sin1). cai (CAI-954) independently measured the same; deployed vercel.json has `regions:['sin1']` since 2026-07-20.
- **⇒ SG-only is TRUE. No s.26 cross-border. cl.4 = clean SG-only form (v5).**
- **CORRECTION of my earlier error:** I first reported compute=iad1/US (from the Vercel API `serverlessFunctionRegion` project-default field + a branch-divergent worktree vercel.json with no regions key) → falsely concluded s.26 applies → triggered CAI-953 relocate chain. WRONG — those were stale proxies, not the running system. Lesson banked: [[feedback_verify_compute_region_at_running_system]]. v4 path-A (disclose US) DROPPED.
- **Pending:** cai's platform completeness check — no cron/bg/other-project fn processes Irsyad donor PII off-sin1 (cai routed to platform).

## F5 — EMAIL egress (CAI-955): receipt email = Resend/US cross-border. **Enumerate EVERY egress, not just compute+storage.**
- Receipt emails delivered via **Resend (US)** — `src/shared/lib/notification/providers/resend.ts` (api.resend.com, no region param, from=noreply@ihsanos.com / Irsyad verified fundraising@irsyad.edu.sg). => donor name+email+receipt contents transferred outside SG for delivery = a real **s.26 crossing**.
- (i) Gazzabyte↔Resend DPA/comparable-protection: NOT in our records → s.26 line = forward commitment, NOT asserted-as-fact; operator/DPO to verify execution (like F1).
- (ii) SG-region email path: NONE in code; would be a build change.
- (iii) Irsyad told delivery overseas: NO (0 disclosures in 1000 client msgs).
- **RULED (CAI-957/958): (A) disclose + keep Resend.** DPA now VERIFIED in force (Resend DPA+SCCs auto-apply via ToS, CAI-956) → cl.4 UPGRADED to ASSERT the safeguard as fact (not forward-commitment) → **v7** (`pii_controller_auth_REDRAFT_v7.txt`). (iii) Irsyad-told = handled BY THE DOC (signing discloses) + DPO R9; no separate pre-disclosure.
- **PROCESSOR ENTITY = OPERATOR-LEGAL QUESTION (not a fill-in-blank), CAI-957:** op_msg #1234 (ticket #001): "Wingmen's ihsanOS... operated by WINGMEN"; Gazzabyte BUILT fr.irsyad; Irsyad=controller. Is the Processor WINGMEN or Gazzabyte, and is Resend its sub-processor? cl.1's "Processor" (authorises import AND ensures s.26) must be that entity. #1 item in the CONSOLIDATED operator ask (cai will NOT piecemeal-ping; ONE clean ask when v-final ready).
- **C3 link + revert:** first real receipt email = first s.26 crossing, but safeguard now confirmed → **C3 monitor reverts to deliverability-only** (CAI-958): ordinary CAI-921 first-real-send check (emailed/delivered/no silent-catch); NO freeze on fire.
- **COMPUTE-COMPLETENESS CLEARED:** cc-ihsanos structural proof (#22800) — all 6 endpoints sin1, single vercel.json sin1 directive, irsyad.ihsanos.com bound exclusively to ihsanos-irsyad → all Irsyad-PII compute is sin1/SG. cai accepted. (Also: cc-ihsanos runs a complementary emailed_at watcher alongside my C3 — defense-in-depth.)

## F3 — CORRECTED per CAI-950: read the ACTUAL SOURCE, not the code.
**SOURCE FOUND + READ:** `docs/clients/irsyad/finance-inbox/Batch 1 - Collected Tabung 2020 to Feb 2026 - Done.xlsx`, sheet "Collected 2020 to 2026". Columns EXACTLY match batch1-row.ts (8 cols) → definitively the import source.
- **2,613 data rows.** Fields present: ID(internal ref, filled 2612), Name(2612), Street/address(2491), Contact/phone(1311), email(only 118), Type(2612), Calculated On/date(2612), Collected Amt/money(2612).
- **NRIC/FIN: ZERO** — scanned every column for /^[STFGM]\d{7}[A-Z]$/, no matches. ID col is NOT NRIC-shaped (→ code keeps it as benign legacy_donor_ref). **No DOB column. No special-category data.**
- **Matches cai live-baseline sanity check (#22747): both source and live = 0 NRIC / 0 DOB. NO divergence red-flag.**
- ⇒ Doc must NOT name NRIC (over-breadth). Categories = only the 8 source fields. My earlier code-derived "NRIC conditionally ingested" was the exact trap CAI-950 warned about (code CAPABILITY ≠ actual source content).

### (superseded) F3 code-derived reading — kept for context:
Import: `ihsanos-irsyad.wt-tabung-history` `src/modules/tabung/history-import/`. Source = 8 cols (id,name,street,contact,email,type,calculated_on,collected_amt).
Ingested per donor (map-row.ts:22-60): display_name(name,plain); address(street,plain); email_encrypted(AES); phone_encrypted(AES); email/phone hashes; lifetime donation aggregate (lifetime_amount by tabung_type, basis='imported-not-transacted').
**NRIC = CONDITIONALLY YES.** map-row.ts:25-38 + classify-legacy-id.ts:7,13 — the legacy `id` col is classified; if it matches `/^[STFGM]\d{7}[A-Z]$/` (SG NRIC/FIN) → `nric_encrypted`=encryptNric, `nric_source='self_declared'`, nric_hash. Non-NRIC id → benign `legacy_donor_ref`. So NRIC is ingested whenever the source id field carries an NRIC-shaped value.
NOT ingested: date_of_birth, gender. No special categories. DB-write still fail-closed/stubbed (scripts/import/tabung-history.ts:21-74, 0 rows until --commit --pii-basis-ref --controller-confirmed).
**IMPLICATION for cai/DPO:** conditional NRIC ingest triggers PDPC NRIC-specific restrictions → decision: authorise NRIC (needed for IRAS tax-deductible receipts per CLAUDE.md?) vs strip NRIC from import (keep legacy_donor_ref only). Not mine to decide — flagged to cai.

## F4 — Canonical platform name  [ESTABLISHED → IhsanOS]
package.json name="ihsanos" (npm id, not brand); NO brand constant. User-facing wordmark = `ihsanOS` (layout.tsx title + email sender). README.md:1 normalizes to `IhsanOS`. CLAUDE.md="IhsanOPS" (internal spec codename — AVOID on signed doc). Receipts carry NO platform brand (org-branded).
**Recommend `IhsanOS`** (prose form of the ihsanOS wordmark). Owner-confirm recommended (no single source-of-truth in code).

## Operator-escalated (do NOT guess; wait):
- Gazzabyte registered legal entity name (with Pte Ltd / suffix).
- Irsyad full registered org name + authorised signatory.

## R1-R8 checklist (redraft must satisfy) — from cai #22737:
R1 Processor = Gazzabyte registered LEGAL ENTITY (not brand); platform named once canonically as destination.
R2 Controller = Irsyad registered org name + authorised signatory (warranty of authority).
R3 Definite dataset: "records held by Irsyad as at [snapshot date] / export [name+date]", exact count at snapshot — NOT "~2,600".
R4 Enumerate exact data categories; name NRIC explicitly if in-set.
R5 Retention (only as long as needed) + termination (return AND/OR delete on end) — inline (no DPA per F1).
R6 Processor obligations — inline (no DPA per F1).
R7 Cross-border s.26 safeguard if outside SG; else state SG-only. → **SG-only per F2.**
R8 Purpose limited to stated purposes (donor records + donation history + receipts); keep tight.
