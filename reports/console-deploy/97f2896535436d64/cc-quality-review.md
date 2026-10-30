# cc-quality — console-deploy Gate 4 review: DeepSeek shared-key fix

- **op:** op#35897 follow-up (orch-console #83560 / #83534 — live "could not read" on the shared row)
- **Worktree/branch:** /Users/sheikhmusa/wingmen/orchestrator.wt-console-revamp @ `lane/console-revamp-deepseek-card`
- **Reviewed head:** `065cfa4` (own parent 8636900; branch base 6cdcc7c)
- **Content hash:** `97f2896535436d64`
- **Reviewer:** cc-quality (ran on claude-opus-4-8)

## VERDICT: GO / PASS (one LOW non-blocking note)

Content hash INDEPENDENTLY recomputed (`source scripts/lib/console_deploy_manifest.sh; console_content_hash`) = **97f2896535436d64** — exact match. Dead-man's-switch satisfied.

**Root cause is correct and the fix is the right shape.** DEEPSEEK_CODING_KEY is vault `leak_flagged` (2026-10-29 04:45 UTC); `_read_key_from_vault`'s refusal (the defensive extra praised in the 55dc855c9a4d2cdc review) correctly declined to call it, which is why the live shared row read "could not read". The fix reads the shared balance from cc-fleet-health's own hourly poller history file instead — removing the leak-flagged key from the console server entirely for that row, i.e. strictly LESS secret surface, not a bypass of the refusal.

### Diff-scope check (did this cleanly)
The TRUE changeset is `git diff 8636900..065cfa4` — 11 CONSOLE files only: deepseek_usage.py (+80), 7 static html + sw.js + fleet.js (version bump fc-v92→fc-v93; fleet.js is version-string-only), conftest.py (+6 fixture), test_deepseek_usage.py (+101). An unscoped `5201b54..065cfa4` diff *appears* to also delete scripts/deepseek_console.py, nervous_system/nazim_bus_notify.py and scripts/reset_auditor.sh — that is a DIFF ARTIFACT, not a change this branch makes: `065cfa4` is NOT a descendant of the merge `5201b54`; both diverge from the common base `6cdcc7c`, which predates those files on the integration branch, and `065cfa4` never had or touched them. A proper 3-way merge of `065cfa4` into `fable/substrate-safe-fixes` (which has blob 67f9d49 of deepseek_console.py) KEEPS them. **Confirm the integration is a real merge, not a reset/force to this tip** — a hard reset to `065cfa4` WOULD drop those three merged PRs. The console content hash is unaffected either way (the gated manifest is console/ files only, all present at 065cfa4).

### (1) shared row genuinely holds no secret — PASS (empirically proven)
`get_shared_key_usage` now: `_read_shared_history()` (reads `~/wingmen/fleet-health/state/deepseek_balance_history.jsonl`) → last row → staleness check → `{available, balance_usd, delta_usd, fetched_at, age_s}`. It makes NO `read_key`/vault, NO `http_get_json`/urlopen, NO `_cached_fetch`, holds nothing in `_keys_in_memory`. Proven, not just read: `test_shared_key_usage_reads_the_pollers_history_file` monkeypatches `read_key` and `http_get_json` to raise `AssertionError` and still passes, so any secret path in the shared row would fail the test. The `/api/fleet` e2e (`..._lanes_never_a_key`) confirms the key is absent from the response body and the access log end-to-end.

### (2) staleness cutoff sane + honest — PASS
`_SHARED_STALE_AFTER_S = 3*3600` (poller is hourly → 3 misses). A reading older than 3h → `_unavailable(reason="could not read")`, `balance_usd=None`, `age_s=None` — never shown stale-as-current (`test_shared_key_usage_stale_entry_is_could_not_read`: a 5h-old row → could not read). Missing file → could not read (`..._missing_history_file...`). A fresh row shows `balance_usd` + signed `delta_usd` vs the poller's previous reading (hourly change — a more meaningful delta than the old per-console-poll one, and still labelled a delta/change, never "spend"); first-ever poll (1 row) omits the delta. `fetched_at`/`age_s` are surfaced so the UI can show "as of".

### (3) no regression to the per-client vault path or the leak_flagged refusal — PASS
`_read_key_from_vault` is unchanged and its `if secret.leak_flagged: raise DeepseekUsageError(...)` refusal is intact (line 139). `get_deepseek_usage` still drives the per-client rows through `_cached_fetch(row["key_name"])` → vault + HTTP, unchanged; none of the 5 per-client keys is leak-flagged. The two distinct failure states ("key not created yet" vs "could not read") are preserved for per-client rows.

## Tests run (this host)
- `tests/console/test_deepseek_usage.py` — **23/23 PASS** (incl. the 5 shared-key tests: reads-history, first-poll-omits-delta, missing-file→could-not-read, stale→could-not-read, malformed-line→could-not-read; the DB-fallback warnings are no-DB noise). 
- `tests/console/fleet_deepseek.test.js` — **10/10 PASS**. fleet.js change is a version-string bump only, so the other 13 JS files are unaffected (were 14/14 at the prior head).
- Full `tests/console/` python suite not run to completion here (DB-dependent tests hang without a test DB — "could not measure", not a finding); the lane reports it green (493 passed) + all 14 JS green — that CI run is the full-suite record; confirm green at the deployed sha.

## NOTE (LOW, non-blocking — not a Gate-4 condition)
Fail-OPEN on an unparseable timestamp: `_read_shared_history` keeps a row that is valid JSON with a floatable `balance_usd` even if its `ts` is a non-ISO string (it does not validate the ts format), and `get_shared_key_usage` then catches `datetime.fromisoformat(last["ts"])`'s `ValueError` with `last_ts = now` → `age_s = 0` → the balance is shown as CURRENT despite an unknowable age. The `malformed_last_line` test only covers a non-JSON line (dropped → empty → could not read), so this path is untested. In practice unreachable while deepseek_balance_poll.py writes ISO timestamps, hence LOW. For honesty-parity with the (correct, fail-closed) stale/missing paths, treat an unparseable `last_ts` as "could not read" too (move that `except ValueError` branch to `_unavailable`). Safe to fix as a fast-follow; does not block this GO.

**GO for Gate 4 at content hash `97f2896535436d64`.** The shared row no longer touches the leak-flagged key, the staleness rule is honest, and the per-client vault path and the leak_flagged refusal are intact. One LOW fail-open-on-bad-timestamp note for a fast-follow.
