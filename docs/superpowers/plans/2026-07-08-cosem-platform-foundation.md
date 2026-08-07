# COSEM Platform Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the multi-tenant modular-monolith foundation (Next.js App Router + Supabase Postgres) that every COSEM platform module sits on — before any feature module (onboarding, exams) is ported.

**Architecture:** A modular monolith partitioned into `core` (universal entities) / `modules` (isolated features) / `actions` (server-action RPC) / `shared` (cross-cutting). Tenancy is `org_id` on every row enforced by Postgres RLS via `SECURITY DEFINER` helper functions — leakage is impossible-by-default, not a per-query discipline. Feature access is a runtime intersection of org-level module enablement and a per-role permission matrix. This is the ihsanos architecture (see `~/wingmen/projects/ihsanos`) applied to a fresh COSEM platform; that repo is the reference implementation for every pattern below.

**Tech Stack:** Next.js (App Router, React Server Components, server actions), Supabase Postgres, TypeScript, Tailwind, Vitest (unit), Playwright (e2e). Package manager per COSEM lane default.

## Global Constraints

- Every tenant table: `org_id UUID NOT NULL REFERENCES organizations(id)`. No exceptions.
- RLS enabled on every table; policy filters `deleted_at IS NULL AND org_id IN (SELECT auth_user_org_ids())`.
- Recursion-safe RLS: org-membership lookups go through `SECURITY DEFINER` helpers, never a direct RLS-subject query.
- Server actions are `"use server"`, re-resolve org context, gate on permission, and return `ActionResult<T> = { data: T | null, error: { code: string, message: string } | null }`.
- RLS-filtered / cross-org rows surface as `NOT_FOUND`, never `FORBIDDEN` — never leak cross-org existence.
- Soft delete everywhere (`deleted_at timestamptz`), except append-only `audit_log`.
- Jurisdiction siloing: one codebase, a **separate Supabase project per jurisdiction**; the schema/migrations are identical, only the connection target differs. No cross-jurisdiction row ever shares a database.
- Module boundaries: `src/modules/<X>` must NEVER import `src/modules/<Y>`; cross-module needs route through `src/core`. Enforced by the boundary lint (Task 9), which must pass in CI.
- All money (if introduced later) is `NUMERIC(15,2)`. All PII dual-stored (encrypted + hashed) — deferred to the module that first needs it, not this foundation.

---

## File Structure

```
cosem-platform/
  supabase/migrations/
    001_foundation.sql          # orgs, profiles, org_members, audit_log, RLS helpers + policies
    002_module_permissions.sql  # org_role_permissions + org module-config
  src/
    core/
      audit/api.ts              # appendAudit() — hash-chained append-only writer
    shared/
      lib/
        supabase-server.ts      # createServerClient (user JWT) / createServiceClient (service role)
        org-context.ts          # getOrgContext(), getOrgSlugFromHeaders()
        modules.ts              # CORE_MODULES, ALL_MODULES, MODULE_DEPENDENCIES, ROUTE_MODULE_MAP, DEFAULT_ROLE_PERMISSIONS
        module-permissions.ts   # getOrgModuleConfig, getRolePermissions, getVisibleModules
        action-gate.ts          # gateAction() -> ActionResult FORBIDDEN
        module-gate.ts          # requireModule() -> notFound()
        result.ts               # ActionResult<T> type + ok()/err() helpers
      ui/                       # design-system primitives (button, card, nav) — minimal for foundation
    actions/
      _example-ping.ts          # a trivial gated server action proving the stack (removed once a real module lands)
    middleware.ts               # subdomain -> x-org-slug; session refresh
    app/
      layout.tsx
      dashboard/layout.tsx      # nav from getVisibleModules(); deny-by-default route allowlist
      dashboard/page.tsx
  scripts/lint/check-module-boundaries.mjs
  tests/                        # vitest unit + playwright e2e
```

---

### Task 1: Scaffold the Next.js + Supabase project

**Files:**
- Create: `cosem-platform/` (Next.js App Router app), `tsconfig.json` (path aliases), `tailwind.config.ts`, `.env.local.example`
- Create: `supabase/` (via `supabase init`)

