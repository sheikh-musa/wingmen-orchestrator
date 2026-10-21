# PB-06 — Framework footguns (use-server / SSR, and the next one we catch twice)
`version:` 1  ·  `owner:` cc-quality  ·  `last-reviewed:` 2026-10-22  ·  `risk:` P1

### 1. WHEN THIS FIRES
You are editing a `'use server'` module (Next.js server actions), or a change in a
Next 16 / Turbopack app touches module-level exports. Generalizes to any
framework-specific compile/runtime footgun this fleet catches more than once.

### 2. THE FAILURE IT PREVENTS
- **`export type { X }` re-export in a `'use server'` module 500s every importing
  route.** Next 16 Turbopack mis-compiles the type re-export into a *runtime* binding
  ref (`registerServerReference(X,…)`) → `ReferenceError` at **SSR module eval**,
  before any function body, so no try/catch can ever catch it. A production
  `next build` does NOT surface it (build-green is not a detector).
  (`use-server-type-reexport-ssr-crash`; ihsanos PR#433, cosem PR#424.)
- **The reviewer trap:** a builder citing a sibling *local* `export type X = {}`
  (safe, erased) as cover for an `export type { X } from '…'` *re-export* (the bug).
  Re-classify every cited precedent by FORM, never by name.

### 3. STEPS
1. **Use-server exports only async functions.** A `'use server'` module must export
   ONLY `async function`s; shared types are imported by consumers from the CORE
   module where they're declared, never re-exported through the action module.
   **[lint: PB-06 lint] + [manual]** *(Blocks: the SSR-500.)*
2. **Discriminate BUG vs SAFE by form:** BUG = `export type { X }` / `export type { X }
   from '…'` (a re-export of a named/imported binding). SAFE = `export type X = { … }`
   (a local alias declaration, fully erased). **[lint]** *(Blocks: mis-reading a
   safe local decl as the bug, or vice-versa.)*
3. **Build-green is not proof.** The definitive check is source-level (the lint) +,
   post-deploy, a prod-log `0×500` on the affected routes. **[manual]**

### 4. ENFORCING HOOK
- **Hook:** a **static lint** — the ONE cleanly hook-able RED row (orch-console
  Condition 2). `grep -rlE '^"use server"'` over src, then for each hit flag any real
  (non-comment) `export type {` — pure text, deterministic, near-zero false positive.
  **TO-BUILD (RED priority #4)**, delivered as an eslint rule in the client repos
  (ihsanos/cosem) and/or a CI/pre-commit lint, as its own reviewed+tested PR with a
  vectors file. LOUD WARN (CI annotation), fail on CI for the clear BUG form.
- **What it CANNOT enforce (residual):** the post-deploy prod-log `0×500` confirmation
  (step 3) — reviewer/SRE.

### 5. DISCOVERY
Index row; the client-repo eslint rule surfaces inline in the editor/CI citing
`PB-06`; client-repo CLAUDE.md pointer → `see PB-06`.

### 6. PROVENANCE
`use-server-type-reexport-ssr-crash` (ihsanos PR#433 root cause digest 703494311,
cosem PR#424 recurrence); orch-console gate #73191 (row-6 = the clean static lint).

---
*This playbook is the home for the NEXT framework footgun the fleet catches twice —
add a section (trigger / bug form / discriminator / lint) rather than spawning a new
playbook per framework quirk.*
