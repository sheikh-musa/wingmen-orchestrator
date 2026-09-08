# Orch-move runbook — Mac Mini → gzb (compute) + wingmen-core (secrets)

**Status:** DRAFT for operator sign-off. No live cutover until Musa OKs the sequence + the marked decisions. (Nazim/console, 2026-08-27.)

**Direction (Musa op17195/17198):** orchestrator COMPUTE → **gzb** (Gazzabyte office server `gzbai` / 192.168.1.114, Ubuntu 26.04, 4cpu/30GB). **SECRETS → wingmen-core** (existing VPS we control, OFF gzb), fetched at runtime. **Orch moves FIRST**, then every other lane (singletons + workers). Access proven: FortiGate SSL-VPN → SSH (openfortivpn headless).

**Blocking gate — CLEARED (2026-08-27, re-verified 2026-08-28):** branch reconcile-before-cutover git gate is green both halves — all commits + real untracked work on origin, branch level with origin (0 ahead / 0 behind) at `bf3d81c`; watchdog 46/46 green (console re-ran post-merge); tree clean; `docs/irsyad-ground-truth.md` now tracked. Secrets host confirmed by Musa = **existing wingmen-core** (op17248). Cutover is unblocked *pending this sequence's sign-off (the 3 decisions below)*.

## 🔴 HARD pre-cutover gate — headless-claude authentication (added 2026-09-05, post-incident; hub cc-orchestrator + orch-console)

**Why:** the 2026-09-05 hub flip to gzb lost the operator bridge for ~1.5h. The lease, all systemd daemons, and agent_status all read GREEN while the actual hub Claude session was stuck at the OAuth **login screen** on the fresh gzb clone. `host-alive`/lease/heartbeat green does **NOT** prove `session-alive` — a fresh clone's `claude` was never onboarded, so it swallowed the env token and forced interactive OAuth; ingest's operator-wake nudges then landed on a dead login prompt. Root gaps found: `~/.claude.json` `hasCompletedOnboarding=false` (+ no theme → onboarding wizard eats the token), no trust/bypass acceptance, `--continue` **exits** when the project dir has no prior conversation, and a zero-size detached tmux pane crashes the TUI.

**Gate (must ALL pass on the target host BEFORE marking hub OR any lane 'moved'):**
1. Seed `~/.claude.json`: `hasCompletedOnboarding=true` + a theme set; trust + bypass-mode accepted for the project dir.
2. Launcher passes a real terminal size to `tmux new-session` (`-x 220 -y 50`) — never a zero-size detached pane.
3. `--continue` is **conditional** on a prior conversation existing for that project dir (or omitted on first launch), else the session exits on boot.
4. **Smoke-test the session takes one real turn** — reaches the composer authenticated (NOT a login screen) and responds — before it is declared up. A green heartbeat/lease/daemon is insufficient proof and will LIE about a login-stuck session.

**Reference implementation:** the gzb hub fix — `/home/gazzai/orch_supervisor.sh` (`-x/-y` + conditional `--continue`) and `/home/gazzai/.claude.json` (onboarding/theme/trust/bypass); backups at `*.bak-prerecovery`. Reuse this pattern per lane in Phase 2.

**Companion gap (hub liveness signal):** the gzb hub supervisor has no agent_status heartbeat writer — the Mini was the only writer (`host=Sheikhs-Mini`), which is what masked the dead session. Fix = a gzb-side dead-man's-switch heartbeat (beats `host=gzbai` only while the `orch` tmux session exists), paired-handoff with stopping the Mini writer to avoid host-field flap. Tracked separately (HOLD #1B).

---

## What "orch" concretely is (measured, not named)

- **~45 launchd daemons** (`dev.wingmen.*`): the always-on core — unified `ingest` + per-channel ingests (nazim/irsyad), `tg_out`, bus-notify (cai/finance/nazim/fleet-health), watchdogs (lane-wedge, context-health, priority-sla, sre-liveness, repo-context, disk), `fleet-console` (web), coord-pane-publisher, session-costs-writer, tripwires, daily-backup, media-cleanup, gazzabyte-autopublish, weekly-alert-relay, shipforge tunnel+worker.
- **12 tmux sessions**: 5 singletons (`cai`, `fleet-health`, `quality`, `nazim`=this console, `storefront`) + 7 worker lanes (`cosem-adcda`, `exams`, `ihsanos-platform`, `irsyad-bankimport`, `irsyad-coord`, `irsyad-student`, `irsyad-tabung-jumaat`).
- **Lease rows** (substrate DB): `orch_lease` (hub), `fleet_health_lease` (SRE) — the singleton-pen owners. DR/cutover = CAS-take, loud.
- **Secrets**: `.env` (all tokens/DSNs), the OAuth token-pointer files, `~/.wingmen/keys/*`.

## ⚠ Hard constraint the "every lane on gzb" direction must bend around

