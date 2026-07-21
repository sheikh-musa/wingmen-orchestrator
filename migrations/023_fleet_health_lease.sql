-- 023_fleet_health_lease.sql — CAI-RESP-501: single-owner lease for TWO of the
-- five ORCH-TOPOLOGY-001 hub singleton pens — (iii) watchdog and (v) fleet-status
-- assertions — so the fleet SRE (cc-fleet-health, Mini) can run detect->act
-- self-healing without the hub.
--
-- Mirrors migrations/016_orch_lease.sql exactly (same shape, same RLS lockdown).
-- The pens belong to whoever holds this row, NOT to a hostname / a promise.
--   - DEFAULT holder = cc-fleet-health (the SRE); it takes at boot, renews in
--     its heartbeat (boot_fleet_health.sh).
--   - DEAD-MAN FALLBACK: on the SRE's lease EXPIRY the HUB reclaims via
--     fleet_health_lease.py reclaim (CAS on expiry). Never two holders.
-- Apply via scripts/apply_fleet_health_lease.py (decision-962: never `supabase db push`).

CREATE TABLE IF NOT EXISTS fleet_health_lease (
    lease_key       text PRIMARY KEY,          -- 'fleet-health' = watchdog + fleet-status pens
    holder          text NOT NULL REFERENCES agents(id),
    holder_host     text,                      -- `hostname` of the holding body; self-stamped via `renew`
    acquired_at     timestamptz NOT NULL DEFAULT now(),
    renewed_at      timestamptz NOT NULL DEFAULT now(),
    ttl_seconds     integer     NOT NULL DEFAULT 900,  -- reclaim-eligible once renewed_at + ttl elapses
    taken_over_from text,
    takeover_reason text
);

ALTER TABLE fleet_health_lease ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON fleet_health_lease FROM anon, authenticated;

-- Seed: the SRE holds it by default (holder_host NULL until it self-stamps on
-- its first `renew`/`take` — the gate fail-safes to ALLOW the recorded holder on
-- NULL host so a pre-adoption Mini daemon is never stranded). Guarded on the
-- agents FK: only seed once cc-fleet-health is registered
-- (scripts/register_fleet_health.py).
INSERT INTO fleet_health_lease (lease_key, holder, holder_host)
SELECT 'fleet-health', 'cc-fleet-health', NULL
WHERE EXISTS (SELECT 1 FROM agents WHERE id='cc-fleet-health')
ON CONFLICT (lease_key) DO NOTHING;
