# Substrate DB password rotation — runbook

*Owner: cc-substrate · written 2026-10-31 · fires only on the console's explicit GO*

The substrate database (`tscuymavysscrvoberrr`) password is being rotated because the
current value has been read by processes it should never have reached. This runbook is
the whole operation: what runs, in what order, what to look at when it fails, and what
"done" means.

**Nothing in this runbook names the credential.** Values are referred to by the first 8
hex characters of their SHA-256. That is enough to tell two values apart and useless to
anyone who reads it.

---

## 0. What is true before the window opens

| Fact | How to check it |
|---|---|
| The live Mini env file carries exactly one key with the rotating credential | `rotate_substrate_dsn.py check` (prints `pw=sha256:…`, never the value) |
| Every target file carries the *same* value | preflight, step 1 below |
| Every daemon that holds a connection is loaded | preflight, step 1 below |
| The Management API PAT is in the environment | preflight, step 1 below |
| The old credential still connects | preflight, step 1 below |

**Checked ahead of the window, and it expires (2026-10-31 20:44Z):** the PAT in the live
env file reads the project — `GET /v1/projects/<ref>` returned **HTTP 200**,
`status=ACTIVE_HEALTHY`, region `ap-southeast-2`. That proves the token is valid and can
see the project, which is the failure mode a 401/403 at patch time would otherwise hide
until step 3. It does **not** prove the token carries the write scope the PATCH needs, and
it is a verification with an expiry: re-run the same GET before the window rather than
trusting this line.

If any of those is false, preflight refuses and nothing is written. That refusal is the
correct outcome, not an obstacle: a rotation against a state you cannot describe is how
you lose a database.

**The hub half is PULL, not push (settled 2026-10-31, bus 87863/87890).** The hub is
the ssh alias `gzb`; its `.env` is a tmpfs file recreated at every boot by
`fetch-secrets`. The Mini's own `authorized_keys` carries one forced command that
assembles the bundle **on this machine** and streams it as a tar, so rewriting the Mini
files is the whole job on this side and the hub pulls the new value when it runs
`fetch-secrets`. **Do not use `--remote` against `gzb`.** A push into its files from
outside that pull was never reviewed, and the pull already does the same work.

**OPEN, and it can sink the hub's restart — ANSWERED 2026-10-31 (87894): it does not.**
The bundle is assembled from three sources — the Mini's orchestrator env file, and two
private env files under `~/.wingmen/private` (one for the write DSN, one for bayanqa).
The hub reads those same bytes in its own tmpfs copy and checked both, hashes only: the
write-DSN file carries the goumlyne and ihsanos values and no substrate value at all, and
the bayanqa file has no database value whatsoever. **Rewriting the live orchestrator env
file is therefore sufficient; there is no dead-value risk on the hub's restart.**

**Also asked, not yet answered:** whether all three Mini env files are in scope, or only
the live one. Preflight reads all three and they all carry the current value, so the
default covers them — but "the default covers it" is not the same as "it was decided".

---

## 1. Plan (harmless, do this first, always)

```bash
cd ~/wingmen/projects/orchestrator.wt-exec      # the worktree carrying the executor
PY=~/wingmen/orchestrator/.venv/bin/python3     # the live venv; this worktree has none
$PY scripts/substrate_rotation_executor.py plan --old-sha8 <sha8 of the CURRENT password>
```

`--old-sha8` is required on purpose: you must state which value you believe you are
rotating. The current one starts `95ea35a8` (as printed by the tool's own check, and
confirmed against the live file on 2026-10-31). A hash prefix is not a secret.

Expected on a healthy host:

```
[2026-10-31T20:13:09Z] preflight: OK targets=3 remote=False daemons=9
plan: preflight OK. Steps that WOULD run: generate, rewrite, patch, propagate, restart, verify, watch
plan: nothing written. Re-run with --execute --confirm rotate-substrate
```

`plan` writes nothing, patches nothing and stages nothing. If it does not print those
lines, stop and read the refusal.

---

## 2. The order, and why each step is where it is

The order **is** the safety property. This is not a preference.

1. **generate** — a new password is produced in-process (`token_urlsafe(36)`, so it is
   safe inside a URL and a libpq conninfo with no percent-encoding) and written to the
   vault. It is never printed and never journaled.
2. **rewrite** — the target env files are rewritten from the old value to the new one,
   each with a timestamped backup and `0600` preserved. On a remote host the value
   travels on **stdin**, never on an ssh argv.
