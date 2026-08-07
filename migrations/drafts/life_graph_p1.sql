-- DRAFT — life_graph P1 (life-context layer, "Jarvis" lever)
-- Pinned draft for cai §6.6 review under CAI-RESP-336 (approved-in-direction, 5 conditions).
-- NOT applied. Migration NNN assigned by CORE at apply (CAI-RESP-334 discipline).
-- Dry-run target: EPHEMERAL non-prod DB (CAI-RESP-356 mandate), never prod first.
--
-- Design refs: docs/superpowers/plans/2026-06-27-life-context-layer.md
-- Target DB: proposed NEW `wingmen-personal` project (ap-southeast-1 / Singapore) —
--   created there from day one so the operator's life data never touches the Sydney
--   monolith and never needs a later migration. See db-decomposition way-forward.
--
-- CAI-RESP-336 condition mapping:
--   C1 deny-by-default cross-slice isolation ... RLS deny-all + slice-gated SECDEF fns (§3,§4)
--   C2 embeddings are derived PII .............. same isolation; similarity is scope-filtered
--                                                INSIDE the SECDEF fn, before ranking (§4.2)
--   C3 calendar-write gate ..................... out of scope here (P3; no calendar DDL)
--   C4 amanah bar .............................. sensitivity tags; 'intimate' payloads
--                                                ciphertext-only (§2 trigger); purge fn (§5)
--   C5 periodic isolation-integrity audit ...... life_isolation_audit() (§6) for fleet self-audit
--
-- Slices model: a "slice" is a requesting context ('operator', 'fleet', 'gazzabyte',
-- 'shen', 'zahidah', ...). Deny-by-default: a row is visible to a non-operator slice
-- ONLY if that slice is explicitly in scope_slices AND sensitivity permits (§4.1).

