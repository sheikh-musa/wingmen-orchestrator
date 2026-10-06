# Audit C — Operator surface (how Musa interacts with the fleet)

Fable substrate audit 2026-10-06 (Musa op#26589/26590). Fork C. READ-ONLY: substrate DB reads, `nervous_system/`, `scripts/`, `launchd/`, `logs/`, `reports/`. No writes outside this file. Numbers as of 2026-10-06 ~19:05Z. Client names and message bodies deliberately not quoted.

Labels: **verified** = measured from DB/logs/code; **inferred** = reasoned from those.

---

## 1. Ask ledger (`operator_asks`)

| metric | value |
|---|---|
| rows total / open | 1912 / **871** |
| open by triage_state | ask 323 · **not_an_ask 292** · **captured 254** · done 2 |
| open age | <1d 197 · 1–3d 307 · 3–7d 331 · >7d 36 |
| open `captured` older than 24h (never triaged) | **194** (oldest 2026-10-02) |
| open `ask` with no `delegated_to` | 32 |
| open rows with no `source_msg_id` (phantom delegations) | 27 |
| open duplicates (same first 80 chars) | 12 keys |
| WAITING ON MUSA open | **26** — 5 past `chase_by`, **21 have no `chase_by` at all** |
| closed in 14d without an `outbound_msg_id` | **908 / 1035 (88%)** |
| asks created/day (14d) | 6 → 314/day; **30–45% of each day's rows end as not_an_ask** |
| ask_surface | client-channel 886 · operator 1026 |

**Verified findings**
- C-1 **The ledger is not a ledger of asks.** 292 open rows are already judged `not_an_ask` but never closed; 254 are untriaged `captured` (194 older than a day). `scripts/asks_daily_digest.py` shows only judged text, so these are invisible to Musa but inflate the "N open" headline (digest went 15 → 111 open in 10 days).
- C-2 **"Waiting on you" never re-chases.** 21 of 26 WAITING-ON-MUSA rows carry no `chase_by`; the 5 that do are past it. Ask #44 (2026-09-27) has appeared in every daily digest for 10 consecutive days with no second-form chase (no one-tap, no re-ask in a different shape). The digest is a list that repeats itself, not a chase.
- C-3 **Closure is unlinked from the reply.** 88% of closures in 14 days have no `outbound_msg_id`; `closed_reason` is free text (stale sweeps "done: answered in chat", "not_a_request (migration 083…)", one-off narratives). You cannot answer "which message answered this ask?" from the table.
- C-4 **Over-capture by heuristic.** The pre-classifier opens a row for ~every inbound (client-channel 886 rows); a third to half are later `not_an_ask`. The cost lands on triage (console) and on digest noise. Daily digest runs 10/10 days (launchd 13:00 host-local = 05:00Z), so the *cadence* is healthy; the *content* is the problem.

## 2. Comms load (`operator_messages`, Telegram)

Last 72h, by channel tag:

| tag | inbound | outbound (non-ack) | auto-acks | avg outbound chars |
|---|---|---|---|---|
| nazim-console (Musa ↔ Nazim) | 297 | 408 | **54** | 644 |
| gazzabyte-irsyad (client) | 148 | 174 | 0 (suppressed) | 412 |
| cosem-exams (client) | 73 | 83 | 0 (suppressed) | 931 |
| cosem-caai (client banter) | 45 | 19 | — | 376 |
| orch-channel (hub ↔ Musa) | 2 | 1 | — | 106 |

Today (24h) on nazim-console: Musa sent **106** messages (36 contain "?"; 10 were ↩️ replies to a specific message); Nazim sent **150** (30 auto-acks). Non-ack length today: <200 chars 4 · 200–600 51 · 600–1200 88 · >1200 23 (avg 749). **16 of today's outbound end in a question to Musa.**

Reply latency (inbound → first non-ack outbound, same tag, 72h):

| tag | n | median | p90 | >60 min / unanswered |
|---|---|---|---|---|
| nazim-console | 297 | **0.9 min** | 2.9 min | 0 |
| gazzabyte-irsyad | 148 | 0.7 | 1.8 | 1 |
| cosem-exams | 73 | 0.9 | 6.3 | 0 |
| cosem-caai | 45 | 294 | 314 | 34 (policy: banter, bot not addressed) |
| coffeemedia | 14 | 12.9 | 16.3 | 0 |

`handled_at` stamping on nazim-console: median 1.6 min, p90 4.4 min, 0 unhandled.

**Verified findings**
- C-5 **Nazim is fast but loud.** Median reply <1 min with zero misses, yet 1.4 outbound per inbound, 60% of replies over 600 chars, and the hub channel is effectively silent (1 outbound in 7 days) — Musa's entire operating picture arrives as long prose on one DM thread. 16 questions/day are typed answers that could be taps.
- C-6 **Auto-acks are pure noise on nazim-console.** 54 in 72h, 35 today, arriving on average **78 s after** the inbound — while the real reply lands at median 54 s. The ack text invites "Reply URGENT", which no one needs when the human reply is faster than the ack. `SUPPRESS_CLIENT_ACK_TAGS` already exists (gazzabyte-irsyad, cosem-exams) but does not include nazim-console (`nervous_system/ingest.py:675-690`).
- C-7 **Registry drift.** `bot_channels.nazim-console.enabled = false` while the channel is the busiest in the fleet (served by the separate `dev.wingmen.nazim-ingest` daemon). Any tooling that trusts the registry as "which channels are live" is wrong about the most important one.

## 3. Doorbell reliability (how a message wakes a body)

Three independent nudge sources target the same console pane: `nervous_system/ingest.py` (per operator message, plus a FORCED nudge when unhandled >600 s), `dev.wingmen.nazim-bus-notify` (bus rows; 5 nudges in 6 min in tonight's log), and `wake_backstop_sweep` (every ~2 min, 39–40 targets considered). Logs today: 142 ingest nudge lines; FORCED-cap nudges fired for nazim-console (1 unhandled at 13:39, 9 unhandled at 22:09 the prior day); backstop `woke=[]` every tick (nothing dropped on its watch).

**Verified findings**
- C-8 **Nudge storms, not nudge drops.** The failure mode today is the opposite of the 09-30 audit's (drops): every inbound and every bus row produces its own mid-turn injection, so the console received 2–4 injections per operator message and 3+ per bus burst, each forcing a DB round-trip. Delivery is reliable (0 misses); efficiency is poor. The ingest already has a `pane_working` sense (`ingest.py:~700`) and an Option-B deferral — the bus notifier does not coalesce with it.
- C-9 (inferred) Because nudges carry only a count, the console cannot batch by priority; a P3 FYI and a Musa order wake it identically.

## 4. Handoff / reconstitution

- `reports/nazim-handoff-NOW.md`: **794,311 bytes, 185 stacked "⚑ NOW" blocks**; 12 blocks written on 10-06 alone, 11 on 10-05 (block-per-event, not per-recycle). Untracked by git.
- `scripts/session_start_reconstitute.py:36,172` injects the first **24,000 chars** then appends "…[handoff truncated — read the full file for the rest]"; the hook additional-context was 28.6KB today and was persisted to disk rather than shown.
- `logs/nazim-session.log`: 54 session (re)starts since 2026-07-08; tonight's model flip required a full process relaunch because `/clear` cannot change `--model` (recorded in the handoff itself).
- Stale blocks contradict newer facts (e.g. "irsyad pair on musa2" vs op#26407's move to Musa-personal) — the console repeated the stale framing twice today until fleet-health corrected it.

**Verified finding** C-10: the handoff is append-only prose with no schema; freshness is encoded by position and version suffix ("NOW-FABLE-1.3 supersedes 1.2 …"). Truncation at 24KB means roughly the top 3 blocks are read; everything else is dead weight that also costs disk, grep time and secret-guard false positives.

## 5. Operator-facing tools

| tool | has | lacks |
|---|---|---|
| `nazim_send.sh` | text, `--ask/--chase-hours`, vault-key injection | inline buttons; threading (reply_to) |
| `nazim_send_photo.sh` / `_file.sh` | image/file + caption | `--ask`; buttons |
| `asks_open/close/triage.py` | CLI lifecycle | linkage enforcement (close requires outbound_msg_id) |
| `ingest.py` | **handles `callback_query`** (`:952,1044,1290`) | **no producer** of `inline_keyboard` anywhere in `scripts/` or `nervous_system/` → the one-tap path exists on receive and is unused on send |
| asks digest | daily, 10/10 delivered | buttons per WAITING-ON-YOU item; de-dup of items already re-asked; hiding not_an_ask/captured |

---

## Ranked improvements (fewer, better messages; one-tap decisions)

| # | change | evidence | owner | effort |
|---|---|---|---|---|
| 1 | **One-tap decisions**: `nazim_send.sh --choices "A|B|C"` renders an inline keyboard; `ingest.py` `_handle_callback` writes the tap as an inbound row + closes/answers the linked ask. Use for every yes/no + option question (16/day today). | C-5, tools table; receive path already built | console tooling (cc-substrate or Tier-B lane) | 3–4h |
| 2 | **Suppress auto-ack on nazim-console** (add to `SUPPRESS_CLIENT_ACK_TAGS`; keep FORCED-cap behaviour). | C-6: 35 acks/day, ack slower than the reply | cc-fleet-health (1-line env/plist) | 15 min |
| 3 | **Ledger hygiene job**: nightly, close `not_an_ask` rows (reason `not_a_request`), auto-triage `captured` >24h to `not_an_ask` unless a lane claims them, require `outbound_msg_id` or `--evidence` on close; dashboard the four counts. | C-1, C-3 | cc-substrate (migration + script) | 2h |
| 4 | **Chase ladder for WAITING ON MUSA**: set `chase_by` on every such row at open; digest shows only items due; second chase goes as a one-tap (#1) in a different wording; third chase escalates to "drop or keep?" with a button. | C-2: #44 repeated 10 days | console tooling | 2h |
| 5 | **Coalesce nudges**: one notifier per body; bus-notify defers while ingest has an in-flight nudge or `pane_working`; include priority in the count line (`P1:1 P3:3`). | C-8, C-9 | cc-fleet-health | 2h |
| 6 | **Bounded handoff**: `reports/nazim-handoff-NOW.md` becomes ONE current block ≤8KB (schema: MODEL/TOKEN, LIVE, HELD-for-Musa, DONE-do-not-regate, DISCIPLINES) rewritten in place; previous blocks archived to a dated file by the recycle script; reconstitution injects the whole current block, never truncates. | C-10: 794KB, 185 blocks, 24KB cut | console (self_recycle.sh + reconstitute.py) | 2h |
| 7 | **Message budget**: default reply ≤600 chars; anything longer goes as a file/page link with a 2-line summary; one "status in this format" message per topic per hour unless Musa asks. Enforce as a soft gate in `nazim_send.sh` (warn >900 chars). | C-5: 60% of replies >600 chars, 1.4 out per in | console discipline + 10-line script change | 1h |
| 8 | **Fix the pre-classifier's capture rate** (client-channel rows should open an ask only when the bot is addressed or a lane claims it) and sync `bot_channels` with reality (nazim-console enabled=true, owner=orch-console). | C-4, C-7 | cc-substrate | 2h |

Out of scope but noticed: cosem-caai's 34 unanswered inbound are by policy (banter, bot not addressed) — fine, but the digest/ask heuristic still opens rows for them (part of #8). The hub's operator channel is silent (1 message/7d): either retire it from Musa's view or route fleet-level status there so the nazim DM stops carrying everything.