3. **patch** — `PATCH /v1/projects/{ref}/database/password` with the value in the JSON
   body. This is the point of no return.
4. **propagate** — the tmux global `DATABASE_URL`, and the standing instruction for the
   hub to re-run `fetch-secrets` (pull) and then its restart groups.
5. **restart** — every launchd loader is kicked. This must be adjacent to the patch: the
   only interval in which the fleet is broken is between 3 and 5, and it should be
   seconds. **This step does NOT cover the two tmux singletons** (console, fleet-health) —
   they get step 7 below.
6. **verify** — the new value must connect, and the old value must **fail**. Both halves
   matter; a rotation where the old password still works did not happen.
7. **singleton restart (operator-driven, not by this tool)** — once verify has shown the
   old value refused, fleet-health restarts first and the console last (see the decided
   design below). The executor does not do this: restarting the console is the one action
   that ends the session running the operation.
8. **watch** — re-probe for the configured window.

**Why the vault write comes before the patch.** The vault lives *inside* the substrate
database. Fetching from it uses the same connection the rotation is rotating. Stage
after the patch and the new value exists only in the executor's memory and in the files
it is about to write; any failure there strands it behind a password nobody knows. Stage
first and the vault is the recovery path.

**Why rewriting files before the patch is safe.** Postgres authenticates at connect
time. A file that carries the not-yet-active value breaks nothing until the moment the
old value stops working — which is the moment the patch lands, immediately followed by
the restart. There is no window where a rewrite alone causes an outage.

**The heartbeat trap (found by correcting my own earlier claim, bus 87897).** The 5-min
heartbeat loops in `boot_nazim.sh`, `boot_cai.sh` and `boot_fleet_health.sh` read the DSN
from `os.environ` inside a bash subshell forked when the singleton booted. A rewritten
file and a `tmux set-environment` change nothing for that already-running process: every
beat after the patch fails, silently (the connect is wrapped in discard-stderr-or-true),
and `last_heartbeat` freezes on the singleton rows — a live body read as dead, exactly
when the console is watching.

**Measured, not assumed (2026-10-31, bus 87964) — the scope is smaller than the plan says.**
Only **two** loops exist tonight. `cai` has no tmux session on this host and its
`agent_status` row has read `offline` since 13 October, so there is no cai body to keep
alive; if it is meant to be up that is a separate outage, not a rotation problem.

Both live loops are **children of their boot shell**, not detached orphans reparented to
pid 1: the nazim pane is `boot_nazim.sh`, alive, with the loop and the claude body as its
two children, and the fleet-health pane is the same shape. That matters because the boot
shell owns the EXIT trap and the trap does exactly one thing to the loop — `kill` the pid
it recorded at boot. Kill the loop and fork a replacement, and that kill lands on a dead
pid forever after: the trap still stamps the row `offline` on exit while the replacement
keeps stamping `working` over it. A dead body then reads alive indefinitely, which is
worse than a frozen row — a frozen row is a false alarm, this is a false all-clear.

`scripts/lib/dsn_at_use.py` makes every **future** fork immune by reading the file at the
moment of use. It does **not** retrofit the running loops, and neither does editing the
boot script: measured on this machine (bus 87909), a running bash loop whose heredoc is
changed — by full-file replacement *and* by in-place overwrite of the same inode — keeps
printing the OLD body, five beats out of five. Bash parses the function once. **There is
no no-restart fallback.** Getting fresh code into a loop means one of the three outcomes
below.

**The decided design (console, bus 87978 — final for this window).** The measured facts
above killed the "no restart" premise and the "replace only the loop" option, so the
design is now three parts and contains **no new lifeline code**:

1. **A bounded declaration, not a suppression.** At GO, cc-substrate posts one durable
   P1 bus row to cc-fleet-health naming the two ids that will go heartbeat-blind
   (`orch-console`, `cc-fleet-health`), T0, an **absolute expiry of at most four hours**
   and the owner. It never asserts the bodies are healthy. The SRE's terms (bus 87965)
   are the contract: a failed read of the declaration pages normally, the declaration
   mutes only heartbeat-derived decisions, and **no surrogate heartbeat is ever written**
   — a fabricated liveness column is worse than a declared gap because it makes a dead
   body look alive.
