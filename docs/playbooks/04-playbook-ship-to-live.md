# PB-04 — Ship-to-live completeness + the test bar
`version:` 1  ·  `owner:` cc-quality  ·  `last-reviewed:` 2026-10-22  ·  `risk:` P1

### 1. WHEN THIS FIRES
You are about to call a change "done/fixed/shipped," merge a PR, or report a
feature live. Also: any PR that renames a string used as a live DB match/join key,
changes an env/config var, or depends on a separate component redeploying.

### 2. THE FAILURE IT PREVENTS
- **"Merged" / "green tests" ≠ LIVE.** The var can be sourced by the boot script yet
  absent from every running process (booted before the PR); the fix can be
  correct-but-UNMERGED; the warning can be real but `2>/dev/null`'d at the live call
  site. (`deployment-completeness-lens`, `deploy-state-read-origin-main-not-local-checkout`.)
- **Renaming a DB-match string without migrating the live rows.** The code matches
  the new string while the live table still holds the old one → the feature renders
  empty until a separate writer repopulates, and an `ON CONFLICT`-keyed UPSERT
  orphans the old row (ghost). (`deployment-completeness-lens` rename sub-lens,
  musa2→irsyad op#32599.)
- **Blind success claims.** Reporting "done" off a measurement whose tooling
  actually failed → a measurement that couldn't run is "could not measure," never a
  finding. (CLAUDE.md verify-not-assert; standing cc-quality rule.)
- **Thin test bar** — unit-green with the destructive branch untested, the full
  domain sampled not enumerated, a computed value never confirmed RENDERED, or a
  mock that hides a broken-on-live write path. (`audit-bar-includes-lint-gates`,
  `cosem-adcda-archive-async-job`, `verify-computed-value-is-rendered`,
  `role-widen-app-gate-vs-write-rls-path`, `cron-system-write-runs-as-anon`.)

### 3. STEPS (a checklist — this playbook is CI/checklist, not a pre-tool hook)
1. **Ancestor-of-trunk check** — `git merge-base --is-ancestor <fixsha> origin/<trunk>`
   AND the specific line changed at the deployed HEAD; "reported fixed" ≠ landed.
   **[CI-assertable]** *(Blocks: a correct-but-unmerged fix counted as live.)*
2. **Live process env, not the boot script** — `ps eww -p <pid>` for the var in the
   RUNNING body; cross-check process `lstart` vs the merge commit time. **[manual]**
   *(Blocks: a var sourced-but-absent because the body predates the PR.)*
3. **Observability at the real call site** — grep the live caller for `2>/dev/null`;
   a test-covered warning that's discarded live is not observable. **[manual]**
4. **DB-match-string rename → migrate the live rows** — query the live value; find
   the WRITER + its conflict/PK key; require a one-time `DELETE`/`UPDATE` + co-deploy
   writer+reader. Classify as a REQUIRED DEPLOY DATA STEP, not a code defect.
   **[manual]** *(Blocks: empty-render + ghost-row after a rename.)*
5. **The test bar** — lint:all + the full unit suite BOTH blocking (vitest-only is
   blind); run the DESTRUCTIVE branch; enumerate the full column/enum/call-site
   domain not a sample; confirm a computed display value is actually RENDERED; for
   any write-path, demand a live write→read (mocks hide anon-key/RLS failures).
   **[CI (per-repo) + manual]** *(Blocks: broken-on-live behind green mocks.)*
6. **Honest status** — "done" only with the verification output in hand; otherwise
   "could not measure" + what's blocked. **[manual]**

### 4. ENFORCING HOOK
- **Honest classification: CHECKLIST / CI — NOT a pre-tool hook.** "Is it actually
  live" is inherently post-deploy + cross-system (process env, real host, live rows,
  trunk ancestry) — a pre-tool text hook cannot see any of it. Partial existing
  enforcement: `scripts/lib/deploy_safety_guard.sh`.
- **To-build (RED priority #2), as CI assertions where decidable:** the ancestor-of-trunk
  check (step 1) and a rename-match-string detector (step 4 — flag a diff that
  renames a string literal also used in a `WHERE … =`/`ON CONFLICT` without a paired
  data migration) can run in CI as LOUD warns. Steps 2/3/5/6 stay reviewer checklist
  — do NOT pretend a hook covers them.

### 5. DISCOVERY
Index row; CLAUDE.md "verify-not-assert" pointer → `see PB-04`; the CI assertions
annotate the PR citing `PB-04`. (No pre-tool guard to carry a refuse message — this
is the one playbook whose discovery leans on Layers B+C + CI, per the honest limit
in the index.)

### 6. PROVENANCE
`deployment-completeness-lens`, `deploy-state-read-origin-main-not-local-checkout`,
`audit-bar-includes-lint-gates`, `cosem-adcda-archive-async-job`,
`verify-computed-value-is-rendered`, `role-widen-app-gate-vs-write-rls-path`,
`cron-system-write-runs-as-anon-rls-denied`, `weekFailed-flag-drop`; CLAUDE.md
verify-not-assert; orch-console gate #73191 (checklist/CI classification).
