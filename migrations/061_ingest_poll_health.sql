-- 061_ingest_poll_health.sql
-- ledger: silo=tscuymavysscrvoberrr
--
-- STEP-0 instrumentation for the channel-liveness watchdog (CAI-501; joint Nazim+hub
-- tasking, bus 38341). THE GAP: a Telegram getUpdates poll can ERROR indefinitely with
-- the ingest PROCESS alive and config fine — gazzabyte-irsyad on 2026-09-09 logged
-- `URLError <urlopen error [Errno 54] Connection reset by peer>` ~5329x over ~11h and
-- only Musa caught it. bot_channels.updated_at is NOT a liveness clock: it advances only
-- on a NON-EMPTY poll (a message actually consumed), so a channel the client sends
-- nothing on looks identical whether polling is healthy or wedged. This table is the
-- missing POLL-HEALTH signal — every ingest cycle records, per (channel_key, host),
-- whether the last getUpdates SUCCEEDED, how many have failed in a row, and the last
-- error string. The watchdog (rung-1) keys its triggers on THIS, never on process
-- liveness and never on updated_at.
--
-- KEYED BY (channel_key, host): ingest runs as MULTIPLE daemons across hosts — the hub's
-- unpinned all-`enabled` instance (VPS) plus the Mini/irsyad INGEST_CHANNELS-pinned
-- instances. Keying on the REPORTING host lets the single watchdog reader distinguish
-- "the hub's ingest went dark" from "the Mini's pinned ingest went dark". host is the
-- daemon's socket.gethostname().
--
-- last_error captures the error TYPE (e.g. 'Errno54 conn reset', and a Telegram '409
-- Conflict' IF one ever occurs). The type is what lets rung-1 tell mechanisms apart
-- LATER — but nothing is assumed here: as of 2026-09-09 the irsyad wedge is 531x
-- [Errno 54] conn-reset with ZERO real Telegram-409s, which points at a TRANSPORT/network
-- reset (middlebox / firewall / IPv4-shim / Telegram drop) at least as much as a competing
-- poller. A 409 — the clean two-pollers-one-token signature ("terminated by other
-- getUpdates") — has NOT been observed. So rung-1's PRIMARY trigger is "no successful poll
-- in X min" (mechanism-agnostic: catches wedge OR competitor OR network); a 409 landing in
-- last_error is what WOULD confirm a competitor, never a thing seen. Keeps to spec's 3 fields.
--
-- channel_key mirrors bot_channels.channel_key (every polled key is loaded FROM
-- bot_channels). No FK for this rung: a channel removed from the registry should leave a
-- STALE health row visible to the watchdog, not silently CASCADE away.
--
-- SERVICE-ROLE-ONLY: RLS deny-all to public/anon/authenticated. The ingest daemon (writer)
-- and the watchdog (reader) both connect via the service DSN, which BYPASSES RLS. RLS
-- deny-all alone fully secures a service-role table — a deny-all USING(false) policy
-- returns zero rows and blocks writes for every non-bypassing role, regardless of any
-- default table grant — so no table-level grant-removal statement is needed here.

CREATE TABLE ingest_poll_health (
  channel_key   TEXT        NOT NULL,               -- the polled channel (bot_channels.channel_key)
  host          TEXT        NOT NULL,               -- reporting daemon host (socket.gethostname())
  last_ok_at    TIMESTAMPTZ,                        -- last getUpdates that returned OK (NULL until 1st success)
  consec_errors INTEGER     NOT NULL DEFAULT 0,     -- consecutive getUpdates errors (reset to 0 on success)
  last_error    TEXT,                               -- short last error string; NULL when the last poll was OK
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(), -- row freshness — bumped EVERY cycle (ok or error)
  PRIMARY KEY (channel_key, host)
);

ALTER TABLE ingest_poll_health ENABLE ROW LEVEL SECURITY;
CREATE POLICY deny_all_ingest_poll_health ON ingest_poll_health FOR ALL TO public USING (false);
