-- 090_held_commitments_payload_json_check.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- orch-console directive (bus #52039/#52060/#52061, 2026-10-05): held_commitments.payload
-- is TEXT and lanes kept writing plain prose into it, tripping the programme-stall-guard
-- (a JSON-shaped reader expected). orch-console wrapped 4 prose payloads by hand tonight;
-- my dry-run (bus #52060) found 8 MORE pre-existing rows that also violate JSON validity —
-- ids 3,4,7,8,12,13,15,16, all from 2026-08-17/18/24, all already terminal
-- (cancelled/discharged) — a cohort orch-console's "expect 0" didn't account for.
-- orch-console picked option (a) (#52061): wrap these 8 verbatim too, then add a FULLY
-- VALIDATED check (not NOT VALID).
--
-- This migration, in ONE transaction:
--   1. snapshots the 8 rows' current (prose) payload, so the wrap can be verified to
--      round-trip EXACTLY — no other column touched (status/updated_at/etc. untouched;
--      confirmed no trigger exists on this table, so a payload-only UPDATE has no
--      side effects).
--   2. wraps those 8 rows as {"note": <original text>} — content preserved byte-for-byte.
--   3. self-verifies BEFORE adding the CHECK, in-transaction, per orch-console's explicit
--      asserts: (a) violations = 0 across the WHOLE table, (b) each of the 8 wrapped rows'
--      (payload::jsonb->>'note') equals its pre-wrap original text exactly. RAISEs and
--      aborts the whole transaction (no partial apply) on any mismatch.
--   4. adds a VALIDATED check constraint so no future write can put prose back in.
--
-- pg_input_is_valid(text, 'jsonb') requires PG >= 16; this store runs PG 17.6 (verified
-- live via `select version()` before writing this file).

do $$
declare
  v_mismatch_id bigint;
  v_remaining_violations int;
  v_snapshot_count int;
begin
  create temporary table _payload_snapshot_090 as
    select id, payload as original_payload
    from held_commitments
    where id in (3, 4, 7, 8, 12, 13, 15, 16);

  select count(*) into v_snapshot_count from _payload_snapshot_090;
  if v_snapshot_count <> 8 then
    raise exception '090: expected exactly 8 snapshotted rows, got %', v_snapshot_count;
  end if;

  update held_commitments hc
  set payload = jsonb_build_object('note', s.original_payload)::text
  from _payload_snapshot_090 s
  where hc.id = s.id;

  -- assert: each wrapped row's note equals its ORIGINAL (pre-wrap) text, exactly
  select s.id into v_mismatch_id
  from _payload_snapshot_090 s
  join held_commitments hc on hc.id = s.id
  where (hc.payload::jsonb ->> 'note') is distinct from s.original_payload
  limit 1;

  if v_mismatch_id is not null then
    raise exception '090: wrapped payload for id % does not round-trip to the original text', v_mismatch_id;
  end if;

  -- assert: zero non-JSON payloads remain store-wide, before adding the CHECK
  select count(*) into v_remaining_violations
  from held_commitments
  where payload is not null and not pg_input_is_valid(payload, 'jsonb');

  if v_remaining_violations <> 0 then
    raise exception '090: % row(s) still violate JSON validity after the wrap — refusing to add the CHECK', v_remaining_violations;
  end if;

  raise notice '090: pre-ADD asserts passed (8 rows wrapped + round-trip verified, 0 remaining violations)';
end $$;

alter table held_commitments
  add constraint held_commitments_payload_valid_json
  check (payload is null or pg_input_is_valid(payload, 'jsonb'));
