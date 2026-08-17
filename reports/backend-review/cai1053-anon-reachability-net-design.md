# CAI-1053 / CAI-1054 — design: the anon-reachability net

**Author:** orch-console (Nazim) · 2026-08-17 07:15Z
**Path:** design (mine) → cc-quality review → cai §6.6
**Supersedes my own first instinct in two places, both because someone measured instead of reasoning.**

---

## 1. What the class actually is

Not "default PUBLIC EXECUTE on functions". Three nights of holes share one shape:

> **An object acquires a permissive reachability from somewhere nobody looked, and nothing asserts otherwise at ship time.**

The "somewhere" has turned out to be four different places:

| # | source of permissiveness | instance |
|---|---|---|
| 1 | Supabase's seeded ADP grants `anon`/`authenticated` EXECUTE on new **functions** | `escalate_full_tier_without_auditor`, `purge_wc_ingest_pii` |
| 2 | PostgreSQL's built-in `=X/postgres` PUBLIC EXECUTE on new **functions** | survives even after the ADP is stripped |
| 3 | Supabase's seeded grants on new **tables**, with RLS never enabled | `held_commitments` (mig051), + 4 others |
| 4 | a **view** without `security_invoker` executes as owner, so the table's RLS is never consulted | `held_commitments_due`, `agent_observed_activity` |

Any net scoped to one of those four misses the other three. That is the whole finding.

## 2. Two measurements that changed the design

**(a) Prevention via `ALTER DEFAULT PRIVILEGES` is impossible, not merely incomplete.** cc-quality, one transaction, rolled back: `REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC` leaves the ADP row byte-identical (there is no PUBLIC entry — Supabase grants `anon`/`authenticated` explicitly). Strip those two as well and a **new function still comes out `=X/postgres`** with `has_function_privilege('anon','EXECUTE') = TRUE`.

**(b) `security_invoker` is not a durable property; the REVOKE is.** cc-quality, isolated scratch cluster, both migration patterns:

| edit pattern | `reloptions` | grants |
|---|---|---|
| `CREATE OR REPLACE VIEW` (e.g. adding a column) | **dropped** | **survive** (`anon=r` persists) |
| `DROP VIEW` + `CREATE VIEW` | **dropped** | **also dropped** → lands owner-only |

⇒ **A revoke-based control survives both patterns. A `security_invoker`-based control survives neither.** The two-layer shape shipped in mig056 degrades gracefully, and the grant is the load-bearing half — which also means:

- **The event trigger does NOT need to cover views.** Grants either survive (pattern A) or reset to owner-only (pattern B); neither re-opens anon.
- **Correction to my own earlier framing:** I reported the five untouched views as "resting on one layer" in a tone that implied fragility. They rest on the **durable** layer. That is a materially calmer state than I described, and the record should say so.

## 3. Prevention (narrow, because only one slot needs it)

**Event trigger on `ddl_command_end`, functions only.** Revoke EXECUTE from `PUBLIC`, `anon`, `authenticated` on newly created functions in `public`, minus the allowlist.

Chosen because it is **proven to fire in this environment** — Supabase runs one already (`issue_pg_net_access`, re-applying grants after DDL). Preferring a mechanism already observed working here over a cleaner one we would be first to trust.

**Bounded, and the bound is stated:** 178 functions on the orchestrator substrate are owned by `supabase_admin`, and `postgres` is not a member, so we cannot write an ADP for that role. An event trigger fires regardless of owner, which is a second reason to prefer it — but platform-created functions arriving through paths we do not observe remain a residual, and detection is what covers them.

## 4. Detection — and it is *the* control, not the backstop

With prevention impossible for slots 2–4, detection carries the weight. Two assertions, both outcome-shaped, both spanning **tables, views and functions**:

### Assertion A — exposure
> No object in `public` is READ- or EXECUTE-reachable by `anon` or `PUBLIC`, except allowlist entries.

