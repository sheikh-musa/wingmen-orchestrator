# Task: add test coverage for scripts/apply_orch_lease.py

You are working in a git worktree of the `orchestrator` repo, on a fresh
branch based on `fable/substrate-safe-fixes`. Make a small, focused change,
commit it, push the branch, and open a pull request against
`fable/substrate-safe-fixes`.

## Background

`scripts/apply_orch_lease.py` is a small (23-line) direct-psycopg migration
applier. It has ZERO test coverage today (confirmed: no `tests/test_apply_orch_lease.py`
and nothing else references it). Read the whole file first — it is short.
In summary, `main()`:

1. Resolves a DSN: `os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")`
   (prefers `DATABASE_URL`, falls back to `SUPABASE_DB_URL`).
2. Reads `migrations/016_orch_lease.sql` from disk.
3. Connects via `psycopg.connect(dsn)`, executes that SQL, then
   `SELECT lease_key, holder, holder_host, acquired_at FROM orch_lease`,
   prints each row, and commits.
4. Prints a confirmation line and returns `0`.

## What to build

Add `tests/test_apply_orch_lease.py` (follow this repo's existing pytest
conventions — look at a couple of other small `tests/test_*.py` files for
style) with **mocked** `psycopg.connect` (do NOT connect to any real
database — use `unittest.mock`/`monkeypatch`, whichever this repo's existing
tests tend to use) covering at least:

1. DSN resolution prefers `DATABASE_URL` when both `DATABASE_URL` and
   `SUPABASE_DB_URL` are set in the environment.
2. DSN resolution falls back to `SUPABASE_DB_URL` when `DATABASE_URL` is
   unset/empty.
3. `main()` executes the real content of `migrations/016_orch_lease.sql`
   (assert the mocked cursor's `execute` was called with that file's actual
   text, read from disk — not a hand-copied string, so the test can't drift
   from the real migration file) and then runs the `SELECT lease_key,
   holder, holder_host, acquired_at FROM orch_lease` query.
4. `conn.commit()` is called, and `main()` returns `0`.

Mock at the `psycopg.connect` boundary (so the connect/cursor/execute/commit
call sequence is exercised exactly as the real code drives it) — do not
rewrite `apply_orch_lease.py`'s logic to make it more testable; the task is
to test the existing code as it stands.

Run the new test yourself and confirm it passes before opening the PR.

## Constraints

- Orchestrator repo only. Do not touch `.env`, any secrets/credentials
  (including not setting a real `DATABASE_URL`/`SUPABASE_DB_URL` anywhere —
  use clearly-fake placeholder values in the test's env/mocks), or any
  client-repo code.
- Do not connect to any real database, local or remote, at any point.
- Do not modify `scripts/apply_orch_lease.py` itself or
  `migrations/016_orch_lease.sql` — this task is purely adding test
  coverage for the existing, unchanged script.
- Write a normal commit message and PR description (no need to mention how
  you were briefed).
- Open the PR against base branch `fable/substrate-safe-fixes` (not `main`).
