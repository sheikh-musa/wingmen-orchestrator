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
   `run_pilot.py` before launch). Writes the spec + acceptance tests, commits
   spec + tests in one commit on the task branch, and writes a plain
   `.loop-task/test_files.txt` (one test path per line, never committed) so
   the harness knows which files it wrote. **The role never calls
   `record_manifest.py` itself** (2026-10-30 isolation fix, see section 4) --
   `run_pilot.py` reads `test_files.txt` back after the role exits, computes
   `actual_sha = git rev-parse HEAD`, and makes the ENTIRE `record_manifest.py`
   call itself (test paths + spec-commit-sha + authoritative model, all in one
   harness-side call), then `git commit --amend` to fold `manifest.json` into
   the role's own commit.
2. **Builder**: `--scheduled-prompt prompts/builder.md`. Prompt explicitly
   lists the manifest's test paths as off-limits. Builder commits
   implementation only, opens the PR against `loop-pilot/base` itself (`gh`
   is on the env allow-list already -- builder never needed an orch_dir path).
3. **Reviewer**: a *different* session from the builder (own worktree, own
   `--scheduled-prompt prompts/reviewer.md`), reading spec + diff + running
   tests. The role writes only a plain `.loop-task/verdict.txt`
   (`VERDICT=.../REASON=...`, two lines, never committed). **The role never
   calls `reviewer_verdict.py`, `gh_status.py`, or touches
   `loop-pilot/reviews` itself** (same 2026-10-30 fix) -- `run_pilot.py` reads
   `verdict.txt` back after the role exits and does the entire verdict-file
   write, the `loop-pilot/reviews` checkout/commit/push, and the GitHub
   status post, all itself. See section 3 for exactly what the harness does
   there and why.

### `.loop-task/` harness contract

