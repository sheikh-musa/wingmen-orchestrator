# Orchestrator compute move: wingmen-core (VPS) → gzb — cutover runbook

**Goal:** move the orchestrator COMPUTE (daemons + live hub session) from wingmen-core
(91.107.235.77) to the Gazzabyte office server `gzb` (192.168.1.114, gazzai). SECRETS
stay sourced from wingmen-core (pulled into gzb tmpfs at boot, never gzb disk). The Mac
Mini stays warm as fallback. Every step is reversible; the Mini + VPS are the rollback.

## Status (2026-09-05)
- **Phase 0 DONE:** gzb runtime = pyenv py3.9.6, node22, psql18, git, tmux (exact venv match).
- **Phase 1 decisions APPROVED** (op "ok to both"): secrets→tmpfs; keep one Mac for MPS.
- **Secrets pipe BUILT:** `gzb:/home/gazzai/fetch-secrets.sh` → `/dev/shm/wingmen-secrets` (RAM only).
- **Clone:** `gzb:/home/gazzai/wingmen/orchestrator` (read-only deploy key).
- **Step 3 VERIFY DONE + ALL-PASS (read-only, zero takeover):** venv + `pip install -r requirements.txt`
  built on gzb; substrate read-only reachable from gzb via tmpfs DSN; ingest/tg_out/wingmen_orch
  import clean, nothing auto-runs (poll loops behind `__main__` guards). Tunnel up/down clean,
  default route intact throughout.
