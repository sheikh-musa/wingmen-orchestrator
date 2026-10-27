# op#34916 — inventory of every place cai can BLOCK or HOLD (read-only)

cc-fleet-health, 2026-10-27 ~22:30Z, for console #78483. **No code changed; no strategic_decisions or grant rows written.** Musa rules per row before anyone edits code.

Scanned:
- **The orchestrator repo** at live HEAD `1e3cba6` (scripts/, nervous_system/, hooks, deploy/, migrations/), excluding tests, drafts and `.claude/worktrees` copies.
- **The live substrate DB** (tscuymavysscrvoberrr): every public function that raises AND consults decision/grant/cai state, every trigger using one, and every RLS policy citing grants or cai.
- **gzb:** `/home/gazzai/*.sh|*.py` plus the irsyad worktrees' `exec_prod.py`.

Method: keyword and line-level search (refuse/raise/exit/fail-closed next to cai/CAI-/grant/ruling/decision), then reading each hit.

**Limit:** a gate phrased with none of those words would be missed. Three such phrasings did surface through other paths (`PROBE_ARMED`, `mizan_retract_gate.unlocked`, `--arm=amber` in a plist). So read this as "every gate I could find", not a proof of completeness.

Proposals: **(a)** record cai's objection or ruling and proceed on Musa's verified instruction; **(b)** KEEP, because it isn't actually a cai gate; **KEEP-Canon** means a hard constant, kept until Musa lifts it.

## A. LIVE cai holds today (cai's state actually stops something)

| # | file:line | What it blocks | What it checks | Proposal |
|---|---|---|---|---|
| A1 | DB `mizan_retract_block()` trigger on `mizan_interactions` (`migrations/infra/bedrock_substrate_core.sql:1338` fn, `:1355` trigger, `:1319` gate table); row `mizan_retract_gate.id=1` is **unlocked=false** | Inserting any user-facing **retraction** (`retraction_of IS NOT NULL`) | `mizan_retract_gate.unlocked`, held "until judge-human agreement documented per MIZAN-EVAL-001 amendment (**CAI-RESP-062**)" | **(a)** Musa's verified instruction unlocks it (flip `unlocked`, log who and why). cai's CAI-RESP-062 view is recorded as advice. |
| A2 | `nervous_system/body_activity_oracle.py:49` `PROBE_ARMED = False` (comment :47 "DISARMED until cai signs") | The oracle's mutating fork-2 probe (sends to a pane when unsure) | A constant held until a cai sign | **(a)** Musa (or console on his word) arms it. cai advises. |
| A3 | `nervous_system/console/app.py:2807` (comment :447) | Console **R4 armed apply** returns 503 "pending **cai sign-off** + Nazim review" | Hard-disabled pending cai sign-off | **(a)** Drop "cai sign-off" as a precondition. Nazim review plus Musa's GO arm it; cai's review is recorded. |
| A4 | `scripts/check_additive_migration.py:545` | Prints "BLOCKED — destructive operations… **Requires cai approval** before pre-apply", exit 1 | Destructive-SQL scan | **(a)**, wording plus authority: the scan stays and the approver becomes Musa/console. No automated caller consumes the exit code today; it's a manual tool, so this is mostly a message fix. |
| A5 | `scripts/irsyad_residency_purge.py:25-26, 67, 92` | The irsyad residency PII purge | (1) `verified_authorization` (Musa), **(2)** an unfreeze sentinel whose purge body is "owned by **operator+cai**… at unfreeze", (3) the body raises NotImplementedError | Split: (1) **(b) KEEP**, since it verifies Musa. (2) **(a)** the unfreeze decision is Musa's alone, cai advisory. (3) is unbuilt code, not a gate. |

## B. Hard rules that came FROM a cai ruling (no runtime cai lookup: cai can't hold them, but they encode cai's ruling as a blocker)

| # | file:line | What it blocks | Rule | Proposal |
|---|---|---|---|---|
| B1 | `scripts/lib/fleet_health_boundaries.py:86` `assert_no_sre_red_reset` + context-health plist `--alert --arm=amber` | Destructive `/clear` reset of singleton bodies (hub/cai/console) by any automated executor | **CAI-500 / CAI-RESP-501**: red stays unarmed and the SRE never drives it | **(a)** Arming red becomes Musa's call; cai's objection is recorded. Safety note: this is the only automated path that can wipe a live singleton's context. I'd keep the code gate and change only *who* can arm it. |
| B2 | `fleet_health_boundaries.py:116` `assert_sre_lane_red_permitted` | SRE worker-lane red reset unless idle + git-clean + fresh-handoff + armed | **CAI-RESP-681** conds 1/2/5 | The **mechanical** checks (idle/clean/fresh handoff) are **(b) KEEP** as data-loss guards, not cai votes. The "armed" switch is already standing-armed (CAI-RESP-1382). |
| B3 | `fleet_health_boundaries.py:98` `assert_sre_never_targets_singleton` | SRE recycling a singleton | CAI-RESP-501 | **(b) KEEP** (role separation, and Musa /clears singletons himself). Musa may lift it. |
| B4 | `fleet_health_boundaries.py:62, 173` `GOVERNANCE_TABLES` / `assert_ops_only` | The SRE writing `strategic_decisions` / grant rows | CAI-RESP-501 | **Musa to rule.** With cai advisory, "governance rows are cai's" loses its basis. But your brief keeps me ops-only, so I'd keep it until Musa says otherwise. |
| B5 | `scripts/switch_lane_token.sh:124, 146`, `scripts/switch_singleton_token.sh:154, 173`, `nervous_system/console/app.py:112, 3025` | Using the gazzabyte **consumer** Max token for any fleet lane | **CAI-729** | **(b) KEEP** as a licensing/ToS safety rule (a consumer token on automation). It's cai-originated, so it's Musa's to lift. |
| B6 | `scripts/launch_dangerous_cc.sh:135` | Booting a lane whose env carries a live **WRITE** client DSN | **CAI-1225 L2** | **KEEP-Canon** (TENANT-RESIDENCY-001 / client-data safety). |
| B7 | `scripts/lib/subagent_model_precedence.sh:17, 26` | Cheaper subagent models on the auditor lane (fail-closed when the lane is unprovable) | **CAI-1170** auditor clamp | **(b) KEEP** (audit quality). It never blocks a Musa instruction, only a cost optimisation. |

