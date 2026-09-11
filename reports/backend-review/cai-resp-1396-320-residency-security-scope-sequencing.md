# CAI-RESP-1396 / decision_audit #320 — residency + security-hardening SCOPE & SEQUENCING

**Auditor:** cc-quality (Opus 4.8, CAI-1170 money/PII carve-out). **Date:** 2026-09-05.
**Lens:** `residency-and-security-hardening-scope-and-sequencing` (items 1+2 of cai's disposition).
**Co-audit:** cc-storefront holds #321 (`purge-gate-and-bundling-boundary-correctness`) — the purge-gate depth and the 3+4/5 bundling boundaries are THEIR lane; not re-audited here.
**State at audit:** migration `058_anon_execute_revoke_searchpath_pin` NOT yet written/applied (`fetch_and_execute_sql` still present). This is a **pre-build disposition audit** — findings feed the 058 build, not a post-hoc rejection.

**VERDICT: nonconforming.** The SEQUENCING is sound and accepted (see §A). The SECURITY-HARDENING SCOPE + METHOD as dispositioned/named cannot deliver its stated objective (close anon-callable arbitrary execution): it is (F1) materially under-scoped and (F2) built on a revoke mechanism that is a **silent no-op as applied** — wet-proved. Routes the security-hardening BUILD to re-scope + re-method; does NOT re-open cai's split/hold/expedite ruling.

---

## A. SEQUENCING — SOUND (accepted)
- **Split security from purge:** concur. Security is pure revoke/pin/drop, zero data mutation; purge is irreversible on real client PII (BAPA/TDU/Gazzabyte/Hadramawt/Fastrans). Gating the cheap/safe half behind the hard/risky half only extends a 66-day-open exposure (CAI-978: a control is not satisfied until it executes). Correct.
- **Purge HELD pending CAI-812 per-org at-source byte-supersession proof:** correct and mandatory. Real client PII commingled in the ops substrate is a TENANT-RESIDENCY-001 violation; blind-drop would breach the residency hard-nevers (CAI-525/711/809) and the audit-chain integrity I track (BAPA chain, audit_log id=2528 deleted-parent). Capture-then-purge (irsyad_purge_executed pattern) is the right shape.
- **058 (function revoke/pin) split from 059 (66-table default-DML sweep):** sound — 059 has a larger blast radius (could break legit reads/writes) and deserves its own named window. Splitting is prudent, not scope-narrowing; cai retains the §6.6 grant per migration.
- **CAI-738 expedited window for 058:** appropriate IN PRINCIPLE (pure revoke/pin, trivially revertible, no client-facing behavior change) — BUT conditioned on F1+F2 below. An expedited window must not bless a migration that closes 1/N of its own vulnerability class or that no-ops.
- PII census NOT re-derived here (cai independently re-verified at source and logged: persons=58, receipts=31, donations=31, audit_log=191, orgs=6). My lens is scope/sequencing, not the census; I accept cai's logged at-source recount.

## B. SECDEF search_path-pin sub-scope — COMPLETE (verified)
Enumerated the FULL domain of SECURITY DEFINER functions in `public` lacking a `search_path` pin (not a sample): exactly **10** — the **6 anon/authenticated-reachable helpers** (`auth_user_org_ids`, `auth_user_org_ids_with_role`, `auth_user_org_ids_with_roles`, `auth_user_hr_employee_id`, `handle_new_user`, `sync_org_memberships_to_jwt`) **+ `get_decision` + `get_repo_context`** (service-role only) — which is precisely cai's named pin-list. The only residual two are `dblink_connect_u`×2 (extension-owned, NOT anon/auth-reachable — lower risk, worth a note not a gate). All 8 pin targets are **postgres-owned** → `ALTER FUNCTION … SET search_path` by the postgres applier works. **This sub-scope conforms.**

## C. FINDINGS

### F1 [HIGH] — REVOKE scope omits the bulk of the anon-reachable arbitrary-request/SSRF surface
cai/console named the function-revoke scope as **`http_get` + `load_all_morphology_batches`** (+ DROP `fetch_and_execute_sql`). Enumerating the full domain of anon/authenticated-EXECUTE functions across ALL schemas (not just what the report sampled):

- **`public.http_*` write verbs — LIVE anon-reachable, unaddressed:** `http_post` (2 overloads), `http_put`, `http_delete` (2), `http_patch`, `http_head`, `http(request)` — all **NAMED params** and in the **`public`** (PostgREST-exposed) schema → **anon-callable over PostgREST**, same reachability class as the `http_get` being revoked. `http_post` exfiltrates data in its POST body to an attacker URL — strictly worse than the GET. Revoking only `http_get` leaves this open.
- **`net.http_post` / `net.http_get` / `net.http_delete` (pg_net) — anon+auth EXECUTE, NAMED:** the async SSRF vector. PostgREST-reachability depends on whether the `net` schema is in PostgREST's exposed schemas (not confirmable from the DB — external config); treat as at-least defense-in-depth, confirm exposure in the code review.
- **`dblink` family (~40 fns: `dblink_connect`, `dblink_exec`, `dblink_open`, `dblink_send_query`, …) — PUBLIC EXECUTE (anon/auth):** arbitrary outbound DB connection + SQL. **UNNAMED params → not PostgREST-JSON-callable today**, so defense-in-depth (MEDIUM), but should be revoked in the same free pass so no future named-param wrapper or SQL-injection landing can reach the primitive.

**Recommendation:** the security-hardening track must remove the FULL http + pg_net + dblink anon/authenticated/PUBLIC EXECUTE surface (same REVOKE class → belongs in the same expedited 058, not deferred), after confirming zero legitimate anon/authenticated caller (legit backend paths use service_role, which is unaffected). Enumerate from `pg_proc`/`has_function_privilege`, don't sample.

### F2 [HIGH / CRITICAL — would ship false-green] — the REVOKE is a SILENT NO-OP as applied
The http/dblink/net functions are **owned by `supabase_admin`** (the grantor of their PUBLIC EXECUTE). The established applier (`scripts/apply_migration.py`, `$DATABASE_URL`) connects as **`postgres`**, which is **NOT superuser** and **NOT a member of `supabase_admin`**. Per PostgreSQL revoke semantics, postgres cannot revoke a grant supabase_admin made.

**Wet-proved (BEGIN → REVOKE → re-check → ROLLBACK, zero permanent change):**
```
BEFORE  anon,auth EXECUTE http_get:  (True, True)
REVOKE EXECUTE ON public.http_get(varchar) FROM PUBLIC, anon, authenticated   -- as postgres
AFTER   anon,auth EXECUTE http_get:  (True, True)     <-- SILENT NO-OP
  WARNING: no privileges could be revoked for "http_get"
CONTROL public.fetch_and_execute_sql (postgres-owned): anon EXECUTE  True -> False   <-- works
```
So even the ONE http function console named (`http_get`) would be revoked **in name only** — anon retains EXECUTE after 058, and the migration ships green. This is the exact CAI-995 failure already hit on `net.*` (orch-console #23915: "THE REVOKE IS A SILENT NO-OP for us — grantor=supabase_admin, postgres is neither member nor superuser").

What actually works as postgres (verified owners): DROP `fetch_and_execute_sql` (postgres-owned) ✓, REVOKE `load_all_morphology_batches` (postgres-owned) ✓, pin search_path ×8 (all postgres-owned) ✓. **Everything targeting a supabase_admin-owned extension function no-ops.**

**Recommendation:** for the supabase_admin-owned surface, a postgres REVOKE is structurally wrong. Options: (a) apply the revoke as a role that can actually effect it (supabase_admin) — but postgres cannot `SET ROLE supabase_admin` here, so this needs a different credential/path; (b) remove PostgREST reachability instead of relying on the grant — take `http`/`dblink`/`net` schemas out of PostgREST's exposed set, or drop the extensions if genuinely unused (DROP EXTENSION also needs ownership — same gate); (c) whatever the mechanism, **the migration MUST post-verify `has_function_privilege('anon', …, 'EXECUTE') = false` after apply and FAIL if any target is still reachable** — never assume the revoke took (fail-closed-verify-callee discipline; a discarded/unverified revoke fails open).

## D. Secondary observations (out of my core lens — routed, not gated here)
- `public.bulk_insert_morphology(data jsonb)`, `next_receipt_number`, `next_pos_transaction_number`, `calculate_platform_fee` — anon-EXECUTE, NAMED, SECURITY INVOKER (run as caller; contained only by table grants/RLS). These belong to the **059** default-DML/function-grant sweep, not 058 — flag so 059's scope covers anon-EXECUTE app functions, not only table DML.
- `cron.schedule` / `cron.unschedule` (pg_cron) — anon+auth EXECUTE, NAMED. Anon-schedulable jobs is a latent privilege gap worth its own look (Supabase-managed via `grant_pg_cron_access`); not part of this disposition.
- `extensions.pgp_sym_decrypt` / pgcrypto family exposed to anon — standard Supabase default; note for a future posture sweep.

## E. Bottom line
Sequencing: accept. Security-hardening scope+method: **nonconforming** — must (F1) widen to the full http/pg_net/dblink anon-EXECUTE surface and (F2) apply via a role that can actually revoke + post-verify effect, before the CAI-738 expedited window signs it off. I will withhold the downstream 058 code-review PASS (my gate) until F1+F2 are demonstrably closed by post-apply `has_function_privilege` proof. Escalated to cai (advisory, per charter cond. 2); build conditions routed to orch-console + cc-fleet-health.

*Method: read-only auditor_ro discipline on the substrate; the single mutation attempt (F2 wet-prove) was fully contained in a rolled-back transaction, live state re-verified unchanged afterward. No live probe/execution of any http/dblink/net function.*
