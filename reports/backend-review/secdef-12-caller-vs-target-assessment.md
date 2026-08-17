# The 12 unassessed SECURITY DEFINER functions — caller-vs-target assessment (money tenant `ceayjeamtmcyzzvqflus`)

**Assessor:** orch-console (Nazim, ceayj credential-holder) · **Read list:** cc-fleet-health #24447
**Date:** 2026-08-17 · **Method:** `pg_get_functiondef` on the LIVE ceayj database, read-only session.
Never a migration file — a source file is a claim about the past, not the applied object.

**Layer/store (LAYER-VOCAB-001):** ihsanos multi-tenant DB, `project ref ceayjeamtmcyzzvqflus` (the money
tenant). Not the irsyad silo.

---

## HEADLINE: none of the 12 is another `purge_wc_ingest_pii`.

`purge_wc_ingest_pii` failed because **the caller named the target org and nothing checked the caller's
membership of it**, while the function destroyed PII. **No function in this set of 12 has that shape.**
Every MUTATOR carries an internal authorization gate that re-derives or re-checks the caller against the
target, and each was read at source rather than inferred from a name.

Two functions DO take a target as a parameter without a membership check — but both are **read-only** and
neither returns PII. They still FAIL the standard and are **not whitelisted** (per the standing rule:
whitelist none).

---

## Verdict table

| # | Function | Target param? | Gate found at source | Verdict |
|---|---|---|---|---|
| 1 | `merge_persons` | yes (`p_org_id`) | actor==`auth.uid()`; both persons must be IN `p_org_id`; caller must be `org_admin` of it | **PASS** |
| 2 | `reverse_merge` | yes | actor==`auth.uid()`; caller `org_admin` of `p_org_id` | **PASS** |
| 3 | `update_person_scoped` | **no** | actor==`auth.uid()`; **org resolved FROM THE PERSON, not a caller arg**; role gate | **PASS (exemplary)** |
| 4 | `update_storefront_settings_for_org` | yes | active `platform_admins` gate + config-key allowlist that fails closed | **PASS** |
| 5 | `mark_ai_editor_proposal` | yes | active `platform_admins` gate | **PASS** |
| 6 | `write_audit_log_secure` | yes | actor==`auth.uid()` (blocks actor-spoof) **and** caller active member of `p_org_id` | **PASS** |
| 7 | `tabung_resolve_missing_tin` | yes (report id) | caller must be `org_admin` of the report's org; ownership checked BEFORE any state disclosure (Satr) | **PASS** |
| 8 | `kk_tins_with_student` | yes (`p_org_id`) | `p_org_id IN (SELECT auth_user_staff_org_ids())` — load-bearing | **PASS** |
| 9 | `handle_new_user` | n/a — trigger | not directly invokable (verified) | **PASS**, hygiene finding F-C |
| 10 | `sync_org_memberships_to_jwt` | n/a — trigger | not directly invokable (verified) | **PASS**, hygiene finding F-C |
| 11 | `has_org_role_permission` | **yes, unguarded** | none | **FAIL — F-B** |
| 12 | `donation_category_in_use` | **yes, unguarded** | none, and **anon-reachable** | **FAIL — F-A** |

### Why #3 is the pattern to copy
`update_person_scoped` does not accept an org at all. It looks the org up **from the person id** and then
checks the caller against that. Cross-org is closed *by construction* rather than by a check someone must
remember to write. Prefer this shape over "take the org and validate it".

---

## F-A — `donation_category_in_use(p_category_id uuid)` — anon-reachable cross-tenant existence oracle

**Grants (verified):** `anon`, `authenticated`, `PUBLIC`, `service_role`.
SECURITY DEFINER, so it **bypasses RLS** and counts rows in `donations` (plus `bank_keyword_mappings`,
`tabung_jumaat_reports`) for **any category UUID the caller supplies**, with no membership check.

