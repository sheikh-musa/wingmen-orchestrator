# Review — PR #420 keluarga page swallow-fix + org-resolution swap (op#15636)

**Auditor:** cc-quality (no-self-merge) · **Date:** 2026-08-21 · **Verdict: PASS, merge-ready.**
**Behavior-preserving verdict on the org-resolution swap: PASS — identical for single-org (this whole org), strictly more correct for multi-org; #418's {org_admin, preparer} gate holds exactly.**
Requested by orch-console (bus #30953, thread `05ca8dff`). Correctness (NOT a gate/access-model change). Pure app-code, **NO migration**.

Pinned HEAD `b1a8b81ee965e576f8a9faddabd93c29b5080e09` (= `gh pr view 420`, MERGEABLE, base `main`). +143/-18. Gates: **lint:all EXIT 0** · **7/7** (3 swallow + 4 render-gate regression).

## 1. Swallow-fix — CONFIRMED.
- **Page:** on `unreturned.error`, `captureActionError(...)` + passes `initialUnreturnedError = unreturned.error ? unreturned.error.message : null` to the client (replacing the `?? []` swallow that made a load failure render identically to a real empty list).
- **Client:** a `useEffect` toasts **iff** `initialUnreturnedError` is truthy (same error path as an in-page search/page-change failure) — a genuine empty (`error === null`) leaves it null → **no toast**, the empty state renders honestly.
- **Tests (3):** error set → toasts even with an empty list; error **absent** → `showToast` `not.toHaveBeenCalled` (real empty stays silent); error **omitted** (optional prop) → not called. The not-toast-on-real-empty case is explicitly covered.

## 2. Org-resolution swap — BEHAVIOR-PRESERVING (the one to scrutinize). CONFIRMED at source.
The raw `.from("org_members").select("role").eq("user_id").is("deleted_at",null).limit(1).single()` is replaced with `resolveActiveOrgContext(supabase)`; `isAdmin = ctx.role === "org_admin"`, `canIssue = isAdmin || ctx.role === "preparer"`.

`resolveActiveOrgContext` (verified): `auth.getUser()` (→ `UNAUTHORIZED` if none) → `org_members` query with the **same `deleted_at IS NULL` filter** → `pickActiveMembershipStrict(list, slug)`. And `pickActiveMembershipStrict` for a **single membership** returns it **unconditionally** — a set slug that matches wins, but an absent or *mismatched* slug falls through to `if (memberships.length === 1) return memberships[0]`. So:
- **Single-org user (everyone in this org):** `ctx.role` == the single membership's role == the old query's role → **identical `isAdmin`/`canIssue`** → #418's exact `{org_admin, preparer}` render-gate is preserved. (The #418 render-gate test still passes: org_admin sees, preparer sees, cashier/viewer don't.)
- **Fail-closed mapping preserved:** no user → `UNAUTHORIZED` → redirect `/login` (== old `!user`); non-member (0 memberships) → `FORBIDDEN` → redirect `/dashboard` (== old `.single()`-errors-on-0-rows → `/dashboard`); ambiguous multi-org (>1, none selected) → `CONFLICT` → redirect `/dashboard` (NEW — the old `.limit(1).single()` would have silently used an *arbitrary* org's role).
- **Strictly more correct for multi-org:** old picked a non-deterministic first membership's role (could show/hide the panel by the wrong org); new uses the active-org role or refuses. No regression for the single-org population; a correctness *improvement* for any multi-org account.

## Verdict
**PASS.** Swallow-fix surfaces real load errors as a toast while a genuine empty stays silent (3 tests). The org-resolution swap is behavior-preserving for `isAdmin`/`canIssue` for every single-org user (identical role resolution via the single-membership fallback in `pickActiveMembershipStrict`), fail-closed mapping preserved, and strictly more correct for multi-org — the #418 render-widen holds exactly `{org_admin, preparer}` (regression test green). Pure app-code, no migration → no apply step. Routing to orch-console for merge (no self-merge).
