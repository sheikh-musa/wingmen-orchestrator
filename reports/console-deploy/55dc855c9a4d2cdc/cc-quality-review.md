# cc-quality — console-deploy Gate 4 review: DeepSeek pool card

- **op:** op#35897 (orch-console #83369/#83385/#83397)
- **Worktree/branch:** /Users/sheikhmusa/wingmen/orchestrator.wt-console-revamp @ `lane/console-revamp-deepseek-card`
- **Reviewed head:** `84e7f5f3f9afdfdd9e51dd14d9388eca5e626f60` (off `fable/substrate-safe-fixes` @ 6cdcc7c)
- **Content hash:** `55dc855c9a4d2cdc`
- **Reviewer:** cc-quality (ran on claude-opus-4-8)

## VERDICT: GO / PASS

Content hash INDEPENDENTLY recomputed via `source scripts/lib/console_deploy_manifest.sh; console_content_hash` = **55dc855c9a4d2cdc** — exact match to the claimed hash. The dead-man's-switch is satisfied: this verdict is keyed to this exact gated tree (42-file manifest), and any later edit re-frames and moves the hash, invalidating it.

All three requested focus areas PASS, plus the secret-handling contract and the fail-closed classification.

---

### 1. Balance/spend labelling + the two distinct failure states — PASS
- **"balance", never "spend":** the endpoint is `GET /user/balance` (no spend figure exists in it); the module surfaces `balance_usd` and `delta_usd` only. `delta_usd` is the signed change since the last persisted *good* read — labelled a delta/change, not "spend". The render side (fleet.js) is proven by `fleet_deepseek.test.js`: "a good per-client read renders a balance figure and never the word spend".
- **Two states, never conflated:** `DeepseekKeyNotProvisioned` (vault `SecretNotFoundError`) → `_unavailable(reason="key not created yet")`; `DeepseekUsageError` (vault read failed / HTTP error / malformed / empty / leak-flagged) and any other exception → `_unavailable(reason="could not read")`. Distinct reasons, distinct render paths (JS test: "key mapped but never provisioned says 'key not created yet', not 'could not read'" + "a key that exists but fails to read says 'could not read'").
- **Never a fabricated 0.00 / never last-known re-served as current:** a failure sets `balance_usd=None` (not 0) and `delta_usd=None`; `_record_and_delta` is called ONLY on a successful fetch, so a failure never persists a baseline and the next good read computes its delta against the last *good* value. First-ever read → `delta=None` → render omits delta (JS test: "first-ever read … omits delta text entirely"). Delta baseline persists to `state/deepseek_card_balance_history.json`, so a post-deploy console restart keeps the baseline rather than showing a spurious first-read.

### 2. never-log / never-send / never-return a key value — PASS (enumerated every flow)
- The key is read from the fleet vault once per process (`_read_key_from_vault`, file-first DSN pin, env saved/restored in `finally`), cached in `_keys_in_memory` (module memory, process-lifetime, **popped on any failure**), and sent to exactly ONE place: `Authorization: Bearer <key>` to `https://api.deepseek.com/user/balance` — the documented, legitimate auth, and the URL has no query string so the key is never in a URL. It is never sent to the browser.
- **Every** raise/log site was checked: vault-miss → `vault has no secret named {name}`; other vault error → `vault read failed for {name}: {type(e).__name__}`; empty/leak-flagged → name only; HTTP error → `deepseek balance HTTP {e.code}`; network/JSON → `{type(e).__name__}`; `_fetch_one` logs `key_name` + a (name/class-only) exception message, and the generic `except` logs `type(e).__name__` only. **No raise or log interpolates `secret.value` or `key`.** No return payload and no state-file write carries the value (payloads carry `key_name`/`families`/balances only).
- Defensive extras: `secret.leak_flagged` is refused; no subprocess/argv for the key; the `_fleet_payload` aggregation wraps `_deepseek_card_payload()` in a belt-and-braces `try/except` that logs `type(e).__name__` only and substitutes an unavailable card, so a bug here can neither leak a key nor blank the whole fleet payload.

