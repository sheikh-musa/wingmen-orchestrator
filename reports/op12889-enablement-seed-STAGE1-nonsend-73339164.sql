-- =====================================================================
-- op#12889 GO-LIVE ENABLEMENT SEED — STAGE 1 (NON-SEND) — org 73339164
-- Madrasah Irsyad Zuhri Al-Islamiah (goumlyne prod Irsyad ONLY).
-- Rulings: CAI-889 (design) / CAI-891 (wording) / CAI-895 (DPA) /
--          CAI-896 (go-live GO) / CAI-897 (seed audit = NULL actor + provenance)
--
-- WHY SPLIT (verify-at-source, this session): the op#12914/12916 donor EMAIL
-- ENVELOPE (one-email-both-docs, subject 'LETTER OF APPRECIATION FOR DONATION',
-- BCC, new body) is NOT wired into any live path — the live send is still the OLD
-- sendReceiptEmail format. Granting receipt:SEND before the envelope ships would
-- fire the OLD format at real donors (violates cai #20942 + hub refuses). So SEND
-- grants are HELD for STAGE 2 (envelope go-live). This file seeds ONLY the
-- NON-SEND grants, which dispatch NO donor email and are safe with #293.
--
-- APPLY ORDER (CAI-883, strict): 1) re-GRANT authenticated on write_audit_log_secure
-- BOTH silos -> 2) deploy #293 (+#300) -> 3) THIS SEED. Step 3 never before step 2.
--
-- STAGE-1 grants (org_role_permissions, access='full', on the `module` column
-- exactly as has_org_role_permission(p_org,p_role,p_permission) reads them):
--   preparer -> receipt:issue      (issue receipts; core op#12638 ask)
--   cashier  -> person:edit        (person edit; #300/mig175)
--   cashier  -> receipt:print      (print receipt PDF; CAI-892 Model B)
-- HELD FOR STAGE 2 (envelope go-live): preparer receipt:send, cashier receipt:send.
--
-- IDEMPOTENT + AUDIT-HONEST (CAI-897): a grant is upserted to 'full'; an audit_log
-- row is appended ONLY when the grant actually changed. Re-running = no-op (0
-- grants, 0 audit rows). actor_id = NULL (platform system seed — no user; never a
-- fabricated actor). The FULL authorization chain is carried in the payload
-- (source / authorized_by / applied_by) so a NULL-actor row is unambiguously an
-- authorized platform seed, not an actor gap. Audit hash = sha256(prev ||
-- canonicalPayloadJson) — payload uses only STRING values (canonicalPayloadJson =
-- JSON.stringify(p, Object.keys(p).sort()) is an ARRAY replacer, which would DROP
-- the keys of any nested object; strings are safe). Wet-proven byte-identical to
-- JS canonicalPayloadJson (2026-08-14).
-- =====================================================================
DO $$
DECLARE
  v_org   uuid := '73339164-7c1f-40ba-a093-33f1f292dd4c';
  v_name  text;
  v_prev  text;
  v_canon text;
  v_hash  text;
  v_eid   uuid;
  v_grants int := 0;
  g       record;
BEGIN
  -- Serialize against other audit-chain appenders on this org (CAI-782 no-fork).
  PERFORM pg_advisory_xact_lock(hashtextextended(v_org::text, 0));

  -- WRONG-ORG GUARD: assert identity at source before any write (fail-closed).
  SELECT name INTO v_name FROM organizations WHERE id = v_org AND deleted_at IS NULL;
  IF v_name IS DISTINCT FROM 'Madrasah Irsyad Zuhri Al-Islamiah' THEN
    RAISE EXCEPTION 'ORG_GUARD_FAIL: org % is % (expected Madrasah Irsyad Zuhri Al-Islamiah)', v_org, v_name;
  END IF;

  -- chain tip for this org (GENESIS if empty), byte-identical to writeAuditLog
  SELECT hash INTO v_prev FROM audit_log WHERE org_id = v_org ORDER BY id DESC LIMIT 1;
  IF v_prev IS NULL THEN v_prev := 'genesis'; END IF;

  FOR g IN
    SELECT * FROM (VALUES
      (1, 'preparer', 'receipt:issue'),
      (2, 'cashier',  'person:edit'),
      (3, 'cashier',  'receipt:print')
    ) AS t(n, role, module) ORDER BY n
  LOOP
    -- GRANT: upsert to 'full', but only UPDATE (and thus RETURN) on real change.
    v_eid := NULL;
    INSERT INTO org_role_permissions (org_id, role, module, access)
    VALUES (v_org, g.role, g.module, 'full')
    ON CONFLICT (org_id, role, module) DO UPDATE SET access = 'full'
      WHERE org_role_permissions.access IS DISTINCT FROM 'full'
    RETURNING id INTO v_eid;

    IF v_eid IS NOT NULL THEN
      -- canonical payload: keys sorted (access, applied_by, authorized_by, module,
      -- role, source), compact, all controlled ASCII (no escaping needed).
      -- [CAI-897] provenance carried in the row: NULL actor is honestly "no user",
      -- authorized_by names the full chain (client op#, cai ruling), applied_by=hub.
      v_canon := '{"access":"full","applied_by":"hub","authorized_by":"client op#12889; cai CAI-896","module":"'
                 || g.module || '","role":"' || g.role
                 || '","source":"go-live-enablement-seed"}';
      v_hash := encode(sha256(convert_to(v_prev || v_canon, 'UTF8')), 'hex');
      INSERT INTO audit_log (org_id, actor_id, entity_type, entity_id, action, payload, prev_hash, hash)
      VALUES (v_org, NULL, 'org_role_permission', v_eid, 'update', v_canon::jsonb, v_prev, v_hash);
      v_prev := v_hash;      -- chain forward only on a real append
      v_grants := v_grants + 1;
    END IF;
  END LOOP;

  RAISE NOTICE 'op#12889 STAGE-1 seed: % non-send grant(s) applied+audited for org % (0 = already enabled)', v_grants, v_org;
END $$;
