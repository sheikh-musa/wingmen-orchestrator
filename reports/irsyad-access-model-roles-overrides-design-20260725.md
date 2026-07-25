# Access-model design — clean roles + per-user overrides (platform-wide)

**Date:** 2026-07-25 · **Author:** cc-orchestrator (hub) · **Repo:** ihsanos (shared frontend) · **Scope:** all client silos (first application: irsyad / goumlyne `goumlynecruxrlmzlntp`)
**Status:** DESIGN PROPOSAL — needs operator + **cai** approval before any build. Money-silo access model → cai's residency/least-privilege domain. No schema change ships without the gate.
**Origin:** operator thread op#6919/6924/6930 — Elly's `preparer` role became a grab-bag (bank_import + donations + pos + tabung); root cause = we widened a SHARED role per-request over time. Operator asked: should access be per-user-granular instead of static roles?

## The problem (grounded)
- Today's model: `org_members` (user → one `role`) + `org_role_permissions` (role → module → `full|read|none`) + org module on/off config. Modules: `bank_import, donations, hr, inventory, invoicing, orders, pos, qurban, school, storefront, tabung`.
- Failure mode observed: to give Elly bank_import + tabung, the shared `preparer` role was widened — and it now also carries `pos:full` + `donations:full` she shouldn't have. Every future preparer inherits the bloat. Grab-bag by accretion.
- Edits ARE audited (`writeAuditLog`, entity_type `org_role_permission` / `org_member`) — but nothing prevents the accretion, and there's no per-person tailoring short of minting/mutating a role.

## Recommendation: roles-as-baseline + per-user overrides (NOT pure per-user)
Pure per-user granular grants (drop roles, admin hand-picks every module per person) were considered and **rejected**: they don't scale or stay auditable — every user becomes a snowflake, onboarding rebuilds access from scratch, and over-grants get *harder* to catch (no baseline to diff against). Roles didn't cause the grab-bag; widening a *shared* role did.

**Two layers:**
1. **Clean role baselines** — each role = exactly one job, least-privilege. One-click assign on invite. Auditable ("who are the preparers?").
2. **Per-user overrides** — a new `org_member_permission_overrides` layer (user → module → access) that overrides the role default *for that one person*. Granular where a person genuinely needs a non-standard combo; templated everywhere else.

**Effective access resolution** (server-side, single resolver — reuse the existing `getEffectiveAccess` path):
`effective(user, module) = override(user, module) ?? roleDefault(user.role, module) ?? none`, then AND with org module-on/off config. One function, fully tested, used by every gate.

## Proposed clean role catalog (irsyad, least-privilege — confirm per client)
| role | modules @ access | one-line job |
|------|------------------|--------------|
| `org_admin` | all `full` + settings | owner/administrator |
| `cashier` | pos, orders, inventory `full` · tabung/donations `full` | front-counter sales + collections |
| `preparer` | **tabung, bank_import `full`** (NO pos, NO donations unless confirmed) | tabung reports + bank reconciliation |
| `viewer` | donations, tabung `read` | read-only oversight |
| `hr_manager` | hr `full` | payroll/HR only |
| `teacher` | school `full` | class/school only |
| `parent` | qurban, school `read` | portal read |
(Existing roles keep their names; **`preparer` gets slimmed** — that fixes Elly. Elly = preparer + a per-user override for **giro** once Gazzabyte confirms what giro means. This is the FIRST real use of the override layer.)

## Guardrails (money-silo — cai to ratify)
- **Money-bearing modules** = `donations, bank_import, tabung, invoicing, orders, pos`. Widening any of these (role default OR per-user override) → **audit + alert the fleet** (bus row to cc-orchestrator/cai), not silently. Optional: require a short justification string on the write.
- Role **definition** edits on money modules: keep behind org_admin AND surfaced to us via the alert (operator leaning "allow + alert" over hard-lock — op#6919/audit finding: definition edits are already audited, so allow-with-trail is viable).
- Overrides can only *narrow or match* what the org module-config allows; can't re-enable a module the org disabled.
- Every override carries actor + timestamp + reason; PII (emails) never logged to the bus.
- RLS on the new table; REVOKE anon/authenticated table grants, RLS-scoped read only ([[pii-table-verify-grants-not-just-rls]]).

## Schema (additive, reversible)
- `org_member_permission_overrides(id, org_id, user_id, module, access, reason, created_by, created_at)` — unique `(org_id, user_id, module)`. Additive; absent row = fall through to role default (zero behaviour change for every existing user on day one).
- No change to `org_members` / `org_role_permissions`.
- Apply via direct psycopg `--expect-ref`, synthetic-first, **cai-gated**, both refs (ceayj + goumlyne parity), NEVER `db push`.

## Migration path (no big-bang)
1. Ship schema (overrides table, empty) — inert, nothing changes.
2. Update the effective-access resolver to consult overrides — behaviour identical while table empty (regression-proof).
3. Slim the grab-bag roles to clean baselines (per client, cai-gated) — **this is where Elly's fix lands**; verify no live user loses needed access first (data-drive each affected user).
4. Admin UI: per-user override editor on the member page (org_admin), with the money-module alert wired.
5. (Later) role-catalog management UI if clients want to self-serve new roles.

## Partner view-as + scoped super-role (op#6954)
Operator wants Gazzabyte (PARTNER managing irsyad) to log in and "view as any role" to understand each user's UX.
- **Residency trap — do NOT grant the existing platform super-admin** (`platform_admins` / `/super-admin`): that is OUR cross-tenant god-mode (sees every client's org). Gazzabyte must be **irsyad-org-scoped only**. Because goumlyne holds ONLY irsyad data, an org-level super/admin role INSIDE goumlyne is naturally contained — no cross-client exposure. Reuse `org_admin` (or a thin `org_super`) in the irsyad org; never `platform_admins`.
- **VIEW-AS (recommended, read-only)** — a role-preview: pick a role → render the dashboard with THAT role's effective permission set (reuse the effective-access resolver), read-only, persistent "Viewing as <role>" banner, no writes. Serves the "understand what they see" intent. Every view-as session audited (actor=real Gazzabyte user, role_previewed, timestamp).
- **ACT-AS (defer unless needed)** — actually performing actions as another user muddies the money audit trail + dual-control. If ever added: hard-attribute every action to the REAL actor (Gazzabyte admin impersonating X), NEVER silently as X; block on money/approval actions. Default build = view-as only.
- No view-as/impersonation feature exists today (verified) — new build. cai-gated (PII + access surface on client silo): scope-containment + the audit are the sign-off items.

## Open items
- Confirm the clean catalog per client (irsyad first) — esp. does `preparer` need `donations`? (ask Gazzabyte alongside giro).
- cai: ratify the money-module guardrail (allow+alert vs. lock) + the both-ref apply.
- Sequencing vs. the op#6834 tabung email workflow — independent; can run in parallel.

## Not blocking Elly
Elly's clean role (slim `preparer` + giro override) ships on the existing model the moment Gazzabyte confirms giro + cai signs off — it does NOT wait on the full overrides build. The overrides layer just makes the giro-override the clean way to grant her that one extra without touching the shared role.
