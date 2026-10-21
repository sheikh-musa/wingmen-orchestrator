# PB-02 — Client-data / residency path
`version:` 1  ·  `owner:` cc-quality  ·  `last-reviewed:` 2026-10-22  ·  `risk:` P0

### 1. WHEN THIS FIRES
You are about to touch real client data or the boundary around it: Read a file
under `logs/tg_media/**` (raw inbound client media), query a client silo
(goumlyne / ihsanos_prod / an ADCDA real-gov-ID surface), change an RLS policy /
grant / SECDEF on a client table, or name a layer/role in code that gates client
data. (irsyad/ihsanos = minors-PII; cosem-adcda = adult real gov-ID — both
"treat_as_real" unless `data_truth.py classify` says otherwise.)

### 2. THE FAILURE IT PREVENTS
- **A lane reading raw, un-staged client media.** Real incident: a lane Read a
  genuine UAE-gov trainee gradebook screenshot straight out of `logs/tg_media`
  (#52461). Staging exists precisely so a lane never sees raw cell values —
  "it won't stop the next lane" to leave it to memory (#52465).
- **A role/app-gate widen that the write-RLS path doesn't actually grant** —
  mocked tests pass green, broken on live (`role-widen-app-gate-vs-write-rls-path`,
  PR#1089); or a cron writing via the anon-key client → `auth.uid()` RLS-denied,
  row never persists, mocked writer hides it (`cron-system-write-runs-as-anon`, PR#1110).
- **An RLS UPDATE policy assumed to restrict columns** — it does not; without a
  column-GRANT or SECDEF write path, "they can only write field X" is false, and
  a trainee can PATCH any column (`rls-update-policy-needs-column-grant-or-secdef`,
  PR#1102). Requires a **negative-control**: a direct PATCH of the forbidden column
  must be *proven* to 42501.
- **A PII read filter written as a blocklist** (`NOT IN(...)`) instead of a
  fail-closed allowlist (`IN(...)`) → a new role silently sees PII
  (`pii-visibility-filter-allowlist-not-blocklist`, mig426).
- **Vocabulary drift** — a layer/role named ambiguously so a reviewer can't tell
  which trust boundary a call sits behind (LAYER-VOCAB: name the layer by its
  trust boundary, not its feature).

### 3. STEPS
1. **Classify first** — run `data_truth.py classify <project_ref>`; absent a
   provenance row, fail-safe **treat_as_real**. Never infer "it's dummy" from the
   repo name. **[manual]** *(Blocks: handling real PII as if it were fixtures.)*
2. **Residency gate** — read client silos ONLY via the sanctioned RO path
   (`auditor_ro` RO-DSN, no write/DDL, BYPASSRLS read) for audits; a lane building
   a feature stages/attest-reads, never raw. Cosem is walled from auditor_ro →
   those are "could not measure" from here. **[hook + manual]** *(Blocks:
   exfil / cross-silo read.)*
3. **Stage before read (media)** — raw `logs/tg_media/**` must be run through
   `stage_client_file.py` (→ CLEAN report) before any Read; coord's direct-read
   needs a prior `attest-read` record. **[hook]** *(Blocks: reading raw client
   cell values / gov-ID images.)*
4. **Negative control on every write-surface security claim** — for any
   "they can only write/read X" claim, PROVE the forbidden action is refused:
   direct PATCH the barred column as the low-trust role → expect `42501`; read as
   the excluded role → expect 0 rows. A positive test is not enough. **[manual]**
   *(Blocks: an RLS/grant gap that mocked green hides.)*
5. **Verify the WRITE path, not just the app-gate** — if you widen a role, confirm
   that role actually holds the write RLS/grant (role-widen trap); if a
   system/cron writes, confirm it uses the service client / SECDEF RPC, not the
   anon-key client, and demand a live write→read. **[manual]** *(Blocks:
   broken-on-live behind green mocks.)*
6. **LAYER-VOCAB naming** — name the new layer/role by the trust boundary it
   enforces (e.g. `*_secdef_service`, `*_anon_read`), so the gate is legible to the
   next reviewer; no feature-named auth helpers. **[manual → lint candidate]**
   *(Blocks: a reviewer mis-reading which boundary a call sits behind.)*

### 4. ENFORCING HOOK
- **Hooks (EXISTING):**
  - `scripts/hooks/client_media_read_guard.py` — refuses a `Read` of any
    `logs/tg_media/**` file with no CLEAN staging report for that stem (exit 2 +
    stderr). Console-exempt (it runs the stager); coord has an `attest-read`
    allow-path. Fail-closed by construction (HOLD writes no report).
  - `scripts/hooks/lane_prod_key_guard.py` — guards prod-key access on Bash/Read.
  - `scripts/hooks/glm_image_read_guard.py` — image-read guard (GLM text-only
    constraint path).
- **What they enforce:** the *media-read* residency boundary (step 3) and prod-key
  access — mechanically, fail-closed.
- **What they CANNOT enforce (residual, the 🔴 gap):** the classify step (1), the
  negative-control proof (4), the write-path-vs-app-gate check (5), LAYER-VOCAB
  naming (6), and allowlist-not-blocklist PII filters — all review-time today.
  → **Enforcement that is honestly feasible (NOT a new fail-closed hook):**
  (a) client-silo DSN residency is ALREADY partly covered — `scripts/lib/lane_db_residency_guard.py`
  is a **detect-only boot nudge** (always exit 0, never blocks) warning when a lane's
  own repo `DATABASE_URL` shadows the inherited bus DSN. **cai (CAI-RESP-1308)
  explicitly DECLINED a fleet-wide hard residency block** ("blast radius on live bus
  plumbing for a risk not currently live"). So the residency floor here is: the
  existing nudge + the `auditor_ro` RO-DSN discipline + CI/review — this playbook
  does NOT propose a fail-closed residency hook (the console cannot override a
  standing cai gate).
  (b) the RLS-`UPDATE`-without-column-`GRANT` risk (step 4) is enforced by the
  **CI/migration-diff lint in [PB-03](03-playbook-rls-grants.md)** (WARN, not a
  pre-tool block — the adequacy of a grant needs cross-migration/live context).
  (c) a `NOT IN (` PII-visibility-filter grep is a review/CI nudge (allowlist), also
  tracked in PB-03.

### 5. DISCOVERY
- Index `README.md` row (risk P0).
- Pointer in the client-lane CLAUDE.md files (irsyad/ihsanos/cosem) + the
  cc-quality charter's review-boundary section.
- `client_media_read_guard`'s refuse message should cite `PB-02`.

### 6. PROVENANCE
`client_media_read_guard` incident #52461/#52465, `ihsanos-pii-money-review-boundary`,
`rls-update-policy-needs-column-grant-or-secdef`, `role-widen-app-gate-vs-write-rls-path`,
`cron-system-write-runs-as-anon-rls-denied`, `pii-visibility-filter-allowlist-not-blocklist`,
`cai1231-auditor-ro-not-yet-enforced`, `mamadah-content-free-envelope-residency`,
`destructive-callable-ui-gate-vs-server-permission`, `cosem-platform-data-classification-pending`;
Musa op#20593 (attest-read), op#51060/#51875 (staging).
