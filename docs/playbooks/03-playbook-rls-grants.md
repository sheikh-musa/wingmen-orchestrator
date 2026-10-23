# PB-03 — RLS / grants / lifecycle-column change
`version:` 1  ·  `owner:` cc-quality  ·  `last-reviewed:` 2026-10-22  ·  `risk:` P0

### 1. WHEN THIS FIRES
You are changing authorization at the row/column layer on a client table: a
`CREATE`/`ALTER POLICY`, a `GRANT`/`REVOKE`, a new table, or **adding a non-terminal
lifecycle state** (`in_progress`/`draft`/`pending`) to a `status`/enum column that
was previously always-terminal. Any migration that touches who-can-read/write-what.

### 2. THE FAILURE IT PREVENTS
- **An RLS `UPDATE` policy assumed to restrict COLUMNS — it does not.** A row-level
  `WITH CHECK` + a whole-table `GRANT UPDATE` lets the low-trust actor PATCH *any*
  column. "They can only write field X" is false without a **column-level GRANT** or
  a SECDEF write path. (`rls-update-policy-needs-column-grant-or-secdef`, PR#1102;
  the fix pattern — REVOKE UPDATE from anon/authenticated + GRANT UPDATE(cols) +
  in-tx `has_column_privilege` self-check — is in cosem PR#429.)
- **A new-table default grant leak** — on Supabase, `postgres→public` +
  `supabase_admin→public` default-privilege rules auto-grant a fresh object to
  `{anon,authenticated,service_role}`. Every `CREATE TABLE`/function needs an
  explicit `REVOKE ALL FROM anon, authenticated` on the table AND its sequence, or
  it ships world-readable. (`new-table-default-grants-checklist`,
  `drop-create-function-acl-net-of-default-privileges`.)
- **A `REVOKE` by the wrong role is a silent no-op** — requires a
  `has_function_privilege=false` / ACL proof, not the presence of the REVOKE line.
  (`revoke-migration-owner-vs-applier-silent-noop`.)
- **A PII read filter written as a blocklist** (`NOT IN(...)`) → a new role silently
  sees PII; must be a fail-closed allowlist `IN(...)`.
  (`pii-visibility-filter-allowlist-not-blocklist`, mig426.)
- **A new lifecycle state exposes EVERY pre-existing reader** that assumed
  all-rows-terminal — results/lock/aggregate/verifier queries (often in files NOT in
  the PR diff) now silently include the transient rows → placeholder-zero pollution,
  and if the row is low-trust-writable, attacker-controlled values.
  (`new-lifecycle-state-column-sweep-all-readers`, cosem PR#429.)

### 3. STEPS
1. **Full-tuple policy diff** — on `DROP+CREATE POLICY`, diff roles / `TO` / `cmd` /
   `WITH CHECK` / `USING` — not just the `USING` qual. **[manual]**
   (`rls-policy-diff-full-tuple-not-just-qual`.) *(Blocks: a silent role/cmd widen.)*
2. **Column-GRANT for every writable-column claim** — if a policy lets a role
   `UPDATE`, the write must be bounded by a **column-level** `GRANT UPDATE(col,…)`
   (+ `REVOKE UPDATE` of the whole-table grant) or a SECDEF RPC — never a row-policy
   alone. **[lint: PB-03 lint, WARN] + [manual]** *(Blocks: PATCH-any-column.)*
3. **Net-ACL, not the GRANT line** — compute `(default-priv auto-grants at CREATE)
   + (explicit REVOKE/GRANT)` and diff vs the LIVE `proacl`/table ACL per silo; an
   explicit `REVOKE anon` is often load-bearing. **[manual]** *(Blocks: a
   default-privilege re-widen on DROP+CREATE.)*
3b. **New table/sequence** — explicit `REVOKE ALL ON <t>, <t>_seq FROM anon,
   authenticated`. **[manual]** *(Blocks: world-readable new object.)*
4. **Lifecycle-column reader sweep** — `git grep "from('<table>')"` / `FROM <table>`
   across ALL of src (not just the PR); every results/lock/aggregate/verifier reader
   must filter to the terminal state or justify why the transient row is safe there;
   watch near-miss filters (a `submitted_at IS NOT NULL` that the transient row also
   passes). **[lint: PB-03 lint flags the column-add → "sweep readers of <table>";
   WARN] + [manual to fix each reader]** *(Blocks: placeholder pollution / tamper.)*
5. **Negative control** — PROVE the forbidden write/read is refused: direct PATCH the
   barred column as the low-trust role → expect `42501`; read as the excluded role →
   expect 0 rows. A green positive test is NOT proof. **[manual]** *(Blocks: an
   RLS/grant gap mocked-green.)*
6. **PII filter = allowlist** — `IN(...)`, fail-closed; never `NOT IN(...)`.
   **[lint: grep `NOT IN (` in a visibility filter, WARN] + [manual]**

### 4. ENFORCING HOOK (two complementary nets — one EXISTING, one TO-BUILD)
- **EXISTING 🟢 — `scripts/rls_grant_lint.py` (live-DB posture, CAI-RESP-511).**
  Connects to the substrate and fails (exit 1) on the exploitable *anon-reachable*
  surface: RLS-off base tables, anon/auth/PUBLIC **TRUNCATE**, permissive
  `USING(true)/WITH CHECK(true)` write policies for anon/public, anon/PUBLIC
  write grants off the allowlist, and postgres-owned default-privilege anon-write
  regressions (tested, `tests/test_rls_grant_lint.py`). This is the live net —
  **it already covers steps 3b-ish and the anon-write class; do NOT rebuild it.**
  LIMITS: it reads the LIVE substrate posture (not a per-PR diff), runs against the
  substrate DB (not per client-silo), and checks table/row-level write grants —
  it does **NOT** check the *column-level* grant gap below.
- **TO-BUILD 🟡 (this track's first code PR, RED-#1) — the DELTA the live lint
  misses:** `scripts/migration_rls_grant_lint.py`, a stdlib-only, **no-DB static
  migration-diff lint** (shape like `ddl_detect.py`). Over the PR's added
  `migrations/*.sql` lines it flags, as a **LOUD WARN** (GitHub-Actions `::warning::`
  annotation on the PR, never a buried log):
  (i) a `CREATE`/`ALTER POLICY … FOR UPDATE|ALL` (or a policy with **no** `FOR`
  clause — defaults to ALL) whose same-diff carries no column-level
  `GRANT … (<cols>) ON <table>` → "an RLS UPDATE policy does not restrict columns;
  confirm a column-GRANT or SECDEF write path";
  (ii) a non-terminal lifecycle value-add (`ADD VALUE`, or a new `DEFAULT
  'in_progress'|'draft'|'pending'`) → "sweep ALL readers of `<table>` (step 4)".
  It is **complementary** to the live lint: authoring-time, diff-scoped, runs for
  any silo's migrations, and catches the column-restriction gap the live net can't.
- **Why a CI-lint (WARN), not a fail-closed pre-tool hook:** a pre-tool hook sees
  one edit in isolation and cannot see whether the *corresponding* column-GRANT
  lives in another migration or already on live — hard-blocking would false-positive
  and wedge the fleet. Match the layer to what's decidable there (cai doctrine,
  orch-console gate #73186/#73191).
- **What NEITHER lint enforces (residual, all [manual] above):** net-ACL vs live
  (step 3), the negative-control proof (step 5), and whether each swept reader was
  actually fixed (step 4) — reviewer + live wet-proof. The lints surface the *shape*;
  they do not prove adequacy on live.

### 5. DISCOVERY
- Index `README.md` row; the lint's WARN annotation cites `PB-03`; CLAUDE.md
  SQL-security pointer → `see PB-03`.

### 6. PROVENANCE
`rls-update-policy-needs-column-grant-or-secdef`, `new-table-default-grants-checklist`,
`drop-create-function-acl-net-of-default-privileges`, `revoke-migration-owner-vs-applier-silent-noop`,
`rls-policy-diff-full-tuple-not-just-qual`, `pii-visibility-filter-allowlist-not-blocklist`,
`new-lifecycle-state-column-sweep-all-readers`, `secdef-revalidation-completeness-full-app-filter-set`,
`completeness-claims-enumerate-full-domain`; cosem PR#429, ihsanos PR#1102/mig426;
orch-console gate #73186/#73191 (CI-lint classification).
