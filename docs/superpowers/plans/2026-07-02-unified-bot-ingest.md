# Unified Bot Ingest — implement BOT-INGEST-TOPOLOGY-001

**Date:** 2026-07-02 · **Owner:** cc-orchestrator · **Governance:** cai — **RATIFIED as implementing design, CAI-RESP-357 (msg #5023)**, with three binding amendments folded in below:
- **A1 idempotent ingest:** at-least-once redelivery (un-acked-offset fail-safe, itself REQUIRED) pairs with mandatory dedupe at the LOG step — UNIQUE (channel_key, telegram_update_id); nudges + responder dispatch key off the deduped row.
- **A2 transport/brains isolation:** ingest.py per-inbound work ends at LOG → GATE → ROUTE (nudge or enqueue). Responder execution (LLM/pgvector/persona) runs as a separate drain unit off the durable log — never inline in the poll loop. 3 units total (ingest + tg_out + responder-runner), still collapsing ~13.
- **A3 Zahidah log destination (P3 gate):** her channels don't cut over until resolved against ZAHIDah-DATA-ISOLATION-001 (operator-reserved); `log_target` column added NOW; at minimum RLS-scope her rows to her sandboxed responder.
Also binding: nudge-only injection universal (supersedes 150-char preview; CAI-RESP-253-compliant); watchdog MUST alert on ingest DB-connect failure; send-script deletion is first-class work (cai will check the aliases died). Execution grant at window close 2026-07-02 21:05Z upon naming the P1 migration file: **`migrations/014_bot_channels_ingest.sql`**.
**Trigger:** Mac Mini→MacBook interim cutover exposed the cost of bot sprawl concretely: only 1 of ~6 Telegram entry points survived the migration (tg-bridge); mizan/mamadah/nutri-study/wingmendev are dark because each is its own bespoke module + launchd plist + offset dotfile + send script + token. Direction is already ratified (bot channels = config rows → one ingestion layer → substrate → concern-agent drains async; never through orch); this plan is the concrete design.

## 1. Problems observed (why now)

1. **Copy-paste stack per channel.** `cai_bridge.py` self-describes as "a clean copy of tg_bridge.py adapted…"; `irsyad_support_bridge.py` is a third variant. Each clone re-solves 4096-chunking, offset persistence, chat-id gating, inject-verification — and they drift (the 2026-06-19 wrapped-paste lessons live in some bridges' code, others' comments only).
2. **Uneven delivery guarantees.** Option B (durable log = truth, keystroke = signal-only, reconciliation cursor) protects the operator channel; other channels have logging but not uniformly the reconciliation discipline; standalone bots (mizan, mamadah, nutri, wingmendev) are separate apps with their own (or no) durability.
3. **Ops state on the machine, not in the substrate.** Offsets are dotfiles; config is constants-in-code; plists are hand-copied. Machine migration = re-porting N units. Watchdog false-alarms on decommissioned bots because bot existence isn't data.
4. **8 send/nudge scripts** re-implementing outbound (tg_send, tg_send_file, tg_send_mamadah, tg_send_mamadah_file, cai_send, irsyad_support_send, lane_nudge, _tg_chunked_send).

## 2. Design

### 2.1 `bot_channels` registry (substrate table)
```
channel_key        text PK          -- 'operator-orch', 'cai-channel', 'gazzabyte-irsyad', 'mamadah', 'nutri-study', 'mizan', 'wingmendev'
token_env_key      text NOT NULL    -- indirection: .env var NAME, never the token itself
mode               text NOT NULL    -- 'agent-session' | 'ai-responder' | 'log-and-route'
inject_target      text             -- tmux session for agent-session mode ('orch','cai')
inject_prefix      text             -- e.g. '📱 Operator (Telegram): ', '📩 Irsyad-Support (Gazzabyte group): '
responder_ref      text             -- for ai-responder mode: persona/handler module key
allowed_chat_ids   bigint[]         -- deny-by-default; empty = accept none
allowed_usernames  text[]           -- scoped-partner auth (Desmond Shen pattern)
group_routing      jsonb            -- group-chat routing rules (multi-sender tagging)
channel_tag        text NOT NULL    -- tag stamped into operator_messages
log_target         text NOT NULL DEFAULT 'substrate'  -- A3: per-channel log destination (Zahidah channels may log to her isolated project)
enabled            boolean NOT NULL DEFAULT false
poll_offset        bigint           -- getUpdates cursor IN THE DB (survives machine moves)
```
Plus a dedupe key on the log step: UNIQUE (channel_key, telegram_update_id) — A1: at-least-once redelivery must never double-log/double-nudge/double-reply.
Adding a bot = BotFather token + one INSERT. Decommissioning = `enabled=false` (watchdog reads the same row → no false alarms).

### 2.2 One ingest daemon (`nervous_system/ingest.py`)
Single asyncio process; one long-poll task per enabled channel. Per inbound, in order:
1. **LOG** to `operator_messages` (channel_tag, chat_id, sender) — durable first, always, before any routing. Nothing else is load-bearing.
2. **GATE** by allowed_chat_ids/usernames (deny-by-default, enforced from config, not per-module constants).
3. **ROUTE** per mode:
   - `agent-session`: nudge the target tmux session. **Nudge-only, never payload** — inject "📱 N unread on <channel> — reconcile operator_log". Kills the 150-char cap + paste-verify fragility class entirely; Option B (log+reconcile) becomes the universal contract for every agent-bound channel.
   - `ai-responder`: dispatch to the channel's responder (persona prompt + its own context: mamadah pgvector notes, nutri-study corpus, mizan flow). Responder answers directly on the channel — conversational UX unchanged. Responders are async drains off the log: crash-safe, at-least-once.
   - `log-and-route`: perimeter channels (Gazzabyte): log + route to hub, no auto-reply (hub owns outbound). Unchanged behavior, now config.
4. One launchd/systemd unit replaces ~6.

### 2.3 One outbound gateway (`nervous_system/tg_out.py` + `tg_out` queue table)
`tg_out(channel_key, text|file, chat_override?)` owns chunking, retries, outbound logging. Optionally queue-backed (INSERT → gateway delivers) so headless agents get at-least-once outbound + audit, symmetric with inbound. Existing send scripts become thin aliases, then get deleted.

### 2.4 What deliberately does NOT unify
Tokens and trust boundaries. CAI-RESP-332 stands: external tenants keep dedicated scope-locked bots, never the fleet bridge bot. This consolidates machinery, not perimeters — the deny-by-default gating moves from per-module constants into enforced config.

## 3. Channel impact (conversational parity)

| Channel | Who | Today | After | Same-or-better? |
|---|---|---|---|---|
| operator-orch (@wingmennorchbot) | Musa + Desmond Shen (scoped) + groups | tg_bridge inject w/ 150-char cap + verify hacks | nudge-only + Option-B reconcile | Better: no more garbled/lost long messages; Shen's scoped auth + group tagging preserved as `allowed_usernames`/`group_routing` config |
| cai-channel (@cai_orch_bot) | Musa | cai_bridge (copy) | same daemon, `inject_target='cai'` | Same UX, minus a whole module |
| gazzabyte-irsyad | Gazzabyte vendor group | perimeter bridge, log+route, no auto-reply | `log-and-route` mode, identical semantics | Same by design (perimeter posture unchanged, CAI-RESP-332/345) |
| mamadah / nutri-study | Zahidah | standalone conversational bots — **currently DARK post-cutover** | `ai-responder` mode; persona + pgvector/second-brain logic kept as the responder | Better: they come back online at all; conversation survives restarts/migrations via the durable log; shared retry/chunking; watchdog notices outages |
| mizan / wingmendev | (varies) | standalone bots, dark | `ai-responder` / `agent-session` per bot | Same as above |

Key point: the ingestion layer changes **transport**, not **brains**. Each conversational bot keeps its own persona, context source, and reply style as a responder bound to its channel row. Users see the same bot, same conversation — with better delivery guarantees behind it.

## 4. Phasing

- **P1 — schema + daemon skeleton:** `bot_channels` + `ingest.py` with `agent-session` mode; migrate operator-orch + cai-channel first (the two live ones). Old bridges retired as each channel cuts over.
- **P2 — outbound gateway:** `tg_out` module + queue; alias old send scripts.
- **P3 — responder mode:** bring mamadah + nutri-study back as `ai-responder` channels (they're dark today — nothing to preserve, cheapest possible migration). Then mizan/wingmendev.
- **P4 — perimeter + watchdog:** gazzabyte-irsyad cutover; watchdog reads `bot_channels.enabled`.

Fits the Linux-cloud migration directly: the systemd blueprint's ~7 bot/bridge units collapse to ingest + tg_out.

## 5. Asks of cai

1. Ratify this as the implementing design of BOT-INGEST-TOPOLOGY-001 (or amend).
2. Confirm nudge-only injection (payload never typed into TUIs) as the universal agent-session contract — supersedes the 150-char preview convention.
3. Confirm `poll_offset` + config in substrate DB is acceptable (substrate becomes single point of coupling for all channels — mitigations: daemon fail-safe holds getUpdates offset un-acked on DB outage, so Telegram redelivers).
4. Sequencing vs the DB-decomposition Track B work (both touch substrate; ingest tables are SUB-classified and stay in the monolith-→-pure-substrate DB).
