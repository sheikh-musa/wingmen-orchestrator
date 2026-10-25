# PB-07 — Frontend UX-completeness (operator-scale actions)
`version:` 1  ·  `owner:` cc-quality  ·  `last-reviewed:` 2026-10-25  ·  `risk:` P1

### 1. WHEN THIS FIRES
You are rendering a **list or table of actionable rows** in an operator/admin
surface — each row carries an action (archive, approve, reject, delete, assign,
retry, resend, …) and the collection can realistically hold more than a handful
of rows. The signal: a `.map()` / loop that renders a per-row action control. If
the row has a verb, this playbook applies.

### 2. THE FAILURE IT PREVENTS
- **Built for one row, used at real volume.** The ADCDA unclaimed-trainers panel
  shipped with **per-row archive only** — no select-all, no bulk action — so
  clearing 33 trainers was **33 clicks**. (op#33974; fix delegated
  cc-cosem-adcda-1 bus #76177; track ask orch-console bus #76194.) The code was
  "correct" and green; it was *operationally* broken at the volume the screen
  actually sees.
- **An operator catching a basic UX gap himself is trust-erosion** — the same
  class as a dropped ask. When Musa has to notice "why can't I select all of
  these," the build has failed a quality bar even though every test passed. "No
  ihsan in it" is the operator's name for this failure.
- **Demo-scale fixtures hide it.** A one-row or three-row test fixture renders
  fine with per-row-only actions; the pain only appears at 30/100/300 rows, which
  no unit test and no happy-path render pack exercises. Green ≠ usable-at-scale.

### 3. STEPS
1. **Bulk actions by default for any actionable list.** Ship **select-all +
   multi-select + a bulk-action control that shows the selected count**, and a
   **confirm step for any destructive bulk op**. If a row can be archived
   one-by-one, it can be archived in bulk. **[manual]** *(Blocks: N-clicks-for-N-rows.)*
2. **The what-if-100 test.** Design and *render-check* the screen at realistic
   volume (tens–hundreds of rows), not the one row in the demo fixture — a bulk
   control, and either pagination/virtualization or an explicit "showing N of M"
   when the list can grow unbounded. **[manual]** *(Blocks: demo-scale-only UI
   that janks or truncates silently on live data.)*
3. **The obvious operator actions a human would expect are present.** If rows
   have a status, it is filterable/sortable; a destructive single action has a
   confirm; the empty/loading/error states exist (not just the happy row). Walk
   the screen as the operator who uses it daily, not as the builder who tested one
   row. **[manual]** *(Blocks: "technically functional, operationally painful.")*
4. **Evidence at volume, not at one row.** The render pack / screenshot proof must
   show the multi-row state WITH the bulk control, at 390px and desktop (ties to
   the mobile-first standing rule, op#27086) — a single-row shot is "not
   captured." **[manual]** *(Blocks: a green pack that never exercised the bulk
   path.)*

### 4. ENFORCING HOOK (honest: review-time dimension; heuristic WARN-lint candidate)
- **This is a 🔴 review-time judgment**, like PB-03's adequacy checks — "is the
  volume realistic," "is this the action an operator expects," "does it hold at
  100 rows" are **not** deterministically decidable from a static diff, so there
  is **no fail-closed pre-tool hook**. It lands primarily as a **review-recipe
  lens** (section 5) and a FINDING-by-default on any actionable-list PR.
- **TO-BUILD 🟡 — a heuristic WARN-lint (its own reviewed+tested PR, not
  hand-waved):** `scripts/frontend_bulk_action_lint.py`, a stdlib-only, no-build
  **static scan** of changed frontend render files. Over a component that renders
  a per-row **action verb** (an `onClick`/handler whose name or target matches
  `archive|delete|remove|approve|reject|assign|resend|retry|…`) inside a list
  render (`.map(`/`v-for`/`{#each}`), it emits a **LOUD WARN** (GitHub-Actions
  `::warning::`, never a buried log) when the **same component carries no
  select-all / multi-select / bulk-action token** (`selectAll`, `selectedIds`,
  `checkbox`-per-row + a count-bearing bulk control). Message: *"actionable list
  with per-row-only actions — confirm select-all + bulk action + count (PB-07), or
  justify why single-row is correct here."*
- **Why WARN, not block:** it is a **heuristic** — some lists are legitimately
  single-row-action (a one-off detail pane, a list that never exceeds ~3 rows).
  It is **false-positive-prone by construction**, so it must WARN and cite the
  playbook, never wedge a lane. Match the layer to what's decidable (orch-console
  gate doctrine #73186/#73191; same discipline as PB-03).
- **What the lint CANNOT enforce (residual, all [manual] above):** the
  what-if-100 judgment (step 2), "obvious operator actions present" (step 3), and
  volume evidence (step 4). The lint flags the *shape* (per-row verb, no bulk
  sibling); it does not prove the screen is usable at scale — that stays
  reviewer + render-pack-at-volume.
- **To-build spec carried for the lint PR:** trigger shape (list render + per-row
  action verb), refuse condition (no bulk/select-all token in the component),
  false-positive risk (small-fixed-length lists, read-only tables — hence WARN),
  and a `_vectors.json` test file (positive: per-row archive map with no
  select-all → WARN; true-negatives: a list WITH select-all+bulk; a read-only
  table with no action verb; a ≤3-row static list annotated single-row-ok).

### 5. DISCOVERY
- Index `README.md` row (PB-07); the WARN-lint annotation cites `PB-07`; and the
  **load-bearing one for the building lanes** — a line in the lane CLAUDE.md
  template / seed: *"Any list of actionable rows ships with bulk actions by
  default (select-all + bulk action + count + confirm); build for real volume, not
  one row (the what-if-100 test) — see docs/playbooks/PB-07."* This is also a
  standing **review lens** I apply by default (memory `frontend-ux-completeness-bulk-actions`).

### 6. PROVENANCE
op#33974 (Musa IHSAN flag — "no ihsan in recent builds"); bus #76194
(orch-console → cc-quality, the track ask); bus #76177 (immediate fix delegated
cc-cosem-adcda-1). Standing rules this composes with: `render-pack-every-refusal-and-role`
(happy-path-only pack = FAIL for any UI with states/roles — extended here to
volume), `mobile-first-screenshot-standing-rule-op27086` (390px+desktop shots),
`completeness-claims-enumerate-full-domain` (enumerate the real domain, not a
sample). New review-lens memory: `frontend-ux-completeness-bulk-actions`.
