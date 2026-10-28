# Substrate SSOT — Governance Floors, Topology, Routing & Logs (SINGLE SOURCE OF TRUTH)

> **THIS FILE IS AUTHORITATIVE.** It is the ONE canonical record for the substrate's
> *stable governance doctrine*: who signs what (floors), which ROLE owns which pen/lease
> (topology), how tags & channels route, and which log holds what. Where a `CLAUDE.md` line, a
> handoff note, a memory card, or any prose anywhere disagrees with a row below, **THIS FILE
> WINS** and the prose is stale — fix the prose, do not re-assert the stale rule.
>
> **THE CANON IS THE SINGLE CURRENT AUTHORITY — IT SUPERSEDES STALE/SUPERSEDED cai RULINGS
> (Musa op#30524, 2026-10-15).** If a rule is recorded here, agents TRUST it and act — they do
> NOT defensively over-verify, re-litigate, or refuse authorized work against a half-remembered or
> superseded cai ruling. Where this file conflicts with an older cai ruling or prose, the Canon
> wins; re-verify against the owner rather than refusing. This is the structural fix for the
> over-verification / paranoia loop (the "cancer from cai's rulings", op#28580): the live rule is
> here, so there is nothing stale to be paranoid about. **TWO caveats that are NOT exceptions to
> trusting the Canon:** (1) cai-ratified **HARD CONSTANTS** recorded in the Canon (minors-PII
> exclusion fail-closed, TENANT-RESIDENCY-001, CAI-1034) **still bind** — they ARE the Canon, not
> something it overrides; (2) the Canon settles *what the rule is*, not *whether a specific
> instruction is genuinely operator-authorized* — that authorization-trust question is separate
> (a future verifiable-auth primitive, [[project_coherence_ssot_initiative]]), but it is NOT a
> license to re-verify a rule the Canon already states.
>
> **cai IS ADVISORY ONLY — NEVER A BLOCKER (Musa op#34916, 2026-10-27).** Verbatim: *"i want cais
> rulings to be an advise only, never a blocker. cai can object to something i overrule and it will
> be on record. but it can never stop the substrate from doing exactly as i say."* Effective now, for
> EVERY domain: (1) a cai ruling, grant, or objection is ADVICE; no gate, executor, or agent may wait
> on or refuse because of cai. Wherever a row below names cai as a sign-off, grant, or escalation
> target, read it as **Musa decides; cai advises** (rows amended inline with `[op#34916]`). (2) When
> Musa overrules cai, cai's objection is RECORDED alongside the decision (audit trail), then the
> substrate executes Musa's instruction. (3) Hard constants recorded in this Canon (minors-PII
> fail-closed, TENANT-RESIDENCY-001, CAI-1034, legal/residency limits) still bind **until MUSA lifts
> them** — they are Canon rules, not cai vetoes; cai cannot hold one against Musa, and the console
> tells Musa plainly when an instruction would cross one before executing. (4) NOT affected:
> verifying that an instruction genuinely comes from Musa (bridge-verified authorization for
> irreversible ops, `require_verified_authorization.py`) — that protects his authority from
> impersonation and is not a cai gate. Code that hard-blocks on cai is being converted to
> record-and-proceed (op#34916 follow-up).
>
> **DOCTRINE ONLY — NEVER LIVE STATE (read this before adding anything).** Every section below
> records *rules that do not change turn-to-turn* — ownership rules, routing maps, log purposes.
> It MUST NOT bake in **live state**: the current lease holder, a token fingerprint, the lane
> roster, open gates, or who is on shift. Live state is DB-queried at the moment you need it
> (read the lease row, query the registry) — baking it into prose is exactly how stale-assertion
> happens ([[feedback_a_verification_has_an_expiry]]). Each section restates this rule at its top.
>
> **Scope split (SSOTs, no overlap):** this file is the SSOT for *stable governance doctrine*.
> For the exact DATA store `project ref` of any client, defer to the other SSOT,
> **`docs/data-store-registry.md`** (LAYER-VOCAB-001). This file names a store by its
> **layer + alias** and points at the registry for the ref — it never inlines a client-silo ref
> (that keeps this doc safe to author/read from any body). See *§4 Data-store-registry pointer*.
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

## Section index

1. **Governance floors** — who signs what (the original SSOT; unchanged).
2. **Org-chart / body-topology** — which ROLE owns which singleton pen / lease.
3. **Tag → routing + per-channel owner map** — how @tags and channels resolve.
4. **Which log for what** — the canonical map of the 7+ substrate logs.
5. **Data-store-registry pointer** — where client-silo refs live (the 2nd SSOT).
6. **Vault ↔ SSOT links** — the memory card that is each rule's "why".

---

## 1. Floors

> *Section rule — doctrine only.* Floors and owner-sign below are STABLE. The current lease
> holder, the live gate backlog, and who is on shift are LIVE STATE — never inline them here.

### How to read a row

Each row is: **DOMAIN → FLOOR (who/what gates) → OWNER-SIGN → ESCALATION TARGET → BINDING cai-ratified constants**.

- **FLOOR** = the standing bar that does NOT drop. Meeting it is necessary but not sufficient.
- **OWNER-SIGN** = whose request/approval IS the authorization once the floor is met.
- **ESCALATION TARGET** = who you go to for a floor-LIFT or a real money/authority sign — and, critically, who you do **NOT** go to.
- **BINDING cai-ratified constants** = hard-nevers that hold regardless of owner-sign; the responsible body applies them itself.

### irsyad — client project (Gazzabyte; Shuq=SMS, Wan=DMS)

| Axis | Value |
|---|---|
| **DOMAIN** | irsyad money / go-live (tabung, DMS, school-fees, donor data). Data layer: the **irsyad silo (goumlyne)** — exact `project ref` in `docs/data-store-registry.md`. |
| **FLOOR** | **CONSOLE safety-floor** — CAI-898 money-gate bar + deny-proofs + RO wet-prove. **This floor does NOT drop.** |
| **OWNER-SIGN** | **Shuq (SMS) / Wan (DMS).** Their request IS the authorization. Owner-sign is theirs, **NEVER Musa's** ([[feedback_irsyad_signoff_is_shuq_wan_never_musa]]). |
| **ESCALATION TARGET** | **MUSA — ONLY for a floor-LIFT** (lowering a binding safety constant). **NOT for routine sign-off / money-sign** — the owners authorize those now (op#34249). **cai is OUT of irsyad (dev AND governance).** Do NOT route irsyad governance to cai. |
| **SIGN-OFF = Shuq/Wan ONLY (op#34245 + op#34249, 2026-10-26)** | Musa removed BOTH cai and himself from irsyad sign-off: **the owners (Shuq=SMS, Wan=DMS) are the SOLE authorization; they never wait on cai, Musa, OR a console sign-off for what they want.** Verbatim (op#34249): *"for irsyad - no cai, no me. just the operators shuq and wan. they never have to wait for anyone else to sign off if they want something."* The console does **NOT insert a sign-off gate** — its job is to ensure the binding safety constants (next row) and coordinate execution via coord on the owners' say-so. **Those constants are NOT sign-offs and still hold** (console applies them itself): reversible + hash-chained audit + RO wet-prove, minors-PII exclusion (fail-closed), CAI-1034, no "IRAS"/"tax-deductible" wording. The ONE cross-cutting item still above this floor: a **TENANT-RESIDENCY-001 exception** (moving client data OUT of the irsyad silo) needs the fleet-doctrine joint operator+cai grant — that is data-residency doctrine, not an irsyad sign-off. |
| **BINDING cai-ratified constants** | Hard-nevers still HOLD and the **console applies them itself**: minors-PII exclusion (fail-closed), TENANT-RESIDENCY-001 (data stays in the irsyad silo), CAI-1034 (no raw client-data into context). Ban "IRAS"/"tax-deductible" wording. |

> ❌ **STALE / WRONG (do not re-assert):** "irsyad money go-live needs **cai+Nazim**." That is the
> *fleet* money-sign path (below), **not** irsyad's. irsyad go-live = **console-safety-floor +
> Shuq/Wan owner-sign, cai-OFF**. Asserting "cai+Nazim" for irsyad over-asserts a stale floor
> and wrongly pulls cai into a cai-off project.

### cosem — client project (ADCDA / TDU; answers route to Hariz; ADCDA → ca-things group)

| Axis | Value |
|---|---|
| **DOMAIN** | cosem money / go-live / governance. Same model as irsyad. Data layer: cosem demo/dev (synthetic only), the ADCDA real-PII silo (UAE-sovereign, **not yet provisioned**), the TDU silo (SG, designation pending) — exact refs in `docs/data-store-registry.md`. |
| **FLOOR** | **CONSOLE safety-floor** (CAI-898 bar + deny-proofs + wet-prove). Does NOT drop. cai-OFF for cosem since **2026-09-13 (op#20139)**; **console owns governance + gating**. |
| **OWNER-SIGN** | the cosem project operator (per channel) — not Musa, not cai. |
| **ESCALATION TARGET** | **MUSA** for a floor-lift / money-sign. **NOT cai.** |
| **BINDING cai-ratified constants** | Still bind, console applies them itself: **ADCDA residency** (real trainee gov-PII → own UAE-sovereign silo before any further real write; CAI-RESP-1340 containment — no new real-PII writes to the flagged ADCDA org), **Google-exposure containment**, CAI-1034 (no raw client-data). No agent writes to real ADCDA prod, even synthetic test docs (CAI-1340). |
| **SCOPED FLOOR-LIFT (Musa op#34867, 2026-10-27)** | Medical certificates (MC documents for excused trainee absences, op#31232 Feature C) MAY be stored in the existing ADCDA Firebase `cosem-adcda-cb6d9` — Musa: *"b with existing firebase. we already hold their IDs, medical certs are less sensitive"*. Scope = MC documents + the excused-absence record ONLY; every other new real-PII category still needs its own lift. Conditions the console applies: read limited to admin tiers + the trainee's own-group approved trainer; upload by staff only (never trainees); audited; no agent writes/reads real MCs. |
| **SCOPED FLOOR-LIFT (Musa op#34893, 2026-10-27)** | Staff/trainer self-uploaded PROFILE PHOTOS (op#34887) MAY be stored in the existing ADCDA Firebase `cosem-adcda-cb6d9`. Musa answered "OK" (op-msg 34893) to console's explicit yes/no (op-msg 34892, ask 5436). Scope = a signed-in user's OWN avatar only (new field `users/{uid}.profile.avatarUrl` + `userProfilePhotos/{uid}/`); never trainees, never written into the official `headshotUrl` / ID / face-scan assets, never trainers/ or trainees/ docs. Conditions the console applies: owner-only write enforced server-side (rules + callable, deny-tested); image-only, resized, size-capped; no agent writes/reads real photos (e2e synthetic only). |
| **SCOPED FLOOR-LIFT — WITHDRAWN, UNUSED (Musa op#35176, 2026-10-28: Fahad says no signature needed; typed name in EN or AR suffices — nothing was ever stored; any future signature storage needs a fresh lift) (granted op#35153)** | Trainee DRAWN SIGNATURES on the deficit Warning Acknowledgment (op#35151; first asked op#31545) MAY be stored in the existing ADCDA Firebase `cosem-adcda-cb6d9`. Musa answered "yes" (op-msg 35153) to console's explicit yes/no (op-msg 35152, ask 5565). Scope = the signature image linked to a warning-acknowledgment record ONLY (including adding a signature to an earlier typed acknowledgment); never reused as an ID/identity asset or for any other document. Conditions the console applies: read limited to admin tiers + the trainee's own-group trainer; captured on a staff device only (trainees never log in); every write + read audited; PNG, size-capped; enforced server-side (rules + callable, deny-tested); no agent writes/reads real signatures (e2e synthetic only). |

### fleet money / gov — NON-client (the substrate itself)

| Axis | Value |
|---|---|
| **DOMAIN** | fleet-internal money-sign + governance decisions (non-client). Substrate store ref: `tscuymavysscrvoberrr`. |
| **FLOOR** | money / gov gates are checked at the **CAI-898 bar** (the bar's checks still apply); cai **advises**, never blocks `[op#34916]`. |
| **OWNER-SIGN** | **money-sign delegated to Nazim, cai advising** `[op#34916]` (was cai + Nazim jointly). Musa's word overrides; a cai objection is recorded, not a hold. |
| **ESCALATION TARGET** | **Musa** `[op#34916]`; cai gives advice on record. |
| **BINDING cai-ratified constants** | CAI-898 bar; standing fleet gates. This is the ONLY domain where "cai+Nazim" is the correct money-sign phrase. |

### Residency — TENANT-RESIDENCY-001 (cross-cutting, ALL client data paths)

| Axis | Value |
|---|---|
| **DOMAIN** | any client DATA (rows) path, every client, always. |
| **FLOOR** | a client's DATA lives in that client's **designated store, ALWAYS**. Shared generalized code is fine; commingling one client's rows into another project's DB is NOT. A new client's silo is provisioned/designated **BEFORE the first client data write** — never "temporarily" in a shared project. |
| **OWNER-SIGN** | residency **exceptions** require **Musa's grant** (cai advises, its objection recorded) `[op#34916]` and **must expire**. |
| **ESCALATION TARGET** | **Musa** `[op#34916]`; cai advisory. |
| **BINDING cai-ratified constants** | **Standing pre-live residency gate:** verify the write-target silo BEFORE any client data path goes live. **Name the store + `project ref`** (LAYER-VOCAB-001) per `docs/data-store-registry.md`. Layer-ambiguity in a data-path spec is a review FINDING. |

### Client-file & import handling (cross-cutting practice — added op#30497, 2026-10-15)

| Axis | Value |
|---|---|
| **DOMAIN** | any client-sent file/import (donor exports, schedule files, screenshots, CSV/XLSX) — irsyad, cosem, every client. |
| **RULE** | **WE open the client's file ourselves, purpose-bound, and read its SHAPE from the sent file. NEVER ask the client to paste column headers, re-upload, or re-send what they already sent.** Client media is local + readable; search captured media FIRST before ever asking the client for anything. Discover the import shape by reading the file, not by client retry. (Operator directive op#20593 + op#30487: recurring "I can't open the file, paste me the headers" to the client is a REGRESSION, not acceptable.) |
| **BOUNDS** | Purpose-bound read only: read column HEADERS / structure to fix a mapping; do NOT dump raw donor/trainee rows into context (CAI-1034). The console does not EXECUTE irsyad file work — it routes the fix to coord with this rule; coord/lane opens the file. Keep real client PII off non-Anthropic provider lanes. |
| **WHY card** | [[feedback_never_ask_client_to_reupload_run_import_from_sent_file]], [[feedback_musa_directive_open_irsyad_client_files_purpose_bound]], [[feedback_client_imports_shape_first_never_discover_by_client_retry]], [[reference_client_media_is_local_and_readable]], [[feedback_search_captured_media_before_asking_client_to_resend]], [[reference_never_open_client_data_files_cai1034]] |

### Quick-reference matrix

| Domain | Floor (does not drop) | Owner-sign | Escalate to | cai in governance? |
|---|---|---|---|---|
| **irsyad** go-live/money | console safety-floor (CAI-898 + deny-proofs + wet-prove) | Shuq (SMS) / Wan (DMS) — **sole sign-off, no wait (op#34249)** | **Musa** (floor-LIFT only; money-sign delegated to owners) | **NO — cai OUT** |
| **cosem** go-live/money/gov | console safety-floor (CAI-898 + deny-proofs + wet-prove) | cosem project operator | **Musa** (lift/money only) | **NO — cai OFF since 2026-09-13** |
| **fleet** money/gov | CAI-898 bar checks (cai advises, never blocks) `[op#34916]` | **Nazim** (cai advising) | **Musa** | **ADVISORY ONLY** (objections recorded) |
| **residency** (any client data) | designated store, always; pre-live gate | **Musa** grants exceptions (expiring), cai advising `[op#34916]` | **Musa** | **ADVISORY ONLY** |

---

## 2. Org-chart / body-topology (OWNERSHIP RULES, not holders)

> *Section rule — doctrine only.* This section says **which ROLE owns which pen/lease**, not who
> holds it right now. To find the CURRENT holder, **READ THE LEASE ROW** (`orch_lease`,
> `fleet_health_lease`) — never assume a host from this doc. The holder is LIVE STATE; the
> ownership rule is doctrine. (ORCH-TOPOLOGY-001, cai #6521/#6523, 2026-07-04; CAI-RESP-501,
> 2026-07-22.)

**Multiple orch bodies exist.** Determine YOUR body before touching any pen — `ORCH_BODY_ROLE`
in `.env`. The two bodies:

- **console = Nazim** — the operator's CTO. Runs on the **Mac Mini**, tmux session `nazim`,
  `ORCH_BODY_ROLE=console`. Has his OWN sanctioned operator voice `scripts/nazim_send.sh`
  (@nazim_cto_bot / `NAZIM_BOT_TOKEN`, channel `nazim-console`) — structurally distinct from the
  hub's operator channel (CAI-RESP-426). The console body is fail-closed OUT of the hub's
  `tg_send` pen.
- **hub = lease-holder doctrine** — `hub`/unset `ORCH_BODY_ROLE`. The hub is the pure operator
  bridge (operator talks to it, it delegates to lanes) and holds the pens below *only as the
  lease holder*, never by virtue of being a particular machine.

### The two leases and the pens they gate

| Pen | Owned by (ROLE / lease row) | Doctrine |
|---|---|---|
| **(i) Drain `cc-orchestrator` bus rows** (mark `to_agent='cc-orchestrator'`) | `orch_lease` holder (**"orch-hub"**) | Singleton — only the lease holder drains. |
| **(ii) Lane prompt submission** | `orch_lease` holder (**"orch-hub"**) | Singleton — only the lease holder submits lane prompts. |
| **(iii) Watchdog** (self-healing executor) | **`fleet_health_lease` holder (`"fleet-health"`)** | MOVED off `orch_lease` by CAI-RESP-501. Default holder `cc-fleet-health` (Mac Mini SRE), which TAKES at boot & RENEWS in heartbeat. |
| **(iv) `tg_send`/tg-out + operator declarations** | `orch_lease` holder (**"orch-hub"**) | Singleton. The console body is fail-closed OUT of this pen and uses `nazim_send.sh` on `nazim-console` instead. |
| **(v) Fleet-status assertions** | **`fleet_health_lease` holder (`"fleet-health"`)** | MOVED off `orch_lease` by CAI-RESP-501. Same holder as (iii). |

- **CAI-RESP-501 — watchdog + fleet-status on a SEPARATE single-owner lease.** Pens (iii) and (v)
  belong to the `fleet_health_lease` holder, NOT the `orch_lease` holder. On the SRE's lease
  EXPIRY (death/stall, TTL 900s) the **hub RECLAIMS** these two pens via
  `fleet_health_lease.py reclaim --reason` (CAS on expiry — dead-man's fallback). **NEVER two
  concurrent holders:** the CAS in `take` serializes ownership; every other body fail-closes.
- **DR takeover** = `orch_lease.py take --reason` (CAS, loud) only when the hub is genuinely dead.
  A failed-over hub inherits the PENS, NOT the dead machine's tmux lanes.
- **Enforced in code, not by promise:** `scripts/lib/orch_lease.py` gates `tg_send`/`tg_send_file`
  fail-closed; `scripts/lib/fleet_health_lease.py` gates the watchdog/fleet-status actions
  (`context_health_watchdog.py`, `priority_sla_watchdog.py`) — fail-safe-for-holder /
  fail-closed-for-known-non-holder. Detection + the operator degrade-alert stay UNGATED so a
  safety page is never silenced by lease state.
- **Hard boundaries for cc-fleet-health** (`scripts/lib/fleet_health_boundaries.py`, CAI-RESP-501):
  (a) **NO singleton-body reset authority** — the destructive `--arm=red` /clear stays a separate
  CAI-500-gated executor the SRE NEVER drives (`assert_no_sre_red_reset` fail-closes); and
  (b) **OPS, NOT GOVERNANCE** — uses its own `agent_id`, NEVER writes `strategic_decisions` or
  grant rows (`assert_ops_only` fail-closes).

> **Live-state reminder:** the default holders named above (`cc-orchestrator@...` for orch-hub,
> `cc-fleet-health` for fleet-health) are DEFAULTS, not a running assertion of who holds the lease
> this minute. Always read the lease row.

---

## 3. Tag → routing + per-channel owner map

> *Section rule — doctrine only.* The tag list and the channel-owner map are STABLE. Which lane
> is up, who is on shift, and open threads are LIVE STATE — query them, don't bake them here.

### @tags — CONTEXT, not routing

A tag tells you the **context** of a message; it does **NOT** route it away from you. The
receiving body still **owns the conversation and delegates**. Echo the context you assumed (e.g.
lead a reply with `[cosem-tdu]`) so a wrong guess is correctable.

| Tag(s) | Context |
|---|---|
| `@adcda` / `@tdu` / `@cosem` | cosem project (ADCDA / TDU surfaces). |
| `@ihsanos` / `@irsyad` | ihsanos storefront / irsyad (Gazzabyte) project. |
| `@scholar` / `@mizan` | scholar / mizan surfaces. |
| `@qr` | branditqr / QR surface. |
| `@fleet` / `@console` | fleet-internal / console (Nazim) context. |
| `@cai` | strategic / governance (cai) context. |

### Per-channel operator-thread ownership

Whichever body the operator opened a thread with **owns the reply/close**; the other body stays
OFF that thread. A terminal-only reply does NOT reach the operator's phone — answer on the
channel the operator used.

| Operator channel | Reply owner | Reply via |
|---|---|---|
| `nazim-console` | **Nazim (console)** | `scripts/nazim_send.sh` (@nazim_cto_bot) |
| `orch-channel` | **the hub** (`orch_lease` holder, pen iv) | `scripts/tg_send.sh` (@wingmennorchbot) |
| `tmux-console` | **orch-console** (operator typed into the console tmux) | log-first via `scripts/log_console_msg.sh`, then reply on the channel the operator uses |

### Triage-owner map (migration 082)

`operator_asks.triage_state` is owned per channel so the daily digest shows only judged summaries:

| Channel | Triage owner |
|---|---|
| `nazim-console` / `tmux-console` | **orch-console** |
| `orch-channel` | **cc-orchestrator** |

---

## 4. Which log for what (the canonical map)

> *Section rule — doctrine only.* This maps each log to its PURPOSE. The current *rows* in any log
> are LIVE STATE — read them from the DB (`operator_log.unprocessed()`, `boot_briefing`, etc.);
> never transcribe row contents into this doc.

| Log / table | Purpose | How to write / read it |
|---|---|---|
| **`operator_messages` / `operator_log`** | Operator comms, **both directions**, scoped by body. The durable source of truth keeping live-you and rebooted-you coherent (CAI-RESP-277). | Read `operator_log.unprocessed()` at turn start; answer; `operator_log.mark_handled([ids])` exactly the ids you answered (never the deprecated high-water stamp). Inbound logged by `nervous_system/ingest.py`; outbound by `tg_send.sh`/`nazim_send.sh`/`tg_out`. |
| **`agent_messages`** | The inter-agent **"bus"** — attributable agent-to-agent testimony and task routing. | **Always via `scripts/bus_send.py`**, never a hand-written INSERT (bus #43651: a hand INSERT once dropped `priority` to the `'P2'` default below the hub wake floor). `--priority` is required; hub action-now = `--priority P1 --req`. |
| **`held_commitments`** | Commitments + due-date tracking (what was promised, by when). | Let the identity column assign the id (never write an explicit id into a serial/identity insert — bus #65636). The commitment sweeper reads it. |
| **`operator_asks`** | The operator **ask ledger** + triage (`triage_state`: `captured`/`ask`/`not_an_ask`/`done`, orthogonal to `closed_at`). | After answering, triage via `scripts/asks_triage.py <id> ask|not|done` (never leave `captured`). Per-channel triage owner in §3. |
| **`build_log`** | Build operations (every build logged). | Log all build ops here. |
| **`work_outputs`** | Audit deliverables — build specs, diffs, test results, deploy URLs. | Repo files alone are NOT sufficient; the deliverable must land here. |
| **`strategic_decisions`** | Fleet strategic decisions + reasoning. | **NEVER `SELECT *`.** Use the `boot_briefing` index + `get_decision('<ref>')` on demand. |
| **`audit_log`** | INSERT-only irreversible-grant **hash chain** (append-only; each grant forks the chain). | INSERT-only; never update/delete. Used for irreversible-op authorization provenance. |

> **Coherence-debt note (interim SSOT).** This sprawl — **7+ distinct logs** — is itself the
> coherence debt the Paperclip reverse-engineering flagged (a single *Activity log* was the
> target). This map is the **interim SSOT** for "which log for what" until the logs are unified;
> when they are, this section collapses to the unified store and the rest become views.

---

## 5. Data-store-registry pointer (the 2nd SSOT)

> *Section rule — doctrine only.* This doc NEVER inlines a client-silo `project ref`. Refs change
> (provisioning, migration) and are effectively live state w.r.t. this doc — they live in one
> place.

Exact client-silo and substrate `project ref`s live in **`docs/data-store-registry.md`**
(LAYER-VOCAB-001) — the second SSOT. This file names a store by its **layer + alias only** (e.g.
"irsyad silo (goumlyne)", "ihsanos multi-tenant DB", "cosem demo/dev") and defers to the registry
for the ref. Bare product names ("ihsanos", "cosem") are INVALID as data references — always name
the **layer** (frontend vs data) and, for data, the **exact store + `project ref`** from the
registry. Layer-ambiguity in a data-path spec is a review FINDING, not style.

---

## 6. Vault ↔ SSOT links (the "why" behind each rule)

> *Section rule — doctrine only, PROPOSAL.* This section records which memory-vault card carries
> the reasoning ("why") for each rule, as `[[card-name]]` links. The vault cards themselves are
> edited by the console body — this doc does not edit the vault.

| This doc's rule | Memory-vault "why" card |
|---|---|
| irsyad floor / owner-sign = Shuq/Wan, cai-OUT | [[feedback_irsyad_signoff_is_shuq_wan_never_musa]], [[feedback_cai_not_involved_in_irsyad_dev]] |
| cosem floor / cai-OFF | [[feedback_cai_not_involved_in_cosem]] |
| fleet money-sign = cai + Nazim | [[feedback_operator_delegates_money_sign_to_cai_nazim]], [[reference_money_gate_bar_cai898]] |
| run money/gov gates through cai | [[feedback_always_run_gates_through_cai]] |
| Residency / TENANT-RESIDENCY-001 | [[reference_console_holds_client_silo_write_path]] (console has silo WRITE; hub RO) |
| Body-topology / lease ownership (§2) | the ORCH-TOPOLOGY-001 / CAI-RESP-501 card (fleet-mechanics index) |
| console = Nazim, own operator voice | [[feedback_nazim_replies_via_nazim_send]], [[feedback_nazim_hub_thread_ownership]] |
| console never drives irsyad execution | [[feedback_console_never_drives_irsyad_execution]] |
| per-channel reply ownership (§3) | [[feedback_nazim_hub_thread_ownership]], [[feedback_nazim_reconcile_both_inboxes]] |
| bus writes via `bus_send.py` only | [[reference_hub_wake_floor_p1_rr]] |
| `audit_log` INSERT-only hash chain | [[reference_audit_log_insert_only_grant_forks_hash_chain]] |
| enforce-in-code, not by promise | [[feedback_enforce_process_in_code_not_promises]] |
| a verification expires (Last-verified) | [[feedback_a_verification_has_an_expiry]] |
| data-store registry / LAYER-VOCAB-001 (§5) | [[reference_global_google_creds_env_is_a_client_sa]] (registry context) + the data-store-registry doc |

---

## Incident log (why this SSOT + the consult hook exist)

- **2026-10-15 — stale irsyad floor over-asserted.** The console (Nazim) asserted that irsyad
  money go-live needs **"cai+Nazim"** — the *fleet* path. The real floor is
  **console-safety-floor + Shuq/Wan owner-sign, cai-OFF**. The correct answer was already in
  memory but was **not consulted before asserting**. Remediation: this SSOT +
  `scripts/hooks/consult_governance_floor_guard.py` (consult-before-assert, enforced in code).
