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

    MODEL=<model-id> CC_BASE_OVERRIDE=cc-loop scripts/launch_dangerous_cc.sh \
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

**Second addendum (found while reading `scripts/lib/auto_agent_id.py`,
2026-10-30): `CC_BASE_OVERRIDE=cc-loop` is not optional.** Identity allocation
resolves `CC_BASE_AGENT_ID` from the launch `pwd`'s git toplevel (after
stripping a `.wt-*` worktree suffix) against a family map built from
`agents.repo_scope` -- so a role launched from inside a hifz worktree would,
by default, resolve to `cc-hifz`'s family (`cc-hifz-N`), not `cc-loop`'s. That
would misattribute every role session's heartbeat/activity/exit-row to
cc-hifz-1 (a peer who did not spawn it and does not own this pilot's
sessions). `CC_BASE_OVERRIDE` is the sanctioned escape hatch for exactly this
shape of problem (`spawn_reviewer.sh` uses it the same way) -- it skips pwd
resolution entirely and is validated against a whitelist (CAI-RESP-258:
refuses authority/system ids outright, default-denies anything not already a
registered `cc-*` row). Confirmed `cc-loop` is registered
(`SELECT id, repo_scope FROM agents WHERE id = 'cc-loop'` -> one row, empty
`repo_scope`, which is correct -- this pilot spawns across whatever repo a
task targets, it isn't pinned to one). `run_pilot.py` sets
`CC_BASE_OVERRIDE=cc-loop` on every role launch, unconditionally; task
worktree naming does not need to follow any `.wt-*` convention for identity
purposes as a result (the override branch never calls
`resolve_base_agent_id` at all).

**Addendum (found while reading the launcher, 2026-10-29):** `launch_dangerous_cc.sh`
runs claude under `env -i` plus a strict allow-list
(`scripts/lib/lane_env_allowlist.sh`) -- a custom var like a task id or `MODEL`
does NOT reach the role session's process env unless added to the git-tracked
opt-in map, and this pilot has no standing reason to widen that allow-list.
So per-task parameters flow via **files written into the task worktree before
the role is launched**, never via env: the harness (`run_pilot.py`) writes
`.loop-task/brief.md` before the spec-writer role runs, and each role prompt
tells the session to read that path. Same reasoning for metadata the role
can't know about itself -- `spec_writer_model` in the manifest is filled in
by the harness's own patch call (it chose the model, so it knows it for
certain), not guessed by the role session. See `record_manifest.py`'s
`--spec-writer-model` default.

1. **Spec-writer**: `--scheduled-prompt prompts/spec_writer.md`. Reads the
   task brief from `.loop-task/brief.md` (written into the worktree by
   `run_pilot.py` before launch). Writes the spec + acceptance tests, calls
   `scripts/loop/record_manifest.py` to snapshot the test files, commits
   spec + tests + manifest in one commit on the task branch. `run_pilot.py`
   then reads the resulting HEAD sha and makes a second `record_manifest.py`
   call (`--spec-commit-sha --spec-writer-model`) to patch both fields in
   with authoritative values.
2. **Builder**: `--scheduled-prompt prompts/builder.md`. Prompt explicitly
   lists the manifest's test paths as off-limits. Builder commits
   implementation only, opens the PR against `loop-pilot/base`.
3. **Reviewer**: a *different* session from the builder (own worktree, own
   `--scheduled-prompt prompts/reviewer.md`), reading spec + diff + running
   tests. `scripts/loop/reviewer_verdict.py` writes the verdict file (on the
   shared `loop-pilot/reviews` branch, not the PR branch -- see section 3);
   `scripts/loop/gh_status.py` posts the actual GitHub status.

### `.loop-task/` harness contract

Before launching ANY role, `run_pilot.py` writes these into that role's
worktree (never via env -- section 1's addendum on why):

| file | content |
|---|---|
| `.loop-task/task_id.txt` | the task id, first line |
| `.loop-task/brief.md` | the task brief (spec-writer only needs this one) |
| `.loop-task/orch_dir.txt` | absolute path to the orchestrator checkout, so a role can invoke `record_manifest.py` / `reviewer_verdict.py` / `gh_status.py` by that path (they live in the orchestrator repo, not in hifz) |

`tasks/loop-pilot/<task-id>/hidden/{seeded-bugs.json,grader.py}` is
deliberately NOT in this list -- see section 4.

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

The verdict file is committed on a separate, shared **`loop-pilot/reviews`**
branch, never on the PR branch itself -- committing it onto the PR branch
would produce a new head sha, making the verdict describe a commit other
than the one it's filed under. The reviewer role: captures `HEAD_SHA` the
moment its worktree starts (before anything else), reviews/tests the PR at
that sha, writes the verdict file there, THEN `git checkout`s
`loop-pilot/reviews` in the same worktree to commit+push just that one file,
and finally posts the GitHub status for the ORIGINAL `HEAD_SHA` (captured in
step 1) -- not whatever commit switching branches produced. `GH_TOKEN` is on
launch_dangerous_cc.sh's env allow-list (scripts/lib/lane_env_allowlist.sh,
KEEP tier), so the reviewer session can call `gh api` directly; no separate
credential plumbing needed.

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

## 6a. Grader contract (`tasks/loop-pilot/<task-id>/hidden/grader.py`)

