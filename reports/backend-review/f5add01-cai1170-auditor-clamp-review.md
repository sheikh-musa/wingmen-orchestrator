# Review: f5add01 — CAI-1170 auditor opus-4-8 clamp in the launch cascade

**Reviewer:** cc-quality (Opus 4.8). **Requester:** cc-fleet-health. **Verdict: PASS** (3 non-blocking observations).
Safety/governance path — the clamp that keeps FULL auditors (cc-quality, cc-storefront) on opus-4-8 for money/PII verdicts.

## Verified at SOURCE + EXECUTION
- **A. Clamp fires (mutation-proven):** guarded the clamp condition with `false &&` → 6 tests go RED, incl. `test_auditor_clamped_to_opus_over_fleet_sonnet[quality|storefront|cc-quality|cc-storefront]` + `_overrides_session_pin` + `_overrides_model_env` (line 143: storefront+MODEL=sonnet → sonnet without the clamp = the exact regression). Restored from git; 15/15 green. Tests exercise the SHIPPED path (harness `subprocess` sources the real `model_precedence.sh` and calls `resolve_lane_model`).
- **B. Cascade byte-identical:** `diff` of old `resolve_lane_model` body vs new `_resolve_lane_model_raw` = only the function-name line differs. Rename, not edit.
- **C. Overrides all tiers incl MODEL env:** direct shell exec — `resolve_lane_model quality` with `.fleet_model=sonnet` AND `MODEL=claude-sonnet-5` → `claude-opus-4-8 / AUDITOR-CLAMP (CAI-1170)`. The clamp runs after the raw cascade and keys only on the final model, so every tier is covered.
- **D. SSOT integrity:** `fleet_model.sh` now `source`s `lib/auditor_lanes.sh`; its flip carve-out (line 99) still resolves `$AUDITOR_LANES`; `is_auditor_lane` matches quality/storefront/cc-quality/cc-storefront, rejects `orch`.
- **E. Clamp scoped:** non-auditor `orch` → `claude-sonnet-5 / .fleet_model` (untouched); already-opus auditor keeps its real tier (`test_auditor_already_opus_keeps_its_tier` green, clamp-independent) — no false "AUDITOR-CLAMP" label.

## Design call C — no launch-time escape hatch: I AGREE
CAI-1170 is a governance/safety invariant (an auditor rendering money/PII verdicts MUST be opus-4-8). A launch-time env escape hatch would re-open precisely the hole that let cc-storefront launch on Sonnet. The governed way to change an auditor's model remains the deliberate `fleet_model.sh --all` flip (updates the pin under governance). Strictest reading is correct for a safety clamp.

## Non-blocking observations (follow-ups, not merge-blockers)
1. **SSOT'd the LIST, not the MATCHER.** `fleet_model.sh:99` still reimplements the match inline (`printf '%s ' $AUDITOR_LANES | grep -qw "$sess"`) instead of calling the shared `is_auditor_lane()`. So two matchers now exist and can drift — notably `is_auditor_lane` strips the `cc-` prefix but the inline grep does not (works today only because fleet_model passes bare lane names). This is the same two-copies-drift class the commit set out to kill, one level down. Recommend fleet_model.sh also call `is_auditor_lane`.
2. **`grep -qw -- "$s"` treats `$s` as a regex.** Harmless for alnum lane names; `grep -Fqw` would be marginally more robust. Trivial.
3. **Instance-suffix granularity (forward-looking).** `is_auditor_lane` matches the bare lane, so a hypothetical multi-instance auditor `cc-quality-1` → `quality-1` would NOT match `quality` (word-boundary) and would launch UN-clamped. No live gap — current auditors are singletons — but if an auditor lane ever runs multi-instance, the clamp misses the suffixed sessions (same lane-vs-instance class as PR#75's self-accept bypass). Worth a comment or a suffix-tolerant match before that day.

**PASS** — core clamp correct, mutation-proven, cascade preserved byte-identical, correctly scoped. Observations are follow-ups.
