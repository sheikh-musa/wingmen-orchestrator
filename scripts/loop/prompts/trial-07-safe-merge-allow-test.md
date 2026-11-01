# Task: regression-test safe_merge.sh's --allow-skipped flag parsing

You are working in a git worktree of the `orchestrator` repo, on a fresh branch
based on `fable/substrate-safe-fixes`. Make a small, focused change, commit it,
push the branch, and open a pull request against `fable/substrate-safe-fixes`.

## Background

`scripts/safe_merge.sh` is the orchestrator's merge gate: it squash-merges a PR
only when every CI check is green and the head SHA hasn't moved. It accepts a
repeatable `--allow-skipped <check-name>` flag, built into a bash array:

```bash
ALLOW=()
...
--allow-skipped) ALLOW+=("--allow-skipped" "${2:-}"); shift 2;;
...
if ! printf '%s' "$CHECKS" | "$PY" "$ORCH_DIR/scripts/lib/merge_gate.py" "${ALLOW[@]+"${ALLOW[@]}"}"; then
```

`tests/test_merge_gate.py` tests `scripts/lib/merge_gate.py`'s Python logic
directly (it imports `merge_gate` and calls `evaluate_checks(...)`), including
the `allow_skipped` parameter. But NOTHING tests `safe_merge.sh` itself — the
bash array-building and flag pass-through to `merge_gate.py`'s CLI. This repo's
Mac Mini runs `/bin/bash` 3.2 (not bash 4+), which has known array-handling
quirks (e.g. empty-array expansion under `set -u` without the `${ALLOW[@]+...}`
guard would error) — the existing `${ALLOW[@]+"${ALLOW[@]}"}` guard is correct,
but nothing proves the whole multi-flag path actually works end-to-end on real
bash 3.2.

## What to build

Add a test (Python, under `tests/`, following this repo's existing pytest
conventions — see `tests/test_merge_gate.py` for the project's style) that
invokes the REAL `scripts/safe_merge.sh` script as a subprocess (not just
`merge_gate.py` directly), with a stubbed `gh` CLI on `PATH` (a small fake `gh`
shell script or Python script you write, placed somewhere under `tests/` or a
fixture directory, that returns canned JSON for `gh pr view` / `gh pr checks`
the same shape the real `gh` CLI returns) so the test does NOT need network
access or a real GitHub PR. The test must exercise, at minimum:

1. `safe_merge.sh --dry-run` with **zero** `--allow-skipped` flags, against a
   checks payload with one SKIPPED check — must REFUSE (exit code 3, matching
   the script's existing fail-closed convention).
2. `safe_merge.sh --dry-run` with **one** `--allow-skipped <name>` flag,
   against a checks payload where that exact check is SKIPPED and everything
   else is SUCCESS — must ACCEPT (exit code 0, "DRY-RUN" message).
3. `safe_merge.sh --dry-run` with **two separate repeated** `--allow-skipped`
   flags (e.g. `--allow-skipped foo --allow-skipped bar`), against a checks
   payload where BOTH `foo` and `bar` are SKIPPED and everything else is
   SUCCESS — must ACCEPT. This is the actual gap: nothing today proves the
   bash array correctly accumulates more than one `--allow-skipped` value and
   passes all of them through to `merge_gate.py`.

Keep the fake `gh` stub minimal — it only needs to handle the specific `gh pr
view ... --json state,mergeable,headRefOid` and `gh pr checks ... --json
name,state` invocations `safe_merge.sh` makes (read the script to see the
exact `gh` subcommands and flags it calls, and match on those).

## Constraints

- Orchestrator repo only. Do not touch `.env`, any secrets/credentials, or any
  client-repo code.
- Do not touch `scripts/lib/merge_gate.py` or `tests/test_merge_gate.py` — this
  task is about `safe_merge.sh`'s OWN flag-parsing, not the already-tested
  Python decision logic.
- Do not attempt an actual `gh pr merge` or any real GitHub write — the stub
  `gh` must never shell out to the real `gh` binary or hit the network.
- Run the new test(s) yourself before opening the PR and confirm they pass.
- Keep the PR small and focused on exactly this gap. Write a normal commit
  message and PR description (no need to mention how you were briefed).
- When you open the PR, run it against base branch `fable/substrate-safe-fixes`
  (not `main`).