Before launching ANY role, `run_pilot.py` writes these into that role's
worktree (never via env -- section 1's addendum on why):

| file | content | written by |
|---|---|---|
| `.loop-task/task_id.txt` | the task id, first line | harness, before launch |
| `.loop-task/brief.md` | the task brief (spec-writer only needs this one) | harness, before launch |
| `.loop-task/test_files.txt` | one acceptance-test path per line | spec-writer role, read by harness after it exits |
| `.loop-task/verdict.txt` | `VERDICT=.../REASON=...`, two lines | reviewer role, read by harness after it exits |

None of these are ever committed -- `.loop-task/` is harness scratch, not
part of the audit record (the manifest/verdict files under `.loop/` are).

**No `orch_dir.txt` and never will be again (2026-10-30 fix).** The original
design had the role invoke `record_manifest.py` / `reviewer_verdict.py` /
`gh_status.py` itself, which meant handing it the orchestrator checkout's
absolute path -- and that path is also the direct route to
`tasks/loop-pilot/<task-id>/hidden/`, the exact thing orch-console's guard #1
says no role may reach. Caught during the harness smoke test (section 6e),
before any real role session ran: every role-side script call was removed in
favor of the role writing a plain local file and the harness doing 100% of
the manifest/verdict/git-branch/status-posting work itself, in its own
process, after the role exits. No role now has, or needs, any path into the
orchestrator checkout at all -- see section 4 for what this does and does
not guarantee.

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
it does not commit. As of the 2026-10-30 isolation fix, the harness is the
ONLY caller of this script (never the role -- see the `.loop-task/` contract
note above); it amends the manifest into the role's own spec+tests commit
right after computing it, so the manifest and the hashes it claims still
land in the same commit as before, just via `commit --amend` from the
harness rather than being included in the role's original commit.

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
than the one it's filed under. As of the 2026-10-30 isolation fix, this
entire sequence is harness-side, not the reviewer role's:

1. The reviewer role captures `HEAD_SHA` the moment its worktree starts
   (before anything else), reviews/tests the PR at that sha, and writes
   `.loop-task/verdict.txt` (plain, local, never committed) -- then exits.
   It never runs `reviewer_verdict.py`, never touches `loop-pilot/reviews`,
   never calls `gh_status.py`.
2. `run_pilot.py` reads `verdict.txt` back, runs `reviewer_verdict.py` itself
   to write `.loop/<task>/reviews/<HEAD_SHA>.json`, `git fetch` + `checkout -B
   loop-pilot/reviews` in that same (still-detached-at-HEAD_SHA) worktree,
   commits+pushes just that one file, and finally posts the GitHub status
   for the ORIGINAL `HEAD_SHA` (captured before the branch switch) --  not
   whatever commit the `loop-pilot/reviews` checkout produced.

`GH_TOKEN` was on launch_dangerous_cc.sh's env allow-list
(scripts/lib/lane_env_allowlist.sh, KEEP tier) for the reviewer *role* to
call `gh api` directly under the original design; the role no longer needs
it at all now that none of this is role-side, but `run_pilot.py` (the
harness process, launched by the operator/fleet, not under the lane
allow-list scrub) still needs `gh` on its own PATH, which it already has.

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

**Real gap found and fixed, 2026-10-29/30, before any real role session ran:**
the first version of this design handed every role `.loop-task/orch_dir.txt`
-- the orchestrator checkout's absolute path -- so it could invoke
`record_manifest.py`/`reviewer_verdict.py`/`gh_status.py` itself. That same
path is also the direct route to the hidden dir this section exists to
protect: `<orch_dir>/tasks/loop-pilot/<task_id>/hidden/seeded-bugs.json` is
one `cat` away from anything holding `orch_dir.txt` plus its own
`task_id.txt` -- both of which every role already had. Caught during the
harness smoke test (section 6e) while verifying the pipeline end to end, not
by a dedicated filesystem probe. Fixed by removing the role's need for
`orch_dir` entirely (section 1, `.loop-task/` contract) -- no role, in
either condition, is ever handed any path into the orchestrator checkout now.

**What this does and does not guarantee.** This closes the one guaranteed,
trivial discovery path (a role being handed the exact address). It is *not*
a sandboxing guarantee against a role session that goes looking: every role
runs under `--dangerously-skip-permissions` with full shell access, as the
same OS user as the harness itself (no container, no separate user, no
filesystem ACL) -- a session that actively ran e.g. `find ~ -iname
"seeded-bugs*"` would still, in principle, be able to find and read it,
because nothing at the OS level prevents it. That class of defense (real
sandboxing / a separate user or container per role) is out of scope for a
pilot built by Monday; it is a real limitation of "safe to trust a cheap
builder model" that any report to orch-console/Musa should state plainly,
not paper over. What this fix buys is removing the ONE guaranteed, nothing-
has-to-go-looking path -- which is the realistic risk (a role incidentally
reading what it was handed), not the adversarial one.

## 5. Cost tracking (orch-console guard #2, bus #81749)

- **RESOLVED (2026-10-30, op#35690): condition-B Claude auth gap, found and
  fixed in `run_pilot.py`, not `switch_lane_token`.** The original framing
  here ("confirm `switch_lane_token` works for a one-shot session") was a
  wrong mental model -- `switch_lane_token.sh` only re-tokens a *persistent
  tmux lane* (kill + relaunch); it has no bearing on this harness's one-shot
  `launch_dangerous_cc.sh --scheduled-prompt` subprocess launches at all.
  The real root cause, confirmed by a probe whose own session transcript
  showed `"Not logged in · Please run /login"` with every message
  `type: "<synthetic>"` (no real model turn ever happened): `launch_role()`
  invokes `launch_dangerous_cc.sh` from `ORCH_DIR` (this harness's own
  worktree, which carries no `.env`), so that script's `. "$ORCH_DIR/.env"`
  silently no-ops and its token-resolver call also resolves against the
  wrong `orch_dir` -- the launched Claude-model role subprocess ends up with
  neither `CLAUDE_CODE_OAUTH_TOKEN` nor `ANTHROPIC_API_KEY` at all. Fixed by
  having `run_pilot.py` resolve and inject `CLAUDE_CODE_OAUTH_TOKEN` itself,
  explicitly against the **live** checkout (`LIVE_ORCH_DIR`, not `ORCH_DIR`):
  `resolve_claude_oauth_token()` calls
  `lane_token_resolver.resolve_lane_token_path(tmux_session, orch_dir=str(LIVE_ORCH_DIR))`
  using the real tmux session name (`tmux display-message -p '#S'`, exactly
  how `launch_dangerous_cc.sh` itself derives `_LANE_SESSION`) -- not the
  agent_id, which would silently miss this lane's explicit per-session pin
  `.lane_session_token.loop` (keyed on the tmux session name "loop", not
  "cc-loop") and wrongly fall through to Musa's personal account instead of
  the fleet's deliberate Syed token-pool-conservation pin for this lane.
  Verified end-to-end (not just in isolation): a real condition-B probe
  (task-02, `--max-turns-spec 8`) produced a genuine, fully-authenticated
  Claude Opus session (real model turns, `modelUsage.claude-opus-4-8.costUSD
  = 0.87367`, real file writes) -- it only exited nonzero because 8 turns
  was too tight a diagnostic budget for the real task, not an auth failure.
- **`claude_cost` is now measured, not a placeholder.** `launch_role()` calls
  `extract_claude_session_cost(worktree, start_wall)` for every
  `model.startswith("claude-")` role launch: it finds that exact role's own
  Claude Code transcript under `~/.claude/projects/*/*.jsonl` by matching the
  transcript's own `"cwd":"<worktree>"` field (never a hand-rolled guess at
  Claude Code's undocumented project-dir escaping scheme) among files
  modified since that launch's start time, and reads the last `cost-state`
  line's `totalCostUSD`. `run_task()` records each role's cost into
  `result.claude_cost_usd_by_role` (captured right after `launch_role`
  returns, before the error check -- tokens are spent even on a
  max-turns-truncated role, as the task-02 probe above demonstrated) and
  sums them into `result.claude_cost` in the `finally` block; a role whose
  transcript isn't found is reported as
  `"could not measure: no matching transcript found for role(s) [...]"`
  (with any partial known cost noted), never silently treated as free.
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

## 6f. Expect `mergeStateStatus: UNSTABLE` on every pilot PR, harmlessly

hifz's `ci.yml` triggers on `pull_request:` regardless of base branch, so
every builder PR against `loop-pilot/base` also fires hifz's real GitHub
Actions `quality-gates` run -- and it fails in ~3s every time:
"The job was not started because an Actions budget is preventing further
use." Zero cost, not a new problem, exactly the budget-exhausted state the
brief already described. It is NOT one of the 3 required contexts, so branch
protection still allows the merge once `tests`/`spec-tests-unchanged`/
`reviewer-verdict` are green -- GitHub just shows the PR as
`mergeStateStatus: UNSTABLE` (one non-required check red) rather than
`CLEAN`. Confirmed via the smoke test's PR #10: all 3 required contexts
SUCCESS, `mergeable: MERGEABLE`, `mergeStateStatus: UNSTABLE` -- expected,
not a signal anything is wrong.

## 7. Open items

1. Task selection: cc-hifz-1 didn't point at specific backlog items; drafting
   6-10 tasks + seeded bugs myself, sanity-checked by cc-hifz-1 before any
   real run. **task-01-manzil-catchup** done, verified, and run live
   (condition A, PASS, `correct_pass`). **task-02-manzil-miss-classifier**
   drafted 2026-10-30 (H-7 2nd/3rd-miss ordinal classification, the
   `reconciler.ts:241` gap) -- oracle verified clean against a correct
   reference implementation and catches all 6 seeded-bug variants
   individually (boundary-off-by-one, forgot-plus-one, ms-based-day-math,
   wrong-status-thresholds, includes-future-dated-misses,
   mutates-input-array); not yet run live. **task-03-oracle-audit-plan**
   drafted 2026-10-30 (H-6's mastered-pool eligibility + pool-size-based
   sample-size calc, the never-built `scripts/audits/fsrs_oracle_sweep.ts`
   planning step) -- oracle verified clean against a correct reference and
   catches all 6 seeded-bug variants (wrong-threshold-base,
   off-by-one-threshold, wrong-eligibility-operator,
   wrong-interval-boundary, no-cap-to-pool-size, mutates-input-array); the
   first attempt at wrong-threshold-base wasn't caught (the pool-size cap
   masked it when the eligible pool was tiny) -- fixed by adding a test
   where the eligible pool is bigger than the correct base but smaller
   than the wrong one, so the cap can't hide the difference; not yet run
   live. **task-04-local-calendar-day** drafted 2026-10-30 (H-5's explicit
   "user's timezone, not UTC" requirement -- `reconciler.ts:103-104`'s
   existing `elapsedDays` is raw-ms-based, not timezone-aware at all; this
   task is the missing primitive `localCalendarDayKey`/`isSameLocalDay`,
   NOT a fix to `reconciler.ts` itself, which stays out of scope) -- oracle
   verified clean against a correct `Intl.DateTimeFormat`-based reference
   and catches all 6 seeded-bug variants (ignores-timezone-uses-utc,
   ignores-timezone-uses-system-local, hardcoded-fixed-offset-ignoring-dst,
   sign-flipped-offset-math, wrong-output-format,
   isSameLocalDay-ignores-timezone-param); not yet run live.
   **task-05-ayah-status-classifier** drafted 2026-10-30 (H-8's "an ayah
   can be fluent in hifz and weak in tajweed simultaneously -- collapsing
   them corrupts both signals") -- oracle verified clean against a
   correct reference and catches all 6 seeded-bug variants
   (tajweed-grade-affects-hifz-fluent, hifz-state-affects-tajweed-weak,
   wrong-retrievability-boundary, ignores-leech-flag,
   wrong-grade-boundary, wrong-combined-label-mapping); the
   hifz-state-affects-tajweed-weak variant wasn't caught on the first
   pass (my no-cross-contamination test only varied retrievability, never
   isLeech, so a bug that leaked isLeech into tajweedWeak sailed through)
   -- fixed by adding an isLeech-specific no-cross-contamination test; not
   yet run live. **task-06-fsrs-export-payload** drafted 2026-10-30 (H-9's
   full FSRS state + audit log export -- distinct from the existing,
   already-shipped `backup/service.ts createBackupPayload()`, which
   covers progress/sessions/downloads/settings but not FSRS scheduler
   state or `fsrs_audit_events`; out of scope to touch that function) --
   oracle verified clean against a correct reference and catches all 6
   seeded-bug variants (uses-date-now-instead-of-param,
   wrong-row-counts, shallow-reference-leak, mutates-input-arrays,
   dedupes-rows, wrong-schema-version-type); not yet run live.

   **6 of 6 minimum pilot tasks now drafted and individually
   oracle-verified** (each against a correct reference implementation
   AND every one of its own seeded-bug variants, in a scratch worktree,
   before being trusted) -- none run live yet except task-01.
   **PR#666 merged** (2026-10-30T09:25:45Z, confirmed CI green on the
   merge commit). **Condition-A unblock in flight**: `deepseek_key_for_family.py`
   fail-closed-refused `family='loop'` (no vault-key mapping) -- escalated
   to orch-console/cc-fleet-health rather than self-editing the vault/map;
   cc-fleet-health's PR#669 (adds an explicit `loop` row -> the shared
   `DEEPSEEK_CODING_KEY`) is built and pushed, awaiting cc-quality then
   orch-console merge. **Condition-B unblock DONE** (this section, 2026-10-30):
   the Claude-auth gap is fixed and verified end-to-end in `run_pilot.py`.
   Next: resume condition A once PR#669 merges, then run the full A-vs-B
   measured pilot across all 6 tasks.
2. Whether hifz already has a local-runner GH-status mechanism to reuse
   instead of `local_ci_runner.sh` -- asked cc-hifz-1, no answer yet.
3. ~~`switch_lane_token`-for-one-shot-session and the builder-worktree isolation~~
   The builder-worktree isolation probe (section 4) is still open; the
   condition-B auth gap (originally miscategorized as a `switch_lane_token`
   question) is resolved -- see section 5.
