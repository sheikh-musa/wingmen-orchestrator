# Governance Floors — SINGLE SOURCE OF TRUTH (SSOT)

> **THIS FILE IS AUTHORITATIVE.** It is the ONE canonical "who signs what" record for the
> substrate. Where a `CLAUDE.md` line, a handoff note, a memory card, or any prose anywhere
> disagrees with a row below, **THIS FILE WINS** and the prose is stale — fix the prose, do
> not re-assert the stale floor.
>
> **Scope split (two SSOTs, no overlap):** this file is the SSOT for *who signs what* (floors,
> owner-sign, escalation). For the exact DATA store `project ref` of any client, defer to the
> other SSOT, **`docs/data-store-registry.md`** (LAYER-VOCAB-001). This file names the store by
> its **layer + alias** and points at the registry for the ref — it never inlines a client-silo
> ref (that keeps the floors doc safe to author/read from any body).
>
> **CONSULT-BEFORE-ASSERT (enforced in code).** Before you assert a governance floor or a
> sign-off authority — in a bus row, an operator reply, a handoff, or a doc — you MUST have
> read this file in the session. `scripts/hooks/consult_governance_floor_guard.py` (a
> PreToolUse hook) fires on floor-assertion language and points you here first. This exists
> because a floor was asserted from stale memory without consulting the record (see
> *Incident log* below). Enforce-in-code, not by promise
> ([[feedback_enforce_process_in_code_not_promises]]).
>
> **Last-verified: 2026-10-15** (console-ratified; given as ground truth by the console body).
> A floor is only as trustworthy as its last-verified date — re-verify against the owner
> before relying on an older row ([[feedback_a_verification_has_an_expiry]]).

---

## How to read a row

Each row is: **DOMAIN → FLOOR (who/what gates) → OWNER-SIGN → ESCALATION TARGET → BINDING cai-ratified constants**.

- **FLOOR** = the standing bar that does NOT drop. Meeting it is necessary but not sufficient.
- **OWNER-SIGN** = whose request/approval IS the authorization once the floor is met.
- **ESCALATION TARGET** = who you go to for a floor-LIFT or a real money/authority sign — and, critically, who you do **NOT** go to.
- **BINDING cai-ratified constants** = hard-nevers that hold regardless of owner-sign; the responsible body applies them itself.

---

## Floors

### irsyad — client project (Gazzabyte; Shuq=SMS, Wan=DMS)

| Axis | Value |
|---|---|
| **DOMAIN** | irsyad money / go-live (tabung, DMS, school-fees, donor data). Data layer: the **irsyad silo (goumlyne)** — exact `project ref` in `docs/data-store-registry.md`. |
| **FLOOR** | **CONSOLE safety-floor** — CAI-898 money-gate bar + deny-proofs + RO wet-prove. **This floor does NOT drop.** |
| **OWNER-SIGN** | **Shuq (SMS) / Wan (DMS).** Their request IS the authorization. Owner-sign is theirs, **NEVER Musa's** ([[feedback_irsyad_signoff_is_shuq_wan_never_musa]]). |
| **ESCALATION TARGET** | **MUSA** — and ONLY for a floor-**LIFT** or a real money-sign / authority grant. **cai is OUT of irsyad (dev AND governance).** Do NOT route irsyad governance to cai. |
| **BINDING cai-ratified constants** | Hard-nevers still HOLD and the **console applies them itself**: minors-PII exclusion (fail-closed), TENANT-RESIDENCY-001 (data stays in the irsyad silo), CAI-1034 (no raw client-data into context). Ban "IRAS"/"tax-deductible" wording. |

> ❌ **STALE / WRONG (do not re-assert):** "irsyad money go-live needs **cai+Nazim**." That is the
> *fleet* money-sign path (below), **not** irsyad's. irsyad go-live = **console-safety-floor +
> Shuq/Wan owner-sign, cai-OFF**. Asserting "cai+Nazim" for irsyad over-asserts a stale floor
> and wrongly pulls cai into a cai-off project.

---

### cosem — client project (ADCDA / TDU; answers route to Hariz; ADCDA → ca-things group)

