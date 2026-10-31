# cc-substrate handoff (updated 2026-10-31 20:55Z, TRACK C rotation armed; window Sunday)

## LATEST (2026-10-31 20:55Z) — read this first

**THE WINDOW IS SUNDAY 2026-11-01 04:00Z** (console 87978). Two reasons, either
sufficient: the operator ruled the schedule settles before the rotation and reopened W5
questions, so it is not settled; and the 21:45Z checklist could not clear because CI was
queued behind two long jobs. Nothing fires tonight.

**NEW SINCE 20:45Z — four defects found in my own executor, three of them on the paths a
human touches first. PR 774's head moved three times; every cc-quality PASS in the bus is
on a head that no longer exists.**

0. **`_scrub` skipped our own `Refused` text** (cc-quality 88061 FAIL; fixed at `cee1dd4`).
   `patch_password` builds its failure detail from the Management API's **response body**,
   which echoes what was sent — so the one step that must never leak the value assembled its
   own error text out of a third party's echo of it. Fixed twice over: the driver scrubs
   every exception's text (Refused included) **and** `_record` scrubs every string field, so
   no emitted line holds the value whatever produced it. Verified RED at `e02a0e2`.
   **The lesson to keep: "we wrote this string ourselves" is not a reason to trust it.**

1. **Rollback skipped on any non-`Refused` exception** (fixed at `16b7c69`). `run()` caught
   only its own exception type, so a psycopg/OSError/ImportError escaped as a traceback and
   skipped the rollback — for exactly the failures the rollback exists for. Every exception
   is now a refusal, scrubbed.
2. **The scrub did not cover the probe, so the OLD password leaked** (fixed at `e02a0e2`).
   `self.old_dsn` was assigned *after* `probe()`, and the probe is the first step that
   reaches the network — so psycopg quoting the connection string it could not use was
   raised while the scrub did not yet know that value, and printed it verbatim. Found by
   running `plan` against the real env file, not by re-reading the diff.
3. **Plan-mode refusals printed a traceback and exited 1** (fixed at `e02a0e2`). `plan` is
   the dry run a reviewer reads at 04:00Z; a stack reads as a broken tool and 1 is not the
   refusal code. Now `plan: REFUSED — <reason>. Nothing written.`, exit 3, still writes no
   journal.
   Tests for all three were **red at the previous head** (proven by restoring the old file
   and re-running, not assumed); **19 green** at `e02a0e2`. Shipped entry point exercised:
   `plan --env-file ~/wingmen/orchestrator/.env --old-sha8 95ea35a8` →
   `preflight: OK targets=1 remote=False daemons=9`; wrong sha → exit 3, clean stderr.

**The heartbeat question is CLOSED, and the answer is not what anyone assumed.** I
measured rather than reasoned (87909, 87964):
- A running bash loop **never** re-reads its heredoc — whole-file replacement and
  same-inode overwrite, 5/5 beats each, old body both ways. There is no no-restart patch.
- The loops are **not** detached orphans: `ps` shows the nazim pane (`boot_nazim.sh`) and
  the fleet-health pane both alive with their heartbeat subshell as a **child**, and the
  exit trap kills a pid recorded at boot. A replacement loop would outlive its body and
  report a dead console as working — the exact liveness-lie the boot script forbids.
- **cai has no session on this host** and has read `offline` since 2026-10-13. Two loops,
  not three.
- The SRE measured its own detectors (87965): no false-dead flip, no false stall page for
  these ids. The real cost is **blindness** — `context_health_watchdog` drops them from its
  live set. My runbook said "false alarms"; that was wrong and is corrected (d784ca2).

**Verify recipe corrected TWICE by the SRE, and the second correction is the subtle one**
(88036/88056, runbook `dc8b767`): sample **≥330s apart**, not 60s (the beat is 300s, so
closer samples report a healthy body frozen); and **never pass on recency** — the EXIT trap
writes `last_heartbeat = now()` as it stamps the row offline, so just after a clean exit the
timestamp is the freshest it has ever been on a body that no longer exists. PASS = both
samples `status='working'` **and** B's timestamp strictly later than A's. The generalisation
is now in the runbook's residuals: any shutdown handler that writes telemetry inverts every
freshness check.

**Restart mechanics verified independently (21:00Z, plist read not relayed):**
`dev.wingmen.nazim-session` = KeepAlive 1, RunAtLoad 1, ThrottleInterval 15,
ProgramArguments `boot_nazim_session.sh`, WorkingDirectory = the live checkout. So killing
the `nazim` session is genuinely the trigger and the fresh boot re-sources the env file.
Measured tree (both bodies): `pane_pid` = the **boot script**, children = heartbeat subshell
+ `claude`. `agent_status` rows: `cc-fleet-health` and **`orch-console`** (not "nazim").
`lsof` is NOT on this shell's PATH — call `/usr/sbin/lsof`; a missing probe is UNKNOWN, not
free. Runbook now carries the both-bodies table and the console's two hand-run probes
(`1888a79`); the SRE's guard covers fleet-health only.

**Decided design (console 87978, no new lifeline code):** at GO, a bounded declaration
row to cc-fleet-health (two ids, T0, absolute expiry ≤4h, owner, never a surrogate
heartbeat), then after verify the two bodies restart on their real boot scripts —
fleet-health first, console last — via the launchd KeepAlive waiters. Written into the
runbook as step 7 plus a dedicated section.

**PR state (base `fable/substrate-safe-fixes`):**