**Interfaces:**
- Produces: path aliases `@core/*`, `@modules/*`, `@shared/*`, `@actions/*` resolving to `src/core`, `src/modules`, `src/shared`, `src/actions`; a linked Supabase project; `pnpm dev` / `pnpm test` scripts.

- [ ] **Step 1:** `npx create-next-app@latest cosem-platform --ts --app --tailwind --eslint --src-dir` (accept App Router). Then `cd cosem-platform && npx supabase init`.
- [ ] **Step 2:** Add path aliases to `tsconfig.json` `compilerOptions.paths`:
```json
{ "@core/*": ["src/core/*"], "@modules/*": ["src/modules/*"], "@shared/*": ["src/shared/*"], "@actions/*": ["src/actions/*"] }
```
- [ ] **Step 3:** Add Vitest + Playwright: `pnpm add -D vitest @vitest/coverage-v8 @playwright/test`. Add scripts `"test": "vitest run"`, `"test:e2e": "playwright test"`.
- [ ] **Step 4:** Create `.env.local.example` with `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `NEXT_PUBLIC_JURISDICTION` (e.g. `sg`).
- [ ] **Step 5:** Verify `pnpm dev` boots and `pnpm test` runs (0 tests). Commit: `chore: scaffold cosem-platform (next app-router + supabase + vitest)`.

---

### Task 2: Foundation migration — orgs, memberships, audit, RLS helpers

**Files:**
- Create: `supabase/migrations/001_foundation.sql`
- Test: `tests/db/foundation.test.ts` (runs against a local `supabase start` Postgres)

**Interfaces:**
- Produces (SQL objects later tasks rely on): tables `organizations(id uuid pk, slug text unique, name text, type text, region text, settings jsonb, created_at, deleted_at)`, `profiles(user_id uuid pk, ...)`, `org_members(org_id uuid, user_id uuid, role text, unique(org_id,user_id))`, `audit_log(id bigserial, org_id, actor uuid, action text, entity text, payload jsonb, prev_hash text, hash text, created_at)`; functions `auth_user_org_ids() returns setof uuid`, `auth_user_org_ids_with_role(text) returns setof uuid`.

- [ ] **Step 1: Write the failing test** (`tests/db/foundation.test.ts`):
```ts
import { pgClient } from './helpers'; // thin pg wrapper over the local supabase db url
test('org_members lookup does not recurse and returns only the caller orgs', async () => {
  const db = await pgClient();
  // seed two orgs + one membership for user A
  const { orgA, orgB, userA } = await seedTwoOrgsOneMember(db);
  await db.query(`select set_config('request.jwt.claim.sub', $1, true)`, [userA]);
  const rows = await db.query('select auth_user_org_ids() as id');
  expect(rows.map(r => r.id)).toEqual([orgA]);
  expect(rows.map(r => r.id)).not.toContain(orgB);
});
```
- [ ] **Step 2: Run it — expect FAIL** (`pnpm vitest run tests/db/foundation.test.ts`) with "function auth_user_org_ids does not exist".
- [ ] **Step 3: Write `001_foundation.sql`.** Core content (adapt exact columns from ihsanos `001_foundation.sql`):
```sql
create table organizations (
  id uuid primary key default gen_random_uuid(),
  slug text unique not null,
  name text not null,
  type text not null check (type in ('academy','unit','company')),
  region text not null,                         -- jurisdiction tag (sg, ae, ...)
  settings jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  deleted_at timestamptz
);
create table profiles ( user_id uuid primary key, display_name text, created_at timestamptz default now() );
create table org_members (
  org_id uuid not null references organizations(id),
  user_id uuid not null,
  role text not null,
  created_at timestamptz default now(),
  unique (org_id, user_id)
);
create table audit_log (
  id bigserial primary key,
  org_id uuid not null references organizations(id),
  actor uuid, action text not null, entity text, payload jsonb,
  prev_hash text, hash text not null,
  created_at timestamptz not null default now()
);
-- recursion-safe membership lookup (bypasses RLS on org_members)
create or replace function auth_user_org_ids() returns setof uuid
  language sql security definer set search_path = public as $$
    select org_id from org_members where user_id = (current_setting('request.jwt.claim.sub', true))::uuid;
  $$;