- **Step 4 CUTOVER = THIS DOC. GATED on operator GO (op#19070 "proceed" + timing pending).**

## Safe-access invariant (never violated)
All gzb access is over the split-tunnel: `sudo /usr/local/sbin/gzb-vpn.sh up|down|status`
(routes ONLY 192.168.1.0/24 via ppp0; aborts if the default route would change; 900s dead-man).
**SINGLE-OWNER tunnel** — one operator/job at a time. `ssh gzb` (key `~/.ssh/gzb_ed25519`).
The default route MUST stay `default via 172.31.1.1 dev eth0` (91.107.235.77) before/during/after.

## The one user-visible effect
Between "stop incumbent poller" and "gzb poller live," the operator's Telegram line is quiet
~2–3 min. No messages are LOST — inbound is durable on Telegram's side and the new poller drains
it on start (Option-B durable-log design). Pick a quiet moment with the operator.

---

## PRE-CUTOVER PREP (non-disruptive — can stage ahead of the GO)
Nothing here starts a poller, claims a lease, or writes singleton state. One tunnel-owning job.

0. **HUB-SESSION LAUNCH PREREQ — BLOCKER found 2026-09-05 (op#19083 "go" → held at this gate).**
   Step 3 proved the daemons + code run on gzb, but the *interactive hub session* (the Claude
   thinking node) cannot boot there yet: **Claude Code CLI is NOT installed on gzb** and there is
   **no Max login** (`~/.claude/.credentials.json` ABSENT; `~/.claude.json` + `~/.claude/` dir exist;
   node22/npm/tmux/boot_orch.sh/agent_wake_subscriber all present). Flipping without this = daemons
   move but nobody answers the operator line. Two sub-steps to green:
   - (a) **Install Claude Code on gzb** (node22 present → npm/official installer). Hub can drive. Non-disruptive.
   - (b) **Max-subscription auth on gzb** — the fleet runs on the donated Max account (launchers unset
     ANTHROPIC_API_KEY → use the logged-in `~/.claude` creds, NOT an API key). gzb needs that login:
     either securely copy `credentials.json` from the Mini (like the secrets pipe) OR run `claude login`
     interactively on gzb once. **Sensitive (account creds) → operator/Nazim call; put to operator op#19083.**
   - Then verify a gzb hub session boots (`boot_orch.sh` in tmux `orch`, Max not API), reloads state, and
     receives wake-nudges (`agent_wake_subscriber`) — before the flip.
1. **Sync clone** to the intended revision (current `main`/`fable` as agreed); confirm HEAD.
2. **Config on gzb** (files only, no processes):
   - Wire the tmpfs secrets into the daemon process env: a launch shim that `source`s
     `/dev/shm/wingmen-secrets/.env` (or exports its DSN vars) before exec — because the clone has
     no on-disk `.env` by design and bare `load_dotenv()` no-ops there.
   - Set `ORCH_BODY_ROLE=hub`, `ORCH_TMUX_SESSION=orch`, `ORCH_AGENT_ID=cc-orchestrator` in that
     shim's env (ORCH-TOPOLOGY-001 — gzb must not shadow-claim a pen until the flip).
   - Prepare launch units (tmux `orch` boot cmd; ingest/tg_out service defs) but DO NOT enable/start.
3. **Refresh `reports/session-handoff-NOW.md`** so the fresh gzb hub boots with full in-flight
   context (this is what makes the new-you coherent — same mechanism as any recycle).
4. **Verify prep** (read-only): re-run the step-3 checks post-config; confirm the shim resolves the
   DSN and imports still clean. Tunnel down. Confirm default route intact.

## CUTOVER (disruptive — only on operator GO, at the agreed time)
Single tunnel-owning executor. Announce start to the operator.
1. **T-0 announce:** tg_send operator "starting gzb cutover — your line goes quiet ~2-3 min."
2. **Stop incumbent pollers** on wingmen-core (systemd, NOT launchd — CLAUDE.md text is stale):
   `sudo systemctl stop wingmen-ingest wingmen-tg-out wingmen-agent-wake-subscriber`. This frees the
   Telegram long-poll so gzb's won't 409. (SRE's `fleet_health_lease` stays with cc-fleet-health.)
2b. **STOP THE LEASE-RENEW TIMER (critical — Nazim 37686):** `orch_lease` is renewed on wingmen-core
   by a ROOT systemd timer `wingmen-orch-lease-renew.timer` (from the /root checkout), INDEPENDENT of
   this session. If left running it re-renews the lease back to wingmen-core and fights the CAS. Stop it:
   `sudo systemctl disable --now wingmen-orch-lease-renew.timer` on wingmen-core BEFORE the CAS (Nazim
   37697: stop AND disable — a reboot of the old box must not resurrect it and CAS-fight). (Also stop the
   hub keepalive `wingmen-orch-hub` so this session isn't relaunched here.)
3. **CAS lease → gzb:** on gzb, `orch_lease.py take --as cc-orchestrator --reason "cutover to gzb"`
   (CAS; loud). Confirm holder flips to cc-orchestrator@gzb. Then START gzb's own lease-renew
   (from the single /home/gazzai checkout — NOT a /root split) so the lease stays held by gzb.
   NOTE: a renewed lease proves HOST-alive, not SESSION-alive — the session-chain agent_status
   heartbeat (below) is the session-liveness signal; both are required.
4. **Start gzb daemons + hub:** `systemctl start` the gzb wingmen-ingest / wingmen-tg-out /
   wingmen-agent-wake-subscriber units (after wingmen-fetch-secrets), then the tmux `orch` hub session
   via the gzb hub supervisor (boot reloading session-handoff-NOW.md + boot_briefing). The supervisor
   must run the SESSION-CHAIN agent_status heartbeat writer (Nazim 37686 UPSERT: register/beat-300s/
   offline, identity GUC `app.current_agent_id`, host=<gzb hostname>, tmux='orch'; beat only while
   `tmux has-session -t orch`) — NOT a timer. Ping Nazim on the first beat with host=<gzb> → he
   releases #1B.
5. **VERIFY live from gzb:** send a test tg_send from gzb; confirm it reaches the operator's phone;
   confirm gzb ingest drains any messages sent during the window; confirm no dual-poll 409 in logs.
6. **T+done announce:** operator "hub is live on gzb — try me." Await his ack.

## ROLLBACK (any failure at any step — Mini/VPS warm)
- If gzb poller/hub won't come up or the operator line is dead after the flip:
  1. Stop gzb ingest/tg_out + the gzb hub session.
  2. `orch_lease.py take` back on wingmen-core (reclaim to cc-orchestrator@wingmen-core).
  3. Restart wingmen-core `dev.wingmen.ingest` + `dev.wingmen.tg-out` (launchd).
  4. tg_send operator "rolled back to VPS hub — no change your side." Confirm his line works.
- Rollback is always available because the VPS config is untouched (only its daemons were stopped)
  and the Mini stays warm. No data is at risk — all state is in the shared substrate/logs.

## HARD BOUNDARIES
- Never two concurrent pollers (409 kills the operator line) → stop incumbent BEFORE starting gzb's.
- Never two concurrent `orch_lease` holders → CAS take only, one direction at a time.
- Never touch wingmen-core's default route. Single-owner tunnel. No `supabase db push`.
- `fleet_health_lease` (SRE) is NOT part of this move — leave it with cc-fleet-health.
- The flip is operator-gated; never self-execute off a background/task event.
