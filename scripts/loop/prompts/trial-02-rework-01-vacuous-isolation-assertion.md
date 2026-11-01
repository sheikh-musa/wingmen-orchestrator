# Rework task: fix a vacuous test assertion in tests/test_run_pilot_worktree_isolation.py

You already added `tests/test_run_pilot_worktree_isolation.py` in this
branch/worktree (PR open against `fable/substrate-safe-fixes`). A reviewer
found one real gap in it. Fix it, commit, and push to the SAME branch (do
not open a new PR — update the existing one).

## The finding

> The 'worktree is free of tasks/hidden dirs' assertion is VACUOUS — the
> test's git-repo fixture only ever seeds `README.md`, never commits a
> `tasks/loop-pilot/<id>/hidden/` file into the base repo, so the
> "no tasks/hidden dirs found under the worktree" assertion passes trivially
> because there was never anything to find. It cannot catch a real
> regression where `make_worktree()` fails to isolate/exclude such a tree
> from a role's worktree. The true isolation guard (grader tree absent from
> the worktree) is neither exercised nor stated honestly as an open
> question.

## What to fix

In whichever test(s) currently assert the worktree contains no
`tasks/`/`hidden/` directories: before calling `make_worktree()`, commit a
file at `tasks/loop-pilot/<some-task-id>/hidden/seeded-bugs.json` (or similar)
into the throwaway base repo's own commit history, so the fixture actually
has something to isolate. Then re-run the real assertion against the real
resulting worktree and report truthfully what `make_worktree()` actually
does:

- If `make_worktree()` (which is just `git worktree add [-B <branch> |
  --detach] <path> <base>` — a plain worktree checkout) DOES still contain
  that file (because `git worktree add` checks out everything in the base
  commit, including that path — this is almost certainly what you'll find,
  since `make_worktree()` has no exclusion logic at all), then the test
  should assert that honestly: either rename/reframe the test to state the
  real, narrower property actually being verified (e.g. "a worktree made
  from a base WITHOUT a hidden/ dir committed into it contains no hidden/
  dir" — a weaker but true statement), or add a code comment stating plainly
  that `make_worktree()` itself does NOT strip `tasks/`/`hidden/` paths, and
  that this isolation property (if it holds at all in production) comes from
  the `tasks/loop-pilot/<task_id>/hidden/` tree never being committed into
  `loop-pilot/base` in the first place — not from anything `make_worktree()`
  does.
- Do not write a test that asserts something false. Run it and see what
  actually happens before deciding what to assert.

Keep every other test in the file unchanged unless this fix requires
touching them. Run the full test file yourself and confirm everything
passes before pushing.

## Constraints

- Same as before: orchestrator repo only, no secrets, every git repo you
  create for a test is a throwaway `tmp_path` repo with no network access,
  do not modify `scripts/loop/run_pilot.py` itself, and do not merge this
  PR yourself (it's a harness-pin item, stays open regardless of verdict
  until the trial concludes).
- Push to the existing branch `cc-loop-trial/02-make-worktree-isolation-test`
  (this worktree is already on it) — do not create a new branch or PR.
