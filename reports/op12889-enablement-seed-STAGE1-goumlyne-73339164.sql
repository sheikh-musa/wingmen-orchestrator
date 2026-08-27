-- =====================================================================
-- op#12889 GO-LIVE ENABLEMENT SEED — STAGE 1 (NON-SEND) — goumlyne org 73339164
-- Rulings: CAI-889 (design) / CAI-896 (go-live GO) / CAI-897 (actor_id=NULL + full-provenance payload)
-- SPLIT rationale (cc-irsyad-b #21013, source-verified): receipt:SEND fires the
-- LIVE donor email which is still OLD-format (envelope unwired: sendDonationDocEmail
-- grep-empty on main + #293 @69643d5). So receipt:SEND is HELD to STAGE 2 (envelope
-- go-live). STAGE 1 = non-donor-facing grants ONLY, safe to apply with the #293 deploy.
--
-- STAGE-1 grants (org_role_permissions, access='full', mapped onto `module`):
--   preparer -> receipt:issue        (op#12638, create receipt record; no email)
--   cashier  -> person:edit          (op#12889, mig175; no email)
--   cashier  -> receipt:print        (op#12889 Model-B counter print; no email)
--   (preparer person:edit NOT seeded — preparer is privileged, edits via the
--    non-granted updatePerson path; a grant would be misleading.)
--
-- APPLY ORDER (CAI-883): 1) re-GRANT authenticated on write_audit_log_secure BOTH
-- silos -> 2) deploy #293 (+#300) -> 3) THIS STAGE-1 SEED (goumlyne only).
-- IDEMPOTENT (audits only real change) · advisory-locked (CAI-782 no-fork) ·
-- org-guarded fail-closed · actor_id=NULL + FULL authorization chain in payload
-- (CAI-897) · audit hash sha256(prev||canonicalPayloadJson) byte-identical to JS.
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
      (1, 'preparer', 'receipt:issue'),
      (2, 'cashier',  'person:edit'),
      (3, 'cashier',  'receipt:print')
    ) AS t(n, role, module) ORDER BY n
  LOOP
    v_eid := NULL;
    INSERT INTO org_role_permissions (org_id, role, module, access)
    VALUES (v_org, g.role, g.module, 'full')
    ON CONFLICT (org_id, role, module) DO UPDATE SET access = 'full'
      WHERE org_role_permissions.access IS DISTINCT FROM 'full'
    RETURNING id INTO v_eid;

    IF v_eid IS NOT NULL THEN
      -- CAI-897 FULL authorization chain, FLAT top-level keys (nested keys would be
      -- dropped by canonicalPayloadJson's replacer-allowlist + jsonb-normalised on
      -- readback = content-unverifiable). Keys sorted: access, applied_by,
      -- authorized_by_cai, authorized_by_op, module, role, source, stage.
      v_canon := '{"access":"full",'
              || '"applied_by":"hub-platform",'
              || '"authorized_by_cai":"CAI-896;CAI-897",'
              || '"authorized_by_op":"op#12889",'
              || '"module":"' || g.module || '",'
              || '"role":"'   || g.role   || '",'
              || '"source":"go-live-enablement-seed",'
              || '"stage":"1-non-send"}';
      v_hash := encode(sha256(convert_to(v_prev || v_canon, 'UTF8')), 'hex');
      INSERT INTO audit_log (org_id, actor_id, entity_type, entity_id, action, payload, prev_hash, hash)
      VALUES (v_org, NULL, 'org_role_permission', v_eid, 'update', v_canon::jsonb, v_prev, v_hash);
      v_prev := v_hash;
      v_grants := v_grants + 1;
    END IF;
  END LOOP;

  RAISE NOTICE 'op#12889 STAGE-1 seed: % grant(s) applied+audited for org % (0 = already enabled)', v_grants, v_org;
END $$;
