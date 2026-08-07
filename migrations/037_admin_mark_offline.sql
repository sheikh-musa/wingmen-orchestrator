-- 037_admin_mark_offline.sql
--
-- WHY (op#11049 / CAI-RESP-760, 2026-08-07): the fleet_health hygiene sweep
-- (scripts/fleet_health.py, cc-fleet-health's §4.3 loop) went DOWN when
-- agent_status writes were hardened to be identity-bound (trigger
-- enforce_agent_status_identity: every write must have app.current_agent_id =
-- NEW.agent_id, per row). The sweep marks MANY dead lanes offline under ONE
-- identity, so the batch UPDATE trips the trigger on the first cross-identity
-- row and aborts the whole transaction — no offline-mark, no reap, no
-- dead-letter archive, no reconcile.
--
-- cai BLESSED Option C (CAI-RESP-760), REJECTED A (per-agent GUC spoof — the
-- audit would read as each corpse marking ITSELF offline: the very
-- identity-forgery the guard exists to stop) and B (a sentinel bypass — too
-- broad; least-privilege forbids a write-anything exemption). NOTE: the existing
-- public.sweep_stale_agents() IS the rejected-A pattern (it does
-- set_config('app.current_agent_id', <ghost>) per row). This migration does NOT
-- touch it; cc-fleet-health has flagged it to cai for separate retirement in
-- favour of this function.
--
-- C is a NARROW, named, offline-only, audited, lease-holder-only cross-identity
-- capability whose audit says the TRUTH ("admin_mark_offline reaped X, caller Y,
-- reason Z"). The reaper calls it once per dead lane.
--
-- Built to cai's five hardening conditions (C1-C5) and verified against the six
-- positive controls. Each block is tagged with the condition/control it serves.
--
-- SCOPE: agent_status + its trigger live on the BUS / coordination silo
-- (tscuymavysscrvoberrr), NOT the product silos -> SINGLE-DB migration.
--
-- APPLY (cai, §6.6 guarded path — NOT db push): see "APPLY NOTES" at the foot;
-- a couple of steps (role creation, ownership reassignment) depend on the
-- substrate privilege setup and are called out for verification at apply time.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Dedicated owner role — the UNFORGEABLE primitive behind the exemption.
--    (C2 access; C5 fn-scoped exemption; positive control 6)
--    The app connects as `postgres` and all existing SECDEF fns are
--    postgres-owned, so a `current_user = 'postgres'` guard would be forgeable
--    by any postgres/service_role raw UPDATE. A dedicated NOLOGIN role that
--    owns ONLY this function makes `current_user = 'fleet_reaper'` true *only*
--    while executing inside admin_mark_offline. NOLOGIN: never a connectable
--    identity, purely the SECDEF definer.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fleet_reaper') THEN
    CREATE ROLE fleet_reaper NOLOGIN;
  END IF;
END$$;

COMMENT ON ROLE fleet_reaper IS
  'CAI-RESP-760: NOLOGIN definer role owning ONLY admin_mark_offline(). Its sole '
  'purpose is to make current_user unique inside that SECDEF fn so the '
  'enforce_agent_status_identity exemption cannot be forged by a postgres/'
  'service_role raw write. Grant no LOGIN, add no other objects, add no members.';

-- Least-privilege grants for the SECDEF body (runs AS fleet_reaper):
--   read prior status + assert lease + write status + append audit. Nothing else.
GRANT USAGE                ON SCHEMA public           TO fleet_reaper;  -- runtime: reach public objects (lockdown migs revoke PUBLIC usage)
GRANT SELECT               ON TABLE agent_status      TO fleet_reaper;
GRANT UPDATE (status)      ON TABLE agent_status      TO fleet_reaper;  -- column-scoped: only status (C1)
GRANT SELECT               ON TABLE fleet_health_lease TO fleet_reaper;

-- ---------------------------------------------------------------------------
-- 2. Audit table — atomic, same-txn, tells the truth. (C3)
--    (caller, target, prior status, reason, ts). Append-only. mig-131/144 idiom.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.admin_offline_audit (
  id           bigserial PRIMARY KEY,
  caller       text        NOT NULL,          -- app.current_agent_id of the caller (the lease holder)
  target_agent_id text     NOT NULL,          -- the lane marked offline
  prior_status text        NOT NULL,          -- status before the write (death evidence context)
  reason       text        NOT NULL,          -- why (attribution — never anonymous)
  marked_at    timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.admin_offline_audit IS
  'CAI-RESP-760 C3: one row per admin_mark_offline() call, written same-txn as the '
  'offline write (atomic — rolls back together). This is the truthful attribution '
  'the rejected A-pattern could not give.';

ALTER TABLE public.admin_offline_audit ENABLE ROW LEVEL SECURITY;
GRANT INSERT ON TABLE public.admin_offline_audit TO fleet_reaper;
GRANT SELECT ON TABLE public.admin_offline_audit TO console_readonly, service_role;
GRANT USAGE, SELECT ON SEQUENCE public.admin_offline_audit_id_seq TO fleet_reaper;

DROP POLICY IF EXISTS admin_offline_audit_reaper_insert ON public.admin_offline_audit;
CREATE POLICY admin_offline_audit_reaper_insert ON public.admin_offline_audit
  FOR INSERT TO fleet_reaper WITH CHECK (true);
DROP POLICY IF EXISTS admin_offline_audit_ro ON public.admin_offline_audit;
CREATE POLICY admin_offline_audit_ro ON public.admin_offline_audit
  FOR SELECT TO console_readonly, service_role USING (true);

-- ---------------------------------------------------------------------------
-- 3. RLS policy letting fleet_reaper read + set-offline agent_status rows.
--    (agent_status RLS is ENABLED but not FORCED; fleet_reaper is neither owner
--    nor bypassrls, so without a policy RLS default-denies.) WITH CHECK enforces
--    offline-only at the RLS layer too — defense in depth with C1 + the trigger.
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS agent_status_reaper_offline ON public.agent_status;
CREATE POLICY agent_status_reaper_offline ON public.agent_status
  AS PERMISSIVE FOR ALL TO fleet_reaper
  USING (true)
  WITH CHECK (status = 'offline');   -- any write by fleet_reaper must leave status=offline

-- fleet_health_lease has RLS ENABLED with NO policies -> a non-owner/non-bypassrls
-- role (fleet_reaper) sees ZERO rows through a bare GRANT SELECT, so the in-fn
-- lease assertion (C2) would silently fail-closed. Give fleet_reaper a read-only
-- view of the lease it must check. Scoped TO fleet_reaper only: owner/bypassrls
-- roles already bypass; anon/authenticated still see nothing.
DROP POLICY IF EXISTS fleet_health_lease_reaper_ro ON public.fleet_health_lease;
CREATE POLICY fleet_health_lease_reaper_ro ON public.fleet_health_lease
  FOR SELECT TO fleet_reaper USING (true);

-- The offline UPDATE fires the (INVOKER) snapshot trigger
-- snapshot_agent_status_to_history, which INSERTs a history row via
-- agent_status_history_id_seq. Running as fleet_reaper, it needs INSERT there
-- (RLS-enabled, no policy for fleet_reaper) + the sequence. INSERT-only: the
-- reaper appends history, never reads/edits/deletes it. (The other trigger,
-- bump_agent_status_updated_at, only mutates NEW.updated_at — no grant needed.)
GRANT INSERT ON TABLE public.agent_status_history TO fleet_reaper;
GRANT USAGE, SELECT ON SEQUENCE public.agent_status_history_id_seq TO fleet_reaper;
DROP POLICY IF EXISTS agent_status_history_reaper_insert ON public.agent_status_history;
CREATE POLICY agent_status_history_reaper_insert ON public.agent_status_history
  FOR INSERT TO fleet_reaper WITH CHECK (true);

-- ---------------------------------------------------------------------------
-- 4. Teach enforce_agent_status_identity a NARROW, fn-scoped exemption. (C5)
--    Base guard is UNCHANGED for every normal write (positive control 5). The
--    exemption fires ONLY when ALL hold:
--      (a) marker app.admin_offline_active = NEW.agent_id  (set-and-reset inside
--          the fn body, bound to the exact target — a stray marker for a
--          different row does nothing), AND
--      (b) current_user = 'fleet_reaper'  (true ONLY inside the SECDEF fn;
--          unforgeable by a postgres/service_role raw write — positive control 6),
--          AND
--      (c) NEW.status = 'offline'  (offline-only at the trigger too — C1).
--    A GUC set OUTSIDE the fn (any role) never satisfies (b) -> no bypass.
--
--    SECURITY MODEL CHANGE (flag for cai): the guard is switched SECURITY DEFINER
--    -> SECURITY INVOKER. A SECDEF trigger runs as ITS OWN definer (postgres), so
--    current_user inside it is ALWAYS 'postgres' — it masks the role that caused
--    the write, making the (b) check impossible. As INVOKER the trigger runs as
--    the writing role, so current_user is 'fleet_reaper' only inside the SECDEF
--    admin_mark_offline body and the caller's own role for any raw write. This is
--    SAFE: the guard does no privileged work (it only reads a GUC, calls the
--    RAISE-NOTICE logger — EXECUTE is granted to PUBLIC — and RAISEs), so no
--    writer loses any capability; the base identity logic is byte-for-byte intact.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.enforce_agent_status_identity()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY INVOKER
 SET search_path TO 'public'
AS $function$
DECLARE
  v_guc_agent   TEXT;
  v_admin_marker TEXT;
BEGIN
  -- CAI-RESP-760 exemption (evaluated FIRST; leaves the normal path untouched).
  v_admin_marker := current_setting('app.admin_offline_active', true);
  IF v_admin_marker IS NOT NULL
     AND v_admin_marker = NEW.agent_id            -- (a) bound to this exact target
     AND current_user = 'fleet_reaper'            -- (b) genuinely inside the SECDEF fn
  THEN
    IF NEW.status IS DISTINCT FROM 'offline' THEN -- (c) offline-only
      RAISE EXCEPTION 'admin_mark_offline exemption is offline-only (got status=%)', NEW.status
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;

  -- Base identity guard — UNCHANGED (every normal write still enforced).
  v_guc_agent := current_setting('app.current_agent_id', true);
  IF v_guc_agent IS NULL OR v_guc_agent = '' THEN
    PERFORM log_agent_status_identity_violation(NEW.agent_id, v_guc_agent, 'guc_not_set', TG_OP);
    RAISE EXCEPTION 'agent_status write rejected: app.current_agent_id GUC not set (use SET LOCAL at session-start)'
      USING ERRCODE = '42501';
  END IF;
  IF NEW.agent_id IS DISTINCT FROM v_guc_agent THEN
    PERFORM log_agent_status_identity_violation(NEW.agent_id, v_guc_agent, 'identity_mismatch', TG_OP);
    RAISE EXCEPTION 'agent_status write rejected: NEW.agent_id=% but GUC app.current_agent_id=% (identity mismatch)', NEW.agent_id, v_guc_agent
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$function$;

-- ---------------------------------------------------------------------------
-- 5. admin_mark_offline — the narrow capability. (C1 offline-only/one-way,
--    C2 lease-gated, C3 atomic audit, C4 single target, C5 marker set-and-reset)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.admin_mark_offline(p_agent_id text, p_reason text)
 RETURNS boolean                                   -- true = newly marked offline; false = already offline (no-op)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'                        -- C2: pinned search_path
AS $function$
DECLARE
  v_caller TEXT;
  v_prior  TEXT;
BEGIN
  -- C4: exactly one target per call; C3/attribution: reason is mandatory.
  IF p_agent_id IS NULL OR p_agent_id = '' THEN
    RAISE EXCEPTION 'admin_mark_offline: p_agent_id is required';
  END IF;
  IF p_reason IS NULL OR p_reason = '' THEN
    RAISE EXCEPTION 'admin_mark_offline: p_reason is required (reaps must not be anonymous)';
  END IF;

  -- C2: the caller must be the ACTIVE fleet_health lease holder. The caller
  -- declares its OWN true identity in app.current_agent_id (legitimate — NOT the
  -- rejected A-spoof of the target's identity). Ties the capability to the live
  -- lease = the dead-man's switch: if cc-fleet-health dies and the lease lapses,
  -- this fn REFUSES. Grant alone is insufficient (cai C2).
  v_caller := current_setting('app.current_agent_id', true);
  IF v_caller IS NULL OR v_caller = '' THEN
    RAISE EXCEPTION 'admin_mark_offline: caller identity (app.current_agent_id) not set';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM fleet_health_lease
     WHERE lease_key = 'fleet-health'
       AND holder    = v_caller
       AND renewed_at > now() - make_interval(secs => ttl_seconds)
  ) THEN
    RAISE EXCEPTION 'admin_mark_offline: caller % is not the active fleet_health lease holder (lease stale or held by another)', v_caller
      USING ERRCODE = '42501';
  END IF;

  -- Read + lock prior status (also proves the row exists).
  SELECT status INTO v_prior FROM agent_status WHERE agent_id = p_agent_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'admin_mark_offline: no agent_status row for % (nothing to reap)', p_agent_id;
  END IF;

  -- C1: one-directional. Already offline -> idempotent no-op (NEVER un-offline;
  -- we only ever write 'offline', so dead->working is structurally impossible).
  IF v_prior = 'offline' THEN
    RETURN false;
  END IF;

  -- C3: atomic audit FIRST, same txn (rolls back with the write if anything fails).
  INSERT INTO admin_offline_audit (caller, target_agent_id, prior_status, reason)
  VALUES (v_caller, p_agent_id, v_prior, p_reason);

  -- C5: marker SET immediately before the write, RESET immediately after — hot
  -- for this single statement only (is_local=true also drops it at txn end).
  PERFORM set_config('app.admin_offline_active', p_agent_id, true);
  UPDATE agent_status SET status = 'offline' WHERE agent_id = p_agent_id;  -- C1: status only
  PERFORM set_config('app.admin_offline_active', '', true);

  RETURN true;
END;
$function$;

COMMENT ON FUNCTION public.admin_mark_offline(text, text) IS
  'CAI-RESP-760: narrow, offline-only, lease-gated, audited cross-identity reap '
  'primitive for the fleet_health sweep. One target/call. Owned by fleet_reaper '
  '(the unforgeable definer). Callers set app.current_agent_id to their OWN id '
  '(lease holder); the fn asserts the live fleet_health lease. Never un-offlines.';

-- C2 ACCESS: default-deny, then grant to exactly the caller tier. REVOKE from
-- PUBLIC/anon/authenticated first so the capability is never world-callable.
-- (COMMENT + these grants are issued while postgres still owns the fn.)
REVOKE ALL ON FUNCTION public.admin_mark_offline(text, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_mark_offline(text, text) TO postgres, service_role;

-- Reassign the fn to its dedicated definer so current_user = 'fleet_reaper'
-- inside it. Both grants below are TRANSIENT, held only across the ALTER OWNER
-- and revoked immediately after, so fleet_reaper ends with the minimal footprint
-- (owns one fn; no schema CREATE; no members) that keeps the exemption unforgeable:
--   * membership: current owner (postgres) must be a member of fleet_reaper to
--     reassign ownership TO it;
--   * schema CREATE: the NEW owner must hold CREATE on schema public at the
--     moment of ALTER OWNER (owning an existing object needs it once, not after).
GRANT fleet_reaper TO postgres;
GRANT CREATE ON SCHEMA public TO fleet_reaper;
ALTER FUNCTION public.admin_mark_offline(text, text) OWNER TO fleet_reaper;
REVOKE CREATE ON SCHEMA public FROM fleet_reaper;
REVOKE fleet_reaper FROM postgres;

COMMIT;

-- ===========================================================================
-- APPLY NOTES for cai (§6.6 verification / guarded apply)
-- ===========================================================================
-- VERIFIED by cc-fleet-health before handoff (rolled-back psql, zero persistence
-- confirmed — no new object exists in the live DB, cc-irsyad-b-1 still 'working',
-- live enforce fn untouched):
--   * whole file applies clean end-to-end inside BEGIN..ROLLBACK;
--   * behavioural tests all PASS: happy-path reap flips status->offline + writes
--     one truthful audit row (caller=cc-fleet-health, prior=working); 2nd call is
--     an idempotent no-op (false, still 1 audit row); non-lease-holder DENIED
--     (PC1); a forged marker from a non-fleet_reaper role does NOT bypass (PC6);
--     a normal cross-identity write is still blocked (PC5).
--
-- TWO CHANGES THAT NEED YOUR EXPLICIT SIGN-OFF (both flagged inline):
--   1. enforce_agent_status_identity is switched SECURITY DEFINER -> INVOKER
--      (section 4). Required: a SECDEF trigger masks the writing role as its
--      definer (postgres), so the exemption's current_user='fleet_reaper' check
--      is impossible under SECDEF. Safe: the guard does no privileged work and
--      log_agent_status_identity_violation has EXECUTE to PUBLIC.
--   2. fleet_reaper's footprint = owns ONLY admin_mark_offline; SELECT+UPDATE
--      (status) on agent_status, SELECT on fleet_health_lease, INSERT on
--      admin_offline_audit + agent_status_history (the latter forced by the
--      INVOKER snapshot trigger firing as fleet_reaper), USAGE on schema public.
--      Everything the offline write + its trigger chain touches, nothing more.
--
-- Positive controls, and where each is enforced:
--   (1) anon/authenticated/non-lease-holder denied  -> REVOKE + GRANT (§C2) + the
--       in-fn lease assertion; anon/authenticated also can't write agent_status
--       (no RLS policy) even by hand.
--   (2) offline-only, no other column mutates        -> UPDATE sets only `status`;
--       fleet_reaper holds UPDATE(status) *column* grant only; RLS WITH CHECK
--       status='offline'; trigger exemption re-checks NEW.status='offline'.
--   (3) no un-offline                                -> fn only ever writes 'offline';
--       already-offline is a no-op returning false.
--   (4) audit row per call, atomic                   -> INSERT precedes the UPDATE in
--       the same txn; both roll back together.
--   (5) normal-write guard still blocks agent-writes-another-row -> base identity
--       branch is byte-for-byte unchanged; exemption is a strictly-additive
--       earlier branch.
--   (6) a GUC set OUTSIDE the fn grants no bypass     -> exemption also requires
--       current_user='fleet_reaper', which a postgres/service_role raw UPDATE
--       cannot produce without SET ROLE fleet_reaper (membership revoked below).
--
-- SUBSTRATE-PRIVILEGE STEPS TO VERIFY AT APPLY (postgres has CREATEROLE, is NOT
-- superuser, CANNOT grant BYPASSRLS — so this migration deliberately avoids
-- BYPASSRLS and uses an RLS policy instead):
--   * CREATE ROLE fleet_reaper NOLOGIN — needs CREATEROLE (postgres has it).
--   * ALTER FUNCTION ... OWNER TO fleet_reaper — VERIFIED to work applied as
--     postgres: schema public is owned by pg_database_owner (postgres is a
--     member), and both prerequisites are granted transiently and revoked right
--     after (GRANT fleet_reaper TO postgres; GRANT CREATE ON SCHEMA public TO
--     fleet_reaper; ALTER OWNER; then both REVOKEd) so fleet_reaper ends owning
--     one fn with no schema-CREATE and no members.
--   * RESIDUAL: a CREATEROLE admin of fleet_reaper (postgres) can always
--     re-grant itself membership and thus SET ROLE fleet_reaper. This is inherent
--     to postgres administering the role and is acceptable (postgres is the
--     trusted app tier and the reaper itself); control 6's target is the
--     RLS-exposed roles, which are fully shut out. Flagging explicitly for your
--     sign-off rather than hiding it.
--
-- LEDGER: record in migration_ledger (repo='orchestrator',
-- migration_name='037_admin_mark_offline.sql', silo_ref='tscuymavysscrvoberrr').
-- ===========================================================================
