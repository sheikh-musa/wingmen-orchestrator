# Task: add test coverage for the .loop-task/ isolation contract in scripts/loop/run_pilot.py

You are working in a git worktree of the `orchestrator` repo, on a fresh
branch based on `fable/substrate-safe-fixes`. Make a small, focused change,
commit it, push the branch, and open a pull request against
`fable/substrate-safe-fixes`.

## Background

`scripts/loop/run_pilot.py` is part of the cc-loop 3-role loop harness
(op#35690). Read `tests/test_run_pilot_redaction.py` first for this repo's
existing test style for this file (it's the only test file for
`run_pilot.py` today — confirmed via `find tests -iname '*run_pilot*'`).

Two small functions have NO test coverage at all:

```python
def write_task_files(worktree: Path, task_id: str, brief_text: str) -> None:
    task_dir = worktree / ".loop-task"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task_id.txt").write_text(task_id + "\n")
    (task_dir / "brief.md").write_text(brief_text)
```

and `make_worktree(hifz_checkout, path, branch, base, detach=False)`, which
just wraps `git worktree add [-B <branch> | --detach] <path> <base>`.

The security property this code exists to enforce (see the comment block
immediately above `write_task_files` in the file, "`.loop-task/` harness
contract") is: a builder role (which may run an untrusted/adversarial model)
is handed a worktree that contains **only** `.loop-task/task_id.txt` and
`.loop-task/brief.md` — nothing that reveals where the orchestrator
checkout lives, and nothing from `tasks/loop-pilot/<task_id>/hidden/` (the
grader's seeded-bugs.json / grader.py, which the role must never see — see
`docs/loop-pilot-design-v1.md` section 1 for the full rationale, specifically
why there's no `orch_dir.txt` or similar path hint written into the
worktree).

## What to build

Add `tests/test_run_pilot_worktree_isolation.py` that:

1. Calls `write_task_files()` against a real `tmp_path` directory (standing
   in for a worktree) and asserts the **exact** resulting file set under
   `.loop-task/`: exactly `task_id.txt` (content = task_id + newline) and
   `brief.md` (content = the brief text, unchanged) — no other files, and
   nothing at the worktree root besides the `.loop-task/` directory itself.
2. Asserts the written content does NOT contain any string that would leak
   the orchestrator checkout path (e.g. assert `str(ORCH_DIR)` — import it
   from `run_pilot` — never appears in either written file's content, for a
   brief_text that doesn't itself mention it).
3. Calls `make_worktree()` against a real throwaway git repository you set
   up in `tmp_path` (`git init`, one commit, so there's a valid base ref to
   create a worktree from) and asserts the resulting worktree directory:
   exists, is a valid git worktree (e.g. `git -C <path> rev-parse HEAD`
   succeeds), and — critically — contains NO `tasks/` directory and NO
   `hidden/` directory anywhere under it (walk the tree and assert this).
   Use `subprocess`/`git` directly for the throwaway repo setup, same style
   as other tests in this repo that build throwaway git repos.
4. One test combining both: build a throwaway repo worktree via
   `make_worktree()`, then call `write_task_files()` on it, then assert the
   full resulting file listing under the worktree root is exactly what a
   builder role should ever see for this contract (the repo's own checked-
   out files plus `.loop-task/{task_id.txt,brief.md}` — nothing else added).

Run the new tests yourself and confirm they pass before opening the PR.

## Constraints

- Orchestrator repo only. Do not touch `.env`, any secrets/credentials, or
  any client-repo code.
- Every git repo your tests create must be a throwaway temp directory
  (`tmp_path`) — never touch this repo's own `.git`, and never push/fetch
  anywhere (everything is local-only, no network).
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
