# Task: add test coverage for scripts/cc_cost_snapshot.py

You are working in a git worktree of the `orchestrator` repo, on a fresh
branch based on `fable/substrate-safe-fixes`. Make a small, focused change,
commit it, push the branch, and open a pull request against
`fable/substrate-safe-fixes`.

## Background

`scripts/cc_cost_snapshot.py` is a read-only reporting script with ZERO test
coverage today (confirmed: no `tests/test_cc_cost_snapshot.py` exists).
Read the whole file first — it is short (~75 lines). In summary:

- `_cost(row, rates)` is a pure function: takes a 4-tuple
  `(input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens)`
  (any element may be `None`, which it treats as 0) and a `rates` dict with
  keys `in`/`out`/`cache_write`/`cache_read` (list-price $/MTok), and
  returns the API-equivalent dollar cost: `(inp*rates["in"] + out*rates["out"]
  + cw*rates["cache_write"] + cr*rates["cache_read"]) / 1_000_000.0`.
- `main()` reads `DATABASE_URL`, returns exit code `2` and prints
  `could-not-measure: DATABASE_URL not set` to stderr if it's missing
  (never fabricates a report with no DB). Otherwise it connects via
  `psycopg2.connect`, runs a per-`cc_identity` aggregate query against
  `cc_session_costs` (grouped totals of token counts over the last
  `--days` days), computes each body's cost via `_cost()`, sorts
  descending by cost, prints a fleet total + cache-read-cost-and-percentage
  line + a per-body table (top `--top` rows), and optionally (`--by-day`)
  a second per-day aggregate query + report. Returns `0`.

## What to build

Add `tests/test_cc_cost_snapshot.py` (follow this repo's existing pytest
conventions — look at a couple of other small `tests/test_*.py` files, e.g.
one that tests a pure calculation function plus one that mocks a DB
connection, for style) covering:

1. **`_cost()` directly, as a pure function** (no DB/mocking needed): at
   least one case with all four token counts set and a hand-computed
   expected dollar value using the `RATES["claude-opus-4-8"]` rates already
   defined in the module; one case where some inputs are `None` (should be
   treated as 0, not raise); one case where everything is 0 (cost is 0.0).
2. **`main()` with `DATABASE_URL` unset** — asserts it returns `2` (does
   NOT attempt a DB connection, does NOT crash) and prints something
   containing `could-not-measure` (check stderr/captured output via
   whatever this repo's tests normally use for that, e.g. `capsys` or
   similar).
3. **`main()` with `psycopg2.connect` mocked** (`DATABASE_URL` set to a
   clearly-fake placeholder value, no real DB contact at all) — feed the
   mocked cursor a small canned result set for the per-body query (2-3
   `cc_identity` rows with token counts) and assert: `main()` returns `0`,
   and the body with the higher computed cost is printed before the lower
   one in the output (locks the "sort descending by cost" behavior) —
   capture stdout the same way the rest of this file's tests do.

Run the new tests yourself and confirm they pass before opening the PR.

## Constraints

- Orchestrator repo only. Do not touch `.env`, any secrets/credentials
  (use clearly-fake DSN placeholders in mocks, never a real
  `DATABASE_URL`), or any client-repo code.
- Do not connect to any real database, local or remote, at any point.
- Do not modify `scripts/cc_cost_snapshot.py` itself — this task is purely
  adding test coverage for the existing, unchanged script.
- Write a normal commit message and PR description (no need to mention how
  you were briefed).
- Open the PR against base branch `fable/substrate-safe-fixes` (not `main`).
