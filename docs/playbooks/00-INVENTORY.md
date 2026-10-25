# Inventory — recurring high-risk build ops (build-rigor track)

cc-quality · op#32734/32735 · 2026-10-22 · for orch-console's approach gate (#73173)

**Method:** mined CLAUDE.md (substrate/security rules), the cc-quality
feedback/reference memory corpus (~60 files, the "Audit technique" + "Substrate
security" + "Ops/process" sections), and the live hook set under
`scripts/hooks/` + both `.claude/settings.json` registrations. A row is here
because it is a **recurring** high-risk build op we keep re-catching *reactively*
in review — the thing a canonical playbook + hook would catch *by default*.

**Hook coverage legend:** 🟢 enforced by a live hook · 🟡 partial (hook catches
one facet, rest is review) · 🔴 no hook (review-time only — the real gap).

| # | High-risk build op | Keeps biting (provenance) | Caught today by | Cover |
|---|---|---|---|---|
| **1** | **Migration / DDL apply to prod** — report-before-DDL, scratch-rehearse, equivalence/deny-proof, direct-psycopg `--expect-ref`, never `supabase db push`, `-v ON_ERROR_STOP=1`, append-above-ROLLBACK, invariant-check each run | `prod-damage-autocommit-append`, `plpgsql-body-not-column-validated-at-apply`, `reaudit-diff-against-live-not-named-migration`, CLAUDE.md substrate rules | `secrets_transcript_guard.py` → `ddl_detect.is_ddl_shaped` **requires `--gate <bus-id>`** → routes to `apply_migration.py --gate --expect-ref`; `gate_validation.py` | 🟢 **→ PLAYBOOK 01** |
| **2** | **Client-data / residency path** — residency gate, negative-control (deny-proof direct write), LAYER-VOCAB naming, stage-before-read, auditor_ro RO-DSN model | `client_media_read_guard` incident (#52461), `ihsanos-pii-money-review-boundary`, `cai1231-auditor-ro`, `mamadah-content-free-envelope-residency` | `client_media_read_guard.py` (raw media Read), `lane_prod_key_guard.py` (prod key) | 🟡 **→ PLAYBOOK 02** |
| **3** | **Secrets handling** — never display a real secret, hash/length sink, no DSN-as-positional-arg | `never-fetch-real-secrets-during-review`, CLAUDE.md | `secrets_transcript_guard.py` (pre), `secrets_output_scanner.py` (post) | 🟢 |
| **4** | **RLS / grant changes** — RLS-UPDATE can't restrict columns (need column-GRANT/SECDEF); new-table default REVOKE anon,authenticated; REVOKE owner-vs-applier no-op; full-tuple policy diff; DROP+CREATE ACL net of default-privs | `rls-update-policy-needs-column-grant-or-secdef`, `new-table-default-grants-checklist`, `revoke-migration-owner-vs-applier-silent-noop`, `rls-policy-diff-full-tuple-not-just-qual`, `drop-create-function-acl-net-of-default-privileges` | review-time only | 🔴 (candidate playbook 03) |
| **5** | **New lifecycle-state column / enum** — sweep ALL readers (incl. files not in the PR), status-filter + column-GRANT | `new-lifecycle-state-column-sweep-all-readers`, `completeness-claims-enumerate-full-domain` | review-time only | 🔴 (fold into 03/04) |
| **6** | **`'use server'` module hygiene** — no `export type { X }` re-export (Next16 Turbopack SSR 500); use-server exports only async fns | `use-server-type-reexport-ssr-crash` | review-time only (build-green is NOT a detector) | 🔴 (candidate lint rule) |
| **7** | **Deployment completeness** — merged≠live; read origin/main not local; rename-of-a-DB-match-string needs a live data migration + UPSERT-orphan cleanup; verify var in RUNNING process env | `deployment-completeness-lens`, `deploy-state-read-origin-main-not-local-checkout` | review-time only | 🔴 (candidate playbook) |
| **8** | **Test rigor** — lint:all + full unit suite BOTH blocking; run the DESTRUCTIVE branch; enumerate full domain not a sample; verify computed value is RENDERED; prove live not static | `audit-bar-includes-lint-gates`, `cosem-adcda-archive-async-job`, `verify-computed-value-is-rendered`, `weekFailed-flag-drop` | CI (per-repo), not fleet-uniform | 🟡 |
| **9** | **Bus / attribution hygiene** — `bus_send.py` not raw INSERT; `--reply-to` mandatory (atomic responded_at); verify-then-stamp, never blind-stamp | `use-bus-send-py-not-raw-insert`, `reply-to-omission-recurs` | `bus_send.py` WARN-mode (soft) | 🟡 |
| **10** | **Operator-facing list/table UI** — any list of actionable rows ships bulk actions by default (select-all + multi-select + bulk action w/ count + confirm); build for real volume not one row (what-if-100); obvious operator actions + empty/loading/error states present | `frontend-ux-completeness-bulk-actions`, op#33974 (ADCDA 33-rows-33-clicks), `render-pack-every-refusal-and-role`, `mobile-first-screenshot-standing-rule-op27086` | review-time only (heuristic WARN-lint candidate) | 🔴 **→ PLAYBOOK 07** |

> **Row 10 added post-approval** (op#33974 Musa IHSAN flag → orch-console #76194) — a NEW
> class beyond the original substrate/data/security inventory: review-time-only, caught
> reactively (Musa himself), the exact "followed-by-default vs caught-reactively" gap.

## Proposed canonical set (the "small set")
Not one-per-row — consolidate. Proposed **6 canonical playbooks**:
1. **Migration / DDL apply** (rows 1) — *drafted, this gate*.
2. **Client-data / residency path** (row 2) — *drafted, this gate*.
3. **RLS / grants / lifecycle-column change** (rows 4, 5) — the review-time SQL-security cluster; highest-value 🔴 gap.
4. **Ship-to-live completeness** (rows 7, 8) — merged≠live + the test-rigor bar.
5. **Secrets & transcript hygiene** (row 3) — mostly 🟢 already; playbook documents the contract the hooks enforce + the residual.
6. **Framework footguns** (row 6 + future) — use-server/SSR + the next framework trap we catch twice.
7. **Frontend UX-completeness** (row 10, added op#33974) — operator-scale actions: bulk-by-default + what-if-100. Review-time lens + heuristic WARN-lint.

Bus/attribution hygiene (row 9) is process, not a build op — it stays in CLAUDE.md
+ the `bus_send.py` WARN, not a playbook.

## The real gap this track closes
Rows 1–3 are already hook-enforced (the rigor "survives a reset"). Rows **4–7 and
10 are 🔴 review-time only** — caught when a reviewer happens to be in the loop,
missed when not (row 10 was caught by the *operator*, not a reviewer — the worst case). That is precisely the "followed-by-default vs caught-reactively" problem
op#32734 names. The playbooks make the rigor discoverable; the **to-build hooks
for rows 4–7** are where this track adds enforcement that doesn't exist today —
each shipped as its own reviewed+tested PR, prioritized by the risk column.
