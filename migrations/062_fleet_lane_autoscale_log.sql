-- 062_fleet_lane_autoscale_log.sql
-- ledger: silo=tscuymavysscrvoberrr (orchestrator substrate)
--
-- The INERT decision log for the irsyad-autoscaler (design: docs/irsyad-autoscaler-design-v1.md,
-- §6 rollout step 1 "INERT phase: deploy detect+log only ... ZERO actuation"). This is the
-- ONLY thing the INERT scaler writes. It records, once per tick, the demand it measured and the
-- would-spin / would-kill decision it WOULD have taken had it been armed — so a later wet-prove
-- (§6 step 2) can show the decisions match reality (correct spins/kills, protected set never
-- selected, cap respected, idle-proof correct) before anything is armed at Nazim's gate.
--
-- INERT INVARIANT: every row asserts detect+log-only. There is no actuation column and no
-- actuation code path — `inert` defaults TRUE and the writer never sets it false. Arming is a
-- later, separately-gated step that would introduce its OWN action-audit table, not flip a bool.
--
-- BOTH demand counts are logged every tick, deliberately (design §2 open-Q1 — coord-dispatch
-- queue vs unclaimed agent_messages is still Nazim's to confirm as canonical):
--   * coord_queue_depth  — depth of the coord-owned claimable dispatch queue (the PREFERRED
--     canonical signal, §2 option-1). Its schema does not exist yet; the scaler reads it
--     DEFENSIVELY (missing table => depth 0, coord_queue_source='absent'). A genuine read
--     ERROR against an existing table => NULL here + would_spin forced false (fail-safe).
--   * option2_count      — the independent §2 option-2 fallback: agent_messages to the
--     `cc-irsyad*` family that are requires_response + unresponded past a grace and not owned
--     by a live lane. Logged alongside so the wet-prove can compare the two signals directly.
--
-- SERVICE-ROLE-ONLY WRITER + CONSOLE-READABLE (design constraint: "readable by the console for
-- wet-prove"). RLS is deny-all to public (the writer/scaler connects via the service DSN, which
-- BYPASSES RLS). The read-only console role (migration 004_console_readonly) gets an explicit
-- SELECT grant + a permissive SELECT policy so the wet-prove board can render these rows —
-- permissive RLS policies are OR'd, so the deny-all never subtracts the console's read.

CREATE TABLE IF NOT EXISTS public.fleet_lane_autoscale_log (
  id                     BIGSERIAL   PRIMARY KEY,
  tick_at                TIMESTAMPTZ NOT NULL DEFAULT now(),  -- when this INERT tick ran
  host                   TEXT        NOT NULL,                -- reporting host (socket.gethostname())
  -- demand signals (BOTH logged every tick) -----------------------------------------------
  coord_queue_depth      INTEGER,                             -- coord claimable-queue depth; NULL = UNREADABLE (fail-safe)
  coord_queue_source     TEXT        NOT NULL DEFAULT 'absent', -- queue table name, or 'absent' when it does not exist yet
  option2_count          INTEGER     NOT NULL DEFAULT 0,      -- §2 option-2 unclaimed-agent_messages count
  -- pool + decision -----------------------------------------------------------------------
  pool_size              INTEGER     NOT NULL,                -- live auto-killable irsyad worker lanes at tick time
  demand                 INTEGER,                             -- the signal that DROVE the decision (= coord_queue_depth); NULL when ambiguous
  would_spin             BOOLEAN     NOT NULL DEFAULT false,  -- would an armed scaler spin +1 this tick?
  spin_target            TEXT,                                -- human description of the proposed spin (pool N -> N+1), else NULL
  would_kill_candidates  JSONB       NOT NULL DEFAULT '[]'::jsonb, -- [{lane, base_agent_id, reason}] the scaler WOULD wind down
  interlocks             JSONB       NOT NULL DEFAULT '{}'::jsonb, -- per-§5-interlock verdict strings (cap, threshold, idle-proof, protected, fail-safe)
  decision_reason        TEXT        NOT NULL,                -- one-line overall verdict for the tick
  ambiguous              BOOLEAN     NOT NULL DEFAULT false,  -- true => fail-safe fired, scaler decided NOTHING
  inert                  BOOLEAN     NOT NULL DEFAULT true,   -- ALWAYS true in this phase: asserts zero actuation
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS fleet_lane_autoscale_log_tick_at_idx
  ON public.fleet_lane_autoscale_log (tick_at DESC);

COMMENT ON TABLE public.fleet_lane_autoscale_log IS
  'INERT decision log for the irsyad-autoscaler (detect+log only, ZERO actuation). One row per '
  'tick: measured demand (coord queue depth + option-2 count), pool size, and the would-spin / '
  'would-kill decision under the §5 interlocks. Source for the wet-prove before any arming.';

-- ---------------------------------------------------------------- RLS: deny-all public + console read
ALTER TABLE public.fleet_lane_autoscale_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY deny_all_fleet_lane_autoscale_log
  ON public.fleet_lane_autoscale_log FOR ALL TO public USING (false);

-- The wet-prove console reads via the SELECT-only console_readonly role (migration 004). A
-- permissive SELECT policy for exactly that role, OR'd with the deny-all above, lets the board
-- render these rows without opening the table to anon/authenticated or granting any write.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'console_readonly') THEN
    EXECUTE 'GRANT SELECT ON public.fleet_lane_autoscale_log TO console_readonly';
    EXECUTE 'CREATE POLICY sel_console_fleet_lane_autoscale_log '
            'ON public.fleet_lane_autoscale_log FOR SELECT TO console_readonly USING (true)';
  END IF;
END
$$;

-- Belt-and-braces: never a PostgREST anon surface.
REVOKE ALL ON public.fleet_lane_autoscale_log FROM anon, authenticated;
