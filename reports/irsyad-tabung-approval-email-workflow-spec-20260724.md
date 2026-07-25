# irsyad — Tabung weekly-report approval-email workflow (op#6834)

**Spec date:** 2026-07-24 · **Author:** cc-orchestrator (hub) · **Repo:** ihsanos (shared frontend) · **Data:** irsyad silo (goumlyne `goumlynecruxrlmzlntp`), org `73339164-7c1f-40ba-a093-33f1f292dd4c`
**Status:** spec — NOT yet dispatched. Client-facing → ihsan-quality-bar + cai review before ship.

## Requirement (verbatim from client via operator, msg#6911)
1. Elly counts the tabungs and enters the collection details into the system.
2. Elly saves the collection report and records the bank deposit by uploading the bank deposit reference.
3. Once submitted, an email is automatically sent to the administrators (**Saddam + VPZ/Zuremi**) requesting them to review and approve the report.
4. When either approver approves, the **other** administrators automatically receive an email that the report is approved and ready for download.

## What already EXISTS (verified on origin/main @ 90a255a — do NOT rebuild)
- **Lifecycle**: `draft → preparer_signed → closed` in `src/actions/tabung-weekly-reports.ts`:
  - `createWeeklyReportAction` → `draft` (step 1).
  - `attachReportDepositAction` (line ~1221, mig 089 / CAI-RESP-412) → records deposit reference + **mandatory bank-in slip** via write-once RPC `tabung_report_attach_deposit`, role gate `{org_admin, preparer}` (step 2 — **confirmed wired**, this was the open verify item).
  - `preparerSignAction` → `draft → preparer_signed` (Elly submits/signs).
  - `endorserSignAction` → `preparer_signed → closed`; **enforces deposit ref + slip present before close** (`tabung_deposit_reference_required_to_close`); dual-control (endorser ≠ preparer, DB-enforced).
- **Email infra**: Resend, app-wide. **Reuse the durable outbox pattern** — `src/modules/storefront/notifications/outbox.ts` + `GET /api/cron/storefront-notifications-drain` + migration `098_storefront_notifications.sql`. It is inline-drain on the happy path with a cron safety-net, **idempotent + claim-safe (atomic CAS on `attempts`), never double-sends**. Mirror this; do NOT add naked inline Resend calls in the actions (a failed send must not fail the sign, and must retry).

## The NEW work — exactly two email hooks + one generalization
### Hook #1 — "ready to approve" (on `preparerSignAction` success → `preparer_signed`)
- Enqueue an email to the **approvers** (see recipient rules) with a deep-link to the report review page.
- Fires only on the `draft → preparer_signed` transition (guard on the successful RPC/update, not on every call).

### Hook #2 — "approved, ready to download" (on `endorserSignAction` success → `closed`)
- Enqueue an email to the **other administrators** (all approvers **except** the endorser who just approved) with a download link (the existing PDF route `src/app/api/tabung/weekly-report/[publicId]/pdf/route.tsx`).

### Recipient rules (⚠ the sharp edge — get this reviewed)
The irsyad `org_admin` roster in goumlyne currently contains **test/uat accounts** alongside real people:
- REAL: `saddam@irsyad.edu.sg`, `zuremi@irsyad.edu.sg`
- NOISE: `admin@irsyad.test`, `zz-uat-tester@irsyad-uat.test`, `uat-operator@qa-madrasah.test`

**Do NOT blindly email every `org_admin`** — that leaks report notifications to test inboxes and is a client-facing embarrassment.
**DECIDED (operator msg#6914): option (A) — editable/self-service approver list.** The client manages who approves; the workflow reads that set, not the raw `org_admin` roster.
- Build a `tabung_report_approvers` config (per org: user_id + email, editable via a small admin UI so irsyad self-services it) — seeded for irsyad = {Saddam, Zuremi}. Future-proof per [[always-build-for-the-future]].
- Recipients resolved server-side from that config in goumlyne, never hardcoded in the email body; addresses are PII → no logging of raw addresses in the bus.
- Migration for the config table: additive, RLS on, REVOKE anon ([[pii-table-verify-grants-not-just-rls]]); apply via direct psycopg, synthetic-first, cai-gated (goumlyne = client silo).

## Files to touch
- `src/actions/tabung-weekly-reports.ts` — add the two enqueue calls (post-success, transition-guarded).
- `src/modules/storefront/notifications/outbox.ts` (or a sibling `tabung` notifications module mirroring it) — add the two templates + recipient resolver. Prefer generalizing the outbox to be domain-agnostic over forking it.
- Email templates (subject/body + deep-links) — mirror existing Resend template usage.
- If option (A): a migration for the approver-config table (additive, RLS on, REVOKE anon — [[pii-table-verify-grants-not-just-rls]]). **Apply via direct psycopg pattern, NOT `db push`; synthetic-first; cai-gated (goumlyne = client silo).**

## Test plan (drive the REAL flow — [[test-end-to-end-before-declaring-live]])
- Unit: transition-guard fires the enqueue exactly once per transition; no enqueue on re-sign / reopen / no-op.
- Integration: preparer-sign a synthetic report → assert one queued outbox row to {Saddam, Zuremi}, none to test addresses. Endorse → assert one queued row to the *other* admin only.
- Idempotency: double-invoke the drain → single send (CAS proof).
- E2E in a synthetic irsyad tenant: full draft→deposit→preparer-sign→endorse, real Resend to a captured test address, confirm both emails land with working links. Empty/edge: sign without deposit (must still be blocked), single-admin org (hook #2 recipient set empty → no send, no error).

## Gates / dependencies
- **cai**: required IF option (A) adds a goumlyne table (client-silo DDL) or if recipient resolution touches money-adjacent rows. The email hooks alone (no schema) are shared-code + no money/permission mutation → lighter gate, but still client-facing so cai heads-up + hub eyeball.
- **Not blocked by** the separate Elly role-redesign (giro clarification pending, msg#6911) — this workflow ships independently.
- Ihsan bar: eyeball the emails (mobile + desktop render) before declaring live.

## Open questions for operator/client
- ~~Approver model~~ → RESOLVED (msg#6914): editable/self-service approver list (option A).
- Hook #1 trigger point: confirm "submitted" = preparer-sign (`preparer_signed`), i.e. after the deposit ref is attached. If they mean a distinct "submit" before signing, the state machine needs a new status — flag before building. (Confirm with Gazzabyte alongside the role scope.)
