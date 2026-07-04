-- 016_orch_lease.sql — ORCH-TOPOLOGY-001 A2/A3: the hub role is a migratable
-- substrate LEASE, not a hostname; the five singleton pens are enforced against
-- this table, not against behavioral promises.
-- Apply via scripts/apply_orch_lease.py (decision-962: never `supabase db push`).

CREATE TABLE IF NOT EXISTS orch_lease (
    lease_key       text PRIMARY KEY,          -- 'orch-hub' = the five singleton pens
    holder          text NOT NULL REFERENCES agents(id),
    holder_host     text,                      -- `hostname` of the holding body; hub self-stamps via `orch_lease.py renew`
    acquired_at     timestamptz NOT NULL DEFAULT now(),
    renewed_at      timestamptz NOT NULL DEFAULT now(),
    ttl_seconds     integer     NOT NULL DEFAULT 900,  -- takeover-eligible once renewed_at + ttl elapses (auto-takeover NOT wired yet)
    taken_over_from text,
    takeover_reason text
);

ALTER TABLE orch_lease ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON orch_lease FROM anon, authenticated;

-- Seed: Studio hub holds the lease today (holder_host NULL until the hub
-- self-stamps on its first `renew` — the gate fail-safes to ALLOW on NULL so
-- the hub is never stranded pre-adoption).
INSERT INTO orch_lease (lease_key, holder, holder_host)
VALUES ('orch-hub', 'cc-orchestrator', NULL)
ON CONFLICT (lease_key) DO NOTHING;
