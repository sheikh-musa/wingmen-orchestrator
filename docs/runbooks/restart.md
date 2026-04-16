# Orchestrator Restart Runbook

Operator manual for safely restarting `wingmen_orch.py` on the Mac Mini.

The one command to memorize:

```sh
./scripts/restart_orch.sh
```

## Why launchctl only

`wingmen_orch.py` runs under a user LaunchAgent with `KeepAlive = true`.
launchd is the single source of truth for the orchestrator process: it
restarts it on crash, ties stdout/stderr to `logs/orch.{log,err}`, and
delivers SIGTERM on stop so the asyncio cleanup path from Job #82
(`_cancel_pending_tasks` → `loop.close()`) runs cleanly.

Any restart path that bypasses launchd (e.g. `nohup python wingmen_orch.py &`)
creates a second orchestrator process outside the supervision tree. The
LaunchAgent keeps restarting the original; the manual process keeps running
alongside it. Two Telegram pollers, duplicate job pickups, double-fired
commands. This has happened before — hence this runbook.

## Label and paths

| Thing | Value |
|---|---|
| LaunchAgent label | `dev.wingmen.orchestrator` |
| Canonical plist (repo) | `ops/launchd/com.wingmen.orchestrator.plist` |
| Installed plist | `~/Library/LaunchAgents/dev.wingmen.orchestrator.plist` |
| launchctl domain | `gui/$(id -u)` |
| Service target | `gui/$(id -u)/dev.wingmen.orchestrator` |
| stdout | `logs/orch.log` |
| stderr | `logs/orch.err` |

Override the label for testing: `ORCH_LAUNCHD_LABEL=dev.wingmen.orchestrator.staging ./scripts/restart_orch.sh`.

## Safe restart (happy path)

```sh
cd ~/wingmen/orchestrator
./scripts/restart_orch.sh
```

What the script does, in order:

1. Refuses to run if invoked under `nohup` (SIGHUP ignored).
2. Prints the current `launchctl print gui/$(id -u)/dev.wingmen.orchestrator`
   state (first 40 lines).
3. Pre-flight: queries Supabase for `jobs.status='running'` rows when
   `supabase` CLI and `$SUPABASE_DB_URL` are available; otherwise prints a
   warning and continues.
4. Runs `launchctl kickstart -k <service>`. This sends SIGTERM to the
   running process, waits for it to exit, then starts a fresh instance in
   a single atomic operation. `KeepAlive` sees this as a normal restart,
   not a crash.
5. Polls `launchctl print` for up to 15s until `state = running` with a
   non-zero `pid`.
6. Tails the last 20 lines of `logs/orch.err` so you see the boot banner.

Expected exit: `0`, with a `Restart complete.` line at the end.

## Safe restart (with pending jobs)

If step 3 reports one or more `running` jobs:

1. Check Telegram for the job IDs — are they mid-deploy, mid-Claude-session,
   or stuck?
2. If stuck: mark them `failed` in Supabase before restarting (the Job #82
   graceful shutdown path cancels in-flight asyncio tasks, but the
   Supabase row still needs to be closed out so the queue-stall detector
   doesn't re-fire later).
3. If legitimately in-flight: wait or escalate to CTO bot; don't restart
   mid-deploy.
4. When clear, run `./scripts/restart_orch.sh` as normal.

## Recovery: stuck process

If the script exits non-zero in step 5 (didn't reach `running` within 15s),
it prints the manual recovery commands. In summary:

```sh
launchctl bootout gui/$(id -u)/dev.wingmen.orchestrator || true
rm -f ~/wingmen/orchestrator/*.pid   # only if a stale pid file exists
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.wingmen.orchestrator.plist
./scripts/restart_orch.sh
```

If `bootstrap` fails with an already-loaded error, `bootout` the service
again and retry. If the plist itself has drifted from the repo copy,
`diff ops/launchd/com.wingmen.orchestrator.plist ~/Library/LaunchAgents/dev.wingmen.orchestrator.plist`
and reinstall from the repo copy.

## Forbidden patterns

### `nohup python wingmen_orch.py &` — **NEVER**

~~`nohup python wingmen_orch.py > orch.log 2>&1 &`~~

This is forbidden. Running the orchestrator via `nohup` (or `disown`, or
`screen`, or `tmux`) creates a process that launchd does not know about:

- launchd still sees its own copy as "not running" and starts it again.
  You now have **two orchestrators** polling Telegram and Supabase.
- `KeepAlive` can never bring the nohup'd process down — it's outside the
  domain. You have to `kill -9` it by hand.
- SIGTERM-driven graceful shutdown (Job #82) still runs only in the
  launchd copy; the nohup copy dies uncleanly and leaves pending asyncio
  tasks.
- `logs/orch.{log,err}` are owned by launchd's copy; the nohup process
  writes to a different file, and half the diagnostics vanish.

### Also forbidden

- `launchctl unload ... && launchctl load ...` — races with `KeepAlive`.
  Use `launchctl kickstart -k` (what the script does).
- `pkill -f wingmen_orch.py` — `KeepAlive` restarts immediately, so you
  just get an uncontrolled bounce with no pre-flight check.
- `./scripts/restart_orch.sh &` — same class of mistake as `nohup`.

## See also

- Job #82 (BUG-015) — graceful shutdown asyncio cleanup. `kickstart -k`
  sends SIGTERM first; the `_cancel_pending_tasks(loop)` helper in
  `wingmen_orch.py` cancels any tasks still pending after
  `run_until_complete` so we don't leak warnings on restart.
- `ops/launchd/com.wingmen.orchestrator.plist` — canonical LaunchAgent
  plist, mirror of the installed copy.