## C. Not cai gates (cai is one of several sufficient approvers, or the check is about something else): (b) KEEP

| # | file:line | Why it isn't a cai block |
|---|---|---|
| C1 | `scripts/apply_migration.py:247` `_DEFAULT_GATE_OWNERS = {"orch-console","cai"}` | Either approver suffices. Console alone gates every apply today (e.g. migrations 122, 123, 127 tonight). |
| C2 | `scripts/lib/exec_broker/gate_validation.py` + gzb `ihsanos-irsyad*/scripts/db/exec_prod.py:230` `_GATE_AGENTS = {"orch-console","cai"}` | Same: console OR cai. |
| C3 | DB `agent_messages_cai_gate` (fires only on `to_agent='cai'` review_request/question/blocker/challenge) + `strategic_decisions_cai_gate` (only `decided_by='cai'`) | These stop **cai** acting where `project_governance.cai_enabled=false` (cai is on only for `substrate`). They restrict cai and never block Musa. |
| C4 | DB `close_decision_by_audit`, `enforce_decision_audit_not_self`, `enforce_challenge_window_requires_expiry`, `project_governance_forbid_delete` | Integrity of decision **records** (auditor≠decider, audit before close). They gate the ledger, not any substrate action. Under op#34916 Musa may want `close_decision_by_audit` to accept a Musa override; that's flagged, not proposed. |
| C5 | `nervous_system/strategic_decisions_poll.py:47-63` | It would hold TASK-/BUG- decisions until `challenge_status='accepted'`, **including `musa_direct`**, which would be an (a). But it is **DORMANT**: no `wingmen_orch` loop runs on the Mini or gzb, nothing imports it, there are 0 `musa_direct` rows in 60 days, and the live statuses (`accepted_by_audit`/`_by_timeout`) don't match its filter. **If it is ever revived, make `musa_direct` bypass the challenge.** |
| C6 | `scripts/hooks/consult_governance_floor_guard.py` | Requires reading `docs/governance-floors.md` before asserting a floor. Not cai. |
| C7 | `scripts/hooks/console_irsyad_guard.py:27` | Keeps the console body off irsyad execution (Musa op#21944/22229). Not cai. |
| C8 | `scripts/lib/auto_agent_id.py:753` | Refuses forging an authority identity (CAI-RESP-258). It *protects* Musa's authority. |
| C9 | `scripts/reset_cai.sh:62`, `scripts/nudge_cai.sh:121` | Refuse clearing or nudging cai's own busy/composer pane. Data-loss guards on cai's session, not cai vetoes. |
| C10 | `nervous_system/console/governance.py:191` / `app.py:2931` | `money_clearance_enabled → true` needs a typed **operator** acknowledgement. Not cai. |
| C11 | `scripts/lib/require_verified_authorization.py` (used by A5) | Verifies the instruction came from Musa. **KEEP**, as you said. |
| C12 | `nervous_system/ihsan_gate.py:14`, `nervous_system/agent_wake.py:6`, `scripts/quality_gate_check.sh:15`, `scripts/boot_cai.sh:4` | Comments about future cai-gated phases. No enforcement exists today. |

## D. Canon hard constants enforced in code: KEEP-until-Musa-lifts

| Constant | Where enforced | Mode |
|---|---|---|
| **minors-PII** (fail-closed) | `scripts/lib/pii_safe_file_inspector.py:13` (fail-closed allowlist masking, CAI-1424); `scripts/irsyad_pii_containment_monitor.py:117` (hourly counts-only monitor; student-contact fail-closed); `scripts/hooks/client_media_read_guard.py` (refuses raw Read of inbound client media) | refuse / mask / page |
| **TENANT-RESIDENCY-001** | `scripts/launch_dangerous_cc.sh:135` (B6, refuses boot); `scripts/lib/provider_policy.py:136-160` (`ProviderNotAllowedError`, fail-closed provider allowlist; no client data on GLM); `scripts/lib/lane_db_residency_guard.py` (**detect-only**, never blocks); `scripts/residency_sweep.py` (audit + page) | refuse (2), detect/page (2) |
| **CAI-1034** (no raw client rows into context) | `scripts/lib/pii_safe_file_inspector.py:8`; `scripts/scope_tg_media_collisions.py:12`, `scripts/mark_shadowed_tg_media_rows.py:15` (metadata-only by construction) | by construction |
| **CAI-898 money bar** | **No code enforcement found.** The bar is applied by the console as a practice (deny-proofs + wet-prove), per governance-floors.md:96/124. | n/a |

## Summary
- **5 live cai holds to convert (A1-A5).** None of them touch money or residency directly. The highest-impact is A1 (user-facing retractions held on a cai amendment) and A3 (console armed apply).
- **7 cai-originated hard rules (B1-B7).** cai can't hold these at runtime, but they encode its rulings. I recommend keeping the mechanical safety checks and moving the *authority* to Musa (B1, B4 are Musa's call).
- **12 not-cai-gates (C), KEEP.** One dormant path (C5) needs a musa_direct bypass only if it's ever revived.
- **Canon constants (D), KEEP until Musa lifts them.** There's no code enforcement of the CAI-898 bar.