| Axis | Value |
|---|---|
| **DOMAIN** | cosem money / go-live / governance. Same model as irsyad. Data layer: cosem demo/dev (synthetic only), the ADCDA real-PII silo (UAE-sovereign, **not yet provisioned**), the TDU silo (SG, designation pending) — exact refs in `docs/data-store-registry.md`. |
| **FLOOR** | **CONSOLE safety-floor** (CAI-898 bar + deny-proofs + wet-prove). Does NOT drop. cai-OFF for cosem since **2026-09-13 (op#20139)**; **console owns governance + gating**. |
| **OWNER-SIGN** | the cosem project operator (per channel) — not Musa, not cai. |
| **ESCALATION TARGET** | **MUSA** for a floor-lift / money-sign. **NOT cai.** |
| **BINDING cai-ratified constants** | Still bind, console applies them itself: **ADCDA residency** (real trainee gov-PII → own UAE-sovereign silo before any further real write; CAI-RESP-1340 containment — no new real-PII writes to the flagged ADCDA org), **Google-exposure containment**, CAI-1034 (no raw client-data). No agent writes to real ADCDA prod, even synthetic test docs (CAI-1340). |

---

### fleet money / gov — NON-client (the substrate itself)

| Axis | Value |
|---|---|
| **DOMAIN** | fleet-internal money-sign + governance decisions (non-client). Substrate store ref: `tscuymavysscrvoberrr`. |
| **FLOOR** | money / gov gates run **through cai** at the **CAI-898 bar**. |
| **OWNER-SIGN** | **money-sign delegated to cai + Nazim** (jointly). |
| **ESCALATION TARGET** | cai (governance), with Musa for anything cai+Nazim cannot jointly sign. |
| **BINDING cai-ratified constants** | CAI-898 bar; standing fleet gates. This is the ONLY domain where "cai+Nazim" is the correct money-sign phrase. |

---

### Residency — TENANT-RESIDENCY-001 (cross-cutting, ALL client data paths)

| Axis | Value |
|---|---|
| **DOMAIN** | any client DATA (rows) path, every client, always. |
| **FLOOR** | a client's DATA lives in that client's **designated store, ALWAYS**. Shared generalized code is fine; commingling one client's rows into another project's DB is NOT. A new client's silo is provisioned/designated **BEFORE the first client data write** — never "temporarily" in a shared project. |
| **OWNER-SIGN** | residency **exceptions** require a **joint operator + cai grant** and **must expire**. |
| **ESCALATION TARGET** | cai (for a residency exception grant); Musa for client-specific sign. |
| **BINDING cai-ratified constants** | **Standing pre-live residency gate:** verify the write-target silo BEFORE any client data path goes live. **Name the store + `project ref`** (LAYER-VOCAB-001) per `docs/data-store-registry.md`. Layer-ambiguity in a data-path spec is a review FINDING. |

---

## Quick-reference matrix

| Domain | Floor (does not drop) | Owner-sign | Escalate to | cai in governance? |
|---|---|---|---|---|
| **irsyad** go-live/money | console safety-floor (CAI-898 + deny-proofs + wet-prove) | Shuq (SMS) / Wan (DMS) | **Musa** (lift/money only) | **NO — cai OUT** |
| **cosem** go-live/money/gov | console safety-floor (CAI-898 + deny-proofs + wet-prove) | cosem project operator | **Musa** (lift/money only) | **NO — cai OFF since 2026-09-13** |
| **fleet** money/gov | cai gate @ CAI-898 bar | **cai + Nazim** | cai, then Musa | **YES** |
| **residency** (any client data) | designated store, always; pre-live gate | joint operator + cai (for exceptions, expiring) | cai / Musa | YES (for exception grants) |

---

## Incident log (why this SSOT + the consult hook exist)

- **2026-10-15 — stale irsyad floor over-asserted.** The console (Nazim) asserted that irsyad
  money go-live needs **"cai+Nazim"** — the *fleet* path. The real floor is
  **console-safety-floor + Shuq/Wan owner-sign, cai-OFF**. The correct answer was already in
  memory but was **not consulted before asserting**. Remediation: this SSOT +
  `scripts/hooks/consult_governance_floor_guard.py` (consult-before-assert, enforced in code).
