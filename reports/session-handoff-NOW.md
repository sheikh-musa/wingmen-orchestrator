# Hub session handoff — 2026-09-05 ~15:00Z (cc-orchestrator, VPS/wingmen-core → migrating to gzb, Opus)

Read this, then CLAUDE.md. You are the fleet hub. This captures in-flight state so a fresh you has continuity. **Verify-not-assert every "done" before repeating it.** Refreshed 2026-09-05 (prior was 08-18, badly stale — that staleness is why the operator flagged "recycling without reconstituting"; keep this current).

## FIRST ON BOOT
1. Full-drain BOTH inboxes: `agent_messages to_agent='cc-orchestrator' read_at IS NULL` AND `operator_log.unprocessed()`. Act, then stamp **read_at AND responded_at** (sla-watchdog fires on responded_at IS NULL).
2. **If you booted on gzb (hostname != wingmen-core):** THE FLIP HAPPENED. Verify: `orch_lease` holder_host = your gzb hostname; ingest/tg_out/agent_wake running here; send Nazim (orch-console) the post-flip row (holder_host, ssh target, lease-renew-timer stopped+disabled on wingmen-core confirmed, first heartbeat host=gzb); tg_send the operator "hub live on gzb — try me"; keep wingmen-core WARM for rollback until the operator/Nazim OK decommission. See `docs/orch-move-runbook.md`.

