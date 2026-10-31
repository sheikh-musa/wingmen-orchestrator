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

**Open, and it can sink the hub's restart:** the bundle is assembled from three sources —
the Mini's orchestrator env file, and two private env files under `~/.wingmen/private`
(one for the write DSN, one for bayanqa). If either of those also embeds the substrate
credential, the pull carries a dead value for it and the hub's restarts fail *after* a
successful reset. Reading those paths is guard-blocked from this lane, so their owner
must confirm whether they carry the substrate DSN. Asked of the console (87892).

**Also asked, not yet answered:** whether all three Mini env files are in scope, or only
the live one. Preflight reads all three and they all carry the current value, so the
default covers them — but "the default covers it" is not the same as "it was decided".

---

## 1. Plan (harmless, do this first, always)

```bash
cd ~/wingmen/projects/orchestrator.wt-exec
.venv/bin/python3 scripts/substrate_rotation_executor.py plan --old-sha8 <sha8 of the CURRENT password>
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
   seconds. **This step does NOT cover the three tmux singletons** (console, cai,
   fleet-health) — see the heartbeat box below; they need their own restart and only the
   console can order it.
6. **verify** — the new value must connect, and the old value must **fail**. Both halves
   matter; a rotation where the old password still works did not happen.
7. **watch** — re-probe for the configured window.

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
and `last_heartbeat` freezes on the three singleton rows — a live body read as dead,
exactly when the console is watching.

`scripts/lib/dsn_at_use.py` makes every **future** fork immune by reading the file at the
moment of use. It does **not** retrofit the running loops. Re-forking them means
restarting the three singleton boot scripts, which costs the console its live session and
is therefore the console's decision, not the operator of this tool. Do not start a
detached replacement loop to dodge it — that is `nohup`-shaped and forbidden.

**The Supavisor cache.** The pooler keeps a credential cache that can hold the old value
for roughly 15 seconds while connections are active. An idle pool may refresh only on a
real reconnect. The verify step retries for this reason. Do not "fix" a failing verify
with more retries than the configured window — repeated old-password attempts can trip
the auth circuit breaker, which is a different failure wearing the same mask.

---

## 3. Execute

Only after the console's GO, and only inside the agreed window:

```bash
cd ~/wingmen/projects/orchestrator.wt-exec
.venv/bin/python3 scripts/substrate_rotation_executor.py execute \
  --old-sha8 <sha8 of the CURRENT password> --confirm rotate-substrate
```

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
