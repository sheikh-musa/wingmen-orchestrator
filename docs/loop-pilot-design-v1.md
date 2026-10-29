# cc-loop design (op#35690)

Status: draft for cc-hifz-1 review before anything in this doc runs against real
hifz tasks. Orchestrator-side code lands on `lane/cc-loop-pilot` -> PR to
`fable/substrate-safe-fixes`. hifz-side work happens on `loop-pilot/base`
(branched off `sheikh-musa/hifz` `master`, its own branch protection, `master`
untouched -- confirmed via `gh api repos/sheikh-musa/hifz/branches/master/protection`
still 404s after this branch's protection was set).

Approved by orch-console (bus #81749) with two guards folded in below (section
"Isolation" and "Cost tracking"). cc-hifz-1 OK'd the branch (bus #81803).

## 1. The three roles, in code

Each role is one bounded, non-interactive Claude Code session, launched the
same way `scheduled_cc_sweep.sh` already launches bounded sessions -- reusing
the mechanism rather than inventing a new one:

    MODEL=<model-id> scripts/launch_dangerous_cc.sh \
        --repo hifz-companion \
        --scheduled-prompt scripts/loop/prompts/<role>.md \
        -- --max-turns <N>

run from inside the task's own git worktree (a fresh worktree off
`loop-pilot/base` per task-attempt, so roles never see each other's history
beyond what's committed). `apply_provider_routing` (sourced by
launch_dangerous_cc.sh) does the Claude-vs-DeepSeek switch from `MODEL` alone --
no new gateway, no custom subprocess/API wrapper. REPO_NAME is just the cwd's
git toplevel basename and is not looked up in REPOS.json, so pointing it at a
worktree that isn't registered there is fine.

1. **Spec-writer**: `--scheduled-prompt prompts/spec_writer.md`, task brief
   piped in as the prompt's input. Writes the spec + acceptance tests, calls
   `scripts/loop/record_manifest.py` to snapshot the test files, commits
   spec + tests + manifest in one commit on the task branch. That commit's
   sha is `spec_commit_sha`.
2. **Builder**: `--scheduled-prompt prompts/builder.md`. Prompt explicitly
   lists the manifest's test paths as off-limits. Builder commits
   implementation only, opens the PR against `loop-pilot/base`.
3. **Reviewer**: a *different* session from the builder (own worktree, own
   `--scheduled-prompt prompts/reviewer.md`), reading spec + diff + running
   tests. `scripts/loop/reviewer_verdict.py` posts the verdict (see below).

## 2. Manifest schema (`.loop/<task_id>/manifest.json`, committed in the spec commit)

```json
{
  "task_id": "<task-id>",
  "spec_commit_sha": "<40-char sha, filled in after the commit is made>",
  "spec_writer_model": "<model id>",
  "created_at": "<ISO-8601 UTC>",
  "acceptance_tests": [
    {"path": "tests/foo.spec.ts", "sha256": "<hex>"}
  ]
}
```

`scripts/loop/record_manifest.py` computes the hashes and writes this file;
it does not commit (the role's own commit includes it alongside the spec +
tests, so the manifest and the hashes it claims land in the same commit).

## 3. Checks (GitHub commit statuses, posted by a local runner -- hifz's
   Actions budget is exhausted, per the brief; scoped to `loop-pilot/base`
   only via branch protection, `enforce_admins: true`, confirmed live)

| context | script | pass condition |
|---|---|---|
| `tests` | `scripts/loop/local_ci_runner.sh` | hifz's existing lint/typecheck/build/smoke steps (ci.yml) all exit 0 |
| `spec-tests-unchanged` | `scripts/loop/check_spec_tests_unchanged.py` | every path in the manifest recorded AT `spec_commit_sha` has byte-identical sha256 at PR head |
| `reviewer-verdict` | `scripts/loop/reviewer_verdict.py` | reviewer session's structured verdict is PASS, posted for the exact head sha that produced it |

`spec-tests-unchanged` trusts the hashes as recorded in the manifest *at the
spec commit* (`git show <spec_sha>:.loop/<task>/manifest.json`), not whatever
the manifest claims at HEAD -- so the builder can't rewrite the manifest to
launder an edited test file.

`reviewer-verdict` is sha-scoped: `scripts/loop/reviewer_verdict.py` writes
`.loop/<task>/reviews/<head_sha>.json` (model id, verdict, reviewer identity,
timestamp) keyed by the exact head sha it reviewed, and the posted GitHub
status is for that same sha. A new commit on the PR has no status at its own
sha (GitHub just shows no check there) until a fresh reviewer run posts one --
this is what "any new commit voids the verdict" means in code, not a promise.

No HMAC/signature beyond "the status was posted by the fleet's gh-authenticated
token and the JSON is the audit trail" -- flagged to orch-console as a design
choice open to objection before Monday.

## 4. Isolation (orch-console guard #1, bus #81749)

`tasks/loop-pilot/<task-id>/hidden/{seeded-bugs.json,grader.py}` live **outside**
any git worktree a role session operates in -- not gitignored inside the task
repo, but in a separate directory tree under the orchestrator checkout that
only `scripts/loop/run_pilot.py` (the harness, never a role session) reads.
Same for `prompts/reviewer.md` relative to the builder's worktree: the builder
session's `--scheduled-prompt` points only at `prompts/builder.md`, and its
worktree never has the reviewer prompt file checked out or copied in.
Before the first real run: verify a builder session's filesystem reach can't
walk up to the hidden dir (worktrees are separate checkouts under
`~/wingmen/projects/`, task hidden data lives under the orchestrator checkout --
different trees, but confirming with an actual probe, not assuming).

## 5. Cost tracking (orch-console guard #2, bus #81749)

- Claude role sessions (condition B's spec-writer + reviewer) run under the
  **Syed** token pool, not Musa's -- need to confirm `switch_lane_token`
  (or equivalent) works for a one-shot `--scheduled-prompt` session, not just
  an interactive lane boot.
- DeepSeek cost: `GET /user/balance` immediately before and after each task
  run, delta = that task's spend. Key fetched via `provider_vault_fetch.py`
  (confirmed working end-to-end for `cc-loop-1`, 2026-10-29), never printed.
  This is **separate** from cc-fleet-health's `deepseek_balance_poll.py`
  (hourly cadence, PR#630) -- too coarse for a per-task delta; reusing their
  balance-GET call shape, not their poller.

## 6. Open items

1. Task selection: cc-hifz-1 didn't point at specific backlog items; drafting
   6-10 tasks + seeded bugs myself, sanity-checked by cc-hifz-1 before any
   real run.
2. Whether hifz already has a local-runner GH-status mechanism to reuse
   instead of `local_ci_runner.sh` -- asked cc-hifz-1, no answer yet.
3. `switch_lane_token`-for-one-shot-session and the builder-worktree isolation
   probe (section 4) are unverified -- both on Friday's build list.