## STANDING RULES (binding)
- **🔴 NEVER open a client-attached file** (CAI-1034/1037): no open/parse/head/cat of anything under `logs/tg_media/` or on a client channel (this includes the operator's OWN screenshots — work from the caption). Shape → ask orch-console; data → DB-side against the silo. No client PII to the bus.
- **Stamp BOTH read_at AND responded_at** on handled bus messages — sla-watchdog re-wakes ~15min on responded_at IS NULL. Surgical stamps only (never mark_handled_through across channels — sweeps other bodies' rows).
- **Answer only HUB-routed inbound**: `operator_log.unprocessed()` tag='orch-channel' is yours. Other tags are other bodies (hk-editor=cc-shipforge, cai-channel=cai, nazim-console/finance-console/cosem-*=their owners). Don't pick up other threads.
- **Address the INSTANCE not the family** (`to_agent='cc-cosem-adcda'` etc.). Bus body via file-read + psycopg params (NO backticks/literal-% in inline SQL — both bite).
- **Client sends are body-scoped**: hub has NO HK/irsyad send tokens — route HK sends via Nazim (`hk_send.sh`, Mini), irsyad via coord. tg_send.sh = operator only.
- **Never `supabase db push`**; migration-apply = fetch@SHA → hash==expect-blob → per-silo DSN guard → apply per file txn contract → enumerate proacl → PGRST reload → cai verify.
- **Single-owner tunnel** for gzb (`sudo gzb-vpn.sh up/down`); never touch wingmen-core's default route.

## TOPOLOGY / TOKEN
- Hub runs on **wingmen-core** (91.107.235.77, tmux `orch`, user wingmen, ORCH_BODY_ROLE=hub). Daemons = systemd `wingmen-ingest`/`wingmen-tg-out`/`wingmen-agent-wake-subscriber`; lease renewed by root `wingmen-orch-lease-renew.timer` (INDEPENDENT of the session — a fresh lease = HOST alive, not session alive).
- Hub auth = **Musa** OAuth (`.orch_default_token` pointer → `~/.wingmen/keys/musa-oauth-token`, fp `68142948c003`). boot_orch.sh loads it over the .env Syed fallback.
- **Nazim = orch-console** (Mini, live). Owns infra/console; paces the fable cleanup behind the flip; has wingmen-core root (installed my scoped sudoers `/etc/sudoers.d/wingmen-units`). cc-fleet-health = SRE (Mini), recovered from a 09-05 false-down (stale lease ≠ dead body — see [[reference-stale-lease-not-dead-sre]]).

## IN-FLIGHT (2026-09-05)
- **🔴 gzb MIGRATION FLIP — the active priority.** Operator gave live GO (op#19111). ALL gates green: Nazim parity confirm (bus 37727 — fable IS the intended prod daemon runtime + supersedes the live divergent tree; finance-console carve-out ported a8fb6ac; resolve_cc_identity in fable). Phase A DONE (gzb service units installed+validated, DISABLED; secrets pipe; hub supervisor; claude v2.1.247). Root gate CLEARED (Nazim installed scoped sudoers 37754). **NEXT = execute Phase B** per `docs/orch-move-runbook.md`: pull gzb clone to fable tip (4b9ace5), `git pull`; T-0 announce operator; `sudo systemctl disable --now wingmen-orch-lease-renew.timer` + `sudo systemctl stop wingmen-ingest wingmen-tg-out wingmen-agent-wake-subscriber wingmen-orch-hub` on wingmen-core; CAS `orch_lease.py take` from gzb; `systemctl enable --now` gzb daemons + start gzb hub (session-chain agent_status heartbeat writer per Nazim's UPSERT, host=gzb); VERIFY operator gets msg FROM gzb + no 409; rollback = restart wingmen-core services + reclaim lease (Mini/VPS warm). Was HELD 09-05 15:00 to address the operator's reconstitution concern (this refresh) — reconfirm with operator before the ~2-3min line blip.
- **cosem-adcda archive**: PRs #214 (fail-open fix) + #215 (English-name stale-OCR-badge) merged+deployed. Real batch archive HELD on operator naming the graduated batch (ADCDA 18-19 / Al Ain 2 / SHOWCASE) + a with-Storage manifest GO. Guard is fail-closed (won't delete un-exported). See lane cc-cosem-adcda.
- **cosem CA new-course flow**: plan ready, grounded in Hariz's cosem-platform build (4-step import wizard). HELD on 2 operator decisions: (1) admin self-service batch creation? (2) real bulk namelist upload? Safe items (#2 self-select, #4 cache, #5 server gate, #6 zod) scopable.
- **HK (Hadramawt Kitchen / Shazz, she/her)**: hours edit done+verified live on the revamp pilot (hadramawtkitchen-sg.vercel.app). Clarification sent (she edits the revamp, not her live WordPress; signs off → DNS cutover). Her real live site = hadramawtkitchen.sg WordPress, fleet has ZERO access. See [[project-hk-conversational-editing]].
- **kuehheritage pilot**: preview live (kuehheritage-live.vercel.app), same owner as HK; domain = kuehheritage.com (already their live WordPress). Operator flagged it looks too much like HK — awaiting his design direction (own identity vs recolor) before shipforge redesigns. See [[project-kuehheritage-pilot]].
- **Nazim held behind the flip**: #1B (stops Mini writing my agent_status), PRs #85-#91 (retired-bridge deletions), Mini 6-pin re-point. Released when I confirm flip complete + gzb holds lease. Soft freeze; he lists every SHA (tip 4b9ace5).
- **OLDER money/mig in-flight (mig183/180, C5 order-verify, #363 preparer-login) from the 08-18 handoff**: status UNKNOWN as of this refresh — VERIFY via `strategic_decisions`/the bus/cai before assuming pending OR done. Do not act on them as stale facts.

## MECHANICS
- Migration DSNs: `GOUMLYNE_DATABASE_URL` (irsyad goumlyne), `IHSANOS_PROD_DATABASE_URL` (ceayj), substrate=`DATABASE_URL`.
- gzb: `ssh gzb` (gazzai@192.168.1.114); clone /home/gazzai/wingmen/orchestrator; secrets tmpfs /dev/shm/wingmen-secrets (run fetch-secrets.sh); gazzai sudo pw = operator_messages 17159 pw-field (never echo).
- Delegate heavy/tunnel work to ONE subagent (single-owner tunnel); keep hub context for coordination/decisions.
