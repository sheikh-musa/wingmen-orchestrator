-- =====================================================================
-- op#12889 GO-LIVE ENABLEMENT SEED — STAGE 2 (RECEIPT:SEND) — goumlyne org 73339164
-- Rulings: CAI-896 (go-live GO) / CAI-897 (actor_id=NULL + full-provenance) / CAI-895/893 (envelope)
-- HELD until BOTH (cai #21023): (a) the compliant op#12914 envelope is DEPLOYED and
-- byte-diff/no-drift-confirmed AND the LIVE send path uses sendDonationDocEmail (NOT
-- sendReceiptEmail) — else receipt:send fires the OLD format; (b) the operator go-live
-- GO is verified in operator_messages, and its op# is substituted below.
--
-- >>> HUB: replace the token __OPERATOR_GO_OP__ (2 occurrences) with the verified
-- >>> operator-GO op# (e.g. op#13001) BEFORE applying. The DO block computes the audit
-- >>> hash from the live tip at apply time, so any valid op# string yields a correct
-- >>> byte-identical chain (mechanism wet-proven; string-agnostic). goumlyne ONLY.
--
-- STAGE-2 grants (org_role_permissions, access='full'):
--   preparer -> receipt:send   (op#12638 urgent donor-email)
--   cashier  -> receipt:send   (op#12889 donor-email)
-- IDEMPOTENT · advisory-locked (CAI-782) · org-guarded · actor_id=NULL + FLAT full
-- provenance incl authorized_by_operator_go (CAI-897) · hash byte-identical to JS.
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
  PERFORM pg_advisory_xact_lock(hashtextextended(v_org::text, 0));

  SELECT name INTO v_name FROM organizations WHERE id = v_org AND deleted_at IS NULL;
  IF v_name IS DISTINCT FROM 'Madrasah Irsyad Zuhri Al-Islamiah' THEN
    RAISE EXCEPTION 'ORG_GUARD_FAIL: org % is % (expected Madrasah Irsyad Zuhri Al-Islamiah)', v_org, v_name;
  END IF;

  SELECT hash INTO v_prev FROM audit_log WHERE org_id = v_org ORDER BY id DESC LIMIT 1;
  IF v_prev IS NULL THEN v_prev := 'genesis'; END IF;

  FOR g IN
    SELECT * FROM (VALUES
      (1, 'preparer', 'receipt:send'),
      (2, 'cashier',  'receipt:send')
    ) AS t(n, role, module) ORDER BY n
  LOOP
    v_eid := NULL;
    INSERT INTO org_role_permissions (org_id, role, module, access)
    VALUES (v_org, g.role, g.module, 'full')
    ON CONFLICT (org_id, role, module) DO UPDATE SET access = 'full'
      WHERE org_role_permissions.access IS DISTINCT FROM 'full'
    RETURNING id INTO v_eid;

    IF v_eid IS NOT NULL THEN
      -- FLAT sorted keys: access, applied_by, authorized_by_cai, authorized_by_op,
      -- authorized_by_operator_go, module, role, source, stage.
      v_canon := '{"access":"full",'
              || '"applied_by":"hub-platform",'
              || '"authorized_by_cai":"CAI-896;CAI-897",'
              || '"authorized_by_op":"op#12889",'
              || '"authorized_by_operator_go":"__OPERATOR_GO_OP__",'
              || '"module":"' || g.module || '",'
              || '"role":"'   || g.role   || '",'
              || '"source":"go-live-enablement-seed",'
              || '"stage":"2-send"}';
      v_hash := encode(sha256(convert_to(v_prev || v_canon, 'UTF8')), 'hex');
      INSERT INTO audit_log (org_id, actor_id, entity_type, entity_id, action, payload, prev_hash, hash)
      VALUES (v_org, NULL, 'org_role_permission', v_eid, 'update', v_canon::jsonb, v_prev, v_hash);
      v_prev := v_hash;
      v_grants := v_grants + 1;
    END IF;
  END LOOP;

  RAISE NOTICE 'op#12889 STAGE-2 seed: % receipt:send grant(s) applied+audited for org % (0 = already enabled)', v_grants, v_org;
END $$;
