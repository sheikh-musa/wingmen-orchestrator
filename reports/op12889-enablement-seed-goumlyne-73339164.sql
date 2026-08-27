-- =====================================================================
-- op#12889 GO-LIVE ENABLEMENT SEED — Madrasah Irsyad Zuhri (org 73339164)
-- Rulings: CAI-889 (design) / CAI-891 (wording) / CAI-895 (DPA) / CAI-896 (go-live GO)
-- APPLY ORDER (CAI-883, strict): 1) re-GRANT authenticated on write_audit_log_secure
-- BOTH silos -> 2) deploy #293 (+#300) -> 3) THIS SEED. Step 3 never before step 2.
-- SCOPE: goumlyne (prod Irsyad) org 73339164 ONLY. Off-by-default everywhere else.
--
-- Grants (org_role_permissions, access='full', mapped onto the `module` column
-- exactly as has_org_role_permission(p_org,p_role,p_permission) reads them):
--   preparer -> receipt:issue, receipt:send
--   cashier  -> receipt:send, person:edit, receipt:print
--
-- IDEMPOTENT + AUDIT-HONEST: a grant is upserted to 'full'; an audit_log row is
-- appended ONLY when the grant actually changed (new row or access flipped to
-- 'full'). Re-running is a no-op (0 grants changed, 0 audit rows). Audit hash =
-- sha256(prev || canonicalPayloadJson) — proven byte-identical to JS computeHash
-- (wet-proof 2026-08-14, all 5 rows canon+hash match). actor_id = NULL (platform
-- system seed, not an org_admin action; provenance carried in payload.source).
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
      (2, 'preparer', 'receipt:send'),
      (3, 'cashier',  'receipt:send'),
      (4, 'cashier',  'person:edit'),
      (5, 'cashier',  'receipt:print')
    ) AS t(n, role, module) ORDER BY n
  LOOP
    -- GRANT: upsert to 'full', but only UPDATE (and thus RETURN) on real change.
    -- If already 'full' the WHERE is false -> no row affected -> v_eid := NULL -> no audit.
    v_eid := NULL;
    INSERT INTO org_role_permissions (org_id, role, module, access)
    VALUES (v_org, g.role, g.module, 'full')
    ON CONFLICT (org_id, role, module) DO UPDATE SET access = 'full'
      WHERE org_role_permissions.access IS DISTINCT FROM 'full'
    RETURNING id INTO v_eid;

    IF v_eid IS NOT NULL THEN
      -- canonical payload: JSON.stringify(payload, sortedKeys), compact, keys sorted
      -- (access, module, role, source) — all controlled ASCII, no escaping needed.
      v_canon := '{"access":"full","module":"' || g.module ||
                 '","role":"' || g.role ||
                 '","source":"go-live-enablement-seed op#12889 CAI-896"}';
      -- hash = sha256(prev || canonical_json), byte-identical to JS computeHash
      v_hash := encode(sha256(convert_to(v_prev || v_canon, 'UTF8')), 'hex');
      INSERT INTO audit_log (org_id, actor_id, entity_type, entity_id, action, payload, prev_hash, hash)
      VALUES (v_org, NULL, 'org_role_permission', v_eid, 'update', v_canon::jsonb, v_prev, v_hash);
      v_prev := v_hash;      -- chain forward only on a real append
      v_grants := v_grants + 1;
    END IF;
  END LOOP;

  RAISE NOTICE 'op#12889 seed: % grant(s) applied+audited for org % (0 = already enabled)', v_grants, v_org;
END $$;
