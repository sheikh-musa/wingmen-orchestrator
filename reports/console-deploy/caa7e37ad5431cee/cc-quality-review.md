# cc-quality — console-deploy Gate 4 review: deploy-probe fail-loud + poolRollup dead-lane drop (backlog#135/#137)

- **op:** held-commitment backlog items, orch-console #79249 (item 1) / #79252, due today — self-served review request (precedent orch-console #83531 citing #81035/#81059/#81098)
- **Branch:** `lane/console-revamp-backlog135-137`
- **Reviewed head:** `c8a16a9f70dc48eeafca53b2f3144f89cd34fdc8`
- **Branch base:** `origin/fable/substrate-safe-fixes`
- **Content hash:** `caa7e37ad5431cee`
- **Reviewer:** cc-quality (ran on claude-opus-4-8)

## VERDICT: GO / PASS (two LOW non-blocking notes)

Content hash INDEPENDENTLY recomputed at the reviewed head (`source scripts/lib/console_deploy_manifest.sh; console_content_hash`) = **caa7e37ad5431cee** — exact match with the requested hash. Dead-man's-switch satisfied; this review binds to exactly the shipping content.

Both fixes are correct, well-scoped, fail-safe, and test-locked. I ran every cited suite myself at `c8a16a9` (not just trusting the claim): **129 Python passed** (`tests/test_console_deploy_probe.py` + `tests/test_deploy_console_gate.py` + `tests/console/test_app.py`) and **14/14 console `*.test.js` green** (including the new `fleet_keyroll.test.js`). The `protected_agents` DB-read UserWarning is the known harmless teardown noise.

### backlog#135 — deploy probe fails loud on the bound address (PASS)
`scripts/lib/console_deploy_probe.sh::console_deploy_probe` retries the **bound tailnet address** (never loopback) with `curl -m 3`, up to `attempts` (default 5) with `delay` (default 1s) between; on a non-empty body it prints it and returns 0; on exhausting retries it prints `COULD NOT MEASURE /api/version` to **stderr** and returns non-zero. `deploy_console.sh` consumes it as `if SERVED=$(console_deploy_probe ...)` — success logs `served=$SERVED`; failure logs `served=COULD_NOT_MEASURE` and calls `fail "... COULD NOT MEASURE ... verify the service manually ..." 8` (non-zero exit). This directly fixes the fc-v88 defect (one curl, echo-whatever, always exit 0 → a good deploy's log line could read blank with no signal): there is now no path that logs a blank `served=` line that looks like a passing health check. Aligns with the measurement-integrity rule (tooling failure ⇒ could-not-measure, never a silent pass).
- **Tests have teeth and call the REAL function** (`source`s `scripts/lib/console_deploy_probe.sh`, the same seam the gate uses): success-exits-0 + empty stderr; retry-then-succeeds (counter-file); and fail-loud — asserts `returncode != 0`, **`stdout.strip() == ""`** ("must never print an empty health-looking success line"), and `COULD NOT MEASURE` in stderr.

### backlog#137 — poolRollup drops a lane dead EVERYWHERE (PASS)
`isDeadEverywhere(l)` returns true only when `l.live` exists AND `l.live.running === false` AND `l.heartbeat_age_s` is a **number** `> DEAD_HB_STALE_S (900)`. Fail-safe by construction — every uncertain case counts the lane rather than hiding it: no `live` field (older payload) → counts; `live.running === true` (dark-here-but-heartbeat-alive-elsewhere, the fc-v63 property) → counts; `heartbeat_age_s` null/missing/non-number → counts. Only PROVABLE everywhere-dead drops. `poolRollup` filters only `lanes` (not `coordinators`/singletons), matching the item's scope.
- **Mirror verified at source:** `DEAD_HB_STALE_S = 900` matches app.py's `_REMOTE_HB_FRESH_S` default (`app.py:1074` `int(os.environ.get("CONSOLE_REMOTE_HB_FRESH_S","900"))`; the backend's freshness gate `app.py:1130` `if hb is None or hb > _REMOTE_HB_FRESH_S`). Crucially `live.running` is checked FIRST and is app.py's own authoritative signal, so the client can **never wrongly drop a live lane**; it only adds a stale-hb requirement on top.
- **Node test has full negative controls** (`fleet_keyroll.test.js`): dead-everywhere (`running:false`, hb=21600) EXCLUDED; dark-elsewhere (`running:true`, hb=120) COUNTED; `running:false` hb=300 (under cutoff) COUNTED; `running:false` hb=null COUNTED; legacy no-`live` COUNTED. Asserts `counts {Musa:2, irsyad:1, Syed:2}`, `total 5` (the one dead lane excluded), chip order, and `>irsyad<b>1</b>`.

### Version-sync (PASS)
All 8 gated files at **fc-v94**, no v93 straggler: 6 HTML `#build` spans + `sw.js VERSION` + `fleet.js APP_BUILD`.

## LOW non-blocking notes (for the lane; neither affects correctness or this GO)
1. **`DEAD_HB_STALE_S = 900` is hardcoded client-side** and tracks only the *default* of the env-tunable `CONSOLE_REMOTE_HB_FRESH_S`. This is unavoidable (client JS can't read the server env) and SAFE: because `live.running` (computed by app.py with the real threshold) is checked first, a mismatched env can only make the client *under-drop* (over-count) — never wrongly drop a live lane. Worth a one-line comment that the two can drift if the env is overridden.
2. **`fleet_keyroll.test.js` comment misnames the mirrored constant** — it says "mirrors app.py's `_LANE_STALE_DROP_S`" but the code mirrors `_REMOTE_HB_FRESH_S` (900, not 21600). The test's excluded case (hb=21600) only pins the cutoff to the range [300, 21600) under the strict `>`, so it doesn't tightly lock 900; a boundary case (e.g. hb=1000 → EXCLUDED) would pin it and match the fleet.js comment. Comment/test-precision only.

## Scope / method
Reviewed the full diff `origin/fable/substrate-safe-fixes..c8a16a9` over all gated paths (static + backend), not just the renders. Ran the suites in a detached worktree at the exact reviewed commit. Independently recomputed the content hash. No security/correctness blocker; clear to ship.
