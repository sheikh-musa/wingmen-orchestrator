# PR #486 (subadmin exposure — THE flip gate) floor-review

**Reviewer:** orch-console (Nazim), opus-4-8. **Date:** 2026-08-23. **Head:** c6182960. **Verdict: FLOOR-PASS (GO).**
Hard-gated: my merge only (no self-merge, op#32506). Gate condition met (Piece-2 live both silos, post-Piece-2 re-verify PASSED 32688).

## What it does
UI/enum-only, 6 files (.ts/.tsx, ZERO migrations): (a) invite.ts enum +subadmin; (b) members.ts roleSchema +subadmin + 3 members-list.tsx UI spots; (c) invite-member-dialog +5th option (default stays viewer); (d) permissions-page-client.tsx subadmin column = static `<span>Full</span>`, inert display-only + note (my visible-locked-with-note decision). Net: org_admin can now SELECT subadmin; subadmin gains zero new power.

## Verified at source (my check + independent adversarial agent)
- **A caller gates UNTOUCHED**: inviteMember (:108/111), updateMemberRole (:79/203), resendInvite (:443), grant path module-permissions.ts (:383/:470) — ALL literal `org_admin`-only, none widened to isAdminEquivalent, none in the diff. subadmin ≠ org_admin → zero invite/role/grant power.
- **B (d) matrix display-only**: subadmin column is a non-interactive `<span>Full</span>` (cursor:not-allowed), no onChange/handlePermissionChange; excluded from the ROLES fetch/save loop. Genuinely inert.
- **C enum value-only**: default stays viewer; no schema default changed.
- **D no RLS/migration/SECDEF** in the diff.
- **E tests non-vacuous**: org_admin-can-promote calls the real updateMemberRoleAction (fails without the widen); bogus role rejected; subadmin-3-block-deny runs real inviteMemberAction → FORBIDDEN.
- **F adversarial**: no self-assignment path (schemas consumed only in org_admin-gated files; auto-provisioners write fixed literals, never from untrusted input); members-list has default fallbacks (moot — subadmin never loads the org_admin-only page); the 3 blocks stay literal org_admin (approve-after-count's approver allowlist even restricts candidates to org_admin members).

## NITs (non-blocking)
1. **Copy overstates capability (client-accuracy, fail-safe):** invite-dialog desc + matrix footnote say "admin-level access to every module except the 3 admin-only actions" — but subadmin is more restricted (weekly-report create/sign are umum-scoped, keluarga/both excluded). Overstates access (fail-safe, no security risk). Tighten copy before Gazzabyte relies on it. Dispatched-worthy, not a flip blocker.
2. Cosmetic: invite.ts bogus-role surfaces Zod's generic enum message vs an explicit list.

## Merge / flip conditions (relayed to coord 32694)
1. **Merge ordering**: #486 merges on CI-green, but NOT live before the capability PRs (#479/#480/#481/#482/#483) are merged+deployed — else a flipped subadmin is selectable with incomplete capability. Batch order on the Studio wake: capability PRs → #486 (my merge). 
2. **Flip greenlight to Gazzabyte** only AFTER the full batch is merged+deployed + a final view-as verification.