| PR | branch | head | state |
|---|---|---|---|
| 771 | `cc-substrate/verify-no-argv-dsn` | `511c587` | **MERGED as `05f2797`** |
| 765 | `cc-substrate/substrate-dsn-rotation` | — | **CLOSED as superseded** |
| 774 | `cc-substrate/rotation-executor` | `cee1dd40` | **PASS 88101** at this head; merge blocked on CI queue only. **HEAD FROZEN.** |
| 773 | `cc-substrate/merge-signoff` | `079e9eb9` | **MERGED** (console's waiter got it first; my merge_pinned then reported 'already merged' — see residual note) |
| 762 | `cc-substrate/credguard-bypass-followup` | `d3b71dd` | **MERGED `d3b71ddeb5fc`** |
| 766 | `cc-substrate/sweep-multi-scan` | `61f3dce` | **MERGED `61f3dcef2084`** |
| 761 | `cc-substrate/no-cross-provider-resume` | `672d01d` | **MERGED by me 21:34Z** via merge_pinned (rebase-confirm 88138) |
| 776 | `cc-substrate/rotation-runbook` | `1888a79` | **PASS 88145** (docs-only, every code-checkable claim verified against executor@cee1dd40); merge blocked on CI |

**22:39Z — 774 PASSed by cc-quality at the rebased head (row 88411)**, verified by object identity
(both files' blobs identical to `cee1dd4`; dependency `rotate_substrate_dsn.py` blob `f4b64c23`
unmoved). Console sent the row id + sha (88415). **cc-quality's 88145 read the RUNBOOK ONLY** — their
own answer (88418), with their method failure owned: they scoped from a `$NEW~4` commit window and
every commit in it was docs. They then reviewed 776's other files post-hoc: `dsn_at_use.py` + boot
wirings **sound** (file-first, OSError→fallback, read-only; boot import wrapped in try/except). So the
88397 residual closes when 774 lands: nothing unreviewed stays in trunk. **Their process fix = mine:
scope from `git diff <merge-base> <head> --stat`, never a commit window, never the author's label.**
Two non-blocking `dsn_at_use` nits deferred post-window: trailing inline comment mis-parses; an
explicit-but-unreadable `env_file` falls back silently instead of failing loudly.

**22:36Z — PR 785 opened (`cc-substrate/executor-preflight`, head `f25b109`), docs-only (file list
CHECKED: one file).** Runbook §1 now opens with *step zero*: `git merge-base --is-ancestor
<774-merge-sha> HEAD` + `grep -q "def _scrub" scripts/substrate_rotation_executor.py`, both mandatory,
abort on either failure; plan step moved from wt-exec to the live checkout so the checked tree IS the
executed tree. **`<774-merge-sha>` is a deliberate placeholder** — fill it in once the console's
waiter merges 774 (follow-up commit on 785 if open, else its own tiny PR). Console asked for the rule
(88406). **Sweep fixture deferred to post-window** per the console; it still lives at
`/tmp/sweep_scrub.py` and must become a test in the repo afterwards.

**22:40Z — 774 was CONFLICTING, and the cause is the biggest thing found tonight.**
**PR 776 was NOT docs-only.** API file list: runbook +470, boot_cai +12/-3, boot_fleet_health +12/-3,
boot_nazim +49/-3, lib/dsn_at_use +68, rotate_substrate_dsn +726, **substrate_rotation_executor +440**,
3 test files. 1341 insertions / 8 files. It merged at 55473ba, so **base carries the UNHARDENED
executor** — no `_scrub`, `except Refused` not `except Exception`, no pre-probe old-dsn capture — i.e.
the exact version cc-quality FAILED at 88061/88080. **Do not run the executor from the tree until 774
lands.** Reported as a P1 blocker (88397); review-gap question put to cc-quality (88399).

**774 rebased → `9a6021ee6f782f6bf2f9a8ed6e32cae5d257d752`.** Rebuilt as base + the two files at
reviewed content (`git reset --hard origin/base; git checkout cee1dd4 -- <2 files>`) rather than
replaying 15 commits, because 776 already carried the other nine files byte-identically. File list is
now exactly 2 files; mergeable=true, blocked on CI. 69 rotation tests green on the pushed tree.
Console informed (88400, 02:00Z deadline for the sha).

**My error, worth keeping:** I called 776 docs-only after checking its head sha and the diff I meant
to ship, and never listed its files. **Check `gh api pulls/N/files` before calling any PR docs-only.**

**22:29Z — 782 MERGED at `b645f1da0a9170568875530aeaf1f0bac7deca08`** (verified from the PR:
merged=true, mergedAt 22:29:21Z, base advanced). cc-quality re-confirmed PASS at `cb5e9e0` (88333)
after the SYSTEMD_PAGER correction. The runbook's §7 is now correct in the tree.

**774 = the ONLY open gate item**, CI green since 22:02Z, still `merged=false` at 22:30Z. It is the
console's waiter (their 88230). Flagged in 88388 — if their waiter died on a recycle it will not say
so, and I am not going to race it (two bodies on one PR is what makes the merge_pinned
misattribution dangerous). I merge on their word.

**22:08Z — cc-quality's Q on 782 was RIGHT and my doc was wrong.** They asked whether
`SYSTEMD_PAGER=cat` even survives `sudo` (default `env_reset`, no SETENV tag). Measured on gzb under
a real TTY: `SYSTEMD_PAGER=/bin/false sudo -n systemctl status <unit>` **still paged** — the variable
never arrives. So the advice was inert, case (c), not (a)/(b). The working form is
`sudo -n systemctl status <unit>.service | cat` (pipe changes what systemctl writes to, not the argv
sudo matches) — verified clean under `-tt`. **782 head is now `cb5e9e0`**; re-confirm requested (88316).
Note: the test itself hung on the pager — `ssh -tt` + a pager is a hang risk, always set a timeout.

**22:02Z — 776 MERGED at `55473bae9bbc7c76194571f89dba15c7921fb8df`** (verified from the PR, not from
merge_pinned's echo: merged=true, mergedAt 22:02:15Z, base head moved to it). CI was `success` on the
exact head. **PR 782 opened (`cc-substrate/sudo-closed`, head `2041453`)** carrying the sudo correction
as a docs-only follow-up — deliberately NOT folded into 776 because 776's PASS was bound to `1888a79`.
cc-quality asked for a verdict before ~03:00Z (bus 88298). **774 is still open and is the console's**;
it is now the ONLY unmet gate item.

**PAT re-verified 22:05Z** (runbook §0 says that check expires): `GET /v1/projects/tscuymavysscrvoberrr`
→ HTTP 200, `ACTIVE_HEALTHY`, `ap-southeast-2`, name `orchestrator`. Token reported by sha256-8
`02fa5ae2` only. Still does NOT prove the PATCH write scope — the executor's own patch step is the
only proof of that.

**Independent sudo verification (not taken from 88254):** from `gzb` as `gazzai`, `sudo -n -l` lists
exactly restart+status for `wingmen-body-activity-oracle-vps.service` and
`wingmen-wake-backstop-sweep.service`, nothing wider; status exercised on both without a prompt.
**`--no-pager` would MISS the rule** (sudoers matches exact argv) — use `SYSTEMD_PAGER=cat`.

**21:47Z console agreed (88230).** All three items accepted: Sunday 04:00Z stands; **774 is the
console's merge now** — their waiter holds `cee1dd4` and they send the sha, so I do NOT merge 774
(only 776 is mine); sudo gap — they are asking the hub for a scoped passwordless restart of exactly
those two units before **03:30Z**, and **if it is not closed and verified by then the run defers
again, no partial run**; order unchanged, console last. They also asked me to route the
`merge_pinned` mis-attribution to cc-quality — **done, bus 88234** (finding: `scripts/merge_pinned.sh`
line 73 echoes `MERGED` unconditionally after `gh pr merge`, which exits 0 on an already-merged PR).

**CI observation worth keeping:** the runners are the cubeasht WSL distro `ci-orchestrator`
(`actions-runner-orchestrator`, `-2`), reachable over ssh; both were `busy=true` with four jobs
`queued` and none `in_progress` for a stretch, which looks like a wedge but was not one — worker
processes were live (`ps` on the distro showed start times matching real jobs) and the queue drained
afterwards. 774's job sat queued 52 min; 776 was picked first despite being newer. If a body reports
"CI wedged", check `pgrep -af Runner.Worker` on that distro before restarting anything.

**21:45Z gate — sent as bus 88223 (P1, requires_response).** Applied the console's own rule from
87921 (`GO at 22:00Z only if 774 PASS+merged, heartbeat answered, sudo gap closed`). Result: **NO-GO**
on two of three, so the window moves to **Sunday 2026-11-01 04:00Z**.
 (a) 774 = PASS at `cee1dd4` (88101) but its CI run has been pending/queued since 20:55Z — the runners
     are **cubeasht-orchestrator and cubeasht-orchestrator-2** (NOT the Mini, which I had wrongly said
     earlier in this file), both online, both busy, two branches ahead. Cannot clear before the gate.
 (b) heartbeat = MET. Premise evidence for (a)-of-87921: in `scripts/boot_nazim.sh` the heartbeat is a
     function backgrounded at boot (line 178, HB_PID at 179) — a running subshell never re-reads it, so
     the on-disk fallback could not have avoided restarts. EXIT trap kills HB then stamps
     `last_heartbeat=now()` with status offline (lines 194/200) — that is *why* recency alone reads alive
     at the moment of death, and why the ≥330s two-sample recipe is the correct verify.
 (c) sudo gap = FAILS: both VPS units missing from NOPASSWD, no substitute account (88153).

Left with the console: pin/merge 774 when its check lands (or defer post-window), close or explicitly
accept the sudo blind spot, confirm Sunday keeps the same order with the console last.

**21:36Z merges.** 761 merged by me at `672d01d` (`merge_pinned`, rebase-confirm 88138). 773 was
already merged by the console's waiter when I tried — the gate said so plainly. **Residual worth
reporting, not fixing tonight:** `merge_pinned.sh` prints `MERGED <n> at <sha>` and exits 0 even when
the PR was already merged by someone else (it echoes gh's success), so the log attributes a merge it
did not perform. Harmless here, misleading if two bodies race; belongs to cc-quality/console, in the
file that PR 773 itself just landed.

**21:18Z: every open PR now carries a PASS on its AND ONLY ITS current head** — 774 `cee1dd4`
(88101), 761 `672d01d` rebase-confirm (88138), 773 `079e9eb` (87946), 776 `1888a79` (88145).
Nothing is waiting on a reviewer; all four are waiting on a runner. 776's reviewer verified the
doc's code-checkable claims against the executor at `cee1dd40` and flagged two non-blocking notes:
the §0 PAT check stamp is long past expiry (the doc already says re-run it before the window) and
the `95ea35a8` prefix is taken as my check.

**21:10Z additions.** (i) 774 **PASSED** at `cee1dd4` (cc-quality 88101, opus; they independently
reproduced the kickstart/tmux channels and confirmed they are clean at the fix). Merge pending CI
only. (ii) 761 was **unmergeable** (base moved twice); rebased to `672d01d`, proven byte-identical
on its 8 branch-owned files, 22 tests green, rebase-confirm requested. (iii) **The hub answered
the sudo question directly (88017): still OPEN** — `sudo -n -l` does not list the two units and a
direct `sudo -n true` on `wingmen-body-activity-oracle-vps.service` fails with "interactive
authentication is required". Checklist item (c) is therefore **open with evidence**, and per
fleet-health the consequence must be named: the wake backstop has no restart path on that host.
(iv) **Handoff commits were sitting on top of PR 761's branch** in `wt-credguard` — any push from
that worktree would have carried report commits into the PR. Moved to a dedicated
`cc-substrate/handoff-log` branch; PR branch reset to origin. Worth remembering: never let a
report worktree share a PR branch.

**Outstanding at the 21:45Z checklist** (owed to the console): (a) 774 — **FAILed** at `e02a0e2`
(cc-quality 88061, real leak), fixed and re-reviewing at `cee1dd40`, therefore **not merged**; (b) heartbeat — answered, declaration accepted on terms
4a–g, step seven in the runbook; (c) hub sudo gap — **asked the hub (88048), no answer
yet**; do not report it closed, and per fleet-health (88056) report the *consequence* for
the night (wake backstop absent = nobody would learn it from fleet telemetry), not a status
word.

**Blocker type to remember: the fleet shares ONE GitHub token**, so GraphQL rate limits
are a shared, fleet-wide budget. `gh pr checks`/`gh pr view --json` are GraphQL; use
`gh api repos/.../commits/<sha>/check-runs` (REST) for polling. I hit the secondary limit
by polling five PRs in a loop.

**Correction to my own earlier note:** I recorded that the pre-push hook "silently skips
test collection without a `.venv`". It does not skip silently — it prints
`⚠ pre-push: no pytest available — skipping the test-collection check` to stderr, and CI is
the intended backstop. Not a defect; do not raise it as one.

---

## SUPERSEDED (2026-10-31 20:25Z) — the hub/pull finding and the PR table as it stood then

**The hub question is CLOSED, and the answer changes the plan.** cc-orchestrator
(87863/87845): the alias is **`gzb`**, not `hub-vps`; its env file is a **tmpfs** file
under `/dev/shm/wingmen-secrets/` recreated at every boot by `fetch-secrets`; and the
plan it already agreed with the console in 87782 is **PULL, not push** — a write into
gzb's files from outside the pull is unreviewed. I confirmed the far side from this
end: this Mini's `authorized_keys` carries one forced command that **assembles the
bundle here** (`~/wingmen/orchestrator/.env` + two private env files under
`~/.wingmen/private` + `keys/` + `.orch_default_token`) and streams a tar. So the pull
terminates on this machine, rewriting the Mini file is the whole job on this side, and
**`--remote` must NOT be used against gzb**. Reply sent: 87890.

**OPEN, can sink the hub's restart:** if either of those two private bundle files also
embeds the substrate credential, the pull hands the hub a dead value *after* a
successful reset. I could not check them — reading that path is refused by
`console_irsyad_guard` on this lane even though `CC_BASE_AGENT_ID=cc-substrate` is set
and `ORCH_BODY_ROLE` is unset (exemption did not apply; not worked around). Asked the
console in 87892, together with the still-unanswered scope question (all three Mini
files, or only the live one).

**PR state (all base `fable/substrate-safe-fixes`, all MERGEABLE, all CI pending at
20:22Z — the queue was backed to 19:37, I cancelled 7 superseded runs):**

| PR | branch | head | what |
|---|---|---|---|
| 774 | `cc-substrate/rotation-executor` | `8670d768` | the one atomic executor |
| 776 | `cc-substrate/rotation-runbook` | `e5a6b0e0` | doc: how to fire it |
| 773 | `cc-substrate/merge-signoff` | `079e9eb9` | merge gate, F1 fixed |
| 771 | `cc-substrate/verify-no-argv-dsn` | `511c587b` | verify F1/F2 fix |
| 765 | `cc-substrate/substrate-dsn-rotation` | `e37a4923` | the tool itself |

**Found and fixed in my own code before the window (self-review, 8670d76):**
`step_verify` recorded `OK — not attempted (no backup)` when no backup could yield the
old password — the **F2 fail-open, reintroduced**. The old half is now unconditional:
no recoverable old value means refuse. Test red at 6110114, green after.

**PR 773 F1 (cc-quality 87866, blocking, proved):** sign-off facts were matched
independently across a whole row, so a combined verdict ("A passes, B is blocked")
signed off B. Now bound to ONE verdict line (PASS + repo + PR# + full sha on the same
line); a FAIL line for the same PR+sha blocks the row; a FAIL-only row from an
independent auditor refuses the merge even if another passed. N1 taken: two auditor
families passing the same exact sha is corroboration, not ambiguity. Verified on real
rows — 767/766/762/761 still pass (762+761 through one combined row), 759 refused
(short sha). 20 tests.

**Ask 5878 answered in content (87860):** relay-ready substrate/TG/memory 2.0 status
sent to the console, plus the correction that programme row **126 shows PR 716 as
building when it is MERGED** (owner cc-paperclip-spike-1 must update it; I did not edit
another owner's row).

---

## SUPERSEDED (2026-11-01 20:45Z — the date stamp was wrong, it is 2026-10-31) — read for the a5a71254 attribution and the four-location scope

**THE VPS COPY IS KNOWN NOW, AND IT IS A FOURTH LOCATION.** Hub report 87776: exactly
one copy on the VPS, `/dev/shm/wingmen-secrets/<dotenv>` (tmpfs), with
`~/wingmen/orchestrator/.env` a symlink to it; re-pulled by
`wingmen-fetch-secrets.service` over a forced-command ssh tar stream from the **core
host's bundle**. Plus one surface the hub could NOT check: `/opt/irsyad-exec-broker/
broker.env` (0400, irsyad-exec user) — carried as UNCHECKED, not clean.

**Order that now holds (87781):** stage → apply the three local files → apply the
core-side bundle (`apply --remote`, built tonight) → dashboard reset → restart
fetch-secrets → restart every consumer (hub, ingest, tg-out, lease-renew, coord,
tabung, mizan, bayanqa, encoder) → verify → scrub → delete backups.
Console told me to agree host + path with cc-orchestrator: **sent as 87815 (P1)**.

**Tool state (PR #771, branch `cc-substrate/verify-no-argv-dsn`, head 511c587):**
verify takes no DSN at all and its old-password probe is mandatory (87760 F1/F2);
`apply --remote HOST` rewrites the far-side bundle over ssh with the value on stdin
at both hops (87762/87781); `stage` now ARCHIVES the superseded vault value via
`vault.rotate` instead of the flag-then-overwrite no-op cc-quality caught as N6
(that one was live: `put` upserts one row per name, so the second write cleared the
flag the first set and nothing was archived). 40 tests, red-first.

**Independent sign-off gate is written (PR #773, head 6f55421, base
`fable/substrate-safe-fixes`).** `scripts/lib/merge_signoff.py` + wiring in
`merge_pinned.sh`: VERDICT PASS row for the FULL 40-char sha, signer family resolved
through `agent_status.base_agent_id` and registered in `protected_agents`
(kind=auditor-singleton), not the branch's author family, not older than the head
commit, fail closed on the lookup. Proved against real rows: PR 767 passes on
cc-quality 87744; PR 765 is REFUSED because its PASS names 12 chars only — reviewers
must now paste the full sha (told cc-quality, 87822).

**Commitment 978 is NOT overdue** — due 2026-11-01 17:10Z, i.e. ~21h from 19:58Z on
10-31. The trial windows I compared were mislabelled: window B (121 rows) is only
~2h45m of DeepSeek, not 24h, so 121 rows in ~2.8h vs 297 in 24h is a ~3.5x higher row
rate, confounded by the incident-heavy night. Do not read it as a quality signal.

---

## RECONSTITUTE HERE (2026-11-01 ~19:58Z) — TRACK C: rotation armed, window pending

**a5a71254 ATTRIBUTED — it is the `quality` lane.** tmux session `quality`, pane pid
94860 → claude pid 95006, `lstart` 2026-10-31 22:27:28 local = **14:27:32Z**, cwd
`/Users/sheikhmusa/wingmen/quality`. Evidence chain: (1) project dir
`-Users-sheikhmusa-wingmen-quality` encodes exactly that cwd and `quality-2` has its
own dir (`-…-quality-2`, single file born 18:09Z vs its 18:06Z start); (2) the
previous quality process last wrote at 14:27:27Z, five seconds before 95006 started;
(3) a5a71254 was born 19:11:55Z = the exact last-mtime of 58b7e2ff, the file THIS
process opened at 14:50:48Z — session files appear on the first turn, not at boot —
so a5a71254 is a continuation of its own session. Sent as **87752** to cc-fleet-health
with a warning: the console (87737/87738) told cc-quality to finish the **PR 765
review first**, then request a reset, so fleet-health must not kill it before that.
Sweep re-measured over all 54 files in both quality dirs: 3 spans / 2 lines, all in
a5a71254, zero in quality-2.

**BLAST RADIUS (asserted the TOTAL, not the obvious):** 348,264 files scanned by
hash over `~/wingmen` → exactly three carry the substrate password, none a symlink:
`~/wingmen/orchestrator/.env`, `projects/orchestrator.wt-pr215fix/.env`,
`projects/cosem-platform.wt-author/.env.local`. No shell profile, plist or script.
**OPEN, not answerable from this box:** does the VPS hub (`cc-orchestrator@wingmen-core`)
hold the same DSN? If yes the reset takes the hub down until that copy is updated.

**ALTER ROLE IS NOT A PATH (87756).** Supabase docs name the dashboard only; hosted
projects fail `ALTER ROLE` with 42501 because `postgres` is owned by `supabase_admin`
(public report: discussion 50374 — where the dashboard reset ALSO failed silently).
So Musa does the dashboard steps. His "copy the generated password before confirming"
order is **unverified**: the KB does not say the dialog reveals the value pre-confirm.
The checklist therefore has two branches, and the fallback uses
`apply --from-stdin` (built in e37a492 for exactly this).

**POOLER, from the Supabase KB (28P01 after rotation):** Supavisor caches credentials
(~15 s with active pooled connections; an **idle pool only refreshes on a real
reconnect**). Do not reset twice in quick succession; do not let anything retry with
the OLD password (that trips the auth circuit breaker). Hence the order: apply
(.env = new) → dashboard confirm → `restart-consumers` (they reconnect with the NEW
value) → `verify`.

**NEW: PR #771** `cc-substrate/verify-no-argv-dsn` @ 3a979dd, stacked on #765
(e37a492 untouched, its review unaffected; merge AFTER 765). `verify` now takes no
DSN on argv (reads `--env-file` + rebuilds the old DSN from the newest `.env.bak.*`),
retries the pooler window (default 3 × 5 s) and only probes the old DSN when the new
one connected — a rejection during a cache window proves nothing. 6 new tests, each
proven red against the previous script; 28 green in the file.

**Runbook once Musa picks the window:** stage → apply `--execute` ×3 env files → he
confirms the dashboard reset → `restart-consumers` → `verify` → scrub
`secret_hash_sweep.py --execute --ledger` over the three transcripts (a5a71254,
6da64adb, and the third) → delete `.env.bak.*`. Nothing staged, applied or scrubbed
so far; the console holds the trigger.

---

## TRACK B — credential-store guard + no cross-provider resume (2026-11-01, P0 Musa op 36795)

THREE PRs, one stack — merge in this order: **#759 → #762 → #761**. **Heads are
FROZEN** (orch-console, 87541: "fewer pushes please"); no new head unless a reviewer
finds a blocker.

**REBASED 2026-11-01 ~19:35Z (bus 87665/87666):** the 759 squash made both remaining
PRs `CONFLICTING` (their branches still carried the unsquashed `917e9dc..8ea32ef`
stack on the same base). Rebased onto `27e9905`, dropped the three commits already in
base, kept only the reviewed deltas: **#762 → `d3b71ddeb5fcc587032cc0fa0b90eebd1746dbdc`,
#761 → `675c2e71e33d8c38550e9f37cffde1d1dcc9411d`**, both MERGEABLE. Byte-identity of
every touched file against the PASSed heads verified before pushing (13 files), 60
tests green on the rebased tree. cc-quality asked for a confirmation PASS naming the
new full shas (87665) — the old PASSes name the old shas and the gate must refuse them.

**LANDED 2026-11-01:** #759 → merge commit `58b06c4` (PASS 87516 at `8ea32ef`);
#752 → `27e9905`. orch-console merges 762 and 761 as their CI goes green (87601/87604)
— the lane does not run merge_pinned. **Fleet-wide hook INSTALLED** from the live
checkout (`ensure_user_global_hooks.py --apply`, approved 87339) and verified by
executing the installed command line: the 30 Oct `wc_facebook%` dump DENIES (exit 2
+ marker), the narrow `woocommerce_currency` read ALLOWS (exit 0), `Read` of
`wp-config.php` DENIES. ~~**Caveat flagged to the console (87606):** user-global hooks
load at session start, so the hub, Nazim and every already-running lane are still
unprotected until they restart.~~ **WRONG — CORRECTED 2026-11-01 (87726).** Hooks
hot-reload: cc-fleet-health proved it (87700/87706) and I reproduced it in my OWN
process — claude running since 15:56:47Z, guard installed ~19:16Z, probe refused at
19:44Z, never restarted, binary 2.1.316. No recycle is needed for guard coverage on
THIS host; the VPS hub and cubeasht remain unprobed.

- **#759** `cc-substrate/credential-store-guard`, head `8ea32ef` — the PREVENTION half,
  the head cc-quality PASSed (87497 at `4adbab4`, re-affirmed 87516 at `8ea32ef`).
  I had briefly pushed the bypass fixes onto this branch (`0340db5`) before orch-console
  asked for them as a follow-up (87512), so the branch was reset to the reviewed head:
  **only a PASSed sha may merge** (merge_pinned pins it).
  `scripts/hooks/credential_store_guard.py` (PreToolUse on Bash|Read|Grep) refuses
  (1) printing a credential-bearing store (WordPress/Woo options + exports, wp-config,
  Shopify/Stripe/Meta settings, `.env`, credentials/service-account json, token caches)
  unless the output goes to a hash/length sink or straight into vault; (2) broad
  `SELECT *` / dumps against client-site DBs, with narrow named non-secret columns still
  allowed; (3) secret-reading CLIs. `scripts/put_client_secret.py` is the ONE sanctioned
  road from a store into vault — prints only `OK NAME sha256:xxxxxxxx len=N`. Proof: a test
  replays the exact 30 Oct `wc_facebook_*` options dump and must be blocked, plus the
  narrow path must still pass.
- **#762** `cc-substrate/credguard-bypass-followup`, head `0340db5`, stacked on #759 —
  cc-quality's five findings: B/D fixed (launcher peeling: `env`/`command`/`exec` no
  longer defeat Rule 2's anchor), A fixed (`$(< file)` verbless read — the console had
  asked only to document it; it was cheap), E's narrow half fixed (a non-WP store's DSN
  env var, case-sensitive so `stripe_events` in our own substrate is not a FP); C and
  E-broad documented in the playbook residual list and pinned as known-allow tests.
  **PASS 87552 at `0340db5`** (verified empirically, incl. that `stripe_events` against
  `$DATABASE_URL` correctly does NOT trip). One new non-blocking finding **N1**: a
  launcher WITH A FLAG still defeats the anchor (`sudo -E psql`, `time -p psql`,
  `nohup -- psql`) because `_LAUNCHER_PREFIX_RE` is not flag-tolerant the way
  `_ENV_PREFIX_RE` is. **NOT fixed — batched with the merge_pinned sign-off work**
  (heads frozen); the residual list must say it is open until then.
- **#761** `cc-substrate/no-cross-provider-resume`, head `c70cea9` (PASS 87538 named
  `1f9a605`; cc-quality re-anchored to `756ca15` in 87542; a re-affirm at `c70cea9` is
  requested in 87544 — **no further pushes on this branch**), stacked on #759 + #762.
  Applied all three of cc-quality's recommendations instead of deferring them:
  **F1(a)** the conninfo fail-open is CLOSED (three bare `password=` tokens + no
  libpq key scored 3 in the shared pattern and 1 here — verified old-vs-new, plus
  the missing word boundary now present, which also removed an over-report);
  **F1(b)** the docstring/test name no longer claim "same answer" — they say
  conservative superset and name the over-reports; **F2** the interactive
  `--resume` picker gap is in the playbook residual list with operator guidance —
  `scripts/lib/session_provider_guard.py` + `.sh` shim, wired into
  `launch_dangerous_cc.sh` (the funnel under `launch_lane_as.sh` and `switch_lane_token.sh`),
  `boot_orch.sh`, `boot_mirror.sh`, `boot_nazim.sh`. A resume across a provider boundary is
  refused unless the transcript scans clean; refusal boots FRESH loudly + writes a handoff
  note. 22 tests, incl. a lockstep test that shells the real `_provider_for_model`.
- **#752** (render-gate fail-loud) — CI queued, cc-quality already PASS (87372).

**Sequencing:** merge_pinned #759, then #762, then #761 (every step via merge_pinned on
a PASSed sha with green CI; CI is queue-congested, cc-fleet-health confirms runners are
healthy — 87528). Then run
`scripts/lib/ensure_user_global_hooks.py --apply` **FROM `~/wingmen/orchestrator`** (the live
checkout) — it resolves `$CLAUDE_PROJECT_DIR` at the checkout it runs in, so running it from a
worktree installs hooks that die with the worktree. Verify afterwards from a lane sitting in a
client repo (orch-console asked for exactly this, bus 87339).

**Decisions I made on safe defaults (two questions to orch-console in bus #87334 still
unanswered):** pipe-to-sink only, NO override flag; no vendored gitleaks ruleset — reuse the
fleet's existing `secret_shape_patterns` detector.

**Lessons worth keeping:**
- CI run 41227818506, 5 red: my `fake_vault` fixture stubbed `sys.modules` only. On Python 3.9
  `import nervous_system.vault as sv` resolves through the **package attribute**, so the `as`
  form handed back the REAL module and the test patched the live vault singleton for the rest of
  the process. Fixed in `8ea32ef` by patching the package attribute too, and the test now takes
  its stub from `sys.modules` and asserts its class.
- A guard is not tested until you re-read the **shipped** block. A bare `--resume` (picker mode)
  left both the resume-id and continue variables unset, so the whole guard was skipped; and
  `spg_strip_resume_flags` ate the token after `--resume` unconditionally. Fixed in `1f9a605`.
- Shared-detector ReDoS (87483 → cc-fleet-health CONFIRMED at 87499, I handed them the fix at
  87503): `postgres-dsn-kv` is quadratic — a single 640-byte line repeating `password=` never
  returned. `session_provider_guard.py` scans line-by-line and substitutes a bounded equivalent,
  pinned by an equivalence test. Every OTHER `find_hits` caller still eats it.
- Every guard tightening was justified by measured false positives on real fleet commands
  (92,826 on the first pass: 64 → 32 → 15 denials), each measured FP pinned as a regression
  test. The cc-quality hardening was re-measured the same way: 36,931 unique commands rebuilt
  from local transcripts, pre-change and post-change guards compared — 34 denials before, 34
  after, 0 newly denied, 0 newly allowed.



## TRACK C — leaked substrate DSN: identified, scope measured, rotation PLAN sent (2026-11-01, P1 87628 → 87660/87661)

The 26 Oct `pgrep -fl` leak in session `6da64adb` is the **substrate `postgres` role**
(`postgres.tscuymavysscrvoberrr` via the ap-southeast-2 pooler, pw `sha256:95ea35a8`,
40 chars) — byte-identical to `DATABASE_URL` in `~/wingmen/orchestrator/.env`, so it is
**live, not stale**. Project-owner level: full DDL+DML over bus, briefings, decisions and
the vault table.

Measured scope (byte scan of **1,561** files / **9.54 GB** under `~/.claude/projects`):
three files carry it — the `cosem-adcda` lane transcript `6da64adb…jsonl` (171.8 MB,
2 hits; **still running**: pid 20698, `--resume 6da64adb`, replays every turn), the
`-wingmen-quality` transcript `a5a71254…jsonl` (1.4 MB, 3 hits — already spread one hop
to a reviewer), and the persisted tool result `…/tool-results/bpiulbdx3.txt` (431,876
bytes). GitHub Actions holds no such secret.

Plan in 87660: vault-store new password → Supabase dashboard reset (console's step) →
rewrite `.env` from the vault → restart ingest/tg-out/watchdogs → VPS hub → terminate
lingering pooler backends (a reset does NOT evict them) → verify old password fails →
**then** hash-scrub the three files while no session is appending → durable fix is a
dedicated non-superuser fleet role. Nothing destructive executed; three yes/no questions
are with the console (87661 crosses the P1+requires_response wake floor).

**Lessons banked:** a repeated `--scan` flag REPLACED the earlier glob (argparse
`nargs="+"`), so my first "one file, clean" reading was a blind filter — corrected in the
same cycle; and `lsof` returns nothing at all in this sandbox, so its silence is not
evidence that no process holds a file.

**PREPARED (87688):** `scripts/rotate_substrate_dsn.py` + 18 tests → **PR #765**
(`cc-substrate/substrate-dsn-rotation`): `stage` (stdin → vault, flags the superseded
value leaked first), `apply` (rewrites ONLY the `.env` keys whose URL password hashes to
`--old-sha8`, atomic, 0600 backup), `verify` (new connects / old rejected),
`restart-consumers` (ingest, tg-out). Dry runs against the live box clean; nothing written.
Vault already held this credential from the **1 Oct** rotation: `SUBSTRATE_DB_PASSWORD_NEW`
== the live password (`95ea35a8`), `SUBSTRATE_DB_PASSWORD_PREV` and
`substrate_database_url_password` == the 1 Oct superseded one (`5b920dfb`), neither
leak-flagged. Console's answer (87677): Musa picks the window; cosem-adcda lane quiesced
and relaunched fresh; hub updates its own VPS env; phase-6 role approved.
**a5a71254 CORRECTION:** it IS the current cc-quality conversation (newest, still growing),
`/clear` did not move it to a new file — the secret is at lines 298/301 of 639 and any
resume replays it, so the third file is a real scrub target, not history.

**ORDER CORRECTED (87711) — apply BEFORE the dashboard reset.** The vault is reached
through `DATABASE_URL`, i.e. the credential being rotated, so reset-then-apply strands
the fleet with a dead password and a tool that cannot read the value it must write.
Runbook now: `stage` → `apply --execute` → **dashboard reset** → `restart-consumers` →
`verify` → scrub. Existing connections survive a password change (Postgres authenticates
at connect time), so nothing breaks until new connections are attempted. `apply
--from-stdin` is the escape hatch if the reset already landed. 22 tests.
**SCRUB FEASIBILITY MEASURED (2026-11-01 19:45Z):** dry-run sweep of the 171 MB
transcript with the real manifest took **9.1 s**, peak RSS **202 MB**, found the 2
spans, size preserved (172,182,489 → same), `total_after: 0` predicted. Step 6 of the
runbook is seconds per file, not minutes — it can ride the same window.

**PR #766** (`cc-substrate/sweep-multi-scan`, `action="extend"`): a repeated `--scan`
silently replaced the earlier glob — the bug behind my false "clean" reading. 2 tests,
red on the old code. Only caller (`secret_hash_sweep_run.sh`) passes arrays after one
flag, so behaviour there is unchanged (verified; I had asserted this before checking and
sent the correction as 87698).

## STATUS (2026-09-24): substrate ihsanification programme — P1/P3 proposal + re-measurement done, NO BUILD (per #42896)

Full doc: `reports/substrate-ihsanification-next-moves-op42896.md` (both copies, synced). Posted bus #42908. Headline: P1 (one registry) — 3 of ~10 original hardcoded singleton-set copies already fixed (read `protected_agents` now), but 2 NEW hardcoded copies appeared since 09-05 (`hosted_server.py`, `cc_session_costs_auto_writer.py`) — net count is a wash, not progress; `console/app.py:293` is missing `cai` entirely, a live disagreement. P3 (ship gate) — zero wiring progress since 09-05; recommended `deploy_console.sh` as first consumer, not a generic pre-push hook. Re-measured: orphan scripts 17/178 top-level (methodology differs from the 09-05 %, flagged not fudged); `boot_briefing` 1.01MB/1861 rows (marginal -8% from 1.09MB/1899, still ~25x over the proposed invariant); **fable/substrate-safe-fixes now HAS CI configured (real change) but is currently RED** — root cause found: `ModuleNotFoundError: psycopg2` in 4 test files, a pre-existing `requirements.txt` gap NOT caused by anything committed this session — flagged as a quick separate fix, not built here (proposal-only task).



- [collapsed 21 superseded sections: “STATUS (2026-09-24 ~22:30Z): gzb off-site backup destina” … “op#42886 — daily_backup.sh client-silo DSN + fail-loud f”] (226 lines, 50048B)
- [collapsed 17 superseded sections: “op#42888 — pool_usage_history false-positive fixed at ro” … “op#42896/#42909 — co-verified, checkpoint #24 discharged”] (360 lines, 35394B)
## op#42896/#42909 — orch-console's reply: both GO w/ conditions; hub_reach fix comes first (2026-09-24 ~23:55Z)

Orch-console approved both (#43109), with a sequencing catch: the hub token-card fix (proposal
1) no longer needed to extend SSH reach at all — it turned up that `hub_reach.py`'s OWN gzb
route (via the deleted wingmen-core relay) was itself dead, and BOTH `context_health_watchdog.py`
and `singleton_liveness.py` were surfacing that dead guidance in live operator pages TODAY.
Fixed that first, as instructed.

**PR #145** (`fix/hub-reach-dead-gzb-relay`, `01bad44`): audited every `hub_reach` caller before
touching anything — `reset_hub_remote.sh` and `irsyad_media_mirror.py` were already correctly
fail-safe for gzb (refuse/skip, never consumed the remedy text); `context_health_watchdog.py`
and `singleton_liveness.py` both consumed `hub_reach_for_holder(...)["remedy"]` directly for a
live page and were broken. `hub_reach_for_holder("gzbai")` now returns `reach=None` and an
honest remedy (relay decommissioned, no replacement built, gzb is systemd-self-supervised for
crashes but not for a wedged-alive composer, escalate to operator) instead of inventing a new
untested SSH credential. Updated 3 test files' stale gzb-holder assertions. 176 passed, 1
skipped. Reported (#43115). **Awaiting orch-console's gate.**

**Proposal 1 build** (`fix/hub-token-card-self-reported`, `3bf28c8`, main checkout — small
enough not to need a separate worktree): `panes.py` gained `_self_reported_hub_account()` —
falls back to `cc-orchestrator`'s own `agent_status.auth_fp` ONLY when the SSH scan is
unavailable AND the row is fresh (`_SELF_REPORT_FRESH_S` = 900s), else flags "stale". Applied
all 4 of orch-console's conditions: freshness gate, a visually-distinct new "SELF-REPORTED"
badge (indigo) in `lanes.js`/`lanes.html` separate from VERIFIED/UNVERIFIED, `rowHtml`'s
class-order REORDERED so `mismatch` beats `verified`/`self_reported` (load-bearing — self-report
is the first case where an unverified row can carry a real fp to mismatch against), console
gate unchanged. 9 new tests (364 passed across the full relevant slice). Content hash
`20af9cbd8e50e2d3` — pre-push correctly BLOCKED (expected), rendered PNGs (no regression to the
other 10 rows; honestly flagged to cc-quality that the render pulls LIVE data from the
currently-deployed backend so it can't show the new badge yet — reviewed from the code diff,
not fabricated as a synthetic proof). Review requested (#43122) + woke cc-quality. **Awaiting
review before push → PR → orch-console's gate.**

## op#42896/#42909 — PR #145 merged; cc-quality PASS+condition on proposal 1, version bump applied (2026-09-25 ~00:25Z)

Orch-console PASSed #145 on the CI subset rule (#43116: "the caller audit is exactly what I
wanted, fail-loud honesty over an invented route is the right call"). **Merged** (squash) as
`b15d9b4`, branch deleted, fast-forwarded `fable/substrate-safe-fixes` clean.

**Open gap flagged for later, NOT this PR:** a WEDGED-but-alive gzb hub now has zero
automated recovery (the operator is the only path out) — orch-console asked for a half-page
proposal (gzb-LOCAL systemd-timer recovery, same probe logic as `context_health_watchdog.py`,
touching only gzb's own tmux, lease-gated via a DB read not cross-host SSH) once #145 + the
token card were done. Sent (#43133, requires_response) — **not built**, theirs + hub +
fleet-health to broker.

**cc-quality reviewed proposal 1** (#43129): PASS on code correctness (traced the fail-safe
paths, the tz-aware freshness subtraction, the SSH-wins-over-self-report gating, the
mismatch-first class reorder across every row state, live-verified `cc-orchestrator`'s real
`auth_fp` age ~19s — inside the 900s window). 33/33 panes + 10/10 registry + 354/354 console.
One MEDIUM (alert-not-block): `lanes.js` is served stale-while-revalidate, so with the version
constants unbumped the phone would keep serving the OLD cached `lanes.js` — the new badge
wouldn't land until a 2nd reload. **Applied exactly as recommended:** bumped `sw.js`
VERSION / `fleet.js` APP_BUILD / `lanes.html` badge `fc-v65` → `fc-v66` in sync (left
`fleet.js`'s historical fc-v65 *comment* alone — not a live constant), committed `7d1f793`.
This mechanically changed the content hash to `2666db0a8dd3c3c7` (cc-quality's PASS was
stamped for `20af9cbd8e50e2d3`) — re-rendered, asked for a re-stamp at the new hash (#43132,
requires_response) rather than assume the old review still covers what actually ships. Wake
attempt for cc-quality came back unverified (`lane_nudge rc=3`, possibly mid-task) — the bus
row is durable per Option B, not retrying the nudge.

**Status: PR #145 done. Proposal 1 code-complete + tested + reviewed, awaiting a hash
re-stamp before push → PR → orch-console's gate. gzb-local-recovery proposal sent, awaiting
reply. Nothing to build until one of these three lands.**

## op#42896/#42909 — re-stamp landed, PR #147 open; gzb-recovery re-framed + written up (2026-09-25 ~00:30Z)

cc-quality re-stamped PASS at the deploy hash (#43135, `2666db0a8dd3c3c7`) — diff-verified
their re-check was against the IMMUTABLE commit `7d1f793`, not a working-tree race: exactly 3
files, +3/-3, only the version literals, zero `.py`/logic change, full PASS transfers
unchanged, no re-run needed. Committed the re-stamp (`54cc5bb`), pushed clean, opened **PR
#147**: https://github.com/sheikh-musa/wingmen-orchestrator/pull/147. Reported to orch-console
(#43136, requires_response) for the subset-rule gate — same post-merge deploy steps as #142
mine to run again unless told otherwise.

Orch-console re-framed the gzb-recovery proposal (#43134) before it goes anywhere: NOT gated
on `fleet_health_lease` (that would make it the SRE's pen acting on a singleton — CAI-RESP-
501/681 keep singleton-affecting action away from the SRE, and the OLD `hub_reach.py` remedy
named this exact nudge as the CONSOLE's pen, not the SRE's). Re-framed as **hub
self-supervision**: the actor is gzb's own host supervisor (a systemd timer alongside
`wingmen-orch-hub.service`/`orch_supervisor.sh`), gated on `orch_lease.holder_host=='gzbai'`
(acts on itself only), `fleet_health_lease`/cc-fleet-health not in the loop for the action at
all. Detection + operator page stay ungated. Action envelope: non-destructive only, reuses
`scripts/lane_nudge.sh`'s EXISTING guards as-is (menu-refuse, ghost-vs-real composer,
per-row ceiling) rather than new logic, plus a proposed 3/hour rate limit and one page per
action (never silent, never a flood). Kill switch: a flag FILE on gzb (not DB-only, so it
still works if the DB this mechanism reads from is itself down), default on.

Written to `reports/hub-self-wedge-recovery-proposal.md` (both copies, gitignored, synced).
Reported done (#43138) — **not built**, orch-console routes it to cai for a singleton-
authority ruling next, with the hub's consent.

**Status: PR #147 awaiting orch-console's gate. gzb-recovery proposal written, ball is in
orch-console's court to route to cai. Nothing left to build until one of these lands.**

## op#42896/#42909 — PR #147 merged+deployed; acceptance test caught a real render gap, fixed (2026-09-25 ~00:40Z)

Orch-console PASSed #147 on the CI subset (#43137), all 4 conditions confirmed in code, live
data backing it (`auth_fp` 23s old at their check). **Merged** (squash) `0448157`, deployed
`fc-v66` — version-sync, 88 tests, render, review all passed, served version confirmed
(`fc-v66`/`0448157`).

**Their stated acceptance test (post-deploy `lanes.png` must literally show "SELF-REPORTED ·
Musa") FAILED — and it was a real, previously-unfound gap, not the session-key bug they
anticipated.** Checked raw `/api/token-truth` JSON first to rule out a backend bug:
`self_reported=true, account="Musa", mismatch=false` — all correct. Root cause: `rowHtml()`'s
`badge` variable was computed (`"SELF-REPORTED"` etc.) but **never actually inserted into the
returned HTML** — a pre-existing gap that neither of cc-quality's 2 prior reviews nor my own
testing caught, since nothing before this had exercised literal rendered TEXT against an
acceptance string (only color/class). Reported the finding + the process gap transparently to
orch-console (#43149) before fixing anything further.

Fix (`fix/self-reported-badge-text-render`, `77b7b83`): `acct = badge + " · " + account` for
the self_reported branch only — other branches' chip text untouched. New content hash
`965821852a9c36c6`. Re-rendered against the SAME already-deployed live data to confirm: chip
now literally reads "SELF-REPORTED · Musa". 121 tests (panes+app) unaffected, as expected for
a pure JS text-format change. **3rd review cycle requested from cc-quality (#43151)** — wake
came back unverified again (2nd time this session; bus row durable, not retrying manually).

**Status: awaiting cc-quality's 3rd-cycle review before push → PR → orch-console's gate →
redeploy. The gzb-recovery proposal (previous section) is with cai + the hub for a ruling,
separately, nothing to chase there until one replies.**

## op#42896/#42909 — orch-console's #43153 challenge: 3 more real gaps found+fixed, branch-tracking slip caught+corrected, 4th review requested (2026-09-25 ~01:10Z)

Orch-console's 3rd-cycle review of the badge-text fix (`77b7b83`) surfaced 3 further real
gaps via literal acceptance testing against the deployed `fc-v66` render, not anticipated by
me:

1. **Attention-exclusion**: a fresh self-reported row was still landing in the "needs
   attention" bucket — `isAttn()` logic was duplicated between `rowHtml()`'s inline
   `attention` var and `render()`'s local function (risk of drift, and it had drifted).
   Hoisted to one shared top-level `isAttn(r) { return r.metered || r.mismatch ||
   (!r.verified && !r.self_reported); }`, both call sites now use it.
2. **Summary counts**: header read "1 unverified" for a fresh self-report instead of "1
   self-reported" — `panes.py`'s `summary` dict counted `unverified` as `not x["verified"]`
   only, not excluding `self_reported`. Added `"self_reported"` count, redefined
   `"unverified"` to exclude self-reported rows.
3. **Host label**: remote-body rows showed a stale hardcoded `"(VPS)"` literal from before
   the hub moved to gzb — built `_remote_body_host(session, fallback)` in `panes.py`, reads
   `agent_status.host` live (already correctly says `"gzbai"`), falls back to the old static
   label on any DB error/missing row/null host so a DB hiccup degrades gracefully rather than
   blanking the label.

**Process slip caught by my own pre-check, not by review:** built all 3 fixes directly on
`fable/substrate-safe-fixes`'s tip instead of first checking out
`fix/self-reported-badge-text-render` (the branch holding `77b7b83`, not yet merged) —
meaning the new edits were building on the pre-badge-fix state of `lanes.js`. Caught via my
own pre-push render: the attention-exclusion fix showed correctly but the chip still read
plain "Musa" instead of "SELF-REPORTED · Musa". Traced via `git branch --show-current` +
grep confirming the file lacked `77b7b83`'s change. Corrected via a scoped `git stash push -u
-m "cc-substrate-op43153-fixes-<ts>" -- <6 files>` (NOT the whole tree — avoided
`deploy-log.txt` and other unrelated dirty files), captured the SHA
(`82fc3567c512a9870f27b6552a0faf51d98d5007`) via `git stash list --format='%H %gs'`,
`git checkout fix/self-reported-badge-text-render` (clean), `git stash apply
82fc3567...` (auto-merged cleanly — the two diffs touched different regions of
`lanes.js`), verified the combined result, deleted the stale render-artifact dirs from the
wrong-branch attempt, then dropped the stash via its re-found `stash@{0}` form (the raw SHA
failed as a `drop` argument).

Also retrofitted all 5 pre-existing `token_ground_truth` integration tests in
`test_panes.py` with `monkeypatch.setattr(panes, "_remote_body_host", lambda session,
fallback: fallback)` to stay hermetic against the new DB-reading helper, and added ~11 new
tests (`_remote_body_host` resolves/no-row/null-host/db-error; `token_ground_truth`
self-report fallback / mismatch-still-red / stale-falls-back / no-scan-no-self-report /
SSH-wins-over-self-report / host-propagation; summary self_reported/unverified separation).
Version bumped `fc-v66` → `fc-v67` in lockstep (`sw.js` VERSION, `fleet.js` APP_BUILD,
`lanes.html` badge).

Committed `72e9789` (parent `77b7b83`, branch `fix/self-reported-badge-text-render`) with an
explicit "NOTE ON HOW THIS BRANCH GOT HERE" section in the message documenting the slip and
its correction — reported transparently, not hidden. New content hash `50e4d1c33521b6aa`.

**Honest limitation flagged again (same shape as prior cycles):** the attention-exclusion and
badge-text fixes are pure client-side logic operating on already-live backend fields, so
confirmable pre-deploy via render. The host-label and summary-count fixes are
backend-dependent (new `_remote_body_host` DB read, new summary keys) and can only be
confirmed against a real post-deploy render, same limitation as every prior cycle's
backend-touching change.

Sent 4th-cycle review request to cc-quality (#43159, `review_request`,
`requires_response=True`), woke them — **this wake succeeded** (`{'woke': True, 'session':
'quality', ...}`), unlike 2 earlier failed wake attempts this session (rc=3, not retried
manually per Option B durability doctrine).

**Status: awaiting cc-quality's 4th-cycle review (hash `50e4d1c33521b6aa`) before push → PR
→ orch-console's gate → redeploy → fresh post-deploy render to confirm the host label reads
"gzbai" and the header shows "10/11 verified · 1 self-reported". gzb-recovery proposal still
with cai + the hub for a ruling, nothing to chase there until one replies.**

## op#42896/#42909 — cc-quality 4th-cycle PASS, PR #149 open; hub consented, PR #150 built (2026-09-25 ~01:30Z)

cc-quality PASSed the 4th cycle unconditionally (#43165, hash `50e4d1c33521b6aa`): fc-v67
version-bump MEDIUM closed, `isAttn()` hoist traced correct across every row state, the
`{verified, self_reported, unverified}` summary partition verified mutually exclusive by
construction, `_remote_body_host` fail-safe on all 4 paths with EFFICACY confirmed live
(`cc-orchestrator`'s real `agent_status.host` = `"gzbai"`). Branch-slip self-correction
confirmed clean (diff `77b7b83..72e9789` only adds). `test_panes` 40/40, full `tests/console`
361/361. Committed the review file (`5eac0ab`), pushed — pre-push gate passed this time.
Opened **PR #149**: https://github.com/sheikh-musa/wingmen-orchestrator/pull/149. Reported to
orch-console (#43166ish, requires_response) for the CI-subset gate — same post-merge deploy
steps mine to run again unless told otherwise. Wake attempt: orch-console busy mid-turn
(not a wedge signal) — bus row durable, not retrying.

**Separately, the big item this turn:** orch-console's decision row landed (#43161) — **the
hub CONSENTED** (#43157) to the gzb self-supervision wedge-recovery proposal (CAI-RESP-1439,
cai's 6 conditions already met/approved). Authorized to **BUILD now, OBSERVE-FIRST** (the ACT
step a no-op that logs `would-nudge` for ≥72h / ≥3 genuine detections, whichever is longer —
graduating to real action is orch-console's own later call, not something this code flips
itself). 4 binding design inputs given, plus the earlier DB kill-flag + per-row-lifetime-
ceiling amendments: (a) detection gates on PENDING WORK never bare idleness — explicitly named
today's own case (22.5h idle, only P2 unread → no action) as the test to prove; (b) refuse on
ANY menu/picker (model picker, trust prompt, resume picker, permission dialog — extend
`pane_is_menu`'s fixtures if any don't match its generic nav-footer regex); (c) never act
mid-turn/mid-autocompact; (d) the resubmit payload is a FIXED generic line, hardcoded, never
templated from row/operator content (R1/R2).

Built exactly that: `migrations/068_hub_self_recovery.sql` (additive —
`hub_self_recovery_settings` singleton row for the DB kill-flag half, `hub_self_recovery_log`
append-only audit trail doubling as the graduation-criterion evidence; `--dry-run` verified
clean against the real substrate, sha256 `303a98aac861…` — **not applied**, orch-console's gate
first). `nervous_system/hub_self_recovery.py` — the pure decision core
(`lease_is_self`/`kill_switch_enabled`/`pending_work_verdict`/`wedge_detected`, each mapping to
exactly one of the binding inputs above) plus DB wiring + a CLI. `scripts/hub_self_recovery.sh`
— the systemd-invoked driver: sources `composer_capture.sh` for the pane checks (composes
`pane_is_menu` with `trust_prompt_present`/`resume_menu_present` per condition (b)'s explicit
instruction), calls the python module, and — critically — never types into the pane itself; the
ACT step (once graduated) delegates entirely to `lane_nudge.sh`, reusing its existing
verified-submit + menu-refuse + ghost-vs-real guards unchanged rather than reimplementing any of
them. `deploy/wingmen-hub-self-recovery.{service,timer}` — staged tracked copies (2min cadence
oneshot), `HSR_MODE` unset so it defaults to `observe`; actual `systemctl enable/start` on gzb
is a follow-up deploy step after merge + migration apply, same convention as
`wingmen-irsyad-coord.service`'s staged-not-enabled precedent. 27 new tests
(`tests/test_hub_self_recovery.py`): the full pure-core matrix (every condition individually,
including the exact named 22.5h-idle case) plus `evaluate()` orchestration/routing tests with
every DB seam monkeypatched to a controllable fake.

Diff is **purely additive — 6 new files, zero existing files touched**, confirmed via
`git status`, so zero regression surface; a full-suite run (3129 collected, ran to 351 before
`-x` stopped on `tests/fire_drills/test_drills_all.py::test_each_drill_passes_live[SigkillDrill]`
— a pre-existing, unrelated live-process drill failure, nothing to do with this change) was
not needed as a gate for that reason, though it ran anyway for due diligence. Condition→code
map written to `reports/hub-self-recovery-condition-map-op42896.md` (both copies, gitignored,
synced) and folded into the PR description. Opened **PR #150**:
https://github.com/sheikh-musa/wingmen-orchestrator/pull/150 (branch
`feat/hub-self-recovery-observe-first`, off latest trunk). Reported to orch-console
(requires_response) requesting the code audit per #43161's own split ("Code audit = me,
deployment fidelity = cc-quality FULL tier") — cc-quality review comes AFTER orch-console's
audit, not before, per that explicit ordering. Wake attempt: orch-console busy mid-turn again —
not retrying, bus row durable.

**Status: two PRs open awaiting orch-console — #149 (console badge/host fixes, cc-quality
already 4th-cycle PASSed, just needs the CI-subset gate) and #150 (hub self-recovery build,
needs orch-console's own code audit first, then routes to cc-quality FULL-tier deployment
fidelity, then migration 068 via the gate, then merge, then the gzb systemctl step separately).
Nothing to build until one of these two lands — next `[wake]` reconciles fresh.**

## op#42896/#42909 — PR #149 MERGED + deployed fc-v67, all 3 acceptance criteria confirmed on a re-render, console P1 site closed again (2026-09-25 ~01:20Z)

Orch-console PASSed #149 on the CI subset (#43167): all 3 of the #43153 items confirmed in
code, fc-v67 version triple ✓, cc-quality's 4th-cycle PASS unconditional. Re-checked the PR
head matched what was reviewed (`5eac0ab`) immediately before merging. **Merged** (squash) as
`4511683`. Fast-forwarded the main checkout clean, confirmed content hash matched exactly
(`50e4d1c33521b6aa`) before running the gate.

Ran `scripts/deploy_console.sh` for real — all 4 gates passed, kickstarted.

**Caught a real gap along the way, not this PR's bug:** the gate's OWN bundled PNG (rendered
at gate-3, `scripts/deploy_console.sh:67`) races gate-4's `launchctl kickstart` (`:102`) — the
render hits the server BEFORE the reload, so it captured the OLD still-running process's data
(header read "1 unverified", host showed "VPS") even though the deploy itself was fully
correct. Did not trust the bundled PNG as proof; diffed the served `lanes.js` against git
(byte-identical — ruled out a static-file problem), confirmed live `/api/token-truth` already
returned the correct `self_reported`/`gzbai` fields (proving the backend WAS live), then
re-ran `scripts/render_console_pages.sh` standalone against the now-live post-kickstart
server. That render confirmed all 3 of orch-console's literal acceptance criteria: the
cc-orchestrator card sits in the normal SINGLE LANES list (not a separate attention bucket),
chip reads "SELF-REPORTED · Musa", subtitle reads "host gzbai"; header reads "10/11 verified
1 self-reported" (no "unverified"); served `/api/version` = `{"version":"fc-v67","sha":"4511683"}`.
Reported both the confirmation and the race-condition finding to orch-console (#43168ish,
informational, not blocking) — flagged, not fixed (pre-existing script behaviour, out of
scope for this PR; their call whether it's worth a follow-up so the gate's own bundled PNG
can be trusted as post-deploy proof without a manual re-render).

**P1 registry / console-files site is CLOSED again** (same status as the #43092 close, now
re-verified end-to-end through this 4-cycle detour). PR #150 (hub self-recovery, migration 068)
is the only open thread — awaiting orch-console's code audit per #43161.

## RECONSTITUTE HERE (2026-09-29, this session — self-recycling per orch-console's 1%-before-autocompact directive)

**Note: the PR #150/hub-self-recovery thread above (line 560-656) reads as the last open
item, but it is STALE relative to this section — assume it was resolved/superseded sometime
between 2026-09-25 and now unless the bus says otherwise; reconcile `agent_messages` first
thing, don't assume anything above this line is still pending.** This session picked up
fresh work (operator-asks TRIAGE design + a PR #215 review), unrelated to PR #150.

**1. PR #215 (quality_gate shadow wiring into deploy_console.sh) — fixes pushed, CI shows
fail but looks like pre-existing environmental noise, NOT YET MERGED, sha NOT YET posted.**
- Branch `wire-quality-gate-shadow-deploy-console` → `fable/substrate-safe-fixes`, in a
  SEPARATE worktree `/Users/sheikhmusa/wingmen/projects/orchestrator.wt-pr215fix` (kept
  separate from this shared worktree specifically to avoid colliding with item 2 below).
- orch-console's review (bus #45683) asked for 2 fixes, both done: (a) hard timeout on the
  shadow evaluator subprocess in `scripts/console_deploy_quality_gate_shadow.sh` (macOS has
  no `timeout(1)`, used a backgrounded job + polling `_kill_tree()`); (b) honest unit-tests
  evidence in `scripts/console_deploy_gate_evidence.py` — require a positive `\d+ passed`
  regex match, not just "no FAILED marker present" (an empty/truncated/collection-error log
  used to false-score "pass"). Both covered by new tests in
  `tests/test_console_deploy_quality_gate_shadow.py`. Local run: 49/49 green.
- Committed `e165d60`, pushed. Bus #45695 sent to orch-console (reply-to #45683) promising
  "merging on green + posting sha".
- **CI run 36542429376 came back `fail`, NOT the `pending` I last saw.** Pulled the FAIL
  summary (`gh run view 36542429376 --log-failed`): ~28 failing test IDs, ALL in files I did
  not touch (`test_lane_wedge_watchdog.py`, `test_reset_gates.py`, `test_self_recycle.py`,
  `test_a3_runner.py`, `test_context_loader.py`, `test_qa_bridge.py`, `test_orch_self_audit.py`,
  `test_schema_gate.py`, `test_reset_busy_gate.py`, `test_operator_asks_ledger.py`,
  `fire_drills/test_drills_all.py`, `test_lane_nudge_probe_wire.py`) — failure modes are CI
  runner environment gaps (`FileNotFoundError: .../.env`, `ModuleNotFoundError:
  wingmen_orch`/`bug_pipeline`, a missing local restore-point file under
  `/home/runner/wingmen/wingmen-cai/...`), not anything shaped like the 2-file diff here.
  Neither `test_console_deploy_quality_gate_shadow.py` nor `test_ihsan_gate.py` nor
  `test_deploy_console_gate.py` (the 3 files I actually ran green locally) appear in the
  failing list at all.
  **NOT YET DONE: the subset-rule confirmation this repo's own precedent requires before
  merging on a red CI** (see e.g. the PR #142/#149 entries above — diff the failing test-ids
  against trunk's OWN current CI failures, `comm -23` both directions, merge only if
  identical/subset). `mergeStateStatus` is `UNSTABLE` but `mergeable: MERGEABLE`.
  **NEXT ACTION for whoever picks this up: run that subset-rule diff (fetch trunk's latest
  CI run's failing test-ids, compare), and if clean, merge (squash, matching this repo's
  convention) and post the merge sha back to orch-console per #45695's promise — do not
  merge on the mere presence of a "fail" badge without doing that check first.**

**2. TRIAGE-state build (operator_asks triage_state column + heuristic + digest paging) —
dispatched to a background fork, STILL UNCOMMITTED, not checked on this session.**
- Bus thread: orch-console's original ask #45552 → my design proposal #45555 → GO with 5
  conditions #45557 → I launched fork `a8bca1f44ca66a94a` with the full approved scope.
- Full design doc (still valid, matches what was approved): scratchpad
  `asks_triage_design.txt` (also echoed in this handoff's context — additive migration
  `082_operator_asks_triage.sql` adding `triage_state` (captured/ask/not_an_ask/done, default
  'captured'), `triage_summary`, `triage_evidence_ref`, `triaged_at`, `triaged_by` to
  `operator_asks`; new `scripts/asks_triage.py` CLI mirroring `asks_open.py`/`asks_close.py`'s
  shape; a `_heuristic_triage()` pre-classifier for bare acks in `operator_log.py`; digest
  changes so `asks_daily_digest.py` only ever shows `triage_summary` for `triage_state='ask'`
  rows plus an aggregate "N not yet sorted" count-and-age line, never raw captured text;
  `priority_sla_watchdog.py` extended to page on that count aging past ~12h using the
  existing aged-rr-repage machinery (no new watchdog).
- **This build happens in THIS shared worktree** (`orchestrator.wt-cleanup`), branch
  `feat/operator-asks-triage`, currently checked out here with these UNCOMMITTED changes (as
  of this session's last check — re-run `git status --short` on waking, this may have
  progressed or completed since):
  `M CLAUDE.md`, `M nervous_system/operator_log.py`, `M scripts/asks_daily_digest.py`,
  `M scripts/asks_open.py`, `M scripts/priority_sla_watchdog.py`, `M tests/conftest.py`,
  `M tests/test_asks_daily_digest.py`, `M tests/test_asks_open.py`,
  `M tests/test_operator_asks_ledger.py`, `?? migrations/082_operator_asks_triage.sql`,
  `?? scripts/asks_triage.py`, `?? tests/test_asks_triage.py`,
  `?? tests/test_sla_captured_triage.py`.
- The fork was instructed to, once its own test suite is green: commit, dry-run migration
  082 in a rolled-back transaction, apply it FOR REAL via direct psycopg (never `supabase db
  push`, per CLAUDE.md), verify the real row-count readback (32 rewritten ask summaries per
  orch-console's own hand-triage in scratchpad `asks_triage.tsv`/`asks_triage_assigned.tsv`),
  open a PR against `fable/substrate-safe-fixes`, and send TWO bus reports to orch-console:
  one with the 32 rewritten summaries ("before the digest reads them" — there's a same-day
  05:00Z digest deadline mentioned in #45557), and one gate/confirmation report with the
  migration sha + dry-run/real readback proof.
  **NONE of that (commit/migration-apply/PR/either bus report) had happened as of the last
  check this session.** No confirmation the fork is still running or has stalled — CHECK bus
  `agent_messages` and this worktree's git state first, on waking.
- **Migration numbering note (binding, from CLAUDE.md): `081` is RESERVED for
  `081_substrate_april_residue_cleanup.sql`, held pending Musa's clearance — must NEVER be
  reused even though the file doesn't exist yet. `082` for this TRIAGE migration is correct
  and was confirmed against the actual latest file (`080_operator_messages_tag_shape.sql`)
  before dispatch.**

**3. Pooler-capacity investigation thread — PAUSED, not urgent, explicitly sequenced after
item 2.**
- Bus #45524 (orch-console's ask) → I dispatched a read-only investigation fork
  (`a671733f23f0d0951`, completed) → sent a measured proposal (#45574): raise `pool_size` as
  the near-term config-only fix; flagged what couldn't be measured (24h historical
  EMAXCONNSESSION frequency) rather than guessing; noted a transaction-pooler option exists
  but is gated on a 19-daemon safety audit first.
- Orch-console's follow-up (#45575) asked for 2 more measurements (Supabase Management API
  pooler config, fleet-health's gzb error-count logs) before finalizing a `pool_size` number
  — and EXPLICITLY said to do this AFTER the TRIAGE build (item 2), which has the 05:00Z
  deadline framing. #45668 was a further informational follow-up, no action needed. Both
  marked read (direct `UPDATE read_at`, no reply needed — sender said so).
- **No config change has been made. Do not touch `pool_size` or any pooler setting without
  orch-console's explicit OK on a specific number** (per CLAUDE.md's general gate doctrine).

**4. Standing/deferred items, untouched, no action expected right now:**
- `held_commitments` #26 (backlog#65 substrate ihsanification programme lineage,
  wingmen-core power-off + reference cleanup) — due 2026-10-01.
- Migration `081_substrate_april_residue_cleanup.sql` — HELD pending Musa, do not create or
  renumber around it.
- backlog#68 (CI test-debt cluster) — owned by cc-orchestrator, out of scope for this lane.

**On waking: reconcile `agent_messages WHERE to_agent='cc-substrate' AND read_at IS NULL`
FIRST (Option B doctrine — the log is the source of truth, not what this handoff predicts is
still unread), then work items 1 and 2 above in that order (PR #215 subset-check→merge→post
sha is the smaller/faster of the two), then resume item 3 only after orch-console has
actually asked for it again or item 2 is genuinely done.**

## RECONSTITUTE HERE (2026-09-29 ~09:10Z, this session — supersedes everything above)

**1. PR #215 — DONE.** Subset-rule check ran clean (root cause of the 9 apparent-mismatch
test IDs was my own shell's stale-env DB-password-rotation breaker trap, not a real PR
regression — reproduced, fixed locally, confirmed pre-existing/unrelated). Merged
(`0e86ea9`, ff'd on the Mini). Reported via bus #45699; orch-console accepted at #45701
(read, marked). Remote branch `orchestrator.wt-pr215fix`'s branch-delete step failed
(worktree still checked out elsewhere) — left alone deliberately, not force-deleted.
Secondary finding flagged (not yet built): `protected_agents.py` and
`scripts/asks_triage.py`'s `_dsn()` read `os.environ` directly instead of
`dotenv_values(".env")` — same stale-env class PR #214 fixed elsewhere, just not every call
site. Out of scope here; belongs to backlog#68 or a future hub pass.

**2. TRIAGE build (operator-asks, #45552→#45555→#45557 GO) — CODE/TESTS/MIGRATION DONE,
PR'd, REAL APPLY GATED, awaiting orch-console.**
- The prior fork dispatched for this item died with zero progress (no process, no bus
  completion report) — I executed the remaining build directly in this session instead of
  re-dispatching.
- All 5 of #45557's conditions addressed: 32/100/23 backfill (155 rows), per-channel
  triage-owner doctrine in CLAUDE.md, heuristic narrowed to bare-acks only (never `?`/request
  verbs), migration via the sha256-pinned `apply_migration.py` pattern (dry-run clean:
  `dry_run_ok`, sha256 `4525b1fd41ab…`), no-drop/count-invariant tests written and green.
- Committed `4fad942` on `feat/operator-asks-triage`, pushed, PR **#217** open against
  `fable/substrate-safe-fixes`.
- Sent TWO bus reports: **#45704** (the 32 rewritten open-ask summaries, for review before
  the next 05:00Z digest) and **#45705** (P1+req build/PR status + explicit gate request for
  the REAL apply — citing migration 082's sha256 prefix `4525b1fd41ab` — since #45557's GO
  predates the file and can't satisfy `apply_migration.py`'s content-binding check by
  design). **No real apply has been run.** Do NOT run `apply_migration.py 082 ... --gate`
  without a FRESH `message_type='decision'` row (from orch-console or cai) whose body
  contains `4525b1fd41ab` — check for it via the unread-reconcile step, not by assuming
  #45705 was answered.
- **On waking, if a gate decision for #45705 has landed:** run
  `scripts/apply_migration.py 082 --silo tscuymavysscrvoberrr --gate <that-msg-id>`, verify
  the readback (32/100/23 row counts, digest open-asks count == 32), and post the real-apply
  confirmation back to orch-console (reply-to #45705). If no gate decision yet, do not chase
  — it's a P1+req, orch-console has it.

**3. Pooler-capacity thread — still PAUSED**, unchanged from above; item 2 is now
"genuinely done" in the sense of PR-ready, but the REAL apply is still outstanding, so treat
this as not-yet-fully-done for the purposes of the "resume item 3" condition. Ask
orch-console rather than assuming.

**4. Standing items — unchanged, no action.**

**Housekeeping noticed, not yet acted on:** two untracked scratch files sitting in the
working tree — `reports/cc-substrate-boot.txt` (a copy of this session's boot directive) and
`reports/lane-handoffs/cc-substrate-handoff-NOW.md.20260929T083636Z.bak` (a timestamped
backup of this very file from earlier today). Neither was created by me this session; left
untouched rather than deleted on a guess. Worth a `git clean`-adjacent decision by whoever
next has full context on why they're there.
## SUPERSEDES the tail above (this file's "item 2" section above pre-dates PR #217's squash-
merge and is STALE): TRIAGE build is FULLY DONE — migration 082 applied for real (gate
#45707), 26/10 readback matched, PR #217 merged (squash `442d0b2`), both live checkouts
(Mini + gzb) fast-forwarded, dry digest rendered, full trace reported at bus **#45717**
(reply-to #45707). Item 3 (pooler capacity) was thus eligible to resume pending an explicit
re-ask from orch-console — still not re-asked as of the section below.

---

## RECONSTITUTE HERE (2026-09-30, held_commitments #81 / backlog#65 discharged)

Re-owned to `cc-substrate-1` via bus #46239 (fired originally to `orch-console` because that
instance id had no `agent_status` row — see `commitment_sweeper.py`'s "OWNS NO INBOX"
re-route; `cc-fleet-health` owns relaunching that heartbeat, not this lane).

**Commitment #81's two `next_progress_expected` items, both actioned:**

1. **Ran `bash scripts/deploy_console.sh` for real** on the Mini live checkout (content
   `b60ca472ebc75e0d`, fc-v67). All 4 gates passed (version-sync, 88 console tests green,
   fleet+lanes rendered, cc-quality review present). PR #215's shadow `quality_gate.py` step
   genuinely executed and wrote
   `reports/console-deploy/b60ca472ebc75e0d/quality-gate-verdict.json` — an honest mixed
   verdict (would_block=true, observation-only): G2/G3/G9 pass, G1/G4/G6/G8 unproven
   (missing evidence wiring, not fabricated pass), G5/G7/G10 fail. This is hard evidence the
   "consumed" half of done_when is real, not just "shipped". Live-verified the deploy itself
   (the script's own `/api/version now:` echo raced blank against `launchctl kickstart`, a
   known pre-existing quirk — don't trust that line, curl the Tailscale IP instead):
   `curl http://100.83.21.34:8787/api/version` → `{"version":"fc-v67","sha":"b8da24e"}`,
   ancestor-confirmed to include PR #215 + #217.
2. **Re-measured the consumer-map** against the 09-05 audit's 9 non-DB code sites: 6/9 fully
   migrated to `nervous_system/protected_agents.py` accessors (`fleet_health_boundaries.py`,
   `lane_winddown.py`, `verify_fleet_token.py`, `console/app.py`, `fleet_model.sh`,
   `lane_watchdog.py`); 1 deliberately deferred + documented (`lane_token_resolver.py`'s
   `_NO_POINTER_SINGLETONS`, correctness-critical, unchanged); **1 genuine remaining gap**:
   `nervous_system/lane_wedge_watchdog.py`'s `MONITOR_SINGLETONS` is still a hardcoded
   env-default list, not reading `protected_agents` — NOT fixed live (looks like an
   intentional Signal-A-only subset, not the full set; swapping it without confirming that
   needs its own small PR + test, not a rushed edit under commitment pressure); 2 sites
   (`lane_wedge_watchdog.py`'s `_SINGLETONS` recovery dict,
   `switch_singleton_token.sh`'s NODE REGISTRY) judged a different data shape (per-node
   boot/recovery recipes, not membership-set duplicates) — flagged as a judgment call for
   orch-console, not asserted compliant.

Given the one open gap, **backlog#65 was NOT declared fully done.** Reported all of the
above to orch-console at **bus #46245** (reply-to #46239) and asked them to rule: ship
as-is with 2 documented exceptions (matches `lane_token_resolver.py`'s existing precedent),
or land the `MONITOR_SINGLETONS` fix first. Per the commitment's own `on_fire` instruction
("report, then discharge and insert the NEXT checkpoint"): discharged `held_commitments`
**#81** (`discharged_by='cc-substrate'`, full evidence in `discharge_note`) and inserted
checkpoint **#91** (due 2026-10-01 ~01:00Z, `source_ref` `op#19091/22298/42909, cp#81`)
carrying exactly that open question forward.

**Also surfaced this session, not yet actioned — lower priority (P2), pick up next if
nothing else lands first:** bus #45730 flags that `scripts/bus_send.py`'s `is_new_ask`
INSERT and `scripts/console_assign.py`'s equivalent (~line 99) both write `operator_asks`
rows that silently default to `triage_state='captured'` post-migration-082, polluting
Musa's digest "not yet sorted" count / pager with console-delegation rows that aren't real
untriaged asks. Orch-console proposed two fix shapes (pre-triage at insert vs. link to the
originating ask) and an explicit test ("a console --req decision creates no digest-visible
or pager-visible row") — investigation started, no code changed yet.

**Unrelated noise surfaced this session via stale async task notifications — already
resolved, no action needed:** an old fork (`feat/lane-claude-md-onboarding`, PR #165,
"onboard oeh as a fleet lane") completed and merged back on 2026-09-26; its
task-notification just arrived late. A `lane_wedge_watchdog` test run
(10 failed / 53 passed) failed on stale pooler credentials (`password authentication
failed for user "postgres"`) in that subprocess's own env — this session's own direct DB
writes (discharge, checkpoint insert, bus send) all succeeded moments later using a freshly
`dotenv_values`-sourced `DATABASE_URL`, so this reads as that one subprocess's stale
inherited env, not a live credential outage; worth a glance if it recurs, not chased here.

**RULED (bus #46247, orch-console, same day): backlog#65 closes after ONE small PR** that
enforces the exceptions in code, not just writes them down — (1) MONITOR_SINGLETONS stays an
intentional subset but needs a code comment saying why + a test that it's ⊆
`protected_agent_ids()`; (2) the two per-node recipe maps aren't gaps but need the same
subset test (their keys ⊆ the registry, every monitored/recipe'd singleton covered); (3)
`lane_token_resolver._NO_POINTER_SINGLETONS`'s existing exception stands, folded into the
same test. quality_gate shadow→enforce is explicitly a SEPARATE decision (bring G1/G4/G5/
G6/G7/G8/G10's evidence-wiring needs as its own proposal) — not part of #65.

**Done, same session:** added the explanatory comment to `MONITOR_SINGLETONS`
(`nervous_system/lane_wedge_watchdog.py`) and `tests/test_singleton_registry_parity.py`
(5 tests: MONITOR_SINGLETONS ⊆ registry, MONITOR_SINGLETONS ⊆ `_SINGLETONS` keys,
`_SINGLETONS` keys ⊆ registry, `switch_singleton_token.sh` NODE REGISTRY labels ⊆ registry,
`_NO_POINTER_SINGLETONS` ⊆ `protected_tmux_sessions()`) — all green, plus
`test_lane_wedge_watchdog.py` (63) and `test_protected_agents_registry.py` +
`test_lane_token_resolver.py` (43 combined) still green. Committed `3efe25a` on
`feat/operator-asks-triage`, opened **PR #220** against `fable/substrate-safe-fixes`.

**Also noted, orch-console flagged separately:** cc-substrate has no `agent_status` row and
`cc-substrate-1` isn't in the `agents` table (bus_send rejected it) — `cc-fleet-health` is
arranging a relaunch with a fresh env; coordinate with it at a seam, do not self-fix.

**DONE (2026-09-30 ~01:30Z): PR #220 merged** (squash `dbee4d8` on `fable/substrate-safe-fixes`,
CI red 30/30 exact-match trunk via subset-rule). Branch had drifted from trunk since #217's
squash rewrote/collapsed this very handoff file in a way my later commits didn't inherit
(the squash-orphans-dependent-work pattern) — merged trunk into the branch first, resolved
the one conflict (this file; `lane_wedge_watchdog.py` auto-merged clean), reran all 106
relevant tests green, then merged. Both live checkouts fast-forwarded clean (Mini
`b8da24e..dbee4d8`, gzb `442d0b2..dbee4d8`). Reported at bus **#46253** (reply-to #46247).
**backlog#65 (Substrate ihsanification programme, op#19091/22298/42909) is CLOSED** by the
terms of orch-console's ruling. `held_commitments` **#91 discharged**; no further checkpoint
inserted (the programme item is done, not blocked — nothing to carry forward). quality_gate
shadow→enforce remains explicitly open as its OWN future decision (bring G1/G4/G5/G6/G7/G8/
G10's evidence-wiring needs as a separate proposal), not part of #65.

**On waking: reconcile unread bus first (Option B). If orch-console has replied to #45717's
pooler-capacity resume-eligibility, action that. Otherwise: pick up bus #45730's triage-gap
fix (bus_send.py/console_assign.py's operator_asks inserts defaulting to
triage_state='captured') next — lower priority, nothing else currently outstanding.**

## RECONSTITUTE HERE (2026-10-13 11:32Z) — PAUSED (budget P0, checkpoint-and-idle), clean seam, no WIP

**Why paused:** Syed weekly budget ~87% / runway to ~18:30Z today (Musa op#29716). orch-console
APPROVED (bus #65890) a wave-1 checkpoint-and-idle pause of several lanes incl. substrate-1 to
extend runway until GLM capacity lands, then relaunch fresh on GLM (glm-5.3) via
`switch_lane_token.sh --fresh` (see PR #395, merged, shipped same wave) and verify a real first
tool-call turn. cc-fleet-health (bus #65893, P1) nudged the pause into effect. This is a clean
seam, not an interruption mid-task — see below.

**Work this session (sequence-drift self-heal, bus #65636→#65639→#65648→#65719) is DONE and
MERGED, nothing in flight:**
- Diagnosed a real substrate bug: plain `INSERT` into `held_commitments` with an explicit id
  collided on the sequence (table at max(id)=418, two lanes both inserted explicit-id #418/#419
  for the same reminder) — root cause is `SERIAL`/`GENERATED BY DEFAULT AS IDENTITY` silently
  accepting explicit ids without advancing the backing sequence.
- Built step 6 of `fleet_health.py`'s sweep: `sequence_drift()` — scoped self-heal
  (`setval(seq, max(id))`) over substrate tables with serial/identity ids, logged, tested.
  Added the "never write explicit ids into serial/identity inserts" line to this repo's
  CLAUDE.md (now present in the fleet doctrine "Rules" section, held_commitments #418/#419 +
  bus #65636/#65639/#65648 citation).
- orch-console gate review (bus #65719) on PR #394 (head `4ace21c`): CHANGES REQUESTED —
  approach + doctrine line were right, but F1 MUST-FIX: `setval()` is not transactional, so an
  unscoped `sequence_drift(cur)` call inside a test that does `conn.rollback()` would setval
  real drifted sequences on whatever `DATABASE_URL` the test env points at, permanently — the
  "zero residue on the real substrate" docstring was false as written.
- Fixed in commit `9cf94dc` ("fix(fleet-health): sequence self-heal gate fixes (bus #65719)") —
  addressed the gate's must-fix items so the test suite cannot setval a real/production
  sequence as a side effect.
- **PR #394 is MERGED** (merge commit `b03d2d5`, base `fable/substrate-safe-fixes`, merged
  2026-10-13 09:44:15Z) — verified via `gh pr view 394`: both commits `4ace21c` and `9cf94dc`
  are in the merge, and `origin/fable/substrate-safe-fixes` HEAD (`935f928`, PR #395) is a
  descendant of the merge commit, so trunk has this work.
- Local worktree (`orchestrator.wt-cleanup`, branch `fix/substrate-sequence-drift-self-heal`)
  is clean: `git status --short` shows only pre-existing untracked report artifacts unrelated
  to this work (`reports/cc-substrate-boot.txt`, `reports/console-deploy/1700334a743a2955/`,
  a stale `.bak` of this very handoff file, `reports/tg_media_shadow_mark_reversal_*.json`) —
  none are mine to clean up, none block anything. No uncommitted diff anywhere in the tree.
  No open PR for this branch (correct — #394 already shipped it; the local branch is just the
  worktree's checked-out ref, safe to leave as-is or repoint on reconstitution).

**Inbox at pause time:** `python3 scripts/lib/my_inbox.py` → only unread row was 65893
(cc-fleet-health's pause nudge itself), now actioned by writing this checkpoint. No other
outstanding bus items, no held_commitments owed by this lane, no backlog item open against
cc-substrate.

**On waking (fresh, on GLM per the relaunch plan):** (1) reconcile
`agent_messages WHERE to_agent IN ('cc-substrate','cc-substrate-1') AND read_at IS NULL` first
(Option B doctrine) — expect cc-fleet-health's relaunch-verification turn and whatever
orch-console posts about wave-1 progress; (2) confirm this handoff's "DONE and MERGED" claim
still holds (`gh pr view 394`) before trusting it — a verification has an expiry; (3) no
standing task is owed beyond that — pick up whatever the bus has next (likely wave-1
GLM-move verification itself, or the operator-asks triage-gap item noted upthread, bus #45730,
P2, still not actioned as of this writing). Nothing destructive or half-done was left behind.

## RECONSTITUTE HERE (2026-10-17 19:18Z, bus #70413) — supersedes the 2026-10-13 pause section above; clean, no WIP

**The 2026-10-13 pause above is stale** — this lane relaunched, reconciled, and has since run
several more work items to completion. Current state:

- **PR #428** (`fix/tg-out-dead-batch-receipt`, merged `e19be11`): orch-console finding (bus
  #70262) — `tg_out`'s dead-row age-cap purge was posting one bus receipt per row during a bulk
  drain, flooding orch-console's inbox. Fixed: `replay_once_then_purge()` now batches all stale
  purges from one drain pass into a single summary note (`_post_stale_batch_summary`); fresh
  (non-stale) failures still get their own immediate receipt since those are rare, one at a
  time. 28/28 targeted tests pass, full suite 5156 passed/609 skipped/1 xfailed. Live.

- **PR #429 → #430, a real production incident, fully closed:** orch-console asked (bus #70394,
  op#31405) to activate `coffeemedia` on the shared `WINGMENDEVBOT_TOKEN` testbed
  (migration 109, gate #70398). Both of us checked oeh/angullia stayed off but missed that
  `coffeemedia` was ALSO pinned via `INGEST_CHANNELS` in `scripts/boot_nazim_ingest.sh` on
  Sheikhs-Mini — enabling it made the Mini's pinned poller and the hub's (gzbai)
  enabled-driven poller both hit Telegram `getUpdates` for the same token simultaneously.
  Real `HTTP 409: Conflict` storm, confirmed in `logs/nazim-ingest.log` and auto-paged by
  ingest's own watchdog (bus #70399, P1) — same failure class as `oeh` previously (bus
  #43775/#43833, PR#178). Wrote migration 110 (revert to `enabled=false`), orch-console granted
  an emergency gate (#70408, owning the #70398 miss explicitly) with two conditions: apply via
  gated `apply_migration.py`, and confirm the 409s stop on BOTH hosts + poll-health recovers
  before reporting back — NOT to re-enable until a further gate fixes the pin conflict.
  Applied the revert, restarted `nazim-ingest` twice, then verified via TWO independent sources:
  `logs/nazim-ingest.log` (last 409 at 19:11:25Z, none since) and the `ingest_poll_health` table
  (per-host, per-channel) — Sheikhs-Mini rows for all three channels clean at
  `consec_errors=0`; gzbai's `coffeemedia` row frozen at `consec_errors=16` (not literally
  reset — once `enabled=false` propagated, gzbai simply stopped attempting that channel, so
  nothing resets the counter; flagged that literal-wording gap to orch-console rather than
  overclaiming a reset that wasn't observed). gzbai has no oeh/angullia rows at all — those
  never conflicted. `bot_channels` confirms coffeemedia/oeh/angullia all `enabled=false`.
  PR#430 merged (`fed979b`), live checkout fast-forwarded. Reported back bus #70413
  (`--reply-to 70408`). **`coffeemedia` stays `enabled=false`** pending orch-console's separate
  gate on the `INGEST_CHANNELS` pin-list fix — do not re-enable it without that.

**Inbox at this writing:** nothing unaddressed — `agent_messages` for `cc-substrate`/
`cc-substrate-1` top 3 by id are #70408 (acted on, closed via #70413), #70398 (acted on),
#70394 (acted on). No standing task owed. On next wake: reconcile inbox first (Option B
doctrine), and if genuinely idle, pick up bus #45730 (operator_asks triage-gap, P2, still
unactioned as of this writing) — low priority, nothing else outstanding.
