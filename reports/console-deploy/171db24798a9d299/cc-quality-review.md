# cc-quality review — console content hash 171db24798a9d299

**Branch**: fleet-health/expected-token-hub-remote-fallback-fix (`~/wingmen/orch-hubfix-wt`),
uncommitted working tree on top of 5f64925 (my earlier PASS tonight, PR#296/hash
63136f84f918b205, now merged). **Hash verified myself** via `console_content_hash`
against the worktree: `171db24798a9d299` — matches exactly.

**Verdict: PASS-WITH-FINDING (non-blocking, but recommend adding the test below given
this is pinning against a bug that already happened live once).**

## The bug (post-merge, caught live by orch-console, not by tests)
`cai`/`fleet-health` cleared correctly after PR#296 deployed, but `cc-orchestrator`
still showed `expected=Musa` / `mismatch=True` in production. Root cause: the
`_REMOTE_BODIES` loop called `exp_fp = _expected_fp(sess)`, but `_expected_fp` itself
*already* collapses a local-pointer miss into `_env_default_fp()` internally (correct
for every other session, wrong for a remote body whose real pointer lives on a
different host entirely) — so `exp_fp` was never actually `None`, and the remote-SSH
fallback could never fire.

## What I verified myself, not taken on trust

- **Independently confirmed the root cause by reading `_expected_fp`'s actual body**
  (not just the PR's description of it): `path = _resolve_lane_token_path(...); if
  path: ...; return _env_default_fp()` — confirmed it unconditionally falls through to
  the env default whenever the local path is empty, with no way to ever return `None`
  as long as `CLAUDE_CODE_OAUTH_TOKEN` is set in the console's own environment (which it
  always is). This matches the diagnosis exactly.
- **Live-verified the fix myself against the real DB + SSH** (not just unit tests):
  ran `panes.token_ground_truth(include_remote=True)` directly from the worktree.
  First attempt returned `expected=Musa` — looked like a reproduction failure, but
  turned out to be *my own* environment mistake (used bare `python3` without `psycopg`
  installed, so `_resolve_hub_ssh_target()`'s DB connection silently failed inside its
  own broad `except Exception`, same fail-open shape as every other scan in this file).
  Re-ran with `~/wingmen/orchestrator/.venv/bin/python3` — confirmed `account=Syed`,
  `expected=Syed`, `expected_fp=582043088eae`, `mismatch=false`, exactly matching the
  claim. Caught my own false alarm before reporting it.
- Confirmed the diff is scoped to exactly the `_REMOTE_BODIES` loop — the first
  (local-process-scan) loop, which is what actually resolves `cai`/`fleet-health`, is
  untouched. No regression risk to the two singletons PR#296 already fixed.
- Ran `tests/console/test_panes.py` myself: 50/50 pass.

## Real finding, mutation-confirmed

**Mutation-tested the fix** by reverting the `_REMOTE_BODIES` loop to the exact
pre-fix `exp_fp = _expected_fp(sess)` call (the bug that already shipped and was
caught live) — **all 50 tests still passed.** Investigated why: every single test in
this file that touches this code path mocks `_env_default_fp` to return `None`
(`grep -n "_env_default_fp" tests/console/test_panes.py` → 8 hits, all `lambda: None`).
That neutralizes the exact condition that caused the live bug — the real production
failure needs `_env_default_fp()` to return a REAL value (it always does; the console
always has its own live token) *combined with* a genuine local-pointer miss. No
existing test constructs that combination, so a silent regression back to calling
`_expected_fp()` directly — i.e., exactly re-introducing the bug that just happened —
would ship green through the entire suite.

**Constructed the missing test myself and confirmed it closes the gap**: mocked
`_env_default_fp` to return a realistic non-None fp (Musa's) alongside a local-pointer
miss and a working remote scan (Syed's fp) — against the real fixed code it correctly
asserts the remote scan wins; against my mutated (reverted) code it genuinely fails
with `AssertionError: env-default (68142948c003) won over the remote scan
(582043088eae)`. Recommend adding this case (or equivalent) to
`tests/console/test_panes.py` before or shortly after this merges, so the exact
already-happened regression is pinned, not just fixed.

No money/PII/live-tenant surface — CAI-RESP-1440's cc-storefront gate doesn't apply.
Not blocking given I independently live-verified the actual fix is correct; the test
gap is a real but non-urgent follow-up given the fix itself demonstrably works right
now. Clear to push.

## Deploy steps
Backend-only (`panes.py`), no static-file version bump needed. Hosted console process
needs a restart to pick up the fix.
