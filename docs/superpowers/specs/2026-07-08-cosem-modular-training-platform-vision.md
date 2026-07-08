# COSEM → Modular Training & Operations Platform — Origin & the Year Ahead

**Status:** Design (brainstormed + operator-approved 2026-07-08). Precedes an implementation plan (writing-plans).
**Owner:** Nazim (CTO console) · **Audience:** COSEM director (Singapore — new to the apps), operator, cosem engineering lanes.
**Grounding:** two code audits (2026-07-08) — current cosem apps + the ihsanos modular pattern.
**Velocity thesis:** the roadmap below is a **one-year plan**. At AI-driven development velocity, one year delivers what three years of pre-AI development would — the compression is the story.
**Confidentiality (operator directive 2026-07-08):** director- / client-facing materials MUST NOT reference ihsanos, storefront, shipforge, or any other venture. The ihsanos pattern is named in THIS internal spec purely as the engineering template the lanes emulate; scrub it from anything the COSEM director sees.

---

## 1. Executive summary

Today COSEM ships one product per client by **cloning an entire codebase** (cosem-tdu was cloned from cosem-adcda). Each clone is a single-tenant Vite/React SPA on Firestore with a ~7k-line monolithic Cloud Functions file and no shared core — ~35–44k lines duplicated per client.

The end state is a **single modular, multi-tenant platform** — modelled on ihsanos — where every client is an *org*, every capability is a *module* toggled per client, and the **module is the unit of value**: a company can buy just the exam engine, or just HR, without needing the rest. The flagship module is an **AI-authored assessment engine** that competes with ExamView by category, not feature. The platform is **AI-native but disciplined** — AI earns its place only where it removes a repeated human cost or does the otherwise-impossible, always human-in-the-loop where stakes are high (certification, safety).

This reframes the "not enough firefighter academies" concern: the addressable market is not fire academies, it is **any company that runs courses, exams, onboarding, or a workforce**, and multi-tenancy is the only architecture that serves small clients at near-zero marginal cost.

---

## 2. The journey so far — how we got here

The platform was never a grand plan. It grew, one genuine request at a time — which is exactly why it fits real work. For an audience new to the apps, this is the arc:

1. **A simple tool for our own trainers.** Onboarding and attendance, built to solve COSEM instructors' day-to-day — nothing more.
2. **TDU wanted their own version.** We cloned the app and began tailoring it to TDU's use case (equipment/vehicle ops — charging, maintenance, defects). *Still ongoing.*
3. **Skill sheets — a single source of truth.** One authoritative record of trainee competencies, replacing scattered paper/spreadsheets.
4. **Practical skill-sheet testing.** On request, skill sheets grew from a record into an assessment tool — structured practical testing against the competency matrix.
5. **The exam module (current).** Theory + practical assessment — the capability that revealed the real opportunity.

Each step came from a real need, not a roadmap. Read together they show a pattern: **these capabilities aren't cosem-specific — they're products.** That insight is what turns "a set of internal tools" into the platform in the next section.

---

## 3. Where we are today (current state)

| Dimension | cosem-adcda | cosem-tdu |
|---|---|---|
| Frontend | Vite 7 + React 19 SPA (React Router 7), Tailwind 4, Capacitor (iOS/Android) + PWA | Same toolchain (clone) |
| Hosting | Firebase Hosting (CI); a `.vercel` link exists (mid-migration) | Firebase Hosting only |
| Backend | Firebase Cloud Functions — monolithic `functions/index.js` (~7.3k LOC) | Same (~6.8k LOC) |
| Data | **Cloud Firestore** (schema implicit via rules) | Firestore |
| Auth | Firebase Auth (Google `@cosem.org.sg` + Phone OTP + passkeys) | Same |
| Roles | role→feature maps in Firestore `config/permissions` | Same, different role vocab |
| Tenancy | **Single-tenant. No org concept.** Multi-client = clone the repo | Single-tenant |
| Size | ~44k LOC src; 134 KB monolithic CSS; in-browser TF/MediaPipe/face-api | ~35k LOC; 142 KB CSS |

