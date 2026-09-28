-- 079_fleet_lessons.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Shared fleet lessons in the substrate (Musa op#23013 YES; orch-console bus #44851,
-- thread ee5797f6-e5f2-4c95-822f-4e3c8756cd2e). Moves fleet-wide "lessons" out of agents'
-- private memory files into one table every body's boot read can draw from, so a lesson one
-- lane earns the hard way doesn't die with that lane's context.
--
-- Modelled on a read-only study of Hermes' self-improvement memory (hermes-agent
-- background_review.py / tools/memory_tool.py), deliberately minus its staleness bug: Hermes'
-- memory has no verified-at concept and now contradicts itself about which host is live. Here,
-- last_verified_at + status='expired' is how a lesson ages out of the boot read instead of
-- quietly rotting in place. (The reviewer, staleness job, and boot-read wiring are separate,
-- code-only follow-ups per orch-console's ask — this migration is schema only.)
--
-- SCHEMA:
--   scope            'fleet' (every body) or 'repo:<name>' / 'client:<name>' (scoped read) --
--                     CHECK enforces the shape so a typo can't silently create an unreadable
--                     scope no boot-read query will ever match.
--   lesson/why/how_to_apply  mirrors this repo's own memory-file convention (rule, then WHY,
--                     then HOW TO APPLY) rather than inventing a new shape.
--   author_agent      plain text, NOT an FK to public.agents -- same reasoning as
--                     held_commitments.owner_agent (migration 051): the row must outlive the
--                     body that wrote it, including an ephemeral reviewer-hook session an FK
--                     to a live registry would risk rejecting.
--   normalized_hash   lesson_add.py's near-dup check is the primary gate (prose similarity,
--                     not just byte-equality); the unique index below is a DB-layer backstop
--                     that only catches an exact-text resubmission within the same active
--                     scope, not a substitute for that check.
--   status            active | superseded | expired. superseded_by links forward to the
--                     replacement so a chain of corrections stays readable, matching
--                     held_commitments' "never delete, the audit trail must survive" stance.
--
-- Per-scope character-cap enforcement (Hermes' 2200-char/scope forced-merge) is an
-- application-level SUM(char_length(lesson)) check in lesson_add.py, not a row-level
-- constraint here -- a cap is a fact about a scope's total, not about any single row.
--
-- Pure additive DDL (new table, no existing object touched) -- no privilege changes or
-- function removal, so no `-- assert:` lines are needed (house convention per migrations
-- 065/069/070/071/072/073/074/076/077/078 headers).
--
-- Apply via scripts/apply_migration.py 079 --silo tscuymavysscrvoberrr (direct psycopg-apply
-- path; decision 962 -- NEVER `supabase db push` against this substrate).
--
-- REVERT: DROP TABLE IF EXISTS public.fleet_lessons;

BEGIN;

CREATE TABLE IF NOT EXISTS public.fleet_lessons (
    id                bigserial   PRIMARY KEY,

    scope             text        NOT NULL
                                   CHECK (scope = 'fleet' OR scope ~ '^(repo|client):[a-z0-9_-]+$'),

    lesson            text        NOT NULL CHECK (char_length(lesson) BETWEEN 1 AND 4000),
    why               text,
    how_to_apply      text,

    source_ref        text,       -- op#/bus#/PR this lesson came from
    author_agent      text        NOT NULL,

    created_at        timestamptz NOT NULL DEFAULT now(),
    last_verified_at  timestamptz NOT NULL DEFAULT now(),

    status            text        NOT NULL DEFAULT 'active'
                                   CHECK (status IN ('active', 'superseded', 'expired')),
    superseded_by     bigint      REFERENCES public.fleet_lessons(id),

    normalized_hash   text        NOT NULL
);

COMMENT ON TABLE public.fleet_lessons IS
  'Fleet-wide shared lessons (Musa op#23013, bus #44851). Written only via scripts/lesson_add.py, never a hand INSERT -- it owns dedup, the per-scope character cap, and the injection/secret scan.';
COMMENT ON COLUMN public.fleet_lessons.scope IS
  '''fleet'' (every body''s boot read) or ''repo:<name>'' / ''client:<name>'' (scoped read).';
COMMENT ON COLUMN public.fleet_lessons.author_agent IS
  'Plain agent id, NOT an FK to public.agents -- same reasoning as held_commitments.owner_agent (051): the row must outlive the body that wrote it.';
COMMENT ON COLUMN public.fleet_lessons.last_verified_at IS
  'Ages out via the staleness reviewer: a lesson referencing hosts/leases/tokens/paths older than N days is flagged for re-verification; status=expired drops it from the boot read.';
COMMENT ON COLUMN public.fleet_lessons.normalized_hash IS
  'DB-layer backstop only -- catches an exact-text resubmission within the same active scope. lesson_add.py''s near-dup/similarity check is the primary gate.';

-- Exact-dup guard at the DB layer (defense in depth alongside lesson_add.py's near-dup check).
CREATE UNIQUE INDEX IF NOT EXISTS fleet_lessons_scope_hash_active_uidx
    ON public.fleet_lessons (scope, normalized_hash) WHERE status = 'active';

-- The boot read and the per-scope character-cap check both filter on (scope, status='active').
CREATE INDEX IF NOT EXISTS fleet_lessons_scope_status_idx ON public.fleet_lessons (scope, status);

-- The weekly staleness reviewer scans active lessons ordered by how long since verification.
CREATE INDEX IF NOT EXISTS fleet_lessons_last_verified_idx
    ON public.fleet_lessons (last_verified_at) WHERE status = 'active';

COMMIT;
