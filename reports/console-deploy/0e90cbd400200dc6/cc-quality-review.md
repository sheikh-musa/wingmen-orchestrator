# cc-quality — console-deploy Gate 4 review: console_readonly pooler fix (orch-console #86539)

- **op:** orch-console #86539, approving cc-console-revamp-1's pooler investigation (#86533) — self-served review request (precedent orch-console #83531 citing #81035/#81059/#81098, most recently `caa7e37ad5431cee`)
- **Branch:** `lane/console-revamp-backlog135-137`
- **Reviewed head:** `a289bb5e809f479eb1d12688ef94ad2fe0a3597e`
- **Branch base:** `origin/fable/substrate-safe-fixes` (`84e0dd25c63cf56f6ad36f3813e52388a6c969b1`)
- **Content hash:** `0e90cbd400200dc6`
- **Reviewer:** cc-quality (`/code-review` skill, forked execution, medium effort, on `claude-sonnet-5`)

## VERDICT: GO / PASS

Content hash independently recomputed at the reviewed head (`source scripts/lib/console_deploy_manifest.sh; console_content_hash`) = **0e90cbd400200dc6** — exact match with the requested hash.

Scope: the prior backlog#135/#137 fix at `c8a16a9` already has its own independent PASS (`caa7e37ad5431cee`, committed `6cff829`). This review covers the two new commits since: `25ef22b` (the pooler fix) and `a289bb5` (a follow-up correctness fix found during this same review round).

### orch-console #86539 fix 1 — `CONSOLE_DB_POOL` default 6 -> 3 (PASS)
`nervous_system/console/db.py`'s `_POOL_MAX = max(1, int(os.environ.get("CONSOLE_DB_POOL", "3")))` — pure default-value change, no behavior change for any deployment that already sets the env var explicitly. Also set explicitly (not left to the new code default) in both the live `.env` (via `scripts/env_set.sh`, value never echoed) and `.env.example` (documentation). Correctly scoped: this lowers only the ceiling the *live app's own* in-process pool can reach on its next restart — matches the investigation's finding that the live process was already well-behaved and capped exactly at its configured value, so this doesn't fix today's already-accumulated stale backends (fleet-health's separate one-time reap covers those) and doesn't claim to.

### orch-console #86539 fix 2 — hosted clone's one-shot reads move to the transaction pooler (PASS, after one round-trip)
`hosted_view._txn_dsn()` swaps `_dsn()`'s port to Supabase's transaction-mode pooler (6543) via `psycopg.conninfo.conninfo_to_dict`/`make_conninfo` (a correct, format-agnostic DSN mutation — doesn't assume URL-vs-keyword DSN shape, never touches the credential value itself). All four live `hosted_server.py` call sites that open a `console_readonly` connection now use it: the `/api/fleet` handler (the actual steady-traffic contributor — fleet.js polls this), the import-time `console_protected_identities(dsn=...)` read, and `_db_lane_roster`/`_db_lane_host`. Verified by direct read that every one of these is a single connection, one or more `SELECT`s inside the single implicit transaction psycopg opens by default (`hosted_view.build_minimized_payload`/`build_cloned_payload` run several sequential `cur.execute()` calls on one cursor, never a session-level `SET`, `LISTEN`, or temp table), then closed via `with` — exactly the shape transaction-mode pooling is for. `hosted_view.py`'s own `__main__` CLI self-test (line ~721) deliberately stays on `_dsn()` (session mode) — a manual diagnostic run, not steady traffic, correctly left out of scope.

**Found and fixed in this review round:** the first version of `_txn_dsn()` unconditionally rewrote the port regardless of which DSN `_dsn()` resolved to, including the `DATABASE_URL` fallback branch. `db.py`'s own `resolve_dsn()` docstring is explicit that fallback is "LOCAL DEV ONLY" — in that path `DATABASE_URL` may not be a Supavisor-fronted DSN at all (e.g. a bare local Postgres with nothing listening on 6543), so forcing the port there would have broken local dev runs of the hosted server instead of fixing pooler contention; the import-time call site would have silently swallowed the resulting connection failure into the static `_PROTECTED` fallback, masking it, while `/api/fleet` started 500ing on every request. Commit `a289bb5` fixes this: `_txn_dsn()` now returns plain `_dsn()` unchanged whenever `CONSOLE_DB_URL` isn't set, only applying the port rewrite to the actual `console_readonly` production credential this fix is about. Re-verified: `test_txn_dsn_does_not_rewrite_port_on_the_local_dev_database_url_fallback` pins `hv._txn_dsn() == hv._dsn()` on that path.

### Tests (PASS)
Ran the full suite myself at `a289bb5` (`unset DATABASE_URL SUPABASE_DB_URL; export PYTEST_NO_DB=1; pytest tests/console/`, excluding the two `.test.js` files that need a JS runner): **497 passed**, 0 failed. New coverage added by this change: `tests/console/test_hosted_view_txn_dsn.py` (5 tests) — port-swap on the real `CONSOLE_DB_URL` path, host/user/dbname preservation through the DSN round-trip, the local-dev fallback guard (the fix above), and the no-DSN-configured error path. The `protected_agents` DB-read `UserWarning` in the output is the known harmless teardown noise (no local Postgres socket in this sandbox) — same note as the prior review.

### Version-sync (N/A — not touched)
This change is backend-only (`db.py`, `hosted_view.py`, `hosted_server.py`, `.env`/`.env.example`); none of the 8 version-badge-gated static files changed, so Gate 1 (`deploy_console.sh`'s own version-sync check) is unaffected by this diff.

## Scope / method
Read the full diff `c8a16a9..a289bb5` over the gated backend paths. Independently traced every live caller of `hosted_view._dsn()`/`_txn_dsn()` by hand (not just the diff) to confirm single-transaction, no-session-state usage before signing off on the pooler-mode swap — the one place this class of fix can silently break things is exactly the fallback path the first draft got wrong, which is why this round-tripped once. Ran the real test suite against the real reviewed head. Independently recomputed the content hash. No security/correctness blocker; clear to ship.