2. **Restart the two bodies on their real boot scripts, inside that window.** After
   `verify` has shown the old value refused and the new one connecting: **fleet-health
   first, console last**, so the operator is never dropped mid-run. The mechanism is the
   one that already owns recycle — kill the tmux session (`fleet-health`, then `nazim`)
   and let the launchd KeepAlive waiters (`dev.wingmen.fleet-health-session`,
   `dev.wingmen.nazim-session`) relaunch `boot_fleet_health.sh` / `boot_nazim.sh` on the
   patched files. Nothing is hand-rolled and nothing is `nohup`-shaped.
   **A reset is not a restart** (SRE, bus 87991): `reset_nazim.sh` / `reset_fleet_health.sh`
   are in-place clears whose own comments say they keep the heartbeat subshell alive, so
   after a reset the loop is still frozen on the dead credential and the declaration does
   **not** close early. Only a clean session exit (the trap kills the loop) followed by a
   fresh boot script re-forks it. And **neither body can do this to itself** — each needs
   an external actor, which is cc-substrate running this step from its own process, not
   from inside a singleton.
3. **The declaration closes by expiry** because the restarts land well inside it. If the
   restarts do not land, it simply runs its four hours and nothing is wrong — that is a
   bounded, declared gap, not a failure. The SRE reads its cost gauge directly during the
   gap rather than pretending a pager covers it (bus 87980) — the blind spot is
   `context_health_watchdog`'s live set, and it is named rather than papered over.
   **Expect, and do not page on:** the SRE's own `fleet_health_lease` renewals fail on the
   same stale credential, its 900s TTL lapses, and the hub reclaims the watchdog and
   fleet-status pens (87991). That is the dead-man's switch working as designed on a body
   that cannot prove it is alive — not the SRE dying.

`cai` is out of scope: it has no session on this host and has read `offline` since
13 October (bus 87965/87978). Its absence is an ownership question for another body, not
a rotation problem.

**The Supavisor cache.** The pooler keeps a credential cache that can hold the old value
for roughly 15 seconds while connections are active. An idle pool may refresh only on a
real reconnect. The verify step retries for this reason. Do not "fix" a failing verify
with more retries than the configured window — repeated old-password attempts can trip
the auth circuit breaker, which is a different failure wearing the same mask.

---

## 3. Execute

**The window is Sunday 2026-11-01 at 04:00Z** (console, bus 87978), not the 22:00Z slot
first proposed. Two independent reasons, either sufficient: the operator ruled that the
schedule settles before the rotation and had just reopened W5 questions, so it is not
settled; and the 21:45Z checklist could not clear because CI was queued behind two
long-running jobs. Only after the console's explicit GO, and only inside the agreed
window:

```bash
cd ~/wingmen/projects/orchestrator.wt-exec
PY=~/wingmen/orchestrator/.venv/bin/python3
$PY scripts/substrate_rotation_executor.py execute \
  --env-file ~/wingmen/orchestrator/.env \
  --old-sha8 <sha8 of the CURRENT password> --confirm rotate-substrate
```

`--env-file` narrows the run to the live file. That is the recommended scope for the
first rotation: the other two places the credential sits are **stale worktree copies**
(one a month old, one inside a client repo), and writing the NEW password into an
abandoned worktree widens the exposure instead of reducing it. The value left there is
dead after the patch, and a dead credential in a stale file is litter, not a leak —
clean those up as their own decision afterwards (bus 87913).

The confirm phrase is deliberately long and not a `-y` flag. A typo must be a refusal.

A JSONL journal is written with mode `0600`, defaulting to
`reports/rotation/<UTC stamp>/journal.jsonl`. It contains step names, verdicts, hashes —
never the value. `--watch-seconds` defaults to 600; `--retries` and `--retry-delay`
control the pooler-cache window.

### Success looks like

```
[…] verify: OK probe=new attempt=1
[…] verify: OK probe=old verdict=rejected
[…] restart: OK job=<label>          (one line per daemon)
execute: rotation COMPLETE — journal at …/journal.jsonl
```

`verdict=rejected` on the old half and the final COMPLETE line. "New password connects"
on its own is half a rotation, which is why the old half is not optional: with no backup
holding the old value the executor refuses outright rather than recording a pass it did
not earn.

`COMPLETE` from the executor is **not** the end of the operation. The singleton restart
(step 7) and the declaration closing are still owed — see the decided design above — and
until both bodies are beating again, the rotation is "in progress" in the console's
words.

---

## 4. Failure branches

**Before the patch (generate or rewrite fails).** The executor restores every file from
the bytes it captured before the first write and prints that the database still has the
old password. Nothing was rotated. Diagnose, fix, re-plan. No rollback of the database is
needed because nothing reached it.

