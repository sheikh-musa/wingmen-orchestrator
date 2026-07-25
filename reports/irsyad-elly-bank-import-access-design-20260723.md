# Elly bank-import access — design spec (money-permission, cai-review-gated)

**Date:** 2026-07-23 · **Author:** cc-orchestrator (hub) · **Status:** DESIGN (client op#6646 confirmed the need); needs cai security review before build.
**Client need (op#6646):** Elly (role=`preparer`) must (1) **upload bank reports** and (2) **verify they import in the correct format + categorize accurately**. Client explicitly wants her kept SCOPED (not full org_admin) — op#6583 "keeping her scoped is the right instinct". Client acknowledges "it's a money permission."

## Current gates (VERIFIED in ihsanos, 2026-07-23)
Bank-import + categorization are **org_admin-ONLY**, hardcoded (NOT module-gated, so NOT grantable via `org_role_permissions` today):
- Route: `src/app/dashboard/admin/bank-import/page.tsx:8` → `requireRole(["org_admin"])`.
- Actions: `src/actions/bank-import.ts:124, 268, 390` → `if (role !== "org_admin")`.
- Categorization keywords: `src/app/dashboard/admin/bank-keywords/page.tsx:10` → `requireRole(["org_admin"])`; its actions likewise.
- (Contrast: POS/donations/tabung gates already include `preparer` + use the module-gate; bank-import does not.)

## Why this is security-sensitive
Bank-import drives how real banked money is categorized into donation categories — a money-integrity surface. Extending it beyond org_admin touches the money plane across ALL tenants. Per fleet doctrine this routes through cai before build ([[gate-client-silo-mutations-through-cai]]).

## Recommended approach — TENANT-SCOPED module grant (mirrors the POS pattern)
Do NOT hardcode `preparer` into the gates (that silently grants bank-import to every tenant's preparers = unwanted all-tenant money-surface broadening). Instead:
1. Introduce a `bank_import` (+ `bank_keywords`, or fold both) **toggleable module** in `src/shared/lib/modules.ts` (DEFAULT_ROLE_PERMISSIONS: org_admin `full`, everyone else `none`).
2. Convert the hardcoded `role !== "org_admin"` checks in `bank-import.ts` (3 sites) + the two admin routes + bank-keywords actions to `requireModule("bank_import")` / `getRolePermissions`-driven checks.
3. Grant **irsyad's** `preparer` role `bank_import=full` via `org_role_permissions` (data-only, per-org) — Elly gets it; no other tenant's preparer does. Add `preparer` to the Settings→Permissions UI (`permissions-page-client.tsx:20` currently omits it) or upsert directly.
   - Net effect: Elly (preparer) can upload + review/categorize bank reports; every other tenant unchanged; org_admin unchanged.

**Alt (faster, worse):** add `preparer` to the 3 hardcoded gates — ~1h code but broadens the money surface for ALL tenants' preparers. Rejected unless cai prefers it.

## Effort / sequence
- Code: convert ~5–6 gate sites to module-gate + module definition + UI role addition → ~0.5 day.
- **cai security review** (money-categorization access change) — mandatory, mirror the storefront-ratings review bar. Re-verify: no tenant except irsyad gets it; org_admin/maker-checker invariants intact; bank-import writes still money-safe.
- Deploy (all-tenant code change → both silos) via the standard worktree→preview-gate→ff-main→verify path. Verify by driving the REAL flow as Elly (upload a sample bank report, confirm categorization) before telling the client live ([[test-end-to-end-before-declaring-live]], [[eyeball-curated-shots-not-verification]]).
- **Client timeline quoted (op#6646 reply): ~1 day.**

## Open for cai
Approach A (tenant-scoped module grant, recommended) vs B (hardcoded preparer add, all-tenant). Recommend A — keeps Elly scoped + no cross-tenant money-surface broadening, matches the existing POS module pattern.
