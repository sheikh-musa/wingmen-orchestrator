# Task: add test coverage for run_task()'s "could not measure" cost-honesty contract in scripts/loop/run_pilot.py

You are working in a git worktree of the `orchestrator` repo, on a fresh
branch based on `fable/substrate-safe-fixes`. Make a small, focused change,
commit it, push the branch, and open a pull request against
`fable/substrate-safe-fixes`.

## Background

`scripts/loop/run_pilot.py` is part of the cc-loop 3-role loop harness
(op#35690). Read `tests/test_run_pilot_redaction.py` first for this repo's
existing test style for this file (it's the only test file for
`run_pilot.py` today).

`run_task()`'s `finally` block (near the bottom of the function) computes
two cost fields on the `TaskRunResult` it returns:

```python
finally:
    if track_deepseek and balance_before is not None:
        try:
            balance_after = deepseek_balance_usd()
            result.deepseek_cost_usd = round(balance_before - balance_after, 4)
        except Exception as e:
            result.deepseek_cost_usd = f"could not measure: {e}"
    elif track_deepseek:
        result.deepseek_cost_usd = "could not measure: balance read failed before task start"

    claude_roles = [r for r in ("spec_writer", "reviewer") if "claude-opus" in models[r]]
    if claude_roles:
        costs = result.claude_cost_usd_by_role
        missing = [r for r in claude_roles if costs.get(r) is None]
        known = [costs[r] for r in claude_roles if costs.get(r) is not None]
        if missing:
            partial = f" (partial, from {len(known)}/{len(claude_roles)} role(s): ${round(sum(known), 4)})" if known else ""
            result.claude_cost = (
                f"could not measure: no matching transcript found for role(s) {missing}{partial}")
        else:
            result.claude_cost = round(sum(known), 4)
```

This is the honesty contract the whole op#35690 trial depends on: a cost
that genuinely cannot be measured must come back as an explicit
`"could not measure: ..."` string, never a silently-wrong `0` or a made-up
number. It has ZERO test coverage today. `CONDITIONS` (near the top of the
file) defines two conditions: `"A"` (spec_writer/builder/reviewer all
`deepseek-flash` — no Claude roles) and `"B"` (spec_writer/reviewer
`claude-opus-4-8`, builder `deepseek-flash`).

**The key fact that makes this cleanly testable without mocking anything:**
calling `run_task(..., dry_run=True)` exercises this exact `finally` block
for real, with no subprocess calls, no filesystem/worktree creation, and no
network access at all. Trace why, by reading the function top to bottom:
`launch_role(..., dry_run=True)` (called unconditionally from
`do_spec_writer`/`do_builder`) short-circuits immediately and returns a
`RoleResult` with `claude_cost_usd=None`, without touching the
network/filesystem. `make_worktree`/`write_task_files` are only called in
the `else` branch of `if dry_run: ... else: make_worktree(...)`. The
`dry_run` early-`return result` inside `do_builder`'s caller happens before
any worktree/network/git-ops code is reached — but it is a `return` inside a
`try:` block, so the `finally:` above still runs. And since
`balance_before` is only ever assigned when `not dry_run`, it stays `None`
in a dry run — which deterministically drives the exact "could not measure:
balance read failed before task start" branch above. Confirm this reading
is right by tracing the full function yourself before writing the test.

## What to build

Add `tests/test_run_pilot_cost_honesty.py` with tests that call
`run_pilot.run_task(...)` directly with `dry_run=True`, a real brief file
written to `tmp_path`, and placeholder strings for `task_id`/`owner`/
`repo`/`hifz_checkout` (none of these are touched on the dry-run path, so
they don't need to point at anything real — verify this is still true by
reading the function before relying on it). Cover:

1. **Condition A** (`condition="A"`, no Claude roles): assert
   `result.deepseek_cost_usd == "could not measure: balance read failed before task start"`
   (exact string) and that `result.claude_cost` stays at its default
   (`None` — condition A's `claude_roles` list is empty, so the whole
   `claude_cost` block is skipped).
2. **Condition B** (`condition="B"`, both Claude roles): assert
   `result.deepseek_cost_usd` is the same "could not measure: balance read
   failed before task start" string (the builder is still `deepseek-flash`
   in condition B, so `track_deepseek` is still `True`), AND assert
   `result.claude_cost == "could not measure: no matching transcript found for role(s) ['spec_writer', 'reviewer']"`
   (exact string — both roles end up with `None` in
   `claude_cost_usd_by_role` since `launch_role` dry-run never sets a real
   cost, so `known` is empty and the `partial` suffix must NOT appear).
3. **The never-silently-zero property stated directly**: for both
   conditions, assert `result.deepseek_cost_usd != 0` and
   `result.deepseek_cost_usd != 0.0` and is a `str` starting with
   `"could not measure"` — i.e. explicitly prove this can never be
   misread as a legitimate zero-cost run.
4. (Optional, only if you want the extra signal) a true unit test of just
   the `missing`/`known`/`partial` list-comprehension logic in isolation,
   if you can extract it without mocking — e.g. by directly constructing a
   `TaskRunResult` with a partially-populated `claude_cost_usd_by_role`
   `{"spec_writer": 1.2345}` (reviewer missing) and checking what the
   *real* finally-block code produces for the "partial" branch. If this
   requires un-constrained access, running the real dry-run calls in 1/2
   is sufficient on its own — don't force it.

Run the new tests yourself and confirm they pass before opening the PR.

## Constraints

- Orchestrator repo only. Do not touch `.env`, any secrets/credentials, or
  any client-repo code.
- These tests must make NO real network calls and create NO real
  worktrees/subprocesses — the whole point is that `dry_run=True` makes
  this safe. If your test needs anything beyond that to pass, STOP and
  re-read `run_task` — you've misunderstood the dry-run path.
- Do not modify `scripts/loop/run_pilot.py` itself — this task is purely
  adding test coverage for the existing, unchanged code.
- Write a normal commit message and PR description (no need to mention how
  you were briefed).
- Open the PR against base branch `fable/substrate-safe-fixes` (not `main`).
- **This PR touches the loop harness that measures an active trial
  (op#35690) — it will be reviewed normally, but regardless of the
  verdict, do not merge it yourself, and do not ask anyone to merge it
  urgently.** Opening the PR and getting it reviewed is the whole task;
  leave it open.
