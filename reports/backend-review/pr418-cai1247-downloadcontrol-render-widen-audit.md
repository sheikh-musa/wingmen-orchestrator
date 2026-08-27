# FULL review — PR #418 CAI-1247 "Tins still out" panel/DownloadControl render-widen

**Auditor:** cc-quality (FULL, no-self-merge) · **Date:** 2026-08-21 · **Verdict: PASS, merge-ready.**
**canIssue verdict (the crux): canIssue == EXACTLY {org_admin, preparer} — matches the effective server authority. No UX/intent finding.**
Requested by orch-console (bus #30897, thread `0c8e5aba`). Minors-PII render surface. Pure app-code, **NO migration**, no goumlyne touch.

Pinned HEAD `e914f9a7d063e81d99bb49fc06d08f3b1be5469b` (= `gh pr view 418`, MERGEABLE, base `main`). 2 files, +23/-13. Gates: **lint:all EXIT 0** · **render-gate test 4/4**.

## The change
`keluarga-admin-client.tsx`: the "Tins still out" worklist panel (which contains the `DownloadControl` CSV/PDF export) changes its render gate `{isAdmin && …}` → `{canIssue && …}`, un-hiding the panel + export for preparers (a code path #416/#417's action-layer widening already served, but the render still hid). Test updated to cover preparer-sees + cashier/viewer-don't.

## CRUX — `canIssue` resolves to EXACTLY {org_admin, preparer}. CONFIRMED at source.
`page.tsx:33,37`: `const isAdmin = (membership.role as string) === "org_admin";` and `const canIssue = isAdmin || (membership.role as string) === "preparer";` → `canIssue = (role==='org_admin') || (role==='preparer')` = **exactly {org_admin, preparer}**. Despite the name, **cashier is NOT in `canIssue`** (the comment notes a preparer may *issue* tins per CAI-642/mig088, but the flag is org_admin||preparer; batch *creation* stays isAdmin-only, kept as a separate flag). Page is fail-closed: membership error / non-member → `redirect("/dashboard")`.

**Reconciliation with the server authority (precise):** the message framed the crux as "canIssue == the server MAY_EXPORT allowlist." To be exact: `exportUnreturnedKkTinsAction`'s outer `MAY_EXPORT` is literally all four roles (`["org_admin","cashier","preparer","viewer"]`), but it loops the **#416-gated** `listUnreturnedKkTinsAction`, whose gate is `role !== "org_admin" && role !== "preparer" → FORBIDDEN` (line 1332) — so the **effective** server export authority is **{org_admin, preparer}**. `canIssue` == that effective authority **exactly**. Therefore the render predicate shows the panel/export to precisely the roles the server actually serves names to: **no role sees a button that errors** — the "excluded role sees an erroring button" UX/intent finding the review was probing for does **not** occur.

## cashier/viewer hidden, org_admin/preparer shown — CONFIRMED (tested 4/4).
`unreturned-status-render-gate.test.tsx`: org_admin sees the panel + student name; preparer (`canIssue`, not `isAdmin`) sees it (the P1 fix); cashier/viewer (neither) never see it even with rows present; a non-admin status-cell click is a no-op. And even if a cashier/viewer reached the endpoint, the inner gate FORBIDs them (no leak) — but they never see the affordance.

## Defense-in-depth note (agree — backlog, NOT a #418 blocker) — 3rd sighting
The PDF route (`/api/tabung/unreturned-tins/pdf`) outer allow-list is all four roles, with protection resting entirely on the inner `listUnreturnedKkTinsAction` gate (single-point). Safe today; this is the **same finding I raised on #416 and #417** — recommend tightening the route's + `exportUnreturnedKkTinsAction`'s outer allow-lists to `{org_admin, preparer}` so the gate is layered. Backlog hardening; #418 is render-only and doesn't touch the route.

## Verdict
**PASS.** `canIssue == {org_admin, preparer}` exactly, matching the effective server export authority — the render-widen is correctly scoped, cashier/viewer stay hidden (and server-FORBIDDEN if they reach it), org_admin/preparer see what the action already serves. Pure app-code, no migration → no apply step. Routing to orch-console for merge (no self-merge). Re-flagged the outer-allow-list single-point-gate as a standing backlog hardening (now seen 3×).
