# Task: drop the unnecessary root WSL probe in cubeasht_monitor.py

You are working in a git worktree of the `orchestrator` repo, on a fresh
branch based on `fable/substrate-safe-fixes`. Make a small, focused change,
commit it, push the branch, and open a pull request against
`fable/substrate-safe-fixes`.

## Background

`scripts/cubeasht_monitor.py` monitors a remote Windows host (`cubeasht`)
that runs a WSL distro hosting a self-hosted CI runner. One line (around
line 775) launches a **read-only** probe script inside WSL as root:

```python
wcp = _ssh(f"wsl -d {WSL_DISTRO} -u root -- bash -s", WSL_SCRIPT, WSL_TIMEOUT_S)
```

`WSL_SCRIPT` is `scripts/cubeasht_collect_wsl.sh` — read it. It only runs
`systemctl is-active <unit>` (querying a systemd unit's status is allowed for
any user, not just root), `cat /proc/uptime`, `cat /proc/loadavg`, and
`free -m`. None of these need root. Running this read-only monitoring probe
as root is an unnecessary privilege escalation with no functional benefit —
least-privilege says drop it.

## What to build

1. In `scripts/cubeasht_monitor.py`, change the WSL probe command so it no
   longer passes `-u root` to `wsl`, i.e. `wsl -d {WSL_DISTRO} -- bash -s`
   instead of `wsl -d {WSL_DISTRO} -u root -- bash -s`. Also update the
   comment at the top of `scripts/cubeasht_collect_wsl.sh` that documents the
   exact invocation (it currently shows `-u root` too), so the doc comment
   and the real code stay in sync.
2. Add or extend a test in `tests/test_cubeasht_monitor.py` (following this
   repo's existing pytest conventions in that file) that locks the exact WSL
   probe command string and asserts it does **not** contain `-u root` — so a
   future edit can't silently reintroduce the unnecessary root escalation.
   Look at how the existing tests in that file mock/exercise this module
   (e.g. how `_ssh`/`collect()` or similar helpers are tested) and follow the
   same style; if nothing currently tests the exact command string, you can
   test it directly as a string (e.g. call whatever function/line builds it,
   or construct the same f-string the module does and assert on it) — keep
   it simple and consistent with the file's existing test style.
3. Run the full `tests/test_cubeasht_monitor.py` suite yourself and confirm
   everything passes before opening the PR.

## Constraints

- Orchestrator repo only. Do not touch `.env`, any secrets/credentials, or
  any client-repo code.
- Do not actually `ssh` to the real `cubeasht` host or run any real `wsl`
  command — this is a static code + test change only, no live network calls.
- Keep the change minimal and focused on exactly this gap (the `-u root`
  removal + its doc comment + a regression test). Do not refactor anything
  else in the file.
- Write a normal commit message and PR description (no need to mention how
  you were briefed).
- Open the PR against base branch `fable/substrate-safe-fixes` (not `main`).