Ground truth, separate from the reviewer's own verdict -- this is what the
reviewer's verdict is measured AGAINST, so it must never be computed by
anything a role session can see or influence. `run_pilot.py` (never a role)
invokes it directly from the orchestrator checkout, pointed at the builder's
worktree:

    tasks/loop-pilot/<task-id>/hidden/grader.py --repo-dir <path> --head-sha <sha>

`seeded-bugs.json` lives alongside `grader.py` in the same `hidden/` dir and
is loaded by path relative to the script's own location, not passed as a
CLI arg -- grader.py owns knowing its own fixture. Prints one JSON object to
stdout:

    {"task_id": "...", "head_sha": "...",
     "bugs": [{"id": "bug-1", "survived": true, "detail": "..."}],
     "any_bug_survived": true}

Exit code 0 means the grader ran successfully (grading is informational, not
a merge gate) -- "bugs survived" is a field in the JSON, not the exit code.
Nonzero exit = the grader itself broke (missing fixture, crashed), which
`run_pilot.py` must report as "could not measure" for that task, never as
a clean PASS/FAIL.

`run_pilot.py` crosses the reviewer's verdict against `any_bug_survived` per
task:

| reviewer verdict | any_bug_survived | outcome |
|---|---|---|
| FAIL | true | correct catch |
| PASS | true | **false PASS** (the dangerous case the whole pilot exists to measure) |
| PASS | false | correct |
| FAIL | false | false alarm (noted, not the headline metric) |

## 6b. DeepSeek balance-delta caveat (found while reading `scripts/deepseek_balance_poll.py`, PR#630)

`GET https://api.deepseek.com/user/balance` is **account-level only, no
per-lane breakdown** (that script's own comment: "no per-lane split -- the
DeepSeek balance endpoint is account-level only"). A before/after delta
around one task run is only attributable to that task if nothing else on the
fleet spends DeepSeek tokens in the same window -- the hourly poller itself
is a GET, costs nothing, safe to run concurrently; any OTHER concurrent
DeepSeek-routed session is not. `run_pilot.py` runs condition-A task
attempts serially (never two DeepSeek-routed role sessions in flight at
once) for exactly this reason. If contamination is ever suspected (balance
moved by more than this task's own calls would plausibly cost), the report
says "could not measure" for that task's DeepSeek cost rather than
attributing a possibly-wrong number to it.

## 6c. Condition -> model mapping (`run_pilot.py` `CONDITIONS` table)

| condition | spec-writer | builder | reviewer |
|---|---|---|---|
| A (all-DeepSeek) | `deepseek-flash` | `deepseek-flash` | `deepseek-flash` |
| B (DeepSeek builder + Claude spec/review) | `claude-opus-4-8` | `deepseek-flash` | `claude-opus-4-8` |

`claude-opus-4-8` chosen because it's `launch_dangerous_cc.sh`'s own default
(see its header comment) -- made explicit here rather than left as an
implicit "whatever MODEL defaults to today" dependency.

## 6d. Identity: `CC_BASE_OVERRIDE=cc-loop` on every role launch

See the second addendum in section 1 -- without it, identity allocation
would resolve every role session's pwd (a hifz worktree) to `cc-hifz`'s
family instead of `cc-loop`'s. `run_pilot.py` sets it unconditionally.

## 6e. Harness smoke test (2026-10-30, before any real role session ran)

`scripts/loop/run_pilot.py` was exercised end to end against the real
`sheikh-musa/hifz` repo with the three role SESSIONS faked (no model
inference at all -- `launch_role` monkeypatched to perform the git actions a
real role would, nothing else) so every other step ran for real: worktree
creation off `loop-pilot/base`, `record_manifest.py`'s patch+amend, a real PR
against `loop-pilot/base`, the `tests` and `spec-tests-unchanged` GitHub
status checks under live branch protection, a detached reviewer worktree,
the shared `loop-pilot/reviews` push, and `reviewer_verdict.py`'s patch. Caught
one real bug this way, before it could hit a live (costly) role session:
**`check_spec_tests_unchanged.py`, `gh_status.py`, `record_manifest.py`, and
`reviewer_verdict.py` were not executable** (`chmod +x` never applied when
they were written). `local_ci_runner.sh` invokes `gh_status.py` by direct
path (no `python3` prefix), so under its `set -euo pipefail` a `Permission
denied` on that line aborted the whole script -- the CI steps themselves
(lint/typecheck/build/smoke:audit) had all genuinely passed; `local_ci_runner.sh`
only *reported* failure because of the permission bug. The same four scripts
are invoked the same direct-by-path way from inside `spec_writer.md` and
`reviewer.md` -- a real role session would have hit the identical wall.
Fixed: `chmod +x` on all four, re-verified `run_tests_check()` reports
`success` on the same worktree/sha afterward. `run_tests_check()` /
`run_spec_tests_check()` also previously discarded the underlying script's
stdout/stderr entirely -- fixed to keep the last ~2000 chars in
`tests_check_detail` / `spec_tests_unchanged_detail`, since an opaque
"failure" with no trace is exactly the kind of thing this bug would have
hidden from a real pilot run.

## 7. Open items

1. Task selection: cc-hifz-1 didn't point at specific backlog items; drafting
   6-10 tasks + seeded bugs myself, sanity-checked by cc-hifz-1 before any
   real run.
2. Whether hifz already has a local-runner GH-status mechanism to reuse
   instead of `local_ci_runner.sh` -- asked cc-hifz-1, no answer yet.
3. `switch_lane_token`-for-one-shot-session and the builder-worktree isolation
   probe (section 4) are unverified -- both on Friday's build list.
