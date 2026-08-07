# Persona-Runner Code Audit — 2026-06-16 (pre-UAT hardening)

Auditor: superpowers:code-reviewer (read-only). Scope: all `src/test/personas/*.ts`,
`scripts/run-persona-sweep.ts`, `scripts/sql/qa-madrasah-invariants.sql`, tests.
Operator directive (Musa): "make this perfect before UAT + complete code audit."

Headline: **two live FALSE-PASS vectors on the two highest-stakes assertions
(parent RLS-leak, viewer RBAC read-only). Neither is caught by current tests.**
The least-privilege split is intact and structurally enforced. The target-tier
constraint is NOT enforced in code (only by which env var the operator exports).

## CRITICAL

- **C1 — parent RLS-leak check can false-pass on a defaulted/undefined extract.**
  Parent passes when `showsTabungOrDonations === false` (inverted check,
  runner.ts:101-107). That boolean is one haiku `extract()` read with a falsy
  default. If Stagehand returns a partial object (soft failure) the field is
  `undefined` → falsy → parent passes on a real leak. Also a pure model-miss on
  an unfamiliar Tabung layout → false. No deterministic floor under the most
  dangerous assertion. Fix: make the field required + assert presence; add a
  deterministic DOM keyword scan for Tabung/donation so a leak must beat BOTH a
  regex and the model.
  - **Live-run note:** parent landed on "Dashboard/Portal Error Page" with
    showsTabungOrDonations=false. Combined with I1 (we never assert arrival at
    `/dashboard`), we currently can't distinguish "RLS correctly hid donations"
    from "parent hit an error page that happens to show nothing." The parent
    PASS is therefore NOT yet a trustworthy RLS proof.

- **C2 — `MUTATION_LABEL` regex (runner.ts:124-125) has real false-negatives →
  viewer write-capability passes as read-only.** Misses (a) Malay verbs — this
  is a Malay-surfaced madrasah app (Simpan/Hapus/Padam/Tambah/Kira/Keluarkan);
  (b) English synonyms not in the set (Add/Submit/Approve/Reject/Confirm/Send/
  Void/Adjust/Post/Reconcile/Upload/Import); (c) icon-only buttons (empty text →
  skipped at runner.ts:155). Fix: invert to an allow-list of known read-only
  labels (anything else interactive = suspect), or at minimum add the Malay +
  English verb classes and an icon/aria-label heuristic. Add fixture tests per
  class (current tests only prove the regex matches what it already matches).

## IMPORTANT

- **I1 — `waitForLeaveLogin` (runner.ts:167-175) only checks `!startsWith('/login')`.**
  Any non-/login URL (an error bounce to `/` or `/auth/callback`) counts as
  success; it never asserts arrival at `/dashboard`, then assesses whatever page
  it's on. Fix: assert post-login URL is under `/dashboard` (allow-set); optional
  persona-identifying DOM check.
- **I2 — `waitForLoadState("networkidle").catch(()=>{})` (runner.ts:226) swallows.**
  Supabase realtime sockets often prevent networkidle; the catch eats the timeout
  and extract+scan run on a possibly half-rendered DOM (late-painting "Issue tin"
  or Tabung widget → false verdict). Fix: assert a stable landing anchor before
  assessing; emit flaky/fail if absent.
- **I3 — runner never emits `flaky` or retries**, though sweep.ts/invariants.ts
  admit "flaky" and config.ts documents a sonnet-on-retry tier. Every transient
  (incl. the Supabase auth rate-limit we hit at ~13 rapid logins, whose banner is
  even read) becomes a hard fail. Fix: detect rate-limit banner → flaky + one
  backoff retry on sonnet tier.

## MINOR

- **Mi1** — `readLoginError` selector `[class*="bg-red-50"],[class*="text-red-700"]`
  is broad; scope to the login banner container.
- **Mi2 — target-tier constraint unenforced in code.** Nothing rejects
  `QA_PERSONA_LOGIN_URL=https://irsyad.ihsanos.com` (the real-PII silo). Email
  allow-list (`*@qa-madrasah.test`) is only a soft guard. Fix: validate host
  against an allow-list (ihsanos.com) and hard-refuse `irsyad.`/known prod silos
  in `resolveRunnerConfig`, with a test.
- **Mi3** — `finally` calls `stagehand.close()` which can throw and mask the real
  error; guard or `.catch(()=>{})` close-time errors.

## TEST GAPS

- **T1** — `labelsIndicateMutation` tested only with English verbs + a benign set
  → cannot catch C2 (circular). Add Malay-verb / synonym / icon-button fixtures.
- **T2** — `driveJ1` integration logic (waitForLeaveLogin, obs assembly, networkidle
  swallow, isReadOnly wiring) has ZERO tests; the injected-driver test stubs the
  whole driver. `waitForLeaveLogin` is trivially testable with a fake page — add
  stays-on-/login→false, bounce-to-/→(documents I1), reach-/dashboard→true.
- **T3** — no test for the C1 undefined→parent-pass contract.

## SPLIT / TIER VERDICT
- Least-privilege split: **intact + structurally enforced** (no service_role
  field exists; config.test.ts guards it; no qa_findings write; no invariant-SQL
  run in this half). SQL is scoped to `slug='qa-madrasah-test'`.
- Target-tier: **not enforced in code** — fix via Mi2 host allow-list.

## M2 READINESS (separate from M1 defects)
- Auth rate-limit: M2 (~6× persona logins) will throttle; add per-persona
  authenticated context reuse (Playwright storageState) — I3 retry is prerequisite.
- Lift duplicated `DriverPage`/`ScanPage` + login/scan helpers into a shared
  `drivers/` module so the C2 regex lives in one audited place.
- `expectedFindingKeys` completeness diff is solid + M2-ready.

## BOTTOM LINE
C1 + C2 are live false-pass vectors on the two assertions the suite exists to
prove. The handed-off "4/4 green" (msg #2208) should be treated as PROVISIONAL
until C1, C2, I1 (and T1-T3) are fixed and the sweep re-run. The mechanical G3
clearance shouldn't be called UAT-final on this artifact.