**Impact, stated precisely:** it returns an array of *table names*, not amounts and not PII. So this is an
**existence oracle**, not a data leak: an unauthenticated caller who knows or guesses a category UUID
learns whether that category is in use and in which subsystems. Guessing a v4 UUID is not practical, so
real-world exploitability is **low** — but `anon` holding EXECUTE on an RLS-bypassing function that reads
`donations` on the money tenant is indefensible on its own terms.

**Remediation (safe, reversible, non-breaking — caller check done first):**
```sql
REVOKE EXECUTE ON FUNCTION public.donation_category_in_use(uuid) FROM anon, PUBLIC;
```
**Verified before proposing:** the only application caller is
`src/shared/lib/category-management.ts:49` (plus `src/actions/donations.ts`), and it calls
`supabase.rpc(...)` **as an authenticated user**. Revoking `anon`/`PUBLIC` therefore does not break it.
`authenticated` is retained deliberately.

⚠ A runtime revoke **RE-OPENS on redeploy** (default PUBLIC EXECUTE re-applies) — this is the lesson from
`purge_wc_ingest_pii`. It must be codified in a migration, not applied by hand.

## F-B — `has_org_role_permission(p_org_id, p_role, p_permission)` — cross-tenant config disclosure

Takes the org as a parameter, reads `org_role_permissions` for it, and **never checks that the caller
belongs to that org**. Any authenticated user of org A can enumerate org B's permission matrix.

**Impact:** booleans about configuration only — no PII, no mutation, no amounts. **Low severity**, but it
is exactly the failing pattern and is therefore **not whitelisted**.

**Remediation is NOT a revoke.** Verified live app callers: `src/actions/persons.ts:145` and
`src/modules/receipts/receipt-permissions.ts:54`, both `supabase.rpc` as `authenticated`. Revoking would
break person editing and receipt permissions. It also runs **inside** `update_person_scoped`, which must
keep working.

⇒ The fix is an **internal membership check** (`p_org_id` must be in the caller's org ids), which is a
function-body change on the money tenant and therefore needs a cai grant + migration. Flagged, not applied.

## F-C — `handle_new_user`, `sync_org_memberships_to_jwt` — unpinned `search_path` (hardening only)

Both are SECURITY DEFINER, owned by `postgres`, hold `PUBLIC`/`anon` EXECUTE, and **have no
`SET search_path`** (every other function in the set pins `public, pg_temp`).
`sync_org_memberships_to_jwt` is sharp on paper: it writes `auth.users.raw_app_meta_data`, i.e. JWT org
claims.

**Two things measured rather than assumed, and both reduce the severity to LOW:**
1. **The EXECUTE grants are inert.** Both return `trigger`, and PostgreSQL itself refuses direct
   invocation — tested on the live DB: `trigger functions can only be called as triggers`. The
   `PUBLIC`/`anon` grant buys an attacker nothing.
2. **The search_path-hijack precondition is absent.** A mutable search_path is exploitable only if the
   attacker can create objects in a schema resolved earlier. Checked every schema on ceayj: **no web role
   (`anon`, `authenticated`) holds CREATE anywhere.**

⇒ **Defense-in-depth, not a live breach.** Fix by pinning `SET search_path = public, pg_temp` and dropping
the meaningless `PUBLIC`/`anon` grants. No urgency.

## Separate track (raised by cc-fleet-health, confirmed): the 8 `auth_user_*`

They are caller-scoped-safe on the SECDEF axis and appear in the A3 FAIL set only via a **PUBLIC EXECUTE**
grant. `PUBLIC` is never the right grantee; `authenticated`-only is correct. Clean, reversible, and
independent of the 12 — but same caveat as F-A: **codify in a migration or it re-opens on redeploy.**

---

## What I am NOT claiming

- This is **one reading, by one body.** cc-fleet-health assesses each definition independently at source;
  a PASS here is a proposal to it, not a self-certification. Two auditors refused a naming table on
  exactly this principle.
- I read the **applied** definitions, but a verification is true at an instant: these were read at
  ~03:55–04:00Z on 2026-08-17. If a migration lands after that, re-read before relying on this.
- I did **not** apply any change. F-A is safe and reversible but it is still a grant change on the money
  tenant, and grant changes go through cai.
