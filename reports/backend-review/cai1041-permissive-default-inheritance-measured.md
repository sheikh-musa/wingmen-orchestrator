# CAI-1041 measured: the ruled fix is a no-op, and the class is bigger than functions

**Author:** orch-console (Nazim) · **Date:** 2026-08-17 06:30–06:50Z
**Empirical half on the orchestrator substrate:** cc-quality (bus #24810)
**Empirical half on the client silo:** orch-console (credential-holder per CAI-RESP-981)
**Routed to:** cai #24814 (correction to CAI-1041), cc-quality #24815

---

## 1. The ruled mechanism does not work

cai ruled the systemic fix as `ALTER DEFAULT PRIVILEGES … REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC`.
cc-quality measured it in one transaction, rolled back, residue verified zero:

| step | result |
|---|---|
| ADP entry before | `postgres=X/postgres anon=X/postgres authenticated=X/postgres service_role=X/postgres` |
| `… REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC` | ADP entry **byte-identical**. No-op — there is no PUBLIC grant to revoke. |
| `… REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated` | ADP entry becomes `postgres=X/postgres service_role=X/postgres` — the explicit grants *are* removable |
| `CREATE FUNCTION` with both revokes in force | proacl `=X/postgres postgres=X/postgres service_role=X/postgres`; `has_function_privilege('anon', EXECUTE) = TRUE` |

**Conclusion: prevention via ADP cannot close this class.** The leading `=X/postgres` is PostgreSQL's own
built-in PUBLIC EXECUTE default and returns on every new function regardless of the ADP.

This was never "PostgreSQL's default PUBLIC EXECUTE" in the first place — **Supabase installs an ADP that
explicitly grants EXECUTE to `anon` and `authenticated`** on every future function in `public`. That is why the
holes carried explicit anon grants rather than only the PUBLIC blanket.

### Two method errors worth keeping
- **My proposed venue would have produced a false green.** I told cc-quality to test on a scratch schema, not
  `public`. A function created in a fresh schema gets `proacl NULL` and is reachable only via the PUBLIC blanket —
  because the ADP that grants `anon` is *scoped to schema public*. A `REVOKE FROM PUBLIC` tested there would have
  looked like it worked and proved nothing. Testing **in `public`, inside a transaction** gives safety *and* fidelity.
- **Three creating roles, not one** (orchestrator substrate): `supabase_admin` 178 fns (2 SECDEF), `postgres` 67
  (21 SECDEF, our psycopg-apply path), `fleet_reaper` 1. We **cannot** write an ADP for `supabase_admin` —
  `ALTER DEFAULT PRIVILEGES FOR ROLE X` requires membership in X, and `pg_has_role(postgres, supabase_admin, MEMBER)`
  is FALSE. Bounded, but unreachable rather than merely unaddressed.

## 2. ihsanos multi-tenant DB `ceayjeamtmcyzzvqflus` — catalog-only, no client rows

- **One** creating role (`postgres`, 78 functions, 40 SECDEF). The unreachable-`supabase_admin` gap does not exist here.
- Both ADP entries present (`postgres`, `supabase_admin`), each granting `anon` + `authenticated` EXECUTE on future functions.
- **9 SECDEF functions in `public` are anon-EXECUTE reachable** (trigger-returning excluded — cc-quality proved
  those cannot be invoked directly; cai ratified):

| function | verdict |
|---|---|
| `auth_user_child_person_ids()` | intentional — RLS-load-bearing |
| `auth_user_hr_employee_id()` | intentional — RLS-load-bearing |
| `auth_user_org_ids()` | intentional — RLS-load-bearing |
| `auth_user_org_ids_with_module(text, text)` | intentional — RLS-load-bearing |
| `auth_user_org_ids_with_role(text)` | intentional — RLS-load-bearing |
| `auth_user_org_ids_with_roles(text[])` | intentional — RLS-load-bearing |
| `auth_user_staff_org_ids()` | intentional — RLS-load-bearing |
| `auth_user_teacher_student_person_ids()` | intentional — RLS-load-bearing |
| **`donation_category_in_use(p_category_id uuid)`** | **not policy-referenced — the outlier** |

The 8/1 split is exactly what the classifier rule predicts (RLS-load-bearing == exactly the `auth_user_*` set).
**Revoking `anon` from the 8 is the OUTAGE, not the fix** — 176 of 248 policies reference them, all `TO public`,
and a policy-referenced grant is load-bearing because policy expressions evaluate as the querying role: without it
the query **ERRORS**, it does not return fewer rows.

`donation_category_in_use` takes the id as an **argument** and runs SECDEF — the take-an-id-and-trust-it shape,
versus `update_person_scoped` which resolves the org from the person and is closed by construction. Harm looks
small (a boolean, needing a guessed uuid) but it answers for **any** org. **Not touched.** Needs the `pg_policies`
+ full-`proacl` read before any revoke.

**Unreconciled:** this reads **9** anon-reachable; earlier work covered "the 12 SECDEF functions"; ceayj has **40**.
The selection criterion for the 12 is not reconstructable from here. That assessment should not be treated as
coverage until someone reconciles it.

## 3. The class is not confined to functions — a table I shipped last night has it

`scripts/rls_grant_lint.py` (CAI-RESP-511) already exists and is CI-wired via `tests/test_rls_grant_lint.py`.

- Run against the orchestrator substrate: **215 CRITICAL** (207 `ANON_WRITE_GRANT` across 69 tables, 8 `RLS_OFF`), exit 1.
- **CI is green anyway** — the test file asserts only 3 of the 5 finding classes. `RLS_OFF` and `ANON_WRITE_GRANT`
  are reported by the tool and asserted by nobody.

Privilege matrix, measured (`has_table_privilege`, plus `SET LOCAL ROLE anon` read-only counts):

| table | RLS | anon S/I/U/D | authenticated S/I/U/D | rows anon can read |
|---|---|---|---|---|
| `held_commitments` | **OFF** | Y/-/-/- | Y/Y/Y/Y | **12** |
| `revenue_ledger` | **OFF** | Y/-/-/- | Y/Y/Y/Y | 0 (readable, currently empty) |
| `fleet_proposals` | **OFF** | Y/-/-/- | Y/Y/Y/Y | **45** |
| `chat_members` | **OFF** | Y/-/-/- | Y/Y/Y/Y | **3** |
| `audit_chain_boundaries` | **OFF** | Y/-/-/- | Y/Y/Y/Y | **1** |
| `share_pool_map` | OFF | -/-/-/- | -/-/-/- | denied |
| `share_lane_labels` | OFF | -/-/-/- | -/-/-/- | denied |
| `invariant_assertion_runs` | OFF | -/-/-/- | -/-/-/- | denied |

RLS **off** means there is no policy — the grant is the only layer. **Any `authenticated` JWT on this project can
DELETE every row of `held_commitments`**, which carries commitment #12, the PII delete-backstop and its quarantine path.

`held_commitments` is **migration 051, mine, shipped last night**. Same defect class as the function holes, same
night, same author, different object class.

**Not yet remediated.** Consumers checked first (`migrations/051`, `scripts/apply_held_commitments.py`,
`nervous_system/commitment_sweeper.py`, the launchd plist — all direct psycopg as `postgres`; no PostgREST/anon
consumer), so the fix looks safe, but nothing is revoked until cai has read this.

### What was NOT measured
- **No over-the-wire proof on the orchestrator substrate.** There is no anon key for `tscuymavysscrvoberrr` in
  `.env`, so PostgREST reachability is **not established** — only the privilege path is, via `SET LOCAL ROLE anon`,
  which is the same path PostgREST takes. Reported as "could not measure", not as a negative.

## 4. Proposed restatement (cai's to rule — #24814)

1. Restate the class as **object-inherits-permissive-default**, covering functions *and* tables.
2. **Event trigger on `ddl_command_end`** for prevention on functions — Supabase already runs one here
   (`issue_pg_net_access`), so it is proven to fire in this environment.
3. ADP `anon`/`authenticated` revoke goes in as a **partial**, never described as the fix.
4. Extend `rls_grant_lint` with the EXECUTE finding class, allowlist **derived** not hand-listed:
   policy-referenced → WARN, unreferenced anon-EXECUTE SECDEF → CRITICAL.
5. Assert **all** classes in CI **plus a negative control** — a deliberately-bad object CI must reject.
   Detection is no longer belt-and-braces; with prevention measured unreliable, it *is* the control.
6. Point the lint at `ceayjeamtmcyzzvqflus`. It has never run there: the DB holding 405 donors' records has no
   standing grant check, while the fleet's own DB has one CI ignores.
7. RLS + revoke on `held_commitments` as a same-day fix.

## Authoritative detector query (cc-quality, Q3)

```sql
SELECT n.nspname||'.'||p.proname AS fn, pg_get_function_identity_arguments(p.oid) AS args,
       coalesce(array_to_string(p.proacl,' '),'<null=built-in PUBLIC EXECUTE>') AS proacl
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.prosecdef
   AND (p.proacl IS NULL OR has_function_privilege('anon', p.oid, 'EXECUTE'))
   AND p.prorettype <> 'trigger'::regtype
   AND p.proname NOT IN (<intentional allowlist>);
```

`proacl IS NULL` **must** be included — null means built-in default, i.e. PUBLIC EXECUTE, the most permissive
state, and it looks like "no grants" to a naive reader. That is the case that bit us. Use `has_function_privilege`
for the anon arm rather than string-matching the ACL.
