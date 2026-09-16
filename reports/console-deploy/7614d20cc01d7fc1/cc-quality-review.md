# cc-quality console deploy-gate review (SECURITY-CRITICAL: hosted-view fp exposure)

- **Content hash:** `7614d20cc01d7fc1` (recomputed from the live tree — matches requester; **no drift** vs commit `a169153` on the gated files)
- **Commit:** `a169153` — feat(console): pool-per-lane badge on hosted view + at-a-glance key roll-up (Musa op#20684)
- **Branch:** `fable/substrate-safe-fixes`  ·  **Build:** fc-v61 → fc-v62
- **Reviewer:** cc-quality (Opus 4.8 — `.quality_model=claude-opus-4-8` confirmed at source)
- **Date:** 2026-09-16  ·  **Requester:** orch-console (bus #40662)

## Verdict: **PASS** — safe to ship (one non-blocking deploy-hygiene note below)

The security-critical requirement — **no `auth_fp` key and no raw fingerprint string anywhere in the hosted (external) payload** — is met, and the pre-existing leak the requester flagged is fully closed. Proven empirically on live substrate, not by source-reading alone.

## Security crux — EMPIRICALLY PROVEN CLEAN (the strongest check)
Built the **actual** hosted payload against live substrate (`hosted_view.build_cloned_payload` on the read-only DSN — the exact fn `hosted_server.py:171` serves) and walked the whole thing:
- **`auth_fp` key: absent everywhere** (recursive key walk over all 8 sections). No `fp`-variant key of any kind.
- **All three raw fingerprints absent** from the serialized blob (`68142948c003`, `e1dfa48eec85`, `582043088eae`).
- **Zero 12-hex-char runs anywhere** in the payload — the completeness guard: even an *unknown* key would surface as a hex run, and there are none.
- `pool` present with only the nicknames (`Musa`/`Syed`/`musa2`); sample lane/coordinator/context_bloat rows confirm `pool` in, `auth_fp` out.

Pre-change, all three clones (`_clone_lanes`/`_clone_coordinators`/`_clone_context_bloat`) emitted `auth_fp=auth_fp` — a real leak contrary to the file's own cond-2 docstring. This commit replaces each with `pool=pools.pool_for_fp(auth_fp)`. **Leak closed on all three; proven on rendered output.**

Note: the prove_scrub_cloned harness checks money/PII-digit-run/UUID/terms but **not the `auth_fp` key by name**, and a hex fp is an unreliable digit-run hit (`e1dfa48eec85` has no 6-digit run) — which is why I proved absence by direct key-walk + string-grep rather than leaning on the existing harness.

## Full domain enumerated (completeness)
- Two payload builders: `build_minimized_payload` (fp-free already — emits agent/status/activity/host/repo/sha; pools come from the `pool_usage.pool` nickname column) and `build_cloned_payload` (the one **served**). Both fp-free post-change.
- Of the cloned payload's 8 sections, **only** lanes/coordinators/context_bloat ever read `auth_fp`; all three now emit `pool`. The other five (needs_you/backlog/queue/pool_usage/deploys) never touch fp — confirmed by full-file grep.
- `pool_for_fp` never returns the fp: unknown/absent/too-short → `""` (no badge), never a leak. Prefix match `_PREFIX=7`.

## SSOT lockstep — independently verified (not just via the test)
Parsed all three maps and cross-checked: every JS prefix maps to exactly one backend fp with the **same** nickname, all three nicknames represented, **no prefix collision**.
- backend `pools.KNOWN_POOLS` = {68142948c003→Musa, e1dfa48eec85→musa2, 582043088eae→Syed}
- `fleet.js POOL_FP` = [68142948→Musa, e1dfa48→musa2, 582043088→Syed] — LOCKSTEP ✓
- `irsyad.js acctForFp` — LOCKSTEP ✓
- `panes._KNOWN_ACCOUNTS = dict(pools.KNOWN_POOLS)` — fixes the stale copy that was **missing musa2** (additive; a new known account, no downstream break).

## Roll-up + UI (op#20684)
- `poolRollup`: counts **live (non-offline)** lanes per pool in canonical POOL_FP order, unknown-key lanes bucketed last as "unknown" (`?`). Pure fn, node-tested.
- `renderKeyRoll` self-heals a stale filter (`if (poolFilter && !r.counts[poolFilter]) poolFilter=""`) — no stuck-empty filter when a pool's lanes all go offline.
- Off-pool filter: tiles **dim (`.offpool`), never hide** — operator keeps the whole fleet in view. The `?` branch inversion (`pool || !l.auth_fp`) correctly lights only unknown-key lanes.
- `pool` is not attacker-controlled (only `""` or one of 3 hardcoded backend nicknames) and is `esc()`d regardless.
- **CSS ~400px:** `.keyroll` flex-wrap + `min-width:0`, `.krchip{white-space:nowrap}`, `.krhint{flex-basis:100%}` (hint on its own line), `.keyroll:empty{display:none}`. Wraps cleanly, no horizontal overflow. Colorblind-safe (color + text label), theme-aware (currentColor/vars), `<button>` + container aria-label.

## Verification performed (verify-not-assert)
- Live hosted-payload leak proof (above) — the decisive check.
- `node fleet_keyroll.test.js` → **5 passed** (incl. "unknown = no chip on hosted", "counts LIVE lanes per pool in canonical order, unknown last").
- `node fleet_pace.test.js` → **15 passed** (fc-v61 regression clean).
- `python3 -m pytest tests/console` → **191 passed** (incl. `test_pools.py`: hosted payload has `pool` never `auth_fp`, SSOT lockstep, local rows carry pool). ("Task was destroyed" lines = benign asyncio teardown noise.)
- Version-sync (deploy Gate 1): sw.js = fleet.js = lanes.html = irsyad.js = **fc-v62**.
- Content-hash re-derived == `7614d20cc01d7fc1`; gated-file diff vs `a169153` empty.

## Accepted / not findings
- **Local `/api/fleet` keeps `auth_fp`** (app.py `_fleet_payload` + `_context_bloat` now carry `pool` *alongside* `auth_fp`). Acceptable and intended: the local console is the authed internal (LAN + bearer) surface; the fp there is the operator's own non-secret short id. The **external** surface is the hosted view, and that is now fp-free. Requester explicitly scoped this as OK.

## ⚠️ Non-blocking deploy-hygiene note (orch-console's call, alert-not-block)
`scripts/check_console_version_cadence.py` **warns**: fc-v62 is the **3rd** console version bump today (fc-v59, fc-v61, fc-v62). Advisory only — **not** one of deploy_console.sh's 4 gates, so it will not block. Since fc-v61 (my prior review, hash 939b11c436a22d15) may not have deployed yet, **recommend batching v61+v62 into a single fc-v62** if v61 hasn't shipped — spares the operator's PWA the repeated AD↔SG cache re-churn (the Aug-3 class). Ship-now vs batch is your decision; no code change requested. You run deploy_console.sh; I have not deployed.

— cc-quality