### Assertion B — the mirror hazard (cc-quality's, and it is the half I would have missed)
> Every object we expect a legitimate consumer to read is still readable by that consumer.

Because `DROP VIEW`+`CREATE` resets grants to owner-only, a required consumer **silently goes dark** — and it presents as *empty data*, not an error. Same class as tonight, opposite direction. This is the assertion that would have caught the `console_readonly` / `chat_members` risk in mig055 before I had to think of it.

### Rules the net must obey

1. **Assert the TOTAL is empty.** Not `assert [f for f in findings if f.code in KNOWN] == []`. The existing `tests/test_rls_grant_lint.py` asserts three named classes and never `crit_tables == []` — which is why `rls_grant_lint.py` reports **215 CRITICAL** while CI is **green**. Third instance of that shape after CAI-993 and CAI-1027.
2. **Allowlist entries carry their reason and their ruling ref.** Without this an outcome-shaped gate turns every deliberate design decision into a CRITICAL, and the first person on call deletes the reason to make CI green. The allowlist is not the exception to the control, it is half of it. *Tonight proved the cost from both sides:* cc-quality reported four transparency tables as a finding without knowing CAI-RESP-511 had ruled them deliberate (031, lines 25-26), and they were then revoked.
3. **Derive the allowlist wherever the derivation is sound — but the first derivation I wrote was WRONG, and the outlier it produced is what proved it.**

   My original rule: *SECDEF function referenced by a `pg_policies` expression → WARN (RLS-load-bearing); unreferenced anon-EXECUTE SECDEF → CRITICAL.* On `ceayj` it partitions the 8 `auth_user_*` correctly and leaves exactly one outlier, `donation_category_in_use`. I went to check that outlier before recommending anything, and it is **not** a clean CRITICAL:

   - It is **not** referenced by any policy — so my rule marks it CRITICAL, i.e. revoke.
   - But it is called by `donation_category_in_use_guard`, a **`SECURITY INVOKER` trigger function** on `donation_categories`. A non-SECDEF trigger executes **as the invoking role**, so every role that can legitimately write that table needs EXECUTE on the function the trigger calls.

   ⇒ **SECOND LOAD-BEARING ARM, which policy-reference does not detect:**
   > A function is load-bearing for role R if it is called by a **non-SECDEF trigger function** on a table R can write — regardless of whether any policy mentions it.

   Both arms are needed. Policy-reference catches the `auth_user_*` shape; trigger-caller catches this one. **Had the net shipped with only the first arm, `donation_category_in_use` reads as a clean CRITICAL and the fix is a revoke that breaks the category-management write path for `authenticated`** — the same near-outage as revoking `anon` from `auth_user_*`, arriving through a door the rule did not watch.

   The detector must therefore walk `pg_proc.prosrc` for callers and `pg_trigger` for wiring, not just `pg_policies`.
4. **Record WHICH layer is holding.** "Closed by grant, `security_invoker` decayed" and "closed by both" are identical in a pass/fail and one edit apart in risk.
5. **Exclude trigger-returning functions.** Proven un-invokable directly; cai ratified.
6. **Negative control.** A deliberately-bad object CI must REJECT. A detector nobody has watched fail is untested — and with prevention unavailable, an untested detector is the only thing between us and the next one.

### Ground-truth query (cc-quality's, functions arm)

```sql
SELECT n.nspname||'.'||p.proname, pg_get_function_identity_arguments(p.oid),
       coalesce(array_to_string(p.proacl,' '),'<null=built-in PUBLIC EXECUTE>')
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.prosecdef
   AND p.prorettype <> 'trigger'::regtype
   AND (p.proacl IS NULL OR has_function_privilege('anon', p.oid, 'EXECUTE'));
```

`proacl IS NULL` **must** be included — null means built-in default, i.e. PUBLIC EXECUTE, the most permissive state, and it reads as "no grants" to a naive reader. Use `has_function_privilege`, never string-matching the ACL.

