# Task: add test coverage for scripts/backlog_done_checker.py

You are working in a git worktree of the `orchestrator` repo, on a fresh
branch based on `fable/substrate-safe-fixes`. Make a small, focused change,
commit it, push the branch, and open a pull request against
`fable/substrate-safe-fixes`.

## Background

`scripts/backlog_done_checker.py` has ZERO test coverage today (confirmed:
no `tests/test_backlog_done_checker.py` exists). Read the whole file first
(~155 lines). In summary, it auto-completes `operator_backlog` rows whose
jsonb `done_signal` column describes a machine-checkable condition:

- `_sig_deploy_live(sig)` — HTTP GETs `sig["url"]`, met iff the status code
  equals `sig.get("expect_status", 200)`. Any exception -> not met (fail-safe).
- `_sig_pr_merged(sig)` — shells out to `gh pr view <pr> --repo <repo> --json
  state,mergedAt`; met iff `state == "MERGED"` and `mergedAt` is truthy. Any
  exception / non-zero `gh` exit -> not met.
- `_sig_deploy_stage(conn, sig)` — queries the `deploys` table for the most
  recent row matching `sig["workstream"]` (ILIKE substring); met iff its
  `stage` column is exactly `"live"`. Any exception -> not met.
- `_evaluate(conn, sig)` dispatches on `sig["type"]`; an unknown/missing
  type (including `"manual"` or `None`) always returns `(False, ...)`.
- `check_once(conn, dry_run)` selects every `operator_backlog` row with
  `status NOT IN ('done','dropped')` and a non-null `done_signal`, skips
  rows whose signal type is `None`/`"manual"`, evaluates the rest, and for
  each MET signal (when `dry_run` is False) runs an `UPDATE operator_backlog
  SET status='done', note = COALESCE(note,'') || '...evidence...',
  updated_at=now() WHERE id=%s AND status NOT IN ('done','dropped')` then
  commits. Returns the count of items it completed (or would have, in
  dry-run — check exactly what it counts).

## What to build

Add `tests/test_backlog_done_checker.py` (follow this repo's existing
pytest conventions — look at a couple of other small `tests/test_*.py`
files that mock `psycopg`/`subprocess`/`urllib` for style) covering, with
EVERYTHING mocked (no real DB, no real HTTP, no real `gh` call):

1. `_sig_deploy_live`: met on matching status code; not met on mismatch;
   not met (not raised) when the GET itself raises.
2. `_sig_pr_merged`: met when `gh pr view` (mocked `subprocess.run`) returns
   `state=MERGED` with a `mergedAt`; not met when state is e.g. `OPEN`; not
   met (not raised) when the `gh` call itself errors or returns non-zero.
3. `_sig_deploy_stage`: met when the mocked query returns a row with
   `stage='live'`; not met for any other stage or no row; not met (not
   raised) when the query raises.
4. `_evaluate`: an unknown or `"manual"`/missing `type` always returns not
   met, for every signal evaluator (don't just test one).
5. `check_once`: with a mocked connection/cursor, feed it 2-3 canned
   `operator_backlog` rows (mix of met/not-met signals, including one
   `"manual"` row that must be skipped entirely — never evaluated or
   touched) and assert: the UPDATE is issued (and `conn.commit()` called)
   only for the met, non-manual row(s); with `dry_run=True`, no UPDATE is
   issued at all even for a met signal.

Run the new tests yourself and confirm they pass before opening the PR.

## Constraints

- Orchestrator repo only. Do not touch any client-repo code.
- **Never let the test fall through to a real `.env` read or a real DB/HTTP/
  `gh` call.** `_dsn()` (used only by `main()`, not by the functions above)
  falls back to opening the repo's real `.env` file if `DATABASE_URL`/
  `SUPABASE_DB_URL` aren't set in the environment — your tests should never
  call `_dsn()` or `main()` at all (test the individual functions directly:
  `_sig_deploy_live`, `_sig_pr_merged`, `_sig_deploy_stage`, `_evaluate`,
  `check_once`, each with a mocked `conn`/cursor passed in directly, exactly
  as the real functions are already structured to take `conn` as a
  parameter). If you ever do need an env var set for any reason, use a
  clearly-fake placeholder, never touch the real `.env` file.
- Do not modify `scripts/backlog_done_checker.py` itself — this task is
  purely adding test coverage for the existing, unchanged script.
- Write a normal commit message and PR description (no need to mention how
  you were briefed).
- Open the PR against base branch `fable/substrate-safe-fixes` (not `main`).
