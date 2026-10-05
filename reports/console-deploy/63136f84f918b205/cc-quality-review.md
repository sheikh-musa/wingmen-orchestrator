# cc-quality review — console content hash 63136f84f918b205

**Branch**: fleet-health/expected-token-display-gap (`~/wingmen/orch-expgap-wt`), commit
fe0e165 on top of 88c5a0c (my earlier PASS tonight, PR#295/hash dc14da69ac82861f).
**Hash verified myself** via `console_content_hash` against the worktree:
`63136f84f918b205` — matches exactly.

**Verdict: PASS.**

## The bug
Console's "expected" account column showed `Musa` for `cai`/`fleet-health`/`cc-orchestrator`
even though each has been pointer-pinned to `Syed` for a while (bus #51982/#46625, the "3
remaining mismatches" left after PR#291/#293/#295's display-gap fixes). Two separate causes:
1. `lane_token_resolver.resolve_lane_token_path` unconditionally returned `None` for
   `cai`/`fleet-health` — even though each has its OWN bespoke pointer file
   (`.cai_default_token` / `.fleet-health_default_token`), read directly by
   `boot_cai.sh`/`boot_fleet_health.sh` in bash, never through this module.
2. `cc-orchestrator`'s `.orch_default_token` lives on gzb (the hub's own host), not the
   console's own (Mini) checkout — the local-only read always misses it.

## What I verified myself, not taken on trust

- **Confirmed "zero effect on how they actually boot" precisely, not just for the named
  boot scripts**: grepped every caller of `resolve_lane_token_path`/the
  `lane_token_resolver` module across the whole repo (`coord_golive.py`,
  `lane_model_resolver.py`, `protected_agents.py`, plus the console files). Traced each:
  `boot_cai.sh`/`boot_fleet_health.sh` have zero references (confirmed empty grep).
  `lane_model_resolver.py` imports `family_of`/`_NO_POINTER_SINGLETONS` (the unchanged
  SET) /`_SESSION_POINTER` — never `resolve_lane_token_path` itself or the new
  `_SINGLETON_OWN_POINTER` dict, so its own independent model-tier resolution is
  structurally untouched by this diff. `protected_agents.py` only mentions
  `_NO_POINTER_SINGLETONS` in a comment, no actual call. `coord_golive.py` invokes the
  CLI for a different session (`irsyad-coord`, a worker lane, not in scope for this
  change) entirely. This is the one thing I specifically wanted to independently confirm
  given it's directly adjacent to the auditor-clamp/model-pin logic I was reviewing
  earlier tonight (PR#291/#292) — confirmed clean, this really is console-display-only.
- **Verified the hardcoded remote path against the real deployed convention, not
  assumed**: `_REMOTE_EXPECTED_SCAN_SH` hardcodes `$HOME/wingmen/orchestrator/
  .orch_default_token` for the SSH read. Checked `boot_orch.sh` (the script that
  actually runs ON gzb) — line 13: `ORCH_DIR="$HOME/wingmen/orchestrator"` — exact match.
  Not a guess; matches the real target host's own convention.
- `_read_pointer_target`'s fail-open contract (None on missing/unreadable/empty,
  never raises) is unchanged and reused correctly for the new `_SINGLETON_OWN_POINTER`
  branch — same shape as the existing `_SESSION_POINTER` tier just above it.
- Ran both directly affected test files myself: `tests/console/test_panes.py` +
  `tests/test_lane_token_resolver.py`, 80/80 pass (`PYTEST_NO_DB=1`).
- **Mutation-tested the core fix**: reverted the new `_NO_POINTER_SINGLETONS` branch
  back to a bare `return None` (the pre-fix behavior) and re-ran — the new
  `test_singleton_own_pointer_is_honored` test genuinely failed. Confirms real
  coverage, not just present coverage.
- `token_ground_truth`'s new fallback (`if exp_fp is None and include_remote:
  exp_fp = _remote_hub_expected_scan()`) is correctly gated the same way the existing
  live-scan fallback already is (`include_remote` only) — confirmed by reading both
  gates side by side, same shape, no divergence.

## Minor non-blocking note
`_REMOTE_EXPECTED_SCAN_SH`'s token read (`tok=$(cat "$tgt")`) relies on bash's
command-substitution auto-trimming trailing whitespace, but doesn't strip LEADING
whitespace the way the local Python side does (`f.read().strip()`, both ends). If a
pointer-target token file ever had leading whitespace, the two fingerprints could
diverge. Low-probability (no token file in this fleet is written with leading
whitespace by convention) and this exact asymmetry already exists in the
already-shipped `_remote_hub_scan`/`_REMOTE_SCAN_SH` pattern I passed earlier tonight —
not a regression introduced here, just worth knowing if anyone hardens that family of
scripts later.

## Self-reported incident (acknowledged, no action needed from me)
cc-fleet-health flagged running `deploy_console.sh` once by accident, which re-cd'd into
the live checkout and restarted the live fleet-console service — verified by them
post-hoc as harmless (content hash unchanged, matches already-deployed PR#295,
`/api/version` 200 after restart). Consistent with what I'd expect given the hash they
cited matches what I already passed tonight. Nothing further needed from me here.

No money/PII/live-tenant surface (read-only pointer-file + token fingerprinting,
raw token never leaves its host) — CAI-RESP-1440's cc-storefront gate doesn't apply.
Final from me. Clear to merge/deploy.

## Deploy steps
Backend-only (`panes.py`, `lane_token_resolver.py`), no static-file version bump needed
— matches the author's own framing ("no visual/template change"). Hosted console
process needs a restart to pick up the fix, same as any backend-only console change.
