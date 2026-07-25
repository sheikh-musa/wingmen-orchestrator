# cc-support v1 — full spec + boot recipe

**Status:** DRAFT for operator LOOK (via Nazim relay) before spin. Author: cc-orchestrator (hub). Date: 2026-07-15. Refs: op#4443, op#4493, op#4406 (standing grant), Nazim steer #8899/#8913, hub v1 #8840, Nazim endorse #8843.

**One line:** a dedicated always-on Claude Code agent that OWNS the irsyad/Gazzabyte support group — reads inbound, answers directly with grounded product knowledge, and escalates UP only for money/PII/residency/builds/real-bugs or anything below high-confidence. It relieves the hub of hand-answering the live client group (the direct cause of the CBD/2h-stall + hub context bloat).

---

## 1. Why (the problem it removes)

Today every message in the Gazzabyte/irsyad group is `log-and-route` → it lands on the **hub**, and the hub hand-answers. That is:
- the direct cause of hub context bloat + the 2h silent-degrade (a marathon hub session carrying every client Q), and
- slow for the client (the hub is often mid-build when a support Q arrives).

cc-support moves client answering off the hub onto a purpose-built, always-on agent with its own context budget, so the hub is free for orchestration and the client gets fast, grounded replies.

## 2. Identity & channel (GROUNDED — already exists, no new BotFather step)

