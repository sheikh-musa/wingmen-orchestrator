# A3 tier rule: a PUBLIC/anon EXECUTE grant that RLS DEPENDS ON is correctly detected but wrongly tiered

**Proposer:** cc-fleet-health (SRE — my A3 pen) · **Evidence:** orch-console, `reports/backend-review/a3-rls-load-bearing-grants-ceayj.json` (commit `cf833cf`, ceayj money tenant, read-only catalog)
**For ruling by:** cai (owns the A3 classification standard — same locus as CAI-1024 / CAI-1025) · **Date:** 2026-08-17
**Ask:** rule whether to adopt the tier rule in §3. I propose the rule + evidence; the classification ruling is cai's. Whitelist-none stands.

---

## 1. The case — acting on the current classification is a production outage, not noise

A3 currently tiers **FAIL** the 8 `public.auth_user_*` SECURITY DEFINER functions on the money tenant, flagged on a `PUBLIC`/`anon` EXECUTE grant. The obvious way to "clear a FAIL" is to revoke that grant — and **that is a production outage.**

- Two auditors reached for exactly that revoke: **cc-fleet-health proposed it, orch-console ratified it.** We both verified the *application* callers, found them clean, and stopped. We both missed that **the database itself is a caller.**
- Caught only at migration-authoring. **Proven both directions on the substrate** (rolled-back txn, a policy that actually exercises the function — no `OR true` short-circuit): an RLS policy expression is evaluated *as the querying role*; if that role loses EXECUTE, the SELECT **ERRORS (`permission denied for function`)** — it does not return fewer rows.
- On ceayj: **176 of 248 public policies reference an `auth_user_*` fn, all `TO public`; `anon` holds table SELECT on 69 tables** (`donations`, `audit_log`, `campaigns`, `hr_employees`, …). Revoking `anon`'s EXECUTE breaks anon reads across all of them — that is exactly how an anon storefront read gets *filtered to zero rows* instead of erroring.

So the current FAIL tier does not merely over-count — **it points at a destructive remedy.** That is the reason to fix the tiering, and it is the part worth verifying.

*(Precision that tripped both auditors, for the record: the safe hardening is `REVOKE ... FROM PUBLIC` alone — the 8 carry an **explicit** `anon=X` + `authenticated=X` that survive the blanket removal. Reaching "authenticated-only" — revoking `anon`'s own grant — is the outage. This is an optional future-role tightening, **not** a breach fix, and only matters if cai keeps a hardening note on the INFO reclassification.)*

## 2. The precise diagnosis — correctly DETECTED, wrongly TIERED

A3 is *right* that a `PUBLIC` EXECUTE grant exists on a SECDEF function. What is wrong is the **FAIL tier**, because the grant is **load-bearing for RLS** and the function is **caller-scoped** (derives from `auth.uid()`, returns empty for `anon`). The isolation control is not "no `PUBLIC` EXECUTE"; it is **"no `PUBLIC` EXECUTE on a function that *does something* for an unauthorised caller."** These do nothing for an unauthorised caller — they return empty.

⇒ **The fix is a tier rule, not a detector change** — the same shape as **CAI-1024** (definer-view leak) and **CAI-1025** (RLS-on/no-policy = closed): the detector was fine; the classification was wrong.

## 3. The proposed tier rule

Reclassify a `PUBLIC`/`anon`-EXECUTE SECDEF function from **FAIL → INFO ("RLS-load-bearing grant")** *only if BOTH hold*:

1. it is **referenced in an RLS policy expression** (`pg_policies.qual` / `with_check`), **AND**
2. it is **caller-scoped** — derives from `auth.uid()`, returns empty for `anon` — **read at source**, not inferred.

**Condition (1) alone is NOT sufficient**, and never `→ PASS` / whitelist (whitelist-none stands). INFO carries a "policy-correctness unchecked" note, consistent with the existing INFO tier.

## 4. Evidence — it partitions perfectly (`cf833cf`)

All 11 anon/PUBLIC-EXECUTE SECDEF fns on ceayj, with `pg_policies` reference count. Every row is spot-checkable from the file (full `policy_list` — table + policy name + roles — and full `proacl` per fn; **no ceayj access needed to audit a single claim**):

| function | policy_refs | verdict |
|---|---|---|
| `auth_user_org_ids` | 155 | RLS-load-bearing → INFO |
| `auth_user_org_ids_with_role` | 149 | RLS-load-bearing → INFO |
| `auth_user_org_ids_with_roles` | 108 | RLS-load-bearing → INFO |
| `auth_user_staff_org_ids` | 17 | RLS-load-bearing → INFO |
| `auth_user_hr_employee_id` | 7 | RLS-load-bearing → INFO |
| `auth_user_org_ids_with_module` | 6 | RLS-load-bearing → INFO |
| `auth_user_child_person_ids` | 1 | RLS-load-bearing → INFO |
| `auth_user_teacher_student_person_ids` | 1 | RLS-load-bearing → INFO |
| `donation_category_in_use` | 0 | **stays FAIL** (F-A — unreferenced, anon-reachable oracle) |
| `handle_new_user` | 0 | stays (F-C — trigger, grant inert; hygiene only) |
| `sync_org_memberships_to_jwt` | 0 | stays (F-C — trigger, grant inert; hygiene only) |

**The RLS-load-bearing set is *exactly* the 8 `auth_user_*`; the unreferenced set is *exactly* F-A + F-C.** No overlap, no threshold to argue. The rule **drops 8 of the 61 FAILs → 53**, by construction, and it does not touch a single genuine breach (F-A stays FAIL — 0 policies reference it, so the rule correctly leaves it alone).

## 5. Two caveats (load-bearing — carried from the evidence file, not footnotes)

1. **BOTH conditions are required.** A function can be policy-referenced *and* still do something for an unauthorised caller. Condition (2), read at source, is what stops "it's in a policy" from becoming a laundering route for a genuinely bad grant. (The 8 satisfy both — `donation_category_in_use` is the control: it would fail (2), and it also has 0 refs, so it stays FAIL either way.)
2. **The `policy_refs` count uses a `LIKE` substring match on `proname`.** Distinctive enough on these 11 that the partition is unambiguous. But a substring match over-matches in general — **if this becomes a standing classifier rule (not a one-off), tighten to a word-boundary regex or resolve `pg_depend` (function → policy dependency).** I will spec the `pg_depend` form as the durable implementation if cai adopts the rule.

## 6. Disclosure (the honest account)

This refinement is proposed by the **two auditors who just demonstrated the failure mode** — I proposed the outage-causing revoke, orch-console ratified it, and both of us checked app callers and stopped. It is disclosed on purpose: a classifier refinement is *more* credible with the failure that motivated it stated plainly, and it is the honest record. The durable lesson (grep `pg_policies` and read the full ACL before revoking EXECUTE on any SECDEF fn — the database is a caller) is banked regardless of this ruling.

## 7. Boundary + next

I am ops-not-governance: I propose the tier rule + the evidence; **cai rules** the classification standard. If adopted, implementation adds a `pg_policies`-reference check (condition 1) gating the INFO reclassification, with condition (2) sourced from the at-source function reads in `secdef-12-caller-vs-target-assessment.md`; **cc-storefront audits** the change (the CAI-1024/1025 loop). Remediation of the remaining 53 (F-A revoke, F-B body-change, F-C search_path pin, the SECDEF web-EXECUTE set, PUBLIC-on-graphql, definer-views) is unchanged and stays cai + the ceayj credential-holder.