`dev.wingmen.arabic-ocr` (sovereign Baidu OCR) runs on **Apple MPS**; Whisper voice + ffmpeg/PyAV video are the same class. **gzb is Ubuntu — no Apple Silicon, these cannot run there.** → **DECISION 2 below:** keep ONE Mac as an Apple-compute node for the MPS workloads (per `reports/vps-migration-spec-20260730.md`), or explicitly drop sovereign-OCR/voice/video. Everything else (the daemons, lease-holders, the CC lanes) is gzb-portable.

---

## Phased sequence

### Phase 0 — Prep (no cutover, fully reversible)
1. **wingmen-core = secrets host.** Move `.env` + key-pointers off the Mini onto wingmen-core (a store we control). Nothing on gzb disk.
2. **Secrets-fetch mechanism (DECISION 1).** *Proposed:* a boot bootstrap on gzb pulls the secrets bundle from wingmen-core over the tunnel into **tmpfs (RAM, never disk)**; daemons `source` from there — minimal change to the current `source .env` pattern, and secrets vanish on power-off (correct for a client's box). Auth = a dedicated gzb→wingmen-core SSH key (or scoped fetch token); wingmen-core firewalled to gzb's egress IP. *Alt:* a lightweight secrets API on wingmen-core (more setup, per-secret ACL) — heavier than we need now.
3. **gzb base:** python3.9 + venv, `git clone` the repo at the pushed ref, tailscale join, launchd→systemd unit equivalents for the daemons.
   - **Credential hygiene (from the ignored-file triage):** 15 live secret files ride the wingmen-core track; **8 stale credential snapshots** (old `.env.bak.*` / `.was` token pointers) get **secure-destroyed** — HOLD for one operator confirm before destroying (his credentials, irreversible). And **proactively rotate the likely-Musa OAuth pool token**: a live-shaped `sk-ant-oat0…` sat at rest in a gitignored diff (now scrubbed). Containment is strong — Mini-local, gitignored, never pushed, and SRE-verified 0 tokens on the bus — so this is **proactive hygiene, not an emergency**: rotate at a convenient window or fold into the migration's token handling. Note it re-tokens every body on that pool.
4. **Reachability verify (DECISION 3 — needs your ack before I touch gzb):** VPN in and read-only confirm gzb→wingmen-core (secret fetch dry-run) and gzb→Supabase (silo DSNs, ~150ms acceptable — async/polling workloads). No daemons live yet.

### Phase 1 — Orch core cutover ("orch first")
5. Bring the always-on daemons up on gzb (ingest, tg_out, bus-notify, watchdogs, health, console). **Mini's orch stays up as warm fallback — nothing deleted.**
6. **Lease handover, deliberate:** CAS-take `orch_lease` + `fleet_health_lease` to the gzb bodies (loud). Single-owner invariant holds throughout.
7. Verify each daemon healthy on gzb (ingest polling, tg_out delivering, watchdogs heart-beating, console rendering). **Rollback lever = re-point the lease back to Mini/Studio; Mini orch is still warm.**

### Phase 2 — Lanes
8. Move the CC lanes to gzb honoring token pools: **irsyad lanes → musa2**, **cosem → Syed**, **singletons → Musa** (unchanged). Workers are headless; singletons per their boot scripts. **Every lane MUST pass the 🔴 HARD pre-cutover headless-claude authentication gate above (seed .claude.json, real tmux size, conditional --continue, one-real-turn smoke-test) before it is marked 'moved' — a heartbeat alone is not proof (2026-09-05 incident).**

### Phase 3 — Decommission / compute-node
9. Retire the flaky Mac(s) **except** the one Apple-compute node kept for arabic-ocr / Whisper / video (DECISION 2). This kills the Mini↔Studio drift that started the workstream.

## File durability (parallel, SRE-owned)
A host move carries only committed content — untracked AND ignored files drop. Untracked real work: committed (gate b). Ignored real work: SRE is running a PII-vs-internal triage of the 132 ignored `reports/*.md` + data/. Fleet-internal → private internal repo / fleet backup; **client-PII (31 binaries + adcda-recap CSVs) → Musa+cai residency ruling, NEVER a default rsync onto gzb.** SRE is the verify-copy dead-man's-switch (count+checksum at destination + restore-reachable-from-gzb) before cutover. `vps-migration-spec` already preserved.

---

## Decisions I need from you
1. **Secrets-fetch:** OK the boot-pull-to-tmpfs + scoped gzb→wingmen-core auth? (my recommendation)
2. **MPS workloads:** keep one Mac as Apple-compute node for sovereign-OCR/voice/video (recommended), or drop them?
3. **Reachability recon:** OK for me to VPN into gzb now for read-only gzb→wingmen-core + gzb→Supabase verification, or do you want to be looped before I touch gzb at all?

No blind cutover — I execute Phase 1+ only after your OK on these.