create or replace function auth_user_org_ids_with_role(want text) returns setof uuid
  language sql security definer set search_path = public as $$
    select org_id from org_members
    where user_id = (current_setting('request.jwt.claim.sub', true))::uuid and role = want;
  $$;
revoke all on function auth_user_org_ids() from public, anon, authenticated;
grant execute on function auth_user_org_ids() to authenticated;
-- enable RLS + canonical policies
alter table organizations enable row level security;
alter table org_members enable row level security;
alter table audit_log enable row level security;
create policy org_self on organizations for select using (id in (select auth_user_org_ids()) and deleted_at is null);
create policy members_self on org_members for select using (org_id in (select auth_user_org_ids()));
create policy audit_read on audit_log for select using (org_id in (select auth_user_org_ids()));
-- audit is append-only: no update/delete policy => denied by default under RLS
```
- [ ] **Step 4: Run the test — expect PASS.** Apply the migration to the local db first: `supabase db reset` (applies migrations), then `pnpm vitest run tests/db/foundation.test.ts`.
- [ ] **Step 5: Commit:** `feat(db): foundation schema — orgs, members, audit, recursion-safe RLS helpers`.

---

### Task 3: Supabase client factories

**Files:**
- Create: `src/shared/lib/supabase-server.ts`
- Test: `tests/lib/supabase-server.test.ts`

**Interfaces:**
- Produces: `createServerClient()` (RLS-enforced, user JWT from cookies) and `createServiceClient()` (service-role key, bypasses RLS — for meta/config only). Later tasks import both from `@shared/lib/supabase-server`.

- [ ] **Step 1:** Write failing test asserting `createServiceClient()` uses `SUPABASE_SERVICE_ROLE_KEY` and `createServerClient()` does not (mock `@supabase/ssr`).
- [ ] **Step 2:** Run — expect FAIL (module not found).
- [ ] **Step 3:** Implement both factories using `@supabase/ssr` `createServerClient` for the user client (cookie-based) and `@supabase/supabase-js` `createClient` with the service key for the service client. Guard: `createServiceClient` throws if `SUPABASE_SERVICE_ROLE_KEY` is unset.
- [ ] **Step 4:** Run — expect PASS.
- [ ] **Step 5:** Commit: `feat(lib): supabase server + service client factories`.

---

### Task 4: Org-context resolution + middleware

**Files:**
- Create: `src/shared/lib/org-context.ts`, `src/middleware.ts`
- Test: `tests/lib/org-context.test.ts`

**Interfaces:**
- Consumes: `createServerClient` (Task 3).
- Produces: `getOrgContext(): Promise<{ orgId: string, role: string } | null>` — resolves the caller's org+role from the authed user's first active `org_members` row (or the `x-org-slug` header when present); `getOrgSlugFromHeaders(): string | null`.

- [ ] **Step 1:** Failing test: given a mocked authed user with a membership `{orgId:'o1', role:'admin'}`, `getOrgContext()` returns `{orgId:'o1', role:'admin'}`; with no membership returns `null`.
- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Implement `getOrgContext()` (auth.getUser → query `org_members` filtered by `x-org-slug`→org if header set, else first row). Implement `middleware.ts` extracting subdomain → `x-org-slug` request header + calling supabase session refresh. (Mirror ihsanos `middleware.ts` + `org-context.ts`.)
- [ ] **Step 4:** Run — expect PASS.
- [ ] **Step 5:** Commit: `feat(lib): org-context resolution + subdomain middleware`.

---

### Task 5: ActionResult contract + audit writer

**Files:**
- Create: `src/shared/lib/result.ts`, `src/core/audit/api.ts`
- Test: `tests/core/audit.test.ts`, `tests/lib/result.test.ts`

**Interfaces:**
- Produces: `type ActionResult<T> = { data: T|null, error: {code:string,message:string}|null }`; `ok<T>(data): ActionResult<T>`; `err(code,message): ActionResult<never>`; `appendAudit(client, {orgId, actor, action, entity, payload}): Promise<void>` — computes `hash = sha256(prev_hash || canonical(payload))`, inserts into `audit_log`.

- [ ] **Step 1:** Failing test: two sequential `appendAudit` calls chain — the second row's `prev_hash` equals the first row's `hash`.
- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Implement `result.ts` (types + `ok`/`err`) and `appendAudit` (select latest hash for org → compute new hash → insert).
- [ ] **Step 4:** Run — expect PASS.
- [ ] **Step 5:** Commit: `feat(core): ActionResult contract + hash-chained audit writer`.

---

### Task 6: Module registry

**Files:**
- Create: `src/shared/lib/modules.ts`
- Test: `tests/lib/modules.test.ts`

**Interfaces:**
- Produces: `CORE_MODULES: string[]` (always-on: people, reports, team, audit, settings); `ALL_MODULES: string[]` (toggleable: onboarding, exams, lms, hr, attendance, assets, incidents); `MODULE_DEPENDENCIES: Record<string,string[]>` (e.g. `lms: ['exams']`); `ROUTE_MODULE_MAP: Record<string,string>` (route prefix → module); `DEFAULT_ROLE_PERMISSIONS: Record<string, Record<string,'full'|'read'|'none'>>`.

- [ ] **Step 1:** Failing test: `MODULE_DEPENDENCIES` has no cycles and every dependency is in `ALL_MODULES ∪ CORE_MODULES`; every `ROUTE_MODULE_MAP` value is a known module.
- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Implement `modules.ts` with the constants above (seed roles: org_admin, instructor, trainee, assessor, viewer).
- [ ] **Step 4:** Run — expect PASS.
- [ ] **Step 5:** Commit: `feat(lib): module registry (core/all modules, deps, route map, default perms)`.

---

### Task 7: Permission matrix migration + resolver

**Files:**
- Create: `supabase/migrations/002_module_permissions.sql`, `src/shared/lib/module-permissions.ts`
- Test: `tests/lib/module-permissions.test.ts`

**Interfaces:**
- Consumes: `modules.ts` (Task 6), `createServerClient`/`createServiceClient` (Task 3).
- Produces: table `org_role_permissions(org_id, role, module, access text check (access in ('full','read','none')), unique(org_id,role,module))`; org module-config stored in `organizations.settings.modules` (jsonb `{[module]: boolean}`); `getVisibleModules(orgId, role): Promise<string[]>` = intersection of org-enabled modules and role-perms `!== 'none'`, lazily seeded from `DEFAULT_ROLE_PERMISSIONS`.

- [ ] **Step 1:** Failing test: for an org with `exams` enabled and role `trainee` perm `exams:read`, `getVisibleModules` includes `exams`; with `exams` org-disabled it does not; a role with `exams:none` never sees it.
- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Write `002_module_permissions.sql` (table + RLS: `org_id in (select auth_user_org_ids())`). Implement `getOrgModuleConfig`, `getRolePermissions` (lazy-seed from defaults on first read via service client), `getVisibleModules`.
- [ ] **Step 4:** Run — expect PASS.
- [ ] **Step 5:** Commit: `feat: org_role_permissions matrix + getVisibleModules resolver`.

---

### Task 8: Action gate + module gate

**Files:**
- Create: `src/shared/lib/action-gate.ts`, `src/shared/lib/module-gate.ts`, `src/actions/_example-ping.ts`
- Test: `tests/lib/action-gate.test.ts`

**Interfaces:**
- Consumes: `getOrgContext` (Task 4), `getVisibleModules` (Task 7), `ActionResult` (Task 5).
- Produces: `gateAction(module, required: 'full'|'read'): Promise<{orgId,role} | ActionResult<never>>` (returns the ctx if permitted, else an `err('FORBIDDEN', ...)` ActionResult); `requireModule(module)` for pages (calls `notFound()` if not visible). `_example-ping.ts` is a `"use server"` action gated on a core module returning `ok('pong')` — proves the stack; deleted when the first real module lands.

- [ ] **Step 1:** Failing test: `gateAction('exams','full')` for a role without exams access returns `{error:{code:'FORBIDDEN'}}`; with access returns `{orgId,role}`.
- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Implement `gateAction` (resolve ctx → getVisibleModules → check access level) and `requireModule` (notFound on miss). Implement `_example-ping` action.
- [ ] **Step 4:** Run — expect PASS.
- [ ] **Step 5:** Commit: `feat(lib): action-gate + module-gate + example gated action`.

---

### Task 9: Module-boundary lint

**Files:**
- Create: `scripts/lint/check-module-boundaries.mjs`
- Modify: `package.json` (add `"lint:boundaries"` script + wire into `pnpm test`/CI)
- Test: `tests/lint/boundaries.test.ts`

**Interfaces:**
- Produces: a lint that scans `src/modules/*` imports and fails if any file imports from a sibling `src/modules/<other>` (Rule A) or a sibling `src/app/dashboard/<feature>` imports another feature's internals (Rule B). Exit non-zero on violation.

- [ ] **Step 1:** Failing test: given a fixture where `src/modules/a/x.ts` imports `@modules/b/y`, the checker reports one violation; with no cross-imports, zero.
- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Implement the checker (walk files, parse import specifiers, apply Rules A/B). Add `"lint:boundaries": "node scripts/lint/check-module-boundaries.mjs"` and call it from the test/CI script.
- [ ] **Step 4:** Run — expect PASS; run `pnpm lint:boundaries` on the real tree — expect 0 violations.
- [ ] **Step 5:** Commit: `feat(lint): machine-enforced module boundaries (Rule A/B)`.

---

### Task 10: Dashboard shell proving the platform hosts a module

**Files:**
- Create: `src/app/layout.tsx`, `src/app/dashboard/layout.tsx`, `src/app/dashboard/page.tsx`, minimal `src/shared/ui/nav.tsx`
- Test: `tests/e2e/dashboard.spec.ts` (Playwright)

**Interfaces:**
- Consumes: `getOrgContext` (Task 4), `getVisibleModules` (Task 7), `requireModule` (Task 8).
- Produces: a dashboard layout that renders nav from `getVisibleModules(orgId, role)` and enforces a deny-by-default route allowlist (routes not in `ROUTE_MODULE_MAP` for the role → `notFound()`). Proves an empty-but-correct platform an engineer can hang a real module on.

- [ ] **Step 1:** Failing e2e: a seeded `org_admin` sees the core-module nav items; a `trainee` with only `exams:read` does NOT see admin-only items; an un-enabled module's route 404s.
- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Implement the layouts + `nav.tsx` (server component reading `getVisibleModules`), the deny-by-default allowlist in `dashboard/layout.tsx`, and a placeholder `dashboard/page.tsx`.
- [ ] **Step 4:** Run — expect PASS.
- [ ] **Step 5:** Commit: `feat(app): dashboard shell — module-driven nav + deny-by-default routing`.

---

## Self-Review

- **Spec coverage (vs vision §4 target architecture, the 5 deltas):** (1) org_id + RLS → Tasks 2, 7; (2) role on org-membership → Task 2 (`org_members`) + Task 4; (3) module registry × role matrix → Tasks 6, 7, 8; (4) machine-enforced boundaries → Task 9; (5) server-action layer + audit + ActionResult → Tasks 3, 5, 8. Jurisdiction siloing → Global Constraints + `organizations.region` (Task 2) + `NEXT_PUBLIC_JURISDICTION` (Task 1). Covered.
- **Placeholder scan:** SQL/TS crux code is inline (Tasks 2, 5, 8). Tasks 3/4/9/10 give exact interfaces + test specs and name the ihsanos reference file to mirror — the port lane has that repo loaded; where a step says "mirror ihsanos X", the exact target file is named. No "TBD"/"handle edge cases".
- **Type consistency:** `ActionResult<T>` (Task 5) is the return type gated actions use (Task 8); `getOrgContext` shape `{orgId,role}` (Task 4) is consumed unchanged by Tasks 8/10; `getVisibleModules(orgId,role)` (Task 7) consumed by Tasks 8/10. Consistent.
- **Note:** this plan is the FOUNDATION only. The onboarding-port and exam-port plans (separate docs) depend on it and consume its interfaces (`gateAction`, `requireModule`, `appendAudit`, the module registry). They are authored next, grounded in the current-code discovery.