-- ── 0. Extensions (already present in target: vector, pgcrypto) ───────────────
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── 1. Typed nodes ─────────────────────────────────────────────────────────────
CREATE TABLE life_entities (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_type  TEXT NOT NULL CHECK (entity_type IN
                 ('person','project','org','place','channel','preference','goal')),
  name         TEXT NOT NULL,
  attributes   JSONB NOT NULL DEFAULT '{}'::jsonb,   -- plaintext attrs (non-intimate only)
  attributes_enc BYTEA,                               -- ciphertext payload for 'intimate'
  sensitivity  TEXT NOT NULL DEFAULT 'personal' CHECK (sensitivity IN
                 ('fleet','partner','personal','intimate')),
  scope_slices TEXT[] NOT NULL DEFAULT '{}',          -- deny-by-default: empty = operator-only
  embedding    vector(1024),                          -- BGE-M3 via Mac Studio (no 3rd-party SaaS)
  embed_text   TEXT,                                  -- exactly what was embedded (auditability)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX life_entities_type_idx ON life_entities(entity_type);
CREATE INDEX life_entities_name_idx ON life_entities(lower(name));

-- 'intimate' rows must carry NO plaintext: attributes forced empty, embed_text NULL
-- (an embedding of intimate text is derived PII; C2/C4 — intimate rows are not embedded).
CREATE OR REPLACE FUNCTION life_intimate_guard() RETURNS trigger AS $$
BEGIN
  IF NEW.sensitivity = 'intimate' THEN
    IF NEW.attributes <> '{}'::jsonb OR NEW.embed_text IS NOT NULL
       OR NEW.embedding IS NOT NULL THEN
      RAISE EXCEPTION 'intimate entities carry ciphertext only (attributes_enc); no plaintext attrs/embeddings';
    END IF;
    IF NEW.scope_slices <> '{}' THEN
      RAISE EXCEPTION 'intimate entities are operator-only; scope_slices must be empty';
    END IF;
  END IF;
  NEW.updated_at = now();
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER life_entities_intimate_guard
  BEFORE INSERT OR UPDATE ON life_entities
  FOR EACH ROW EXECUTE FUNCTION life_intimate_guard();

-- ── 2. Typed edges ─────────────────────────────────────────────────────────────
CREATE TABLE life_relationships (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  from_entity  UUID NOT NULL REFERENCES life_entities(id) ON DELETE CASCADE,
  to_entity    UUID NOT NULL REFERENCES life_entities(id) ON DELETE CASCADE,
  rel_type     TEXT NOT NULL,                         -- 'is-on','belongs-to','maps-to','married-to',...
  attributes   JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (from_entity, to_entity, rel_type)
);
CREATE INDEX life_rel_from_idx ON life_relationships(from_entity);
CREATE INDEX life_rel_to_idx   ON life_relationships(to_entity);

-- Edge visibility is derived from BOTH endpoint nodes (an edge is as sensitive as
-- its most sensitive endpoint) — enforced in the traversal fn (§4.3), so there is
-- no way to learn an intimate node exists by seeing an edge that touches it.

-- ── 3. Commitments (dated obligations) ────────────────────────────────────────
CREATE TABLE life_commitments (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title        TEXT NOT NULL,
  due_at       TIMESTAMPTZ,
  status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','dropped')),
  owner_entity UUID REFERENCES life_entities(id) ON DELETE SET NULL,
  about_entity UUID REFERENCES life_entities(id) ON DELETE SET NULL,
  attributes   JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- F2 (CAI-RESP-360): no 'intimate' tier on commitments in P1 — the ciphertext/guard
  -- machinery exists only on life_entities; an intimate commitment models as an
  -- intimate ENTITY (goal/preference) until the pattern is extended deliberately.
  sensitivity  TEXT NOT NULL DEFAULT 'personal' CHECK (sensitivity IN
                 ('fleet','partner','personal')),
  scope_slices TEXT[] NOT NULL DEFAULT '{}',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX life_commitments_due_idx ON life_commitments(status, due_at);

-- ── RLS: deny-all to public on ALL three tables (service_role bypasses) ───────
ALTER TABLE life_entities      ENABLE ROW LEVEL SECURITY;
ALTER TABLE life_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE life_commitments   ENABLE ROW LEVEL SECURITY;
CREATE POLICY deny_all_life_entities      ON life_entities      FOR ALL TO public USING (false);
CREATE POLICY deny_all_life_relationships ON life_relationships FOR ALL TO public USING (false);
CREATE POLICY deny_all_life_commitments   ON life_commitments   FOR ALL TO public USING (false);
REVOKE ALL ON life_entities, life_relationships, life_commitments FROM PUBLIC, anon, authenticated;

-- ── 4. Slice-gated access (the ONLY read path for non-service callers) ────────
-- 4.1 The visibility predicate, in ONE place.
CREATE OR REPLACE FUNCTION life_slice_can_see(p_slice TEXT, p_sensitivity TEXT, p_scopes TEXT[])
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN p_slice = 'operator' THEN true                       -- his data, full view
    WHEN p_slice IS NULL OR p_slice = '' THEN false           -- unknown caller: nothing
    WHEN p_sensitivity IN ('personal','intimate') THEN false  -- never cross-slice
    ELSE p_slice = ANY(p_scopes)                              -- explicit grant only
  END
$$;

-- 4.2 Scope-filtered semantic recall (C2: filter BEFORE ranking; neighbours can
--     never include out-of-scope rows because they are excluded pre-ANN).
CREATE OR REPLACE FUNCTION life_recall(p_query vector(1024), p_slice TEXT, p_k INT DEFAULT 8)
RETURNS TABLE (id UUID, entity_type TEXT, name TEXT, attributes JSONB, distance FLOAT)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT e.id, e.entity_type, e.name, e.attributes,
         e.embedding <=> p_query AS distance
  FROM life_entities e
  WHERE e.embedding IS NOT NULL
    AND life_slice_can_see(p_slice, e.sensitivity, e.scope_slices)
  ORDER BY e.embedding <=> p_query
  LIMIT LEAST(p_k, 50)
$$;

-- 4.3 Scope-filtered traversal: neighbours of an entity, one hop. An edge is
--     visible only if BOTH endpoints are visible to the slice.
CREATE OR REPLACE FUNCTION life_neighbours(p_entity UUID, p_slice TEXT)
RETURNS TABLE (rel_type TEXT, direction TEXT, entity_id UUID, entity_name TEXT, entity_type TEXT)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT r.rel_type,
         CASE WHEN r.from_entity = p_entity THEN 'out' ELSE 'in' END,
         o.id, o.name, o.entity_type
  FROM life_relationships r
  JOIN life_entities s ON s.id = p_entity
  JOIN life_entities o ON o.id = CASE WHEN r.from_entity = p_entity
                                      THEN r.to_entity ELSE r.from_entity END
  WHERE (r.from_entity = p_entity OR r.to_entity = p_entity)
    AND life_slice_can_see(p_slice, s.sensitivity, s.scope_slices)
    AND life_slice_can_see(p_slice, o.sensitivity, o.scope_slices)
$$;

-- SECDEF fns are the only grant surface; callers get EXECUTE, never table access.
REVOKE ALL ON FUNCTION life_recall(vector, TEXT, INT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION life_neighbours(UUID, TEXT) FROM PUBLIC, anon, authenticated;
-- F1 (CAI-RESP-360, BINDING): p_slice is caller-asserted, so in P1 EXECUTE is granted
-- ONLY to the operator-context service role — no partner/vendor/agent role may hold
-- EXECUTE until slice derivation is identity-bound (per-slice DB roles + current_user,
-- or an identity->slice registry inside the fn). Gates the first non-operator consumer.

-- ── 5. Operator-controlled purge (C4) ─────────────────────────────────────────
CREATE OR REPLACE FUNCTION life_purge_entity(p_entity UUID)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  DELETE FROM life_relationships WHERE from_entity = p_entity OR to_entity = p_entity;
  UPDATE life_commitments SET owner_entity = NULL WHERE owner_entity = p_entity;
  UPDATE life_commitments SET about_entity = NULL WHERE about_entity = p_entity;
  DELETE FROM life_entities WHERE id = p_entity;   -- row + embedding + ciphertext gone together
END $$;
REVOKE ALL ON FUNCTION life_purge_entity(UUID) FROM PUBLIC, anon, authenticated;

-- ── 6. Isolation-integrity audit (C5 — fold into fleet self-audit) ───────────
CREATE OR REPLACE FUNCTION life_isolation_audit()
RETURNS TABLE (violation TEXT, entity_id UUID) LANGUAGE sql SECURITY DEFINER
SET search_path = public AS $$
  SELECT 'intimate row carries plaintext', id FROM life_entities
   WHERE sensitivity='intimate'
     AND (attributes <> '{}'::jsonb OR embed_text IS NOT NULL OR embedding IS NOT NULL)
  UNION ALL
  SELECT 'intimate row is slice-scoped', id FROM life_entities
   WHERE sensitivity='intimate' AND scope_slices <> '{}'
  UNION ALL
  SELECT 'personal row leaked into scope_slices', id FROM life_entities
   WHERE sensitivity IN ('personal','intimate') AND scope_slices <> '{}'
  UNION ALL
  -- F2 (CAI-RESP-360): audit covers commitments too
  SELECT 'personal commitment leaked into scope_slices', id FROM life_commitments
   WHERE sensitivity = 'personal' AND scope_slices <> '{}'
  UNION ALL
  SELECT 'RLS disabled', NULL::uuid FROM pg_tables
   WHERE schemaname='public' AND tablename LIKE 'life\_%' AND NOT rowsecurity
$$;
-- F3 (CAI-RESP-360): the audit fn is SECDEF too — same grant surface as the rest.
REVOKE ALL ON FUNCTION life_isolation_audit() FROM PUBLIC, anon, authenticated;
