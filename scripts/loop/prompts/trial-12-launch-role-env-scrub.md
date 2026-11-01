# Task: scrub ORCH_DIR/ORCH_ROOT from the loop harness's role env

You are working in a git worktree of the `orchestrator` repo, on a fresh
branch based on `fable/substrate-safe-fixes`. Make a small, focused change,
commit it, push the branch, and open a pull request against
`fable/substrate-safe-fixes`.

## Background

`scripts/loop/run_pilot.py`'s `launch_role()` function launches a role
session (spec-writer/builder/reviewer) in an isolated `hifz-companion`
worktree, via `scripts/launch_dangerous_cc.sh`. The isolation design
(documented in `docs/loop-pilot-design-v1.md`) is explicit that a role must
never be able to locate or reach the orchestrator checkout's hidden task
data (`tasks/loop-pilot/<id>/hidden/`) — the role only ever sees the public
`.loop-task/` files written into its own worktree.

A cc-quality review of this harness (bus #83795, on PR#661) flagged a
residual LOW: `launch_role` builds the subprocess env as
`env = dict(os.environ)` (see the function body) before passing it to
`launch_dangerous_cc.sh`. That inherited env is then further filtered by
`scripts/lib/lane_env_allowlist.sh`'s `lane_env_scrubbed_pairs0()` (the
`env -i` allow-list that actually reaches the role's `claude` process) —
and `ORCH_DIR` + `ORCH_ROOT` are both on that allow-list's SAFE tier
(`lane_env_safe_names()` in `scripts/lib/lane_env_allowlist.sh`). That means
the role process's environment DOES carry the orchestrator checkout's
absolute path today, via `ORCH_DIR`/`ORCH_ROOT`. The review called this
"theoretical" at the time (no inherited env var pointed directly at the
hidden task data path, and nothing in the role's job needs it), but nothing
was ever verified or hardened to close it.

## What to build

1. In `scripts/loop/run_pilot.py`'s `launch_role()`, after building
   `env = dict(os.environ)`, explicitly remove `ORCH_DIR` and `ORCH_ROOT`
   from that `env` dict before it's passed to `subprocess.run(...)` (use
   `env.pop("ORCH_DIR", None)` / `env.pop("ORCH_ROOT", None)`, matching the
   existing style right below it that pops `ANTHROPIC_API_KEY`). Add a
   one-line comment explaining why (ties back to the PR#661 residual LOW /
   the isolation invariant — a role process should never carry the
   orchestrator checkout's absolute path in its environment, even though
   today nothing exploits it).
2. Add a test in a test file under `tests/` (create a new one, e.g.
   `tests/test_run_pilot_env_scrub.py`, or extend an existing loop-related
   test file if one fits better — check what's already there first) that
   calls `launch_role()` with `subprocess.run` mocked/monkeypatched (do NOT
   actually launch a real session — follow whatever mocking style
   `tests/test_run_pilot_redaction.py` or similar existing tests in this
   repo use for `run_pilot.py`, to stay consistent) and asserts that the
   `env` dict passed to the mocked `subprocess.run` call does NOT contain
   `ORCH_DIR` or `ORCH_ROOT` as keys.
3. Run the new test (and the existing `tests/test_run_pilot_redaction.py`,
   to make sure nothing broke) yourself and confirm everything passes
   before opening the PR.

## Constraints

- Orchestrator repo only. Do not touch `.env`, any secrets/credentials, or
  any client-repo code.
- Do NOT edit `scripts/lib/lane_env_allowlist.sh` — that allow-list is used
  fleet-wide by every lane, not just this harness, and removing `ORCH_DIR`/
  `ORCH_ROOT` from it globally is a much bigger change with its own blast
  radius that is explicitly OUT OF SCOPE here. This task is scoped narrowly
  to `run_pilot.py`'s own `launch_role()` — stop the leak at the one place
  that controls what this harness's role processes see, not fleet-wide.
- Do not actually launch a real Claude Code session, real `claude` process,
  or real subprocess during the test — mock/monkeypatch `subprocess.run`.
- Keep the change minimal and focused on exactly this gap. Do not refactor
  anything else in `run_pilot.py`.
- Write a normal commit message and PR description (no need to mention how
  you were briefed).
- Open the PR against base branch `fable/substrate-safe-fixes` (not `main`).
- **Important: do not merge this PR yourself under any circumstances and do
  not ask anyone to merge it urgently — this change touches the measurement
  harness itself and is deliberately held open/unmerged by policy until a
  separate review process completes.** Opening the PR and getting it
  reviewed is the whole task; leave it open.