**Key facts:** the two apps are ~90% identical by construction; there is **no shared library/monorepo/design system**; the apps are in **different jurisdictions** (adcda = Abu Dhabi, tdu = Singapore) with distinct data-residency regimes.

**Reframe:** "modularize like ihsanos" is **not a database migration — it is a re-platform** (Vite SPA → Next.js, Firestore → Postgres, clone-per-client → multi-tenant). The DB migration is one strand of a larger, more valuable move.

---

## 4. Target architecture (the ihsanos pattern applied)

A **modular monolith**, multi-tenant, on Next.js App Router + Supabase Postgres. The five deltas cosem must adopt:

1. **`org_id` on every row + Postgres RLS as the tenancy boundary** — cross-tenant leakage impossible-by-default via `SECURITY DEFINER` helper functions (`auth_user_org_ids()`), not per-query application discipline.
2. **Role on the org-membership join** (`org_members(org_id, user_id, role)`) — role is contextual per organization, resolved on every request.
3. **Data-driven module registry × role-permission matrix** — `getVisibleModules(org, role)` = intersection of org-level enablement and per-role `full/read/none`; no hardcoded `if (isAdmin)`.
4. **Machine-enforced modular boundaries** — code partitioned into `core` (universal entities: persons / ledger / audit) / `modules` / `actions` (server RPC) / `shared`, with a CI + pre-commit lint forbidding module→module imports.
5. **Server-action data layer** — all writes go through `"use server"` actions that re-resolve org context, gate on permission, append to a hash-chained `audit_log`, and return a uniform `{data, error}` (RLS misses surface as `NOT_FOUND`, never leaking cross-org existence).

**Jurisdiction siloing:** one codebase, **separate databases per jurisdiction** (SG vs UAE) so data residency (PDPA / UAE) stays clean. Tenancy is multi-tenant *within* a jurisdiction, siloed *across* — consistent with fleet doctrine TENANT-RESIDENCY-001.

---

## 5. The module map

The unit of value is the module. Two product lines, plus commerce (already a separate workstream on the same idea). All are grounded in code that already exists in cosem or ihsanos.

### Training line (the LMS / ERP-for-training vision)
1. **Exam & Assessment engine — FLAGSHIP.** Theory quizzes + practical assessments + skill/competency matrices. ~80% already built in adcda (`ScdfTheory`, practical assessments, skill sheets). The ExamView wedge.
2. **Onboarding.** Self-serve enrolment, identity/ID capture (OCR), allowlists. Useful for any course or company; natural companion to exams.
3. **LMS core.** Courses, content delivery, progress tracking, certification — the wrapper that turns 1+2 into a full student LMS.