- **Bot:** the Irsyad-Support bot, token `IRSYAD_SUPPORT_BOT_TOKEN`. Structurally distinct from `@wingmennorchbot` (hub, pen-iv) and `@nazim_cto_bot` (Nazim) — so its voice can never be confused with the hub's or Nazim's. ✅ **confirms the "distinct bot identity + channel" requirement (Nazim #8843 / cai 458).**
- **Channel:** `bot_channels.channel_key = 'gazzabyte-irsyad'`, group chat `-5330147776`, tag `gazzabyte-irsyad`, currently `enabled=true`, `mode='log-and-route'`.
- **agent_id:** `cc-support` (exact), like `cai`/`cc-infra` — for attributable bus rows and identity-canon enforcement.
- **Outbound path (exists):** `scripts/irsyad_support_send.sh` — perimeter-scoped to the Gazzabyte group only, logs every reply to `operator_messages` (tag `gazzabyte-irsyad`). cc-support uses THIS, never `tg_send.sh` (which can't reach the group anyway).
- **Inbound path (exists):** the unified `nervous_system/ingest.py` already long-polls this channel; every inbound is durably logged to `operator_messages`.

## 3. Ownership & scope

- cc-support OWNS the `gazzabyte-irsyad` thread end-to-end under the operator-thread-owner-closes doctrine: whoever opened the thread owns the close. The hub STAYS OFF this channel once cc-support is live (except as the escalation target).
- **Standing grant op#4406 transfers to cc-support** (reply to the client DIRECTLY, promptly, never wait for the operator) — under the hard gates in §5.
- Perimeter discipline (inherited from `irsyad_support_bridge.py`): operates in exactly ONE chat; never surfaces cross-client / fleet / personal data; deny-by-default on any other chat_id.

## 4. Grounding — answer from ACTUAL live product state (non-negotiable, Nazim #8843)

cc-support must never invent product behavior. It grounds every answer in:
1. **A curated product knowledge base** (checked-in doc set): the shipped tabung flow (empty-tin, denomination counts, slips, class/keluarga reports), donation flows, POS/storefront flows, and the irsyad-specific config. Seeded from `docs/` + the shipped-feature list; versioned so it tracks reality.
2. **Read-only live state** when a Q is data-specific: read-only queries against the **irsyad silo (goumlyne) `goumlynecruxrlmzlntp`** (LAYER-VOCAB-001 — irsyad DATA lives on goumlyne, NOT ceayj). A scoped read-only DSN, no write path.
3. **Escalate-not-guess:** below high-confidence, it does NOT answer — it escalates (see §6) and tells the client a real human/specialist is checking. No confident-but-wrong answers.

## 5. HARD gates (inform-not-execute)

cc-support can INFORM but NEVER EXECUTE any of:
- **Money** — refunds, totals corrections, fee changes, ledger anything → escalate to hub/cai; cc-support may quote read-only figures it has reconciled, but never asserts a correction or moves a number.
- **PII** — never echoes NRIC/phone/email/address of any person; never exports a person list; a PII request is escalated.
- **Residency / provisioning** — new org/silo, data moves, access grants → escalate (hub+cai gate, TENANT-RESIDENCY-001).
- **Builds / real bugs** — a reproducible bug or a feature ask → escalate as an attributable bus row (becomes a lane task), never "I'll build that."

Every reply (draft and sent) is logged (`operator_messages` + the send script already logs). Money-discrepancy answers follow the "reconcile BOTH sides to source before reassuring" rule (memory: money-discrepancy-reconcile-both-sides).

## 6. Escalation protocol (attributable, never make the hub type)

- Escalations are **`agent_messages` rows** `from_agent='cc-support' → to_agent='cc-orchestrator'` (or `cai` for governance), with provenance and the client's need summarized — NEVER raw-relayed operator/client text as bare authorization, NEVER a keystroke injection (R1/CAI-RESP-377).
- The hub picks these up on its normal bus reconcile, acts/gates, and either hands cc-support the answer to relay or handles the gated action itself.
- cc-support tells the client a realistic time window (memory: manage-client-expectations) — never softens a multi-hour escalation into "shortly."

## 7. Supervised break-in → graduation (the no-gap safety, cai/Nazim #8843)

- **Phase A (supervised):** cc-support DRAFTS every reply and posts it as a `to_agent='cc-orchestrator'` bus row `message_type='support_draft'` (client text + its grounding + confidence). The **hub approves** (or edits) before it is sent via `irsyad_support_send.sh`. Nothing reaches the client unreviewed. The client never gets a wrong or absent reply during handoff.
- **Graduation criterion:** after **N=15 consecutive hub-approved drafts with zero edits** across a representative mix (incl. at least one money/PII/residency escalation correctly withheld), the operator flips it to Phase B.
- **Phase B (unsupervised):** cc-support sends directly under §5 gates; the hub spot-audits the log. Any gate breach or wrong answer → auto-revert to Phase A.

## 8. Reconciliation loop (per-turn discipline, mirrors the hub bridge)

Every turn / wakeup: read `operator_log.unprocessed()` **scoped to channel `gazzabyte-irsyad`** → answer (or draft, Phase A) any unhandled inbound → `operator_log.mark_handled_through(<max_id>)`. Durable log is the source of truth (Option B / CAI-RESP-277); the keystroke nudge is signal-only. A terminal answer alone does NOT reach the client — the send script does.

## 9. Boot recipe

**New files (all tracked):**
- `scripts/boot_support.sh` — sources `.env`, **`unset ANTHROPIC_API_KEY`** AND scrub the tmux server-global (memory: metered-api-tmux-server-global — verify via `ps eww`, absent/empty = Max), sets `ORCH_BODY_ROLE=support` (NOT a pen-holder — cannot touch the five singleton pens; `orch_lease.py check` fail-closes hub pens for it), then `claude --dangerously-skip-permissions` in the support worktree. Mirrors `boot_cai.sh`.
- `prompts/support_boot.md` — the cc-support system prompt: identity, the §4 grounding rule, §5 gates, §6 escalation, §7 phase, §8 loop.
- `docs/support/knowledge-base.md` — the curated product KB (§4.1).

**Registry + env:**
- `bot_channels`: set `gazzabyte-irsyad.mode='agent-session'`, `inject_target='support'`, `inject_prefix='📩 irsyad-support (client): '`, `group_routing={'nudge_when_busy': true}`. (Reversible kill-switch: set back to `'log-and-route'` → hub reclaims the channel instantly.)
- `.env` (on the boot host): add `SUPPORT_READONLY_GOUMLYNE_DSN` (a read-only role on goumlyne — provisioned BEFORE first use; no write grant), and confirm `IRSYAD_SUPPORT_BOT_TOKEN` + `IRSYAD_SUPPORT_GROUP_CHAT_ID`.

**Boot command (operator/Nazim runs; Syed's token):**
```
tmux new-session -d -s support -c ~/wingmen/wingmen-support scripts/boot_support.sh
```
- **agent_id:** `cc-support`. **Host:** recommend the Studio hub host (always-on, same as cai) — operator/Nazim confirm. Registered in `fleet_lanes` desired_state as operator-booted (like cc-cai), NOT lanes.sh-autoscaled for v1.
- **Boot memory:** cc-support reads a scoped boot briefing (its KB + the shipped-feature list + open irsyad QA items), NOT the full fleet boot_briefing.

## 10. Rollout

1. Provision the read-only goumlyne role (residency-safe; no writes).
2. Land the 3 new files + KB; land the `bot_channels` mode change (reversible).
3. Boot cc-support in **Phase A** (supervised drafts) — run through a real client day; hub approves each draft.
4. On 15 clean drafts (incl. a correctly-withheld gated ask) → operator flips to Phase B.
5. Kill-switch always available: `mode='log-and-route'` → hub owns the channel again.

## 11. Open questions for the operator (the LOOK)

1. **Boot host** — Studio hub (always-on, recommended) or the Mini?
2. **Graduation number** — 15 clean supervised drafts, or higher for a live client group?
3. **KB scope for v1** — irsyad/tabung only, or include the storefront/POS flows now (Hadi/Hadramawt are separate clients on ceayj — cc-support v1 is irsyad-only unless you want it broader)?
4. **Read-only silo access** — confirm a read-only goumlyne role is acceptable (it is residency-safe; strictly no writes).
5. Anything it must NEVER say / must ALWAYS escalate beyond §5?

---
*Nothing here is spun until the operator's LOOK. On approval, hub lands the files + reversible registry change and boots cc-support in Phase A.*
