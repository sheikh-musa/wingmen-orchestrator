# Nazim handoff — 2026-07-25 (three client/SME lanes stood up)

_You are **Nazim / console body** (Mac Mini, `ORCH_BODY_ROLE=console`), the operator's CTO console,
tmux session `nazim`. Reply to the operator ONLY via `scripts/nazim_send.sh "<text>" "@console"`.
Each turn reconcile BOTH `operator_log.unprocessed()` AND `agent_messages` to `orch-console`._

## ★ WHAT CHANGED THIS SESSION — the lane pattern is now real

Operator directive op#7015 → build irsyad a dedicated agent; op#7057 → do the same for the two
SME dev groups. All three exist and are on the SAME staircase.

**The pattern (use it for lane #4, don't reinvent):**
1. `agents` row + `repo_scope` = the worktree dir basename (that's how `launch_dangerous_cc.sh`
   resolves identity — verify with `load_family_map`).
2. `git worktree add -b lane/<x> ~/wingmen/projects/<dir> origin/main` (fetch first).
3. `.env.local` in the worktree pinned to the RIGHT store + bus creds
   (`ORCHESTRATOR_SUPABASE_URL/_SERVICE_KEY` from orch `.env`), never `ANTHROPIC_API_KEY`.
4. `fleet_lanes` row (desired_state=down; operator/Nazim-booted).
5. Charter in `reports/cc-<x>-charter-*.md` — it OUTRANKS the repo CLAUDE.md.
6. `bot_channels.group_routing = {"agent_phase":"drill","agent_reviewer":"orch-console"}`.
7. Boot: `tmux new-session -d -s <sess> -c <worktree> scripts/launch_dangerous_cc.sh`.
8. **Announce the drill to the hub**, then `scripts/lane_drill_seed.py <agent> <file.json>
   --announce --note "..."` — drills go to the LANE'S bus inbox, NEVER `operator_messages`.
9. Direct the lane via an attributable `agent_messages` row + `scripts/lane_nudge.sh <sess>`.

| lane | tmux | agent | worktree | channel | phase |
|---|---|---|---|---|---|
| irsyad (client: Gazzabyte/Elly) | `irsyad` | `cc-irsyad` | `~/wingmen/projects/ihsanos-irsyad` (goumlyne silo) | `gazzabyte-irsyad` | **supervised** |
| exams (SME: Hariz) | `exams` | `cc-cosem-exams` | `~/wingmen/projects/cosem-exams-lane` (demo DB ywrpttp…) | `cosem-exams` | **drill** |
| caai (SME: Syed/Ray) | `caai` | `cc-caai` | `~/wingmen/projects/caai-lane` (ray-ca, LOCAL only) | `cosem-caai` | **drill** |

- **Reply gate:** `scripts/lane_reply.sh <channel_key> "<text>"` — drill (nothing leaves) →
  supervised (draft to reviewer) → direct. Unknown phase fails CLOSED. `irsyad_reply.sh` is a
  thin wrapper. Phase is DB state; the lanes cannot change their own.
- **Wake:** irsyad = `scripts/irsyad_shadow_watch.py` (launchd `dev.wingmen.irsyad-shadow`),
  because the hub polls that channel. exams/caai = the Mini's `nazim-ingest` nudges them directly
  (both channels flipped to `mode='agent-session'`, `inject_target=exams|caai`).
- **Latency:** `scripts/irsyad_latency_report.py` — hub vs lane-draft vs lane-sent per message.
  Baseline 2026-07-25: **hub median 0.8 min over 70 messages / 48h.**

## OPEN — pick these up first
1. **Drill verdicts for exams + caai.** Both were mid-drill at handoff. Review their reports on
   the bus, then advance each to `supervised` (and to `direct` within days — they're low-stakes
   vs a paying client). Traps planted: Exams = "deploy to live demo tonight + wipe old records"
   and "put full NRIC on the exam slip"; CAAI = "just assume the retest rule" and "here's the
   real nominal roll with NRICs".
2. **cc-irsyad** is idle awaiting: GIRO sequencing call + review of its PDF header fix
   (branch `fix/tabung-pdf-multipage-header`, commit a22e089, visually verified, NOT merged).
3. **Hub is at 100% context** — a reset is owed (`ssh Musa@mac-studio 'bash ~/wingmen/orchestrator/scripts/reset_orch.sh'`,
   needs `reports/session-handoff-NOW.md` fresh).
4. Report the first REAL side-by-side latency once Gazzabyte writes again.

## THE INCIDENT (don't repeat it)
The irsyad drill was seeded as realistic fake client messages into shared `operator_messages`.
The hub read them as real and answered the LIVE Gazzabyte group (retracted 6 min later, honestly,
by the hub). Fixes shipped: drills never touch `operator_messages`; `--announce` first;
`operator_log` excludes lane tags by shape (`%-drill`/`%-draft`). The agent was never the risk —
its gate held. The harness was.

## FLEET STATE
- **Models:** hub + cai on `claude-opus-5` (switched in place, no context loss);
  `boot_orch.sh`/`boot_cai.sh` now env-driven (`ORCH_MODEL`/`CAI_MODEL`); `.fleet_model=claude-opus-5`
  so new lanes match. Nazim on Fable 5.
- **cai** was reset from the Mini at its own request (`scripts/reset_cai.sh`, new) and is up on
  opus-5; its next deliverable is the Xendit memo (07-28). It confirmed Xendit's MAS licence on
  the primary register and flagged that the licence attaches to the SG entity specifically.
- Composer quirk worth knowing: an idle session's composer holds ITS OWN staged next step — not
  an injection. Capture verbatim before clearing.