### Operations line (any company)
4. **HR / workforce.** Staff records, rostering, part-timers (TDU's flow, generalized; ihsanos `hr` module as template).
5. **Attendance.** Geofence / face / self check-in. Standalone or inside HR/LMS.
6. **Asset & equipment ops.** Inventory, maintenance, defects, charging logs (TDU). Any org with fleets/gear/labs.
7. **Incident & safety reporting.** Observations + incident templates. Construction, security, manufacturing, healthcare.

*(Other verticals — e.g. commerce — can ride the same modular platform later; kept out of COSEM-facing materials per the confidentiality note above.)*

---

## 6. AI thesis — "the engine, not a badge"

**Test for every AI touch:** does it kill a repeated human cost, or do something impossible without it? If neither, it does not ship. High-stakes AI (certification, grading, safety) is **always human-in-the-loop.**

1. **AI-authored assessment (the moat).** Point the exam engine at a syllabus / manual / SOP → it drafts the question bank (MCQs, distractors, difficulty-tagged) for instructor approval; grades free-text against a rubric; flags practical-assessor drift from the norm. This is a different category from ExamView, and it alone justifies the flagship.
2. **Closed learning loop.** From results → targeted remediation (what was wrong, what to study next) + a tutor **grounded (RAG) in the client's own course material** — not a generic chatbot.
3. **Run it by chat.** Conversational management: set up an exam, ask "how did cohort 3 do on pump-ops", or onboard a new client by *describing* the course → the platform scaffolds the module config + seeds the question bank. This is what makes multi-tenant onboarding nearly free — the core economic thesis.
4. **Ops intelligence.** AI-assisted incident/observation drafting from notes + photos; pattern detection across incidents (surfaces recurring safety issues).

**Takalluf exclusions (deliberately NOT built):** a generic chatbot in the corner; AI content/grades shipped without human review where stakes are high; "predictive" anything before the data exists; "AI-powered" badges on plain forms.

---

## 7. Transition strategy — strangler, not big-bang

Rebuilding two live products at once is reckless. Approach:

- **Strangler pattern.** Stand up the new Next.js + Postgres platform alongside the live Firestore apps. Build *new* modules on the new stack; migrate existing modules one at a time; the old app shrinks until it is retired.
- **Flagship-first.** The exam engine is the first module built new (highest value, mostly-built domain logic to port, cleanest bounded context).
- **Data migration per module.** Firestore collections → Postgres tables with `org_id`, migrated module-by-module (not one big dump), each with a verified parity check (mirrors the fleet's migration discipline).
- **Jurisdiction-siloed from day one** — provision SG and UAE databases separately; never commingle client rows (residency gate before any client write, cai-reviewed).
- **Frontend overhaul is inherent** (SPA → Next.js RSC/server actions), which also fixes the current client-weight problems (in-browser ML, 130 KB+ CSS) by moving heavy compute server-side and rendering on the server.

---

## 8. One-year roadmap (AI-accelerated)

A **one-year** plan. At AI-driven development velocity this delivers what a pre-AI team would spend roughly **three years** on — that compression is the point, and the credibility for it is the journey in §2 (an exam module already stands where a traditional team would still be scoping).

**Q1 — Foundation.** Multi-tenant core (orgs, memberships, RLS helpers, module registry, action-gate, audit) on Supabase; jurisdiction-siloed DBs; the modular-monolith skeleton + boundary lint; shared design system. → An empty-but-correct platform that can host a module.

**Q2 — Flagship.** The Exam & Assessment engine, built new and AI-native (AI question-authoring + rubric grading, human-reviewed). Onboarding module. ADCDA runs its theory/practical exams on the new stack in parallel with Firestore. → A sellable exam product + first paying non-cosem pilot.

**Q3 — Suite.** LMS core (courses/progress/certification) wraps exams; HR/workforce module generalized from TDU; conversational management + NL analytics. TDU migrates onto the platform. → Full training suite + onboarding-by-chat proving near-zero marginal cost per client.

**Q4 — Breadth & scale.** Attendance, asset/equipment ops, incident/safety modules + ops intelligence. Retire the Firestore apps. Harden for many small tenants. → The full modular ERP for training + operations, several clients across verticals.

*(Quarter boundaries are directional; each gets its own implementation plan.)*

---

## 9. Risks & open decisions

- **Residency across jurisdictions** — SG vs UAE siloing must be settled before any cross-tenant feature (cai gate).
- **Live-product continuity** — the strangler must never break adcda/tdu in production during migration.
- **AI cost/quality** — question-authoring quality gates + human review; monitor token cost per tenant.
- **Market validation** (operator-owned) — the ExamView-competitor thesis and the generalized-module TAM are business bets, not engineering facts.
- **Open:** which non-cosem pilot client to target first for the exam engine; build-vs-buy for any commodity module.

---

## 10. Success metrics

- **Marginal cost per new client** trends toward ~zero (multi-tenant onboarding replaces repo-cloning).
- **Time to onboard a new client/course** (target: hours, via describe-the-course scaffolding — vs weeks of cloning today).
- **Exam-authoring time** cut by an order of magnitude (AI-drafted + reviewed vs hand-written).
- **# of clients / verticals** served on one codebase (vs # of forks maintained).
- **Zero cross-tenant data incidents** (RLS-enforced).