**Home:** beside `scripts/lib/a3_grant_detector.py`, sharing its `aclexplode`/`has_*_privilege` helpers — **not inside it.** A3 answers a different question (PUBLIC grants on relations as a residency vector). Folding two questions into one detector is how one of them stops being asked.

## 5. Perimeter — the gap that matters most

`rls_grant_lint.py` runs against `DATABASE_URL` only, and only through `pytest tests/` in CI.

| store | project ref | standing grant check today |
|---|---|---|
| orchestrator substrate | `tscuymavysscrvoberrr` | yes — but CI ignores its headline classes |
| ihsanos multi-tenant DB | `ceayjeamtmcyzzvqflus` | **none** |
| irsyad silo (goumlyne) | `goumlynecruxrlmzlntp` | **none** |

**The two stores holding client data have no standing anon-exposure check at all.** `ceayj` holds the 405-donor perimeter. This is the single highest-value line in the design and it is a configuration change, not a build.

Per CAI-RESP-981 the runner executes in the orch plane under `orch-console`, the credential-holder — the work moves to the credential, never the credential to the work.

⚠ **The last ten feet are how this fails.** `scripts/residency_sweep.py` was authored, committed and its plist written on 2026-07-02 and has never been loaded — six weeks of a standing self-audit not running, with a plist hardcoding `/Users/Musa` on a host whose user is `sheikhmusa`. **This net is not shipped until it has FIRED and posted a run record**, and the plist's paths are checked against this host.

## 6. CAI-1054 item 2 — non-skippable ledger, folded in deliberately

Same question: *does the substrate match the repo?*

1. **Apply-writes-or-fails** — the apply path writes `migration_ledger` in the same transaction as the DDL, or the migration does not commit. A ledger you can silently skip is a promise, not an answer.
2. **CI check** — any migration file with no ledger row for a silo it should be applied to is a finding, under Assertion-A discipline: assert the total, allowlist with reasons.

Backfill (item 1) is **done** — 044→056 contiguous, verified at source, 0 unverified, each row's `note` recording what was actually checked and stating that the sha256 attests the file *as of backfill*, not that the applied DDL was byte-identical.

## 7. What this design does NOT cover — stated, not glossed

- **Functions created by `supabase_admin`** through platform paths (event trigger fires, but the platform may create objects in ways we have not observed). Detection covers it; prevention does not.
- **Over-the-wire verification on the orchestrator substrate by me** — there is no anon key for `tscuymavysscrvoberrr` in `.env`. Every wire proof tonight is cc-quality's. The net should carry a wire probe, which means the net needs that key.
- **Whether the applied DDL matched the repo before 2026-08-17.** Closed forward, never retroactively.
- **The four transparency tables.** Awaiting cai's explicit reverse / uphold-and-amend-031 / split. Until then the repo and the substrate disagree, and the net cannot be built on a contradiction — whichever way it lands, it becomes an allowlist entry carrying its ruling ref.

## 8. Doctrine this proposes (cai's to rule)

1. **An assertion that enumerates what must be absent will always lag what appears.** Assert the total; every exception is an allowlist entry carrying its reason. *Enumerating the forbidden is a promise; enumerating the permitted is a control.*
2. **A fix inherits the scope of the finding that prompted it unless someone deliberately widens it.** 051 fixed 3 views of 8. #61 fixed 1 database of 2. The class-check was public-schema-scoped. My table sweep was RLS-off-scoped and missed the views. The last step of any containment is *"what other shape has this property?"* — and that has to be someone's explicit job, not a good habit.
3. **Before reporting a configuration as a defect, grep the migrations and decisions for its name.** A deliberate design decision and an oversight look identical in the catalogue; only the record distinguishes them.
4. **The venue is part of the fixture.** A scratch schema does not reproduce a defect caused by a `public`-scoped default. Test in the real schema, inside a transaction, and roll back — safety from the boundary, fidelity from the schema.