**The patch itself fails (4xx/5xx).** Same as above: files restored, nothing rotated.
A 401 means the PAT is wrong or expired; a 403 means it lacks the project.

**After the patch (propagate, restart, verify, watch fails).** The executor says plainly:
*THE DATABASE NOW HAS THE NEW PASSWORD*. It does not claim a rollback it cannot perform —
there is no path back to the old value from here, and pretending otherwise is worse than
saying it.

What to do: the value is in the vault and in the rewritten files. The remaining step is
to get every holder to read it. Re-run the restart pass, and for anything still failing,
read the file at use rather than restarting again.

**Verify says the old password still works.** The reset did not land. Do not proceed and
do not retry blindly — check the API response in the journal first.

---

## 5. What "done" means

- New value connects from every holder.
- Old value is **rejected** — not "untested".
- The journal shows a restart entry for every daemon.
- The vault holds the new value with the previous one archived in `vault_secrets_history`.
- **Both singletons were relaunched, not reset**, and their `last_heartbeat` rows are
  advancing again on the new credential — checked in the rows, not inferred from "the
  restart command returned 0". A reset leaves the frozen loop alive (87991).
- The declaration has expired or been superseded by a restart, and nothing in a later
  reader's view suggests the gap is still open.
- A 10-minute watch shows no new authentication failures.

Anything less is "in progress", and the console should hear that word rather than "done".

---

## 6. Known residuals, stated rather than hidden

- **`tmux set-environment -g`** puts the value in a tmux argv for the milliseconds the
  command runs. It is visible to anyone who can see that process's argv in that instant.
  Accepted: the alternative is a holder that never learns the new value.
- **The watch step probes connectivity, not the server's auth-failure log.** It cannot
  read Supabase's log by `peer_ip`; the operator's requirement to watch that log is met
  by the console watching the dashboard, not by this executor. Do not describe the watch
  step as if it did more than it does.
- **The hub's own env file is not rewritten by this run.** Neither is it pushed: the hub
  pulls. It re-runs `fetch-secrets` in the same window and restarts its groups — the
  auto-respawning ones and the manual ones, per the hub's own list (bus 87827). Until it
  does, the hub holds a dead value, which is why it must act in the same window.

---

## 7. The hub host half (settled 2026-10-31, bus 87827/87834)

The hub did a live `/proc` scan (hash only) rather than reasoning from a list, and it
corrected the plan in four ways. All four are accepted; three of them mean **less** work,
not more, which is why they are recorded rather than absorbed silently.

- **Skip the tmux global on the hub host.** Its tmux session-environment table and the
  server's inherited env are already stale orphans from an earlier secrets split, and no
  live process reads `DATABASE_URL` by inheriting it — they source the tmpfs file
  themselves. The executor's propagate step records this as a TODO for the hub, so the
  value being set on the Mini's tmux server does not imply the hub is done.
- **Timer-fired oneshots self-heal.** `wingmen-orch-lease-renew`,
  `wingmen-hub-self-recovery` and `wingmen-media-reaper` re-source the env on every tick
  (4, 2 and 10 minutes), and `coordinator-pane-publisher` / `hub-ctx-publish` never
  inherit the DSN at all. No restart needed for any of them.
- **The paperclip canary serve process holds the credential** and was not on the original
  list. It is now.
- **Restart groups differ by how each process comes back.** Signal-safe ones (systemd
  restarts and relaunches them): `wingmen-ingest`, `wingmen-tg-out`,
  `wingmen-agent-wake-subscriber`, `wingmen-irsyad-coord`,
  `wingmen-irsyad-tabung-jumaat`, `wingmen-body-activity-oracle-vps`,
  `wingmen-wake-backstop-sweep`, paperclip canary, and the hub itself last. No
  auto-respawn (killing the tmux session is not enough; the original boot command must be
  re-issued immediately): bayanqa, encoder, scholar's lane, the three irsyad pool workers,
  and cosem-convergence. The hub has the exact relaunch command staged for each.

**The sudo gap is a gate, not a footnote.** The hub has passwordless sudo for every unit
on that list except `wingmen-body-activity-oracle-vps` and
`wingmen-wake-backstop-sweep`. Both held the current password at scan time and both need
a password the hub does not have. Until that is closed — by adding the two labels to the
hub's NOPASSWD line, or by restarting those two from another account — the hub cannot
complete its half, and a rotation that leaves a live holder on the dead value is worse
than a rotation deferred by a day. The console owns closing it (bus 87834).