### 3. lane-truth cannot be spoofed by a stale pointer file — PASS
- `panes.token_ground_truth` derives `on_deepseek` from `ANTHROPIC_BASE_URL` parsed off the **live process line** (`ps eww -o pid=,command=` — the `e` flag includes the process environment, so the var exported by `provider_routing.sh` is actually present on the line; verified the ps invocation), matching substring `api.deepseek.com`. The `.<lane>_model` pointer file is **never read** on this path, so a stale/forged pointer cannot change the classification. An empty `ANTHROPIC_BASE_URL=` → `base_url=None` → `on_deepseek=False` (correct: not routed).
- `app._deepseek_card_payload` maps each live on-deepseek lane onto a key row via `_family_of` = `scripts.lib.lane_token_resolver.family_of` — the **same** resolver the real per-client DeepSeek routing uses — against the **same** `scripts/lib/deepseek_key_map.json`. So the card's lane→family→key assignment mirrors routing exactly. A lane whose family doesn't resolve is dropped from every per-client row (never guessed onto one) and remains only in the shared-key row (correct: today every live lane still draws on `DEEPSEEK_CODING_KEY`).

### Correctness / concurrency spot-checks — clean
- Lock order consistent: `_state_lock` is only ever acquired inside `_lock` (via `_fetch_one`); no inverse path, no deadlock.
- Per-key TTL: good read reused 300s, failure retried after 60s; shared and per-client keys share the keyed caches without collision (distinct `key_name`s).

## Tests run (this host)
- **Changed python modules — 84/84 PASS:** `tests/console/test_deepseek_usage.py` + `test_panes.py` + `test_proc_ground_truth.py` (`env -u DATABASE_URL`, orchestrator `.venv`; the asyncio `feeder`/DB-fallback lines are teardown/no-DB noise, not failures).
- **All 14 JS console test files — 14/14 PASS**, including `fleet_deepseek.test.js` 10/10 (shared-row-first, balance-not-spend, both failure states, grouped families, signed delta both directions, first-read-omits-delta, mapped-vs-unmapped lanes, missing-payload renders nothing, backward-compat with an old server).
- **Could NOT run the full `tests/console/` python suite to completion here:** DB-dependent console tests hang without a test DB on this box (tooling limit, not a finding — reported as "could not measure", not a pass or a fail). The lane's CI run (full `tests/console/` + all JS green; full-repo pytest 263 pre-existing failures, none touching panes.py/app.py/deepseek_usage.py/fleet.js) is the full-suite execution record — confirm it stayed green at the deployed sha.

## NOTES (non-blocking; not Gate-4 conditions)
1. `ps eww` places every process's FULL environment (incl. secrets) on the host-local `ln` string. This is PRE-EXISTING (fingerprint/billing detection already use `ps eww`); the new code extracts only `ANTHROPIC_BASE_URL` (a non-secret URL) and `--model` into the row and never adds the raw `ln` to any payload, so this PR adds NO new secret surface. Recorded for completeness.
2. `_keys_in_memory` keeps a good key for process life (popped only on failure). On a key rotation the cached-good key persists up to the 300s TTL before the next re-read; minor staleness that self-heals on the next failed call. Acceptable (mirrors `glm_usage`).
3. `_save_state` writes `deepseek_card_balance_history.json` non-atomically (`write_text`, not tmp+rename). A crash mid-write corrupts the file, but `_load_state` catches `ValueError` → `{}` → degrades to "first-ever read" (delta omitted, never a wrong delta). Safe-but-lossy; a `tmp + os.replace` would harden it. Low.

**GO for Gate 4.** No blocking findings. cosem/fleet-infra advisory gate; the balance/spend honesty, the two-failure-state separation, the never-log-a-key contract, and the pointer-file-proof classification all hold at content hash `55dc855c9a4d2cdc`.
