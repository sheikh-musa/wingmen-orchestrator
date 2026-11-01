# Task: add test coverage for scripts/loop/check_spec_tests_unchanged.py

You are working in a git worktree of the `orchestrator` repo, on a fresh
branch based on `fable/substrate-safe-fixes`. Make a small, focused change,
commit it, push the branch, and open a pull request against
`fable/substrate-safe-fixes`.

## Background

`scripts/loop/check_spec_tests_unchanged.py` is part of the cc-loop 3-role
loop harness (op#35690). It has ZERO test coverage today (confirmed: no
`tests/test_check_spec_tests_unchanged.py` exists). Read the whole file
first — it is short (~90 lines). The security property it enforces (see its
own module docstring): a builder role may implement code but must NOT edit
the acceptance test files the spec-writer committed. The check works by:

1. Reading `.loop/<task_id>/manifest.json` **as it existed at
   `spec_commit_sha`** (via `git show <spec_commit_sha>:<path>`) — never
   trusting whatever the manifest says at HEAD, so a builder can't launder
   an edited test by also rewriting the manifest's recorded hashes.
2. For each `{"path": ..., "sha256": ...}` entry in that manifest's
   `acceptance_tests` list, re-reading that same path **at `head_sha`**
   and recomputing its sha256.
3. `check()` returns `(True, "<n> acceptance test file(s) unchanged ...")`
   if every recomputed hash matches the manifest's recorded hash for every
   file; `(False, "<mismatches joined by '; '>")` otherwise — one
   mismatch message per changed-or-missing file.
4. `main()` CLI wraps this, prints `PASS: ...`/`FAIL: ...`, optionally
   posts a GitHub commit status (`--post-status`, requires `--owner`/
   `--repo`), and returns exit code `0`/`1` accordingly.

## What to build

Add `tests/test_check_spec_tests_unchanged.py` (follow this repo's existing
pytest conventions — look at a couple of other small `tests/test_*.py`
files for style; `pytest`'s built-in `tmp_path` fixture is the natural tool
here) that tests `check()` (and ideally `manifest_at()`/`git_show()`
indirectly through it) against a **real, throwaway local git repository**
created in `tmp_path` for each test — this is the most faithful way to test
genuine git-blob-at-revision behavior rather than mocking `subprocess.run`
and risking a test that passes without actually exercising git. A typical
test does something like: `git init` a temp repo, write `.loop/<task_id>/
manifest.json` + a fake acceptance test file, `git add`+`git commit` (that's
your "spec commit" — capture its sha), optionally make a further commit
that does or doesn't touch the test file (that's your "head"), then call
`check(repo_dir, task_id, spec_commit_sha, head_sha)` and assert the result.
Cover at least:

1. **The real gap this check exists to catch**: the acceptance test file is
   edited (even by just one byte) in a commit after the spec commit — the
   manifest's recorded hash is now stale — `check()` must return `False`,
   with a mismatch message naming that path.
2. **The case this check must NOT false-positive on**: nothing changes
   between the spec commit and head (same tree, or an unrelated file added
   in a later commit) — `check()` must return `True`.
3. **A missing file at head** (the acceptance test file is deleted in a
   later commit) — `check()` must return `False`, with a message that says
   the file is missing, not confuse it with a hash mismatch.
4. **The anti-tamper property itself**: the builder "launders" an edited
   test by ALSO rewriting the manifest's hash — **but only at HEAD, not at
   the spec commit**. Since `check()` always reads the manifest at
   `spec_commit_sha` (never at head), this must still return `False` for
   the original (now-stale, correctly-detected) hash — prove this by making
   a head commit that edits the test file AND rewrites `.loop/<task_id>/
   manifest.json` to match the new hash, and confirming `check()` still
   catches it as a mismatch against the ORIGINAL (spec-commit) manifest.

Run the new tests yourself and confirm they pass before opening the PR.

## Constraints

- Orchestrator repo only. Do not touch `.env`, any secrets/credentials, or
  any client-repo code.
- Every git repo your tests create must be a throwaway temp directory
  (`tmp_path`) — never touch this repo's own `.git`, and never push/fetch
  anywhere (everything is local-only, no network).
- Do not modify `scripts/loop/check_spec_tests_unchanged.py` itself — this
  task is purely adding test coverage for the existing, unchanged script.
- Write a normal commit message and PR description (no need to mention how
  you were briefed).
- Open the PR against base branch `fable/substrate-safe-fixes` (not `main`).
- **This PR touches the loop harness that measures an active trial
  (op#35690) — it will be reviewed normally, but regardless of the
  verdict, do not merge it yourself, and do not ask anyone to merge it
  urgently.** Opening the PR and getting it reviewed is the whole task;
  leave it open.
