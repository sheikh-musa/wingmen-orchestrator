# GLM (z.ai) playbook — canonical, boot-loaded

Doctrine, not live state. Query the live numbers fresh; never trust a cached %.
This exists so a freshly-booted console does NOT re-derive GLM mechanics from
script headers every time (Musa, nazim-console, 2026-10-16: "every time i talk
about glm i see you trying to figure out what it is and how to do the move…
everything is documented" — the token-waste this file kills). First slice of the
coherence/canon SSOT ([[project_coherence_ssot_initiative]]).

## The one command you almost always want
```
.venv/bin/python3 scripts/glm_overflow_switch.py            # READ-ONLY (default): prints GLM wk/5h %, Musa headroom, # of glm lanes, and whether a switch is due
.venv/bin/python3 scripts/glm_overflow_switch.py --apply    # MOVE every GLM lane -> Musa token (use when GLM is spent / Musa says "move to musa")
```
"GLM burned through / move active lanes to musa" == run it with `--apply`.
If the read-only run already says `glm lanes=0`, there is **nothing to move** —
the peak/overflow automation already did it; just confirm state and report.

## How a lane is "on GLM" (the only signal that matters)
Its per-lane model pointer `.<session>_model` says `glm-*`. The launcher routes a
`glm-*` model to z.ai; the lane's Claude OAuth token is unchanged. So "move to
Musa" = move the `glm-*` pointer aside (renamed `.bak-glmoverflow-<UTC>`,
restorable) so the lane resolves to its default Claude model, then relaunch with
`switch_lane_token.sh --model-apply --drain` onto Musa's token, and verify the
live argv no longer says `glm-*`. `glm_overflow_switch.py` does all of this.
To eyeball by hand: `for f in .*_model; do echo "$f=$(cat $f)"; done | grep glm`.

## Switch trigger rule (what the automation enforces)
- GLM weekly ≥ 95% → SWITCH all glm lanes to Musa.
- GLM 5h ≥ 95% AND weekly ≥ 90% → SWITCH.
- GLM 5h ≥ 95% with weekly < 90% → PAGE only (5h resets itself; waiting is cheaper).
- Musa 5h or 7d ≥ 90% when a switch is due → PAGE instead of switching (don't exhaust Musa too).
- NEVER auto switch-back; switch-back is the console's judgment call.

## Peak automation (no human at fire time)
`scripts/glm_peak_pause_resume.py` (launchd, runs every few min). **Peak window =
06:00–10:00 UTC** (z.ai peak 14:00–18:00 UTC+8). At peak: a glm lane doing URGENT
work MOVES to Musa and keeps working; a glm lane doing backlog PAUSES (0 pool
burn). Off-peak: reverse both. The one human job: keep `fleet_lanes.glm_peak_urgent`
current as the day's hot work shifts (a coordination call the script can't infer).

## Account / token fingerprints (verify with `verify_fleet_token.py`)
- **musa**  = `68142948c003` — fleet default lane pool (most cosem lanes).
- **syed**  = `582043088eae` — console body's pool; also where fleet-health parks a
  lane it recovers off exhausted GLM (observed 2026-10-16: adcda-1 429'ing → re-tokened to Syed).
- **musa2** = irsyad lanes' pool.
Both musa and syed are healthy Anthropic pools; a recovered lane on Syed is fine —
only move it to Musa if the operator wants uniformity (no churn otherwise).

## Hard rules / gotchas
- **cosem-adcda-photos is HARD-EXCLUDED from GLM** forever (minors / image residency;
  vision-MCP-off condition). The peak-resume branch must never flip it back TO glm.
- The GLM key (`GLM_CODING_KEY`) is leak-flagged; continued use is operator-accepted
  risk (Musa op#24626, 2026-10-01).
- GLM weekly quota reset prints in the 429 / overflow readout (e.g. this week: 2026-10-20 03:31Z).
- **Cancel the z.ai/GLM plan before it renews 6 Nov 2026** — surface to Musa by ~4 Nov (ask #3447).
- Fail loud: an unreadable GLM or Musa reading raises; a monitor that silently reads
  "fine" because its probe failed is worse than none.

## Related memories
`[[feedback_glm_peak_hours_pause_or_move_daily_proactive]]`,
`[[reference_provider_keys_vault_fetched_check_running_state]]`,
`[[feedback_no_bus_rows_to_paused_lanes_during_zero_burn]]`.
