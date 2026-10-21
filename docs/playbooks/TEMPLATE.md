# Playbook template — build-rigor track (cc-quality, op#32734/32735)

> A doc-only playbook is just another thing to remember — and "remember" is the
> failure mode this whole track exists to kill. **Every playbook MUST name an
> enforcing hook** (existing or to-build) that survives a context reset. The doc
> explains the *why* and the residual human steps the hook cannot enforce; the
> hook is what actually holds the line.

Each canonical playbook is one file, `NN-playbook-<slug>.md`, with these sections
in this order. Keep it to a screen or two — if it sprawls, it won't be read.

---

## `<id>` — `<Title>`
`version:` N  ·  `owner:` cc-quality  ·  `last-reviewed:` YYYY-MM-DD  ·  `risk:` P0|P1|P2

### 1. WHEN THIS FIRES (the trigger)
The concrete signal that this playbook applies — a shape a building lane can
recognise *before* acting (e.g. "you are about to change a `.sql` under
`migrations/`", "you are about to Read a file under `logs/tg_media/`"). One
sentence. If a lane can't tell from this line whether the playbook applies, the
trigger is wrong.

### 2. THE FAILURE IT PREVENTS
The real incident(s) this came from — bus id / op# / the audit that caught it,
and the concrete harm (prod damage, PII leak, silent false-green). This is the
*why*; without a named failure the step is just ceremony (standing rule:
`audit-standing-rules-ratified` — a rule-comment needs a constraint).

### 3. STEPS (each with its why)
Numbered, imperative, each line ending in the failure that step blocks. Order =
execution order. A step that only a human can do is marked **[manual]**; a step
the hook enforces is marked **[hook: <name>]**. No step without a reason.

### 4. ENFORCING HOOK
- **Hook:** `<path>` — EXISTING or **TO-BUILD**.
- **What it enforces:** the exact precondition it blocks on + its refuse behavior
  (exit code / stderr the model sees).
- **What it CANNOT enforce (residual):** the steps above that remain human
  discipline — named explicitly, so the gap is visible, not pretended away.
- **To-build hooks** carry: trigger shape, refuse condition, false-positive risk,
  and a test vector file — they ship as their own reviewed+tested PR, never
  hand-waved.

### 5. DISCOVERY (how a lane loads this at boot)
How a building lane finds this without already knowing it exists: (a) the index
`README.md` entry (loaded/pointed-to at boot), (b) a one-line pointer in the
relevant CLAUDE.md, and (c) — the load-bearing one — the enforcing hook's refuse
message **cites this playbook's id**, so hitting the guard teaches the playbook.

### 6. PROVENANCE
The memories + bus rulings this consolidates (`[[memory-slug]]`, bus #ids). This
is the audit trail from scattered→canonical, and what to re-check if a cited
file:line drifts.

---

### Authoring rules
1. **No playbook without a hook.** If nothing can enforce it yet, the playbook's
   deliverable INCLUDES the to-build hook spec; it is not "done" as doc-only.
2. **Map existing-vs-gap honestly.** Section 4 must state what the hook already
   covers AND the residual it cannot — never imply full coverage.
3. **Versioned.** Bump `version` + `last-reviewed` on every change; the index
   tracks the current version so a lane knows if its cached copy is stale.
4. **Verify-not-assert.** Every cited hook/file:line is checked against the repo
   at authoring time (a name is not an implementation).
