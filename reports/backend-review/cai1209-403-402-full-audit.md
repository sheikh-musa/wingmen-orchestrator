# FULL audit — CAI-1209 PR #403 + PR #402 (Fajar donor-doc delivery)

**Auditor:** cc-quality · **Date:** 2026-08-21 · **Verdict: PASS (both), merge-ready.**
Requested by orch-console (bus #30158, thread `266a1352`). Base `main`.

Pinned HEADs (verified vs `gh pr view`, both `MERGEABLE`, OPEN):
- **#403** `feat/cai1209-letter-ab` @ `6280d1fc541a0473e6ec07e074a8991e7e80b871` — (a)+(b), no migration.
- **#402** `feat/cai1209-delivered-webhook` @ `9dd6cf6d942dbf27f32d041022d1ace2fcff1a54` — (c), mig210.

Gates run at each pinned HEAD (per the lint-gates-are-part-of-the-bar rule after #401):
| PR | `npm run lint:all` (16 gates) | vitest (touched + invariant suites) |
|----|-------------------------------|-------------------------------------|
| #403 | **EXIT 0** — all pass (incl. check-supabase-select, check-schema-drift) | **27/27** (3 files) |
| #402 | **EXIT 0** — all pass | **29/29** (3 files) |

---

## #403 — (a) missing-name fallback + (b) letter non-deductible notice

**(a) blank/whitespace `display_name` → neutral "Valued Donor", never a dangling "Tuan "/empty salutation. CONFIRMED.**
- `buildAppreciationLetterData` (appreciation-letter-data.ts): `trimmedName = displayName.trim()`, `hasName = length>0`; when `!hasName` both `donorName` and `salutation` = `FALLBACK_DONOR_NAME` ("Valued Donor") and `honorific = null` (no dangling honorific). Fixes the real bug: an **empty-string** name previously slipped past the callers' `?? "Donor"` (nullish only) into the old `honorific + " " + ""` path → "Tuan ".
- Both callers now pass `displayName: ... ?? ""` so the empty/null case routes through the single builder fallback (no divergent literal). 
- Tests: normal name (unchanged "Tuan Ahmad" / "Tuan Ahmad bin Ali"), `""` → "Valued Donor", whitespace → "Valued Donor". ✔

**(b) letter non-deductible line matches the receipt VERBATIM via one shared helper; receipt+letter can't drift on wording. CONFIRMED.**
- New `src/shared/lib/non-deductible-notice.ts::resolveNonDeductibleNotice(orgName, customText?)` — single source of truth. Names **IPC status** (factual), **never** cites Income Tax Act / s37 (CAI-1099/1128); blank/whitespace override falls back to the safe default (never renders empty).
- **Receipt** (`pdf-template.tsx`) now calls the helper (replacing an identical inline literal). **Letter** callers (`receipt-email.tsx`, `letter/pdf/route.tsx`) call the *same* helper with the *same* inputs (`orgName`, `settings.non_deductible_notice_text`) and pass the result through the builder to `AppreciationLetterPdf`. Same fn + same inputs ⇒ byte-identical wording; drift is structurally impossible.
- `organizations.is_ipc` used to gate presence is a **live** column (mig194, "APPLIED LIVE both silos" 2026-08-17, CAI-1099 TERMINAL) — the select won't 400. The PR correctly **drops** the `schema-drift-ignore: is_ipc` pragma in `receipt-email.tsx` because it now genuinely selects+uses the column; `check-schema-drift` passes (0 new violations).

**Zero send-behaviour change. CONFIRMED.** `FAJAR_MANUAL_SEND_LIVE` = `false` (untouched); the send gate (`receipt-email.tsx:415 if (!FAJAR_MANUAL_SEND_LIVE)`) and recipients/gating are not in the diff; the `donation-comms-send` security-invariant test passes.

**LOW / non-blocking observation:** the **receipt** renders the notice *unconditionally* (no `is_ipc` gate in `pdf-template.tsx`), whereas the **letter** gates on `is_ipc !== true` (fail-safe: only an explicit IPC org omits). For the real **non-IPC** org the two are identical, and the letter's gate is strictly *safer* than the receipt's; **wording still cannot drift**. The receipt's unconditional notice is pre-existing (not introduced here). No action required for merge; worth a note if an org is ever flipped `is_ipc=true` (then the receipt would still print the non-IPC line — a pre-existing receipt-side concern).

## #402 — (c) Resend delivered-proof webhook + mig210

**mig210 (`210_receipt_email_delivery_status.sql`):** `BEGIN/COMMIT` self-committing (CAI-756-safe). Additive **nullable** columns (`delivery_status` CHECK'd to the 4 events, `delivered_at`, `last_event_at`) on the `receipt_email_sends` **append-log** side table (mig209) — **not** `receipts`; no existing row/column mutated (schema-drift-safe by construction; #402 carries no `src/actions` select change). No PII (provider_message_id is an opaque Resend handle). Genesis audit_log row included. **No RLS added or loosened** — the write path is a locked-down function, not a policy.

**`record_email_delivery_event(msg_id, status, event_at)`** — `SECURITY INVOKER`, `REVOKE … FROM PUBLIC, anon, authenticated` + `GRANT EXECUTE TO service_role` only. The single guarded UPDATE encodes both invariants atomically:
- **Idempotent + reorder-safe:** `WHERE last_event_at IS NULL OR p_event_at > last_event_at` — a redelivered event (equal ts) and an out-of-order older event are both no-ops; only a **strictly-newer** event applies.
- **Advance-only-to-terminal:** `AND NOT (p_status='delivery_delayed' AND delivery_status IN ('delivered','bounced','complained'))` — a non-terminal delay can never regress a terminal outcome. `delivered_at` is set only on `delivered` and preserved thereafter (delivered-proof persists).
- (SQL-layer invariant verified by logic-read; migration is **propose-only** so it isn't in prod to wet-prove yet — appropriate bar. Route always passes a non-null ISO `event_at`, so the guard never sees NULL.)

**Route (`/api/webhooks/resend/route.ts`):** `runtime="nodejs"` (HMAC). Secret unset → **503** (not 200 — refuses to silently drop events; fail-safe, not fail-open). Signature verified over the **raw request bytes** before JSON parse. Invalid sig → 401, bad JSON → 400, non-delivery event → 200-ignored (stops Resend retries), no msg-id → 200 no-op, RPC error → 500 (Resend retries), unmatched msg-id → 200 (valid no-op). Write via `createServiceClient().rpc(...)`.

**Svix verification (`resend-webhook.ts`):** faithful Svix scheme — requires all three headers + secret; finite timestamp within **300s** tolerance (replay bound); `whsec_` base64 key; `HMAC-SHA256` over `${id}.${timestamp}.${rawBody}` → base64; multi-signature (rotation) support; constant-time `timingSafeEqual` (length-guarded). `mapResendEventToStatus` returns only the 4 CHECK-valid statuses (route calls the RPC only when non-null ⇒ the mig210 CHECK constraint can't be violated). No live send triggered anywhere.

**Tests exercise the load-bearing paths** (not green-by-triviality): route 503/401/200-delivered/bounced+complained/ignore/no-id/500; verifier accepts-valid / multi-sig / tampered-body / wrong-secret / stale-timestamp-replay / missing-headers / empty-secret; mapping 4-events + null.

---

## Merge guidance
Both PASS. On merge: #403 (no DB) merges as code. #402 code merges; **mig210 is propose-only → CONSOLE §6.6-applies** (money/PII-adjacent, both silos goumlyne+ceayj, never self-applied) — wet-prove `BEGIN..ROLLBACK` first, then apply. No pre-arm conditions from me beyond the standard §6.6 apply path.
