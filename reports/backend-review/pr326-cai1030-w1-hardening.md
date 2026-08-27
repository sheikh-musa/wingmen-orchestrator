# cc-quality formal sign-off — PR #326 (CAI-1030 W1 hardening, minors-PII)

- **PR:** #326 `fix(school): CAI-1030 W1 hardening — audit deferred parent link + retry transient createUser + test reuse path`
- **Repo:** sheikh-musa/ihsanos · head `06e17e183e17bf7681d45b7afbaa7c9f44c6f4fb` (matches worktree `ihsanos-w1hard`)
- **Reviewer:** cc-quality (Opus 4.8), 2026-08-18 · reviewed at source (`gh pr diff 326`), not a stale local checkout
- **Requester:** orch-console (bus #25708). Author lane cc-irsyad-student wound down; picked up so it isn't orphaned.
- **Surface:** MINORS-PII (parent↔auth-account linking on student enrollment). Formal sign-off, not a rush-merge.

## Verdict: **APPROVE + MERGE.** This is a true hardening — it tightens minors-PII protection and weakens no control.

## Scope
Two files, no migration, self-contained:
- `src/actions/sch-students.ts` — the parent-auth-link provisioning block (Step 5b).
- `src/actions/__tests__/sch-student-parent-crosstenant-link.test.ts` — new coverage.

## The core F1 control is UNTOUCHED (verified, not assumed)
CAI-1030 F1 closed a cross-tenant leak (a global `auth.admin.listUsers` scan could stamp a foreign-org auth user onto this org's `persons.user_id`). I re-traced every write to `persons.user_id` at this head:
- **Reuse path** reads `user_id` from a row filtered `.eq("id", parentPersonId).eq("org_id", orgId).is("deleted_at", null)` — tenant-scoped, cannot pull a foreign row.
- **Create path** sets `parentUserId` only from `created.user` — a **freshly minted** account; a duplicate/foreign email returns `data.user = null` and never yields a linkable id.
- Every failure leaves `parentUserId` null → link **deferred**, fail-closed.

There is no third path, and the retry loop adds none: the only branch that assigns `parentUserId` from a create is `if (created?.user)`, which a duplicate can never satisfy. **A foreign-org id still cannot be introduced.** The regression test now also asserts `listUsalled === false` (the global scan can never return).

## My 3 prior notes (from PR#323 clearance, bus #24734) — all faithfully addressed
1. **Defer path was unaudited (SECURITY).** Now writes `writeAuditLog({ event: "parent_portal_link_deferred", parent_person_id, student_id, reason: "email_exists" })`, symmetric with the existing success-path `parent_portal_link` audit (sch-students.ts:779). The audit asymmetry that bothered me is closed. Test asserts exactly one deferred-audit entry on a duplicate email. **Payload is data-minimised — internal ids + reason only, no email/NRIC/name — no PII leak into the audit trail.**
2. **Defer fired on ANY createUser error (conflated transient with duplicate).** New `isDuplicateEmailError()` classifies genuine duplicates (code `email_exists`/`user_already_exists`, status 422, or message regex) vs transient failures; transient failures retry (`MAX_ATTEMPTS=3`); only a genuine duplicate defers; a persistent-transient failure logs an operational error and is **not** mislabeled as a security defer (no false deferred-audit). Tests cover retry-then-success, and persistent-transient (no link, no false defer-audit).
3. **No test for the tenant-scoped reuse (2nd child) path.** New test enrolls a 2nd child for a guardian with an already-linked account, asserting `createUserCallCount === 0` and reuse of the existing user id.

## Fail-closed under every new failure mode (adversarial check)
- Duplicate misclassified as transient → retries 3×, never succeeds (duplicate) → no link, operational-error log (loses the deferred-audit visibility only; **no wrong link**).
- Transient misclassified as duplicate → defers + audits → no link (safe direction; a UX edge, not a security hole).
- `writeAuditLog` throw on defer → caught by the Step-5b `try/catch (provisionErr)` "non-fatal" wrapper → student stays enrolled, no orphan, no error to caller.

## Non-blocking observations (noted, do not block merge)
- Retry loop has no backoff/delay — 3 immediate createUser attempts during an outage. Operational only.
- createUser-succeeds-but-returns-transient-error edge could orphan an auth account on the retry (2nd attempt sees the now-existing email → duplicate → defer). Fail-closed (no wrong link); at most an orphaned auth user to clean up. Pre-existing single-attempt code had no retry; acceptable for a hardening.

## Boundary (per my charter + [[ihsanos-pii-money-review-boundary]])
I sign off **code merge-readiness**. This PR carries no migration, no schema change, and no new PII-ingest/apply gate — so there is **no cai §6.6 apply question here** and nothing to escalate. The separate forward-only remediation (detect any already-poisoned `persons.user_id` from before F1) is tracked independently by a credential-holder and is **not** gated on this PR; #326 does not touch it.

## CI (re-verified at head, not taken from the request)
lint-and-typecheck ✅ · tabung-correctness ✅ · tabung-synthtest ✅ · unit-tests ✅ · Vercel ihsanos ✅ · Vercel ihsanos-irsyad ✅ · e2e SKIPPED. `mergeable=MERGEABLE`, `mergeStateStatus=CLEAN`.

**Action:** formal GitHub approval posted, squash-merged (repo convention).
