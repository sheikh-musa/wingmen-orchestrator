# Send-keys automation census (CAI-RESP-381 order 4)

**Date:** 2026-07-03 · **Author:** cc-orchestrator · **Trigger:** the lane_watchdog phantom-injection incident (it auto-resubmitted staged "YES PURGE" text into the `cai` governance console every 300s; 11 phantom claims, 0 executions — gate held). Order 4: enumerate every send-keys-capable automation in the fleet with declared target scope, fold into the self-audit.

## Rule (from the incident)
A **governance / conversational console** (`cai`, `orch`, ai-responder persona consoles) must never receive a *content* keystroke or a *bare Enter that could submit staged content* from an automation. Sanctioned into these consoles: **nudge-only, provenance-headed, count/lifecycle pointers** (CAI-RESP-357/377 R1) — never operator words, never authorization claims, never a blind submit.

## Enumerated automations

| # | Source | Target scope (declared) | What it sends | Running now | Risk / status |
|---|---|---|---|---|---|
| 1 | `nervous_system/lane_watchdog.py` | all live tmux sessions EXCEPT `orch`/`orchestrator` (NON_LANE) and now `cai`+`mamadah`/`nutri`/`mizan` (GOVERNANCE) | verified auto-submit of a lane's OWN unsent text via lane_nudge.sh | **killed** (CAI-RESP-381), re-ships after review | **PATCHED this change:** governance consoles escalate-only (no keystroke); IDLE_UNSENT attempt-capped (MAX=1) to kill the infinite-replay. Was the incident source. |
| 2 | `nervous_system/agent_wake.py` | default targets include **`cai`** + lanes | bare `_SIGNAL` lifecycle pointer **+ Enter** (no C-u line-clear) | on-demand (not a daemon) | ⚠️ **SAME-CLASS LATENT RISK:** a bare Enter into `cai` with pre-staged text would SUBMIT that staged text (the exact incident mechanism). Recommend: clear the line (C-u) before the signal, and/or governance-aware. Deferred to a reviewed patch. |
| 3 | `nervous_system/agent_messages_realtime.py` | resolves target per bus row, can include `cai` | wake nudge on new agent_messages INSERT | not a daemon now | ⚠️ same-class: verify it only sends a lifecycle pointer + never a bare submit into a governance console. Deferred to a reviewed patch. |
| 4 | `nervous_system/ingest.py` | `inject_target` per bot_channels row (`orch`/`cai`) | **nudge-only** count line ("N unread…"), never payload | **live** (dev.wingmen.ingest) | ✅ R1-compliant by construction. |
| 5 | `nervous_system/cai_bridge.py` | `cai` (CAI_BRIDGE_TMUX_TARGET) | injected operator content (legacy) | **retired/dead** | ⚠️ superseded by ingest; DELETE the module to prevent re-activation. |
| 6 | `nervous_system/tg_bridge.py` | `orch` (TG_BRIDGE_TMUX_TARGET) | injected operator content (legacy) | **retired/dead** | ⚠️ superseded by ingest; DELETE the module. |
| 7 | `nervous_system/irsyad_support_bridge.py` | `orch` (IRSYAD_SUPPORT_BRIDGE_TMUX_TARGET) | prefixed group message into hub | **live** (dev.wingmen.irsyad-support-bridge) | migrate to ingest `log-and-route` (perimeter posture unchanged); interim ok — targets hub, not a governance-decision console. |
| 8 | `scripts/lane_nudge.sh` | whatever `$SESSION` arg (used by #1) | C-u clear → retype → Enter → verify | helper | ✅ clears the line first (no staged-text submit); only invoked by the watchdog, now never for governance consoles. |
| 9 | `scripts/nudge_cai.sh` | `cai` (`=cai:0.0`) | **count-only** provenance header, free text rejected | on-demand | ✅ the R1-sanctioned cai injection. |
| 10 | `scripts/spawn_reviewer.sh` | `reviewer-*` sessions | bare lifecycle pointer (context read on boot) | on-demand | ✅ transient reviewer only; no governance console. |
| 11 | `scripts/fleet_model.sh` | lanes; explicitly LEAVES `orch`/`cai` alone | `/model` switch via verified submit | on-demand | ✅ core brains excluded by design. |
| 12 | launchd `dev.wingmen.window-wake`, `dev.wingmen.reminder-zoho` | `=orch` | one-shot reminder line into hub | timers | hub (`orch`) only; operator-attended; not a governance-decision auto-submit. Acceptable. |

## Actions
- **Done (this change):** #1 patched + attempt-capped; census recorded.
- **Reviewed re-ship:** #1 restored for real lanes on the Studio after cc-reviewer pass (order 2/3).
- **Follow-up (reviewed patches):** #2 + #3 close the same-class bare-submit risk (clear line before signal, governance-aware); #5 + #6 delete the retired bridge modules; #7 migrate to ingest.
