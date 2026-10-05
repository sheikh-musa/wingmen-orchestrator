-- bedrock_substrate_core.sql — NOT a ledger-tracked migration (migrations/infra/ is excluded
-- from the normal `migrations/*.sql` walk and from `apply_migration.py`'s CLI).
--
-- cp#83 / backlog#68 (CI DB-integration bootstrap, held_commitments id=176, bus #46907/#47147):
-- these 11 tables + 1 view are referenced (ALTER/GRANT/trigger/FK) all over schema.sql,
-- supabase/migrations/*.sql and migrations/*.sql, but NONE of those three layers ever
-- CREATEs them — confirmed by grepping every "create table" in all three layers for each
-- name below and getting zero hits. They predate this repo's migration tracking entirely
-- (created by hand against the live substrate before any convention existed). Captured
-- read-only via `pg_dump --schema-only -t <name>` against the live orchestrator substrate
-- (tscuymavysscrvoberrr) on 2026-10-05, then hand-trimmed:
--   - the two pg_dump `\restrict`/`\unrestrict` meta-lines (psql-only, not valid SQL) removed.
--   - split OUT (-> bedrock_substrate_deferred.sql) every statement that forward-references
--     an object this repo's OWN migration chain creates LATER: the `console_readonly` role
--     (migrations/004) and the `mamadah_sources` table (migrations/012). Everything else
--     here is self-contained and safe to apply FIRST, before schema.sql.
--
-- Tables: agent_context, agent_messages, agents, repo_context, chat_members,
-- coord_dispatch_queue, coordinator_panes, fleet_lanes, fleet_stall_state,
-- identity_allowlist, invariant_registry, mamadah_notes, migration_ledger,
-- qa_findings, session_digests. View: agent_observed_activity.
--
-- fleet_lanes and agent_context both added 2026-10-05 on the same walk, surfaced by
-- migrations/003_cc_reviewer.sql and migrations/013_substrate_rls_grant_lockdown.sql
-- respectively — each has exactly one migrations/*.sql referrer that assumes it already
-- exists, none ever CREATEs it. Both FKs to agents(id) are safe here since agents is
-- created above in this same file; both console_readonly SELECT policies are deferred
-- (role doesn't exist yet) same as session_digests/cc_work_sessions below.
--
-- qa_findings is a special case: schema.sql DOES have a `create table if not exists
-- qa_findings (...)`, but for a repo_name/title/description/source design that
-- pg_dump confirms was never actually applied to the live substrate. The live table
-- is a different, role/flow/pass-fail-flaky shape that predates migration tracking
-- just like the rest of these — so it's captured here instead, and schema.sql's own
-- (stale) qa_findings block was neutralized to a comment so it doesn't fight this
-- one. See that file's comment for the full story and the open product question
-- (nervous_system/qa_bridge.py targets the OTHER, non-live shape).
--
-- migration_ledger is included here deliberately: apply_migration.py's own ledger-insert
-- target table is itself one of the never-committed phantom tables.

CREATE TABLE public.agent_messages (
    id bigint NOT NULL,
    thread_id uuid DEFAULT gen_random_uuid() NOT NULL,
    from_agent text NOT NULL,
    to_agent text,
    message_type text NOT NULL,
    subject text NOT NULL,
    body text NOT NULL,
    requires_response boolean DEFAULT false NOT NULL,
    responded_at timestamp with time zone,
    response_ref text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    read_at timestamp with time zone,
    claimed_by text,
    claimed_at timestamp with time zone,
    forwarded_to_telegram_at timestamp with time zone,
    priority text DEFAULT 'P2'::text NOT NULL,
    skipped_at timestamp with time zone,
    sub_tag text,
    posted_by_identity text,
    from_agent_verified boolean,
    cai_session_id text,
    is_test boolean DEFAULT false NOT NULL,
    project text,
    CONSTRAINT agent_messages_message_type_check CHECK ((message_type = ANY (ARRAY['review_request'::text, 'question'::text, 'decision'::text, 'agreed'::text, 'challenge'::text, 'update'::text, 'blocker'::text, 'counter'::text]))),
    CONSTRAINT agent_messages_priority_check CHECK ((priority = ANY (ARRAY['P0'::text, 'P1'::text, 'P2'::text, 'P3'::text]))),
    CONSTRAINT agent_messages_sub_tag_family_prefix_chk CHECK (((sub_tag IS NULL) OR ((length(from_agent) > 0) AND ("left"(sub_tag, (length(from_agent) + 1)) = (from_agent || '-'::text)))))
);

ALTER TABLE public.agent_messages ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.agent_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE VIEW public.agent_observed_activity WITH (security_invoker='on') AS
 SELECT from_agent AS agent_id,
    max(created_at) AS last_observed_at
   FROM public.agent_messages am
  WHERE ((from_agent IS NOT NULL) AND (is_test IS NOT TRUE))
  GROUP BY from_agent;

CREATE TABLE public.agents (
    id text NOT NULL,
    display_name text NOT NULL,
    repo_scope text[] DEFAULT '{}'::text[] NOT NULL,
    status text DEFAULT 'idle'::text NOT NULL,
    current_task text,
    last_heartbeat timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agents_status_check CHECK ((status = ANY (ARRAY['active'::text, 'idle'::text, 'blocked'::text, 'offline'::text])))
);

CREATE TABLE public.repo_context (
    id bigint NOT NULL,
    repo text NOT NULL,
    current_phase text,
    architecture_summary text,
    active_modules text[] DEFAULT ARRAY[]::text[],
    planned_modules text[],
    known_debt text[],
    blockers text[],
    recent_changes text,
    test_health text,
    deploy_url text,
    active_constraints text[],
    decision_refs text[],
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text DEFAULT 'claude_code'::text NOT NULL
);

ALTER TABLE public.repo_context ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.repo_context_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.chat_members (
    chat_id text NOT NULL,
    user_id text NOT NULL,
    username text,
    display_name text,
    known_label text,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    msg_count integer DEFAULT 0 NOT NULL
);

CREATE TABLE public.coord_dispatch_queue (
    id bigint NOT NULL,
    title text NOT NULL,
    spec_ref text,
    family text DEFAULT 'cc-irsyad'::text NOT NULL,
    priority text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_by text,
    claimed_at timestamp with time zone,
    done_at timestamp with time zone,
    blocked_on text,
    blocked_since timestamp with time zone,
    CONSTRAINT coord_dispatch_queue_blocked_ck CHECK ((((blocked_on IS NULL) AND (blocked_since IS NULL)) OR ((blocked_on IS NOT NULL) AND (btrim(blocked_on) <> ''::text) AND (blocked_since IS NOT NULL))))
);

CREATE SEQUENCE public.coord_dispatch_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.coord_dispatch_queue_id_seq OWNED BY public.coord_dispatch_queue.id;

ALTER TABLE ONLY public.coord_dispatch_queue ALTER COLUMN id SET DEFAULT nextval('public.coord_dispatch_queue_id_seq'::regclass);

CREATE TABLE public.coordinator_panes (
    agent_id text NOT NULL,
    pane_text text NOT NULL,
    captured_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.fleet_stall_state (
    lane text NOT NULL,
    stalled_since timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_stalled timestamp with time zone DEFAULT now() NOT NULL,
    alerted_at timestamp with time zone,
    last_produce timestamp with time zone
);

CREATE TABLE public.identity_allowlist (
    posted_by text NOT NULL,
    allowed_from_agent text NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.invariant_registry (
    invariant_ref text NOT NULL,
    domain text NOT NULL,
    statement text NOT NULL,
    gate_ref text,
    gate_status text DEFAULT 'MANUAL'::text NOT NULL,
    severity text,
    origin_incident text,
    last_asserted_at timestamp with time zone,
    stewarded_by text DEFAULT 'cai'::text NOT NULL,
    seeded_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT invariant_registry_gate_status_check CHECK ((gate_status = ANY (ARRAY['COVERED'::text, 'MANUAL'::text, 'pending'::text])))
);

-- NOTE: embedding uses the real pgvector type (public.vector), matching the live substrate's
-- `CREATE EXTENSION vector WITH SCHEMA public`. The platform-object shim in
-- scripts/ci_bootstrap_schema.py installs pgvector before this file runs.
CREATE TABLE public.mamadah_notes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    chat_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source_type text NOT NULL,
    source_name text,
    subject_tag text,
    content text NOT NULL,
    embedding public.vector(1024),
    chunk_index integer DEFAULT 0 NOT NULL,
    source_id uuid,
    CONSTRAINT mamadah_notes_source_type_check CHECK ((source_type = ANY (ARRAY['photo'::text, 'pdf'::text, 'text_file'::text, 'manual'::text, 'summary'::text])))
);

CREATE TABLE public.migration_ledger (
    repo text NOT NULL,
    migration_name text NOT NULL,
    silo_ref text NOT NULL,
    sha256 text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    applied_by text,
    note text
);

ALTER TABLE ONLY public.migration_ledger
    ADD CONSTRAINT migration_ledger_pkey PRIMARY KEY (repo, migration_name, silo_ref);

ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_pkey PRIMARY KEY (id);

ALTER TABLE public.agent_messages
    ADD CONSTRAINT agent_messages_reject_pseudo_targets CHECK ((to_agent <> 'substrate'::text)) NOT VALID;

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_pkey PRIMARY KEY (id);

-- Seed row: 'cc-orchestrator' is referenced (migrations/016_orch_lease.sql's
-- orch_lease.holder FK, migrations/066/067's protected_agents UPDATEs) but
-- never INSERTed anywhere in schema.sql/supabase/migrations/migrations --
-- same hand-created-on-the-live-substrate-before-migration-tracking class as
-- the rest of this file. Confirmed via the walk: migration 016 fails
-- ForeignKeyViolation on orch_lease_holder_fkey without this row.
INSERT INTO public.agents (id, display_name) VALUES ('cc-orchestrator', 'cc-orchestrator')
ON CONFLICT (id) DO NOTHING;

ALTER TABLE ONLY public.chat_members
    ADD CONSTRAINT chat_members_pkey PRIMARY KEY (chat_id, user_id);

ALTER TABLE ONLY public.coord_dispatch_queue
    ADD CONSTRAINT coord_dispatch_queue_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.coordinator_panes
    ADD CONSTRAINT coordinator_panes_pkey PRIMARY KEY (agent_id);

ALTER TABLE ONLY public.fleet_stall_state
    ADD CONSTRAINT fleet_stall_state_pkey PRIMARY KEY (lane);

ALTER TABLE ONLY public.identity_allowlist
    ADD CONSTRAINT identity_allowlist_pkey PRIMARY KEY (posted_by, allowed_from_agent);

ALTER TABLE ONLY public.invariant_registry
    ADD CONSTRAINT invariant_registry_pkey PRIMARY KEY (invariant_ref);

ALTER TABLE ONLY public.mamadah_notes
    ADD CONSTRAINT mamadah_notes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.repo_context
    ADD CONSTRAINT one_per_repo UNIQUE (repo);

ALTER TABLE ONLY public.repo_context
    ADD CONSTRAINT repo_context_pkey PRIMARY KEY (id);

CREATE INDEX agent_messages_forwarded_idx ON public.agent_messages USING btree (forwarded_to_telegram_at) WHERE (forwarded_to_telegram_at IS NULL);
CREATE INDEX agent_messages_skipped_idx ON public.agent_messages USING btree (skipped_at) WHERE (skipped_at IS NOT NULL);
CREATE INDEX idx_agent_messages_cai_session ON public.agent_messages USING btree (cai_session_id, created_at DESC) WHERE (from_agent = 'cai'::text);
CREATE INDEX idx_agent_messages_claim ON public.agent_messages USING btree (to_agent, claimed_by, claimed_at) WHERE ((claimed_by IS NULL) OR (claimed_at IS NOT NULL));
CREATE INDEX idx_agent_messages_from_id ON public.agent_messages USING btree (from_agent, id DESC);
CREATE INDEX idx_agent_messages_from_recent ON public.agent_messages USING btree (from_agent, created_at DESC);
CREATE INDEX idx_agent_messages_open_by_priority ON public.agent_messages USING btree (priority, created_at) WHERE (read_at IS NULL);
CREATE INDEX idx_agent_messages_response_queue ON public.agent_messages USING btree (to_agent, responded_at) WHERE (requires_response = true);
CREATE INDEX idx_agent_messages_sub_tag ON public.agent_messages USING btree (from_agent, sub_tag) WHERE (sub_tag IS NOT NULL);
CREATE INDEX idx_agent_messages_thread ON public.agent_messages USING btree (thread_id);
CREATE INDEX idx_coord_dispatch_queue_unclaimed ON public.coord_dispatch_queue USING btree (created_at) WHERE (claimed_by IS NULL);
CREATE INDEX mamadah_notes_chat_created ON public.mamadah_notes USING btree (chat_id, created_at DESC);
CREATE INDEX mamadah_notes_fts ON public.mamadah_notes USING gin (to_tsvector('english'::regconfig, COALESCE(content, ''::text)));
CREATE INDEX mamadah_notes_source_chunk_idx ON public.mamadah_notes USING btree (source_id, chunk_index) WHERE (source_id IS NOT NULL);

-- Trigger function bodies captured via pg_get_functiondef() against the live substrate
-- (2026-10-05) — also never committed anywhere in this repo (grepped zero hits).
CREATE OR REPLACE FUNCTION public.agent_messages_cai_gate() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public'
AS $function$
DECLARE
  p TEXT;
BEGIN
  p := COALESCE(NEW.project, public.project_for_agent(NEW.from_agent));
  IF p IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.project_governance g
     WHERE g.project = p AND g.cai_enabled = false
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = 'check_violation',
      MESSAGE = format(
        'cai is OFF for project %s (op#20708): route this to orch-console '
        '(operator-domain), not cai', p
      );
  END IF;
  RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.populate_agent_messages_provenance() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public', 'pg_temp'
AS $function$
BEGIN
  IF NEW.posted_by_identity IS NULL THEN
    NEW.posted_by_identity := current_user;
  END IF;

  IF NEW.from_agent_verified IS NULL THEN
    IF EXISTS (
      SELECT 1 FROM identity_allowlist
       WHERE posted_by = NEW.posted_by_identity
         AND allowed_from_agent = NEW.from_agent
    ) THEN
      NEW.from_agent_verified := true;
    ELSE
      NEW.from_agent_verified := NULL;
    END IF;
  END IF;

  RETURN NEW;
END;
$function$;

CREATE TRIGGER trg_agent_messages_cai_gate BEFORE INSERT ON public.agent_messages FOR EACH ROW WHEN (((new.to_agent = 'cai'::text) AND (new.message_type = ANY (ARRAY['review_request'::text, 'question'::text, 'blocker'::text, 'challenge'::text])))) EXECUTE FUNCTION public.agent_messages_cai_gate();
CREATE TRIGGER trg_agent_messages_provenance BEFORE INSERT ON public.agent_messages FOR EACH ROW EXECUTE FUNCTION public.populate_agent_messages_provenance();
-- trg_agents_single_owner_repo_scope is NOT created here: its function
-- (agents_single_owner_repo_scope) is created in-chain by migrations/043, which runs
-- AFTER this file. bedrock_substrate_deferred.sql adds this trigger at the very end.

ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_from_agent_fkey FOREIGN KEY (from_agent) REFERENCES public.agents(id);

ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_to_agent_fkey FOREIGN KEY (to_agent) REFERENCES public.agents(id);

-- mamadah_notes_source_id_fkey (-> mamadah_sources) is deferred: mamadah_sources is created
-- in-chain by migrations/012, which runs AFTER this file.

ALTER TABLE public.agent_messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_messages_service_only ON public.agent_messages TO service_role USING (true) WITH CHECK (true);
CREATE POLICY cto_desktop_insert_self ON public.agent_messages FOR INSERT TO cto_desktop WITH CHECK ((from_agent = 'cto-desktop'::text));
CREATE POLICY cto_desktop_read_own ON public.agent_messages FOR SELECT TO cto_desktop USING (((to_agent = 'cto-desktop'::text) OR (from_agent = 'cto-desktop'::text)));

ALTER TABLE public.agents ENABLE ROW LEVEL SECURITY;
CREATE POLICY agents_service_only ON public.agents TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.chat_members ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.coord_dispatch_queue ENABLE ROW LEVEL SECURITY;
CREATE POLICY coord_dispatch_queue_service_only ON public.coord_dispatch_queue TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.coordinator_panes ENABLE ROW LEVEL SECURITY;
CREATE POLICY coordinator_panes_service_only ON public.coordinator_panes TO service_role USING (true);

ALTER TABLE public.fleet_stall_state ENABLE ROW LEVEL SECURITY;
CREATE POLICY fleet_stall_state_service_only ON public.fleet_stall_state TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.identity_allowlist ENABLE ROW LEVEL SECURITY;
CREATE POLICY identity_allowlist_service_only ON public.identity_allowlist TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.invariant_registry ENABLE ROW LEVEL SECURITY;
CREATE POLICY deny_all_invariant_registry ON public.invariant_registry USING (false);

ALTER TABLE public.mamadah_notes ENABLE ROW LEVEL SECURITY;
CREATE POLICY service_role_only_mamadah_notes ON public.mamadah_notes USING (false);

ALTER TABLE public.repo_context ENABLE ROW LEVEL SECURITY;
CREATE POLICY repo_context_service_only ON public.repo_context TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.migration_ledger ENABLE ROW LEVEL SECURITY;
CREATE POLICY deny_all_migration_ledger ON public.migration_ledger USING (false);

-- qa_findings — see this file's header comment for why it's here instead of a
-- straightforward ADD COLUMN patch to schema.sql's existing block.
CREATE TABLE public.qa_findings (
    id bigint NOT NULL,
    repo text NOT NULL,
    role text NOT NULL,
    flow text NOT NULL,
    status text NOT NULL,
    screenshot text,
    error text,
    found_at timestamp with time zone DEFAULT now(),
    resolved_at timestamp with time zone,
    deleted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    severity text,
    severity_classified_at timestamp with time zone,
    severity_classifier text,
    CONSTRAINT qa_findings_severity_check CHECK (((severity IS NULL) OR (severity = ANY (ARRAY['P0'::text, 'P1'::text, 'P2'::text, 'P3'::text])))),
    CONSTRAINT qa_findings_severity_classifier_shape CHECK ((((severity IS NULL) AND (severity_classifier IS NULL) AND (severity_classified_at IS NULL)) OR ((severity IS NOT NULL) AND (severity_classifier IS NOT NULL) AND (severity_classified_at IS NOT NULL)))),
    CONSTRAINT qa_findings_status_check CHECK ((status = ANY (ARRAY['pass'::text, 'fail'::text, 'flaky'::text])))
);

COMMENT ON TABLE public.qa_findings IS 'Synthetic user test results. Every agent (CI, Playwright, future QA bots) writes here. wingmen_brain snapshot picks this up for a live picture of what is broken. Origin: claude.ai recommendation after the supplier portal RLS bug (2026-04-12).';

CREATE SEQUENCE public.qa_findings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.qa_findings_id_seq OWNED BY public.qa_findings.id;

ALTER TABLE ONLY public.qa_findings ALTER COLUMN id SET DEFAULT nextval('public.qa_findings_id_seq'::regclass);

ALTER TABLE ONLY public.qa_findings
    ADD CONSTRAINT qa_findings_pkey PRIMARY KEY (id);

CREATE INDEX idx_qa_findings_repo_role ON public.qa_findings USING btree (repo, role);
CREATE INDEX idx_qa_findings_severity ON public.qa_findings USING btree (severity) WHERE (severity IS NOT NULL);
CREATE INDEX idx_qa_findings_severity_pending ON public.qa_findings USING btree (found_at) WHERE (severity IS NULL);
CREATE INDEX idx_qa_findings_status ON public.qa_findings USING btree (status) WHERE (deleted_at IS NULL);
CREATE INDEX idx_qa_findings_unresolved ON public.qa_findings USING btree (found_at DESC) WHERE ((resolved_at IS NULL) AND (deleted_at IS NULL));
CREATE INDEX qa_findings_created_at_idx ON public.qa_findings USING btree (created_at DESC);

ALTER TABLE public.qa_findings ENABLE ROW LEVEL SECURITY;
CREATE POLICY service_role_only_insert_qa ON public.qa_findings FOR INSERT WITH CHECK ((auth.role() = 'service_role'::text));
CREATE POLICY service_role_only_select_qa ON public.qa_findings FOR SELECT USING ((auth.role() = 'service_role'::text));
CREATE POLICY service_role_only_update_qa ON public.qa_findings FOR UPDATE USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));

-- session_digests — zero CREATE anywhere in any of the 3 committed layers (same
-- class as the 11 tables above), yet referenced by schema.sql's own boot_briefing
-- view (`latest_digest` arm, aliased `sd`/`dig`).
CREATE TABLE public.session_digests (
    id bigint NOT NULL,
    session_date date NOT NULL,
    title text NOT NULL,
    topics_covered text[] NOT NULL,
    key_reasoning text NOT NULL,
    decisions_made text[] NOT NULL,
    open_questions text[],
    action_items text[],
    chat_url text,
    council_sessions_referenced bigint[],
    external_contacts text[],
    repos_discussed text[],
    source text DEFAULT 'claude_ai_session'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.session_digests IS 'Extracted before conversation deletion. Contains reasoning chains, rejected alternatives, and exploration context that strategic_decisions does not capture. The institutional memory of HOW decisions were reached.';

ALTER TABLE public.session_digests ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.session_digests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE ONLY public.session_digests
    ADD CONSTRAINT session_digests_pkey PRIMARY KEY (id);

ALTER TABLE public.session_digests ENABLE ROW LEVEL SECURITY;
CREATE POLICY session_digests_service_only ON public.session_digests TO service_role USING (true) WITH CHECK (true);
-- session_digests_console_ro (TO console_readonly) deferred — see bedrock_substrate_deferred.sql.

-- fleet_lanes — zero CREATE anywhere in any of the 3 committed layers (same class as
-- the tables above); migrations/003_cc_reviewer.sql is the first of 11 migrations/*.sql
-- files that ALTER/INSERT into it assuming it already exists.
CREATE TABLE public.fleet_lanes (
    lane text NOT NULL,
    worktree_path text,
    branch text,
    model text DEFAULT 'claude-opus-4-8'::text NOT NULL,
    launcher text DEFAULT 'launch_dangerous_cc.sh'::text NOT NULL,
    base_agent_id text,
    desired_state text DEFAULT 'up'::text NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fleet_lanes_desired_state_check CHECK ((desired_state = ANY (ARRAY['up'::text, 'down'::text, 'paused'::text, 'on-demand'::text]))),
    CONSTRAINT fleet_lanes_launcher_check CHECK ((launcher = ANY (ARRAY['launch_dangerous_cc.sh'::text, 'boot_cai.sh'::text, 'boot_fleet_health.sh'::text, 'boot_quality.sh'::text])))
);

COMMENT ON COLUMN public.fleet_lanes.branch IS 'REGISTRATION-TIME SNAPSHOT, informational only -- NOT authoritative and NOT maintained. A lane real branch is LIVE state (tmux pane_current_path / git HEAD in the worktree); this column is written once at registration and drifts freely (14/26 lanes drifted 2026-08-18). Never key tooling on it -- recycle/discovery deliberately resolves worktree from live cwd (Phase-3.5 wrong-worktree fix). See memory fleet-lanes-branch-is-vestigial / bus #27924.';

ALTER TABLE ONLY public.fleet_lanes
    ADD CONSTRAINT fleet_lanes_pkey PRIMARY KEY (lane);

CREATE INDEX idx_fleet_lanes_desired ON public.fleet_lanes USING btree (desired_state);

ALTER TABLE ONLY public.fleet_lanes
    ADD CONSTRAINT fleet_lanes_base_agent_id_fkey FOREIGN KEY (base_agent_id) REFERENCES public.agents(id);

ALTER TABLE public.fleet_lanes ENABLE ROW LEVEL SECURITY;
CREATE POLICY fleet_lanes_service_only ON public.fleet_lanes TO service_role USING (true) WITH CHECK (true);
-- fleet_lanes_console_ro (TO console_readonly) deferred — see bedrock_substrate_deferred.sql.

-- agent_context — zero CREATE anywhere in any of the 3 committed layers (same class as
-- fleet_lanes above); migrations/013_substrate_rls_grant_lockdown.sql is its sole referrer.
CREATE TABLE public.agent_context (
    agent_id text NOT NULL,
    active_decision_refs text[] DEFAULT '{}'::text[] NOT NULL,
    pending_review_refs text[] DEFAULT '{}'::text[] NOT NULL,
    current_blockers text[] DEFAULT '{}'::text[] NOT NULL,
    repo_health jsonb DEFAULT '{}'::jsonb NOT NULL,
    session_notes text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.agent_context
    ADD CONSTRAINT agent_context_pkey PRIMARY KEY (agent_id);

ALTER TABLE ONLY public.agent_context
    ADD CONSTRAINT agent_context_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE CASCADE;

ALTER TABLE public.agent_context ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_context_service_only ON public.agent_context TO service_role USING (true) WITH CHECK (true);
-- agent_context_console_ro (TO console_readonly) deferred — see bedrock_substrate_deferred.sql.

-- profiles, organizations, anchor_batches, site_templates — NOT referenced by name by any
-- migrations/*.sql statement; surfaced transitively while backfilling the 14 tables below
-- (anchor_queue -> anchor_batches -> organizations -> profiles -> auth.users; client_repos/
-- provisions/usage_log -> clients, already captured elsewhere; provisions -> site_templates).
-- Captured read-only via pg_dump against the live substrate on 2026-10-05, then trimmed to
-- the minimum needed for FK integrity -- none of these 4 are migration 013's actual target,
-- they only need to EXIST with compatible columns:
--   - every CREATE TRIGGER dropped (public.update_updated_at() is itself uncommitted anywhere
--     in the 3 layers; cosmetic for a CI schema build that writes zero rows).
--   - organizations.fee_tier / fee_tier_verification narrowed from two live-only enum types
--     (public.fee_tier, public.fee_tier_verification_status — also uncommitted anywhere) to
--     plain text; nothing in the walk inserts/selects these columns.
--   - organizations' 3 live RLS policies dropped: all three gate on auth.uid(), which this
--     file's auth schema stub deliberately does NOT provide (see ci_bootstrap_schema.py's
--     auth.role() comment) -- adding auth.uid() here just to satisfy an incidental dependency
--     of a transitive dependency would be scope creep. RLS stays enabled with zero policies
--     (default-deny), which is schema-valid and irrelevant since nothing in the walk queries
--     these tables' rows.
CREATE TABLE public.profiles (
    id uuid NOT NULL,
    display_name text NOT NULL,
    phone text,
    avatar_url text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_id_fkey FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE;

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

CREATE TABLE public.organizations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    uen text,
    type text,
    settings jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    slug text,
    org_tags text[] DEFAULT '{}'::text[] NOT NULL,
    fee_tier text,
    fee_tier_verification text DEFAULT 'pending'::text NOT NULL,
    fee_tier_verified_at timestamp with time zone,
    fee_tier_verified_by uuid,
    CONSTRAINT organizations_type_check CHECK ((type = ANY (ARRAY['mosque'::text, 'madrasah'::text, 'ngo'::text, 'school'::text, 'business'::text, 'platform'::text, 'restaurant'::text, 'retail'::text, 'services'::text, 'home_business'::text, 'freelancer'::text])))
);

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_slug_key UNIQUE (slug);

CREATE INDEX idx_organizations_slug ON public.organizations USING btree (slug);

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_fee_tier_verified_by_fkey FOREIGN KEY (fee_tier_verified_by) REFERENCES public.profiles(id);

ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;

CREATE TABLE public.anchor_batches (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    batch_from bigint NOT NULL,
    batch_to bigint NOT NULL,
    entry_count integer NOT NULL,
    merkle_root text NOT NULL,
    solana_tx_id text,
    solana_slot bigint,
    memo_payload jsonb NOT NULL,
    status text NOT NULL,
    trigger_reason text NOT NULL,
    submitted_at timestamp with time zone,
    finalized_at timestamp with time zone,
    failure_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT anchor_batches_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'submitted'::text, 'finalized'::text, 'failed'::text]))),
    CONSTRAINT anchor_batches_trigger_reason_check CHECK ((trigger_reason = ANY (ARRAY['count'::text, 'hourly'::text, 'high_value'::text, 'manual'::text])))
);

CREATE SEQUENCE public.anchor_batches_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.anchor_batches_id_seq OWNED BY public.anchor_batches.id;

ALTER TABLE ONLY public.anchor_batches ALTER COLUMN id SET DEFAULT nextval('public.anchor_batches_id_seq'::regclass);

ALTER TABLE ONLY public.anchor_batches
    ADD CONSTRAINT anchor_batches_org_id_batch_from_batch_to_key UNIQUE (org_id, batch_from, batch_to);

ALTER TABLE ONLY public.anchor_batches
    ADD CONSTRAINT anchor_batches_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.anchor_batches
    ADD CONSTRAINT anchor_batches_public_id_key UNIQUE (public_id);

CREATE INDEX idx_anchor_batches_audit_range ON public.anchor_batches USING btree (org_id, batch_from, batch_to);

CREATE INDEX idx_anchor_batches_org_status ON public.anchor_batches USING btree (org_id, status);

ALTER TABLE ONLY public.anchor_batches
    ADD CONSTRAINT anchor_batches_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);

ALTER TABLE public.anchor_batches ENABLE ROW LEVEL SECURITY;

CREATE TABLE public.site_templates (
    id text NOT NULL,
    name text NOT NULL,
    description text NOT NULL,
    github_template text NOT NULL,
    default_config jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.site_templates
    ADD CONSTRAINT site_templates_pkey PRIMARY KEY (id);

ALTER TABLE public.site_templates ENABLE ROW LEVEL SECURITY;

-- The 14 tables below ARE migrations/013_substrate_rls_grant_lockdown.sql's actual targets
-- (batch-diffed cp#83: every table name 013 references, minus every CREATE TABLE across all
-- 4 layers = exactly these 14). Same zero-CREATE-anywhere class as fleet_lanes/agent_context
-- above. Captured read-only via pg_dump on 2026-10-05; console_ro policies deferred same as
-- everything else in this file (console_readonly role doesn't exist yet at this point in the
-- walk) -- see bedrock_substrate_deferred.sql.
CREATE TABLE public.anchor_queue (
    id bigint NOT NULL,
    batch_id bigint NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    last_attempt_at timestamp with time zone,
    last_error text,
    queued_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone
);

CREATE SEQUENCE public.anchor_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.anchor_queue_id_seq OWNED BY public.anchor_queue.id;

ALTER TABLE ONLY public.anchor_queue ALTER COLUMN id SET DEFAULT nextval('public.anchor_queue_id_seq'::regclass);

ALTER TABLE ONLY public.anchor_queue
    ADD CONSTRAINT anchor_queue_batch_id_key UNIQUE (batch_id);

ALTER TABLE ONLY public.anchor_queue
    ADD CONSTRAINT anchor_queue_pkey PRIMARY KEY (id);

CREATE INDEX idx_anchor_queue_unresolved ON public.anchor_queue USING btree (queued_at) WHERE (resolved_at IS NULL);

ALTER TABLE ONLY public.anchor_queue
    ADD CONSTRAINT anchor_queue_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.anchor_batches(id);

ALTER TABLE public.anchor_queue ENABLE ROW LEVEL SECURITY;
CREATE POLICY anchor_queue_service_only ON public.anchor_queue TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.brain_sync_log (
    id bigint NOT NULL,
    task_name text NOT NULL,
    status text DEFAULT 'success'::text NOT NULL,
    duration_ms integer,
    details text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.brain_sync_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.brain_sync_log_id_seq OWNED BY public.brain_sync_log.id;

ALTER TABLE ONLY public.brain_sync_log ALTER COLUMN id SET DEFAULT nextval('public.brain_sync_log_id_seq'::regclass);

ALTER TABLE ONLY public.brain_sync_log
    ADD CONSTRAINT brain_sync_log_pkey PRIMARY KEY (id);

ALTER TABLE public.brain_sync_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY brain_sync_log_service_only ON public.brain_sync_log TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.challenge_enforcer_dryrun_log (
    id bigint NOT NULL,
    decision_ref text NOT NULL,
    current_challenge_status text NOT NULL,
    challengeable_until timestamp with time zone,
    proposed_new_status text DEFAULT 'accepted_by_timeout'::text NOT NULL,
    logged_at timestamp with time zone DEFAULT now() NOT NULL,
    processed boolean DEFAULT false NOT NULL,
    review_outcome text,
    review_notes text
);

CREATE SEQUENCE public.challenge_enforcer_dryrun_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.challenge_enforcer_dryrun_log_id_seq OWNED BY public.challenge_enforcer_dryrun_log.id;

ALTER TABLE ONLY public.challenge_enforcer_dryrun_log ALTER COLUMN id SET DEFAULT nextval('public.challenge_enforcer_dryrun_log_id_seq'::regclass);

ALTER TABLE ONLY public.challenge_enforcer_dryrun_log
    ADD CONSTRAINT challenge_enforcer_dryrun_log_decision_ref_key UNIQUE (decision_ref);

ALTER TABLE ONLY public.challenge_enforcer_dryrun_log
    ADD CONSTRAINT challenge_enforcer_dryrun_log_pkey PRIMARY KEY (id);

CREATE INDEX idx_dryrun_log_unprocessed ON public.challenge_enforcer_dryrun_log USING btree (logged_at) WHERE (processed = false);

ALTER TABLE public.challenge_enforcer_dryrun_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY challenge_enforcer_dryrun_log_service_only ON public.challenge_enforcer_dryrun_log TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.client_repos (
    id bigint NOT NULL,
    client_id bigint NOT NULL,
    repo_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.client_repos ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.client_repos_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE ONLY public.client_repos
    ADD CONSTRAINT client_repos_client_id_repo_name_key UNIQUE (client_id, repo_name);

ALTER TABLE ONLY public.client_repos
    ADD CONSTRAINT client_repos_pkey PRIMARY KEY (id);

-- client_repos_client_id_fkey deferred: public.clients is created by schema.sql, which
-- runs AFTER this file -- see bedrock_substrate_deferred.sql.

ALTER TABLE public.client_repos ENABLE ROW LEVEL SECURITY;
CREATE POLICY client_repos_service_only ON public.client_repos TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.deploy_status (
    workstream text NOT NULL,
    repo text NOT NULL,
    stage text NOT NULL,
    detail text,
    url text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text DEFAULT 'cc-orchestrator'::text NOT NULL,
    CONSTRAINT deploy_status_stage_check CHECK ((stage = ANY (ARRAY['pending'::text, 'pushed'::text, 'in_review'::text, 'merged'::text, 'live'::text, 'blocked'::text])))
);

ALTER TABLE ONLY public.deploy_status
    ADD CONSTRAINT deploy_status_pkey PRIMARY KEY (workstream);

ALTER TABLE public.deploy_status ENABLE ROW LEVEL SECURITY;
CREATE POLICY deploy_status_service_only ON public.deploy_status TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.field_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    project text NOT NULL,
    event_type text DEFAULT 'update'::text NOT NULL,
    summary text NOT NULL,
    next_action text,
    waiting_since timestamp with time zone,
    expected_by timestamp with time zone,
    resolved boolean DEFAULT false,
    resolved_at timestamp with time zone,
    priority text DEFAULT 'normal'::text,
    created_at timestamp with time zone DEFAULT now()
);

ALTER TABLE ONLY public.field_events
    ADD CONSTRAINT field_events_pkey PRIMARY KEY (id);

CREATE INDEX field_events_created_idx ON public.field_events USING btree (created_at DESC);

CREATE INDEX field_events_project_idx ON public.field_events USING btree (project);

CREATE INDEX field_events_resolved_idx ON public.field_events USING btree (resolved, expected_by);

ALTER TABLE public.field_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY field_events_service_only ON public.field_events TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.ingestion_manifest (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    manifest_label text NOT NULL,
    target_tables text[] NOT NULL,
    target_corpus_label text,
    source_artifacts jsonb NOT NULL,
    authenticity_gates jsonb NOT NULL,
    attestation jsonb,
    proposed_provenance_rows jsonb NOT NULL,
    proposed_content_rows jsonb NOT NULL,
    status text DEFAULT 'awaiting_approval'::text NOT NULL,
    approved_by text,
    approved_at timestamp with time zone,
    approved_via text,
    committed_provenance_ids uuid[] DEFAULT '{}'::uuid[],
    committed_content_ids uuid[] DEFAULT '{}'::uuid[],
    committed_at timestamp with time zone,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ingestion_manifest_status_check CHECK ((status = ANY (ARRAY['awaiting_approval'::text, 'approved'::text, 'committed'::text, 'rejected'::text, 'superseded'::text])))
);

ALTER TABLE ONLY public.ingestion_manifest
    ADD CONSTRAINT ingestion_manifest_pkey PRIMARY KEY (id);

CREATE INDEX idx_ingestion_manifest_label ON public.ingestion_manifest USING btree (manifest_label);

CREATE INDEX idx_ingestion_manifest_status ON public.ingestion_manifest USING btree (status, created_at DESC);

ALTER TABLE public.ingestion_manifest ENABLE ROW LEVEL SECURITY;
CREATE POLICY ingestion_manifest_service_only ON public.ingestion_manifest TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.message_queue (
    id bigint NOT NULL,
    chat_id text NOT NULL,
    message_id bigint NOT NULL,
    message_type text DEFAULT 'text'::text NOT NULL,
    content text,
    file_id text,
    processed boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.message_queue ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.message_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE ONLY public.message_queue
    ADD CONSTRAINT message_queue_chat_id_message_id_key UNIQUE (chat_id, message_id);

ALTER TABLE ONLY public.message_queue
    ADD CONSTRAINT message_queue_pkey PRIMARY KEY (id);

CREATE INDEX idx_message_queue_unprocessed ON public.message_queue USING btree (processed, created_at) WHERE (processed = false);

ALTER TABLE public.message_queue ENABLE ROW LEVEL SECURITY;
CREATE POLICY message_queue_service_only ON public.message_queue TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.orchestrator_runtime_config (
    key text NOT NULL,
    value text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT orchestrator_runtime_config_value_check CHECK ((((key = 'challenge_enforcer_mode'::text) AND (value = ANY (ARRAY['dry_run'::text, 'write_mode'::text]))) OR (key <> 'challenge_enforcer_mode'::text)))
);

ALTER TABLE ONLY public.orchestrator_runtime_config
    ADD CONSTRAINT orchestrator_runtime_config_pkey PRIMARY KEY (key);

ALTER TABLE public.orchestrator_runtime_config ENABLE ROW LEVEL SECURITY;
CREATE POLICY orchestrator_runtime_config_service_only ON public.orchestrator_runtime_config TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.platform_admins (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    email text NOT NULL,
    role text DEFAULT 'super_admin'::text NOT NULL,
    granted_by uuid,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT platform_admins_role_check CHECK ((role = ANY (ARRAY['super_admin'::text, 'support'::text, 'billing'::text])))
);

ALTER TABLE ONLY public.platform_admins
    ADD CONSTRAINT platform_admins_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.platform_admins
    ADD CONSTRAINT platform_admins_user_id_key UNIQUE (user_id);

CREATE INDEX idx_platform_admins_email ON public.platform_admins USING btree (email);

CREATE INDEX idx_platform_admins_user ON public.platform_admins USING btree (user_id);

ALTER TABLE ONLY public.platform_admins
    ADD CONSTRAINT platform_admins_granted_by_fkey FOREIGN KEY (granted_by) REFERENCES public.profiles(id);

ALTER TABLE ONLY public.platform_admins
    ADD CONSTRAINT platform_admins_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id);

ALTER TABLE public.platform_admins ENABLE ROW LEVEL SECURITY;
CREATE POLICY platform_admins_service_only ON public.platform_admins TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.provisions (
    id bigint NOT NULL,
    client_id bigint,
    template_id text,
    slug text NOT NULL,
    site_name text NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    repo_name text,
    deploy_url text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.provisions ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.provisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE ONLY public.provisions
    ADD CONSTRAINT provisions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.provisions
    ADD CONSTRAINT provisions_slug_key UNIQUE (slug);

-- provisions_client_id_fkey deferred: public.clients is created by schema.sql, which
-- runs AFTER this file -- see bedrock_substrate_deferred.sql.

ALTER TABLE ONLY public.provisions
    ADD CONSTRAINT provisions_template_id_fkey FOREIGN KEY (template_id) REFERENCES public.site_templates(id);

ALTER TABLE public.provisions ENABLE ROW LEVEL SECURITY;
CREATE POLICY provisions_service_only ON public.provisions TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.repo_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    repo text NOT NULL,
    event_type text DEFAULT 'push'::text NOT NULL,
    commit_sha text,
    commit_message text,
    author text,
    branch text,
    payload jsonb,
    created_at timestamp with time zone DEFAULT now()
);

ALTER TABLE ONLY public.repo_events
    ADD CONSTRAINT repo_events_pkey PRIMARY KEY (id);

CREATE INDEX repo_events_created_idx ON public.repo_events USING btree (created_at DESC);

CREATE INDEX repo_events_repo_idx ON public.repo_events USING btree (repo);

ALTER TABLE public.repo_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY repo_events_service_only ON public.repo_events TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.usage_log (
    id bigint NOT NULL,
    client_id bigint,
    action_type text NOT NULL,
    repo_name text,
    tokens_used integer DEFAULT 0,
    duration_seconds double precision DEFAULT 0,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.usage_log ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.usage_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE ONLY public.usage_log
    ADD CONSTRAINT usage_log_pkey PRIMARY KEY (id);

CREATE INDEX idx_usage_log_client ON public.usage_log USING btree (client_id, created_at DESC);

-- usage_log_client_id_fkey deferred: public.clients is created by schema.sql, which
-- runs AFTER this file -- see bedrock_substrate_deferred.sql.

ALTER TABLE public.usage_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY usage_log_service_only ON public.usage_log TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.wingmen_brain (
    id bigint NOT NULL,
    snapshot_at timestamp with time zone DEFAULT now() NOT NULL,
    repos jsonb NOT NULL,
    job_queue jsonb,
    clients jsonb,
    sync_health text DEFAULT 'healthy'::text NOT NULL,
    context_notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.wingmen_brain_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.wingmen_brain_id_seq OWNED BY public.wingmen_brain.id;

ALTER TABLE ONLY public.wingmen_brain ALTER COLUMN id SET DEFAULT nextval('public.wingmen_brain_id_seq'::regclass);

ALTER TABLE ONLY public.wingmen_brain
    ADD CONSTRAINT wingmen_brain_pkey PRIMARY KEY (id);

CREATE INDEX idx_wingmen_brain_snapshot_at ON public.wingmen_brain USING btree (snapshot_at DESC);

ALTER TABLE public.wingmen_brain ENABLE ROW LEVEL SECURITY;
CREATE POLICY wingmen_brain_service_only ON public.wingmen_brain TO service_role USING (true) WITH CHECK (true);

-- portfolio_entries + site_content: surfaced by migrations/030_rls_lockdown_3_tables.sql
-- (same zero-CREATE-anywhere class). Captured read-only via pg_dump on 2026-10-05.
-- Unlike the console_ro policies elsewhere in this file, the anon/authenticated roles
-- these two tables' public-read policies grant to are NOT deferred -- apply_full_schema()
-- runs the platform-object shim (which creates anon/authenticated/service_role) BEFORE
-- this file, so referencing them here is safe.
CREATE TABLE public.portfolio_entries (
    id bigint NOT NULL,
    slug text NOT NULL,
    client_name text NOT NULL,
    site_url text,
    category text,
    blurb text,
    before_img text NOT NULL,
    after_img text NOT NULL,
    after_url text,
    style text,
    specs jsonb DEFAULT '[]'::jsonb NOT NULL,
    entry_type text DEFAULT 'client'::text NOT NULL,
    consent_flag boolean DEFAULT false NOT NULL,
    featured boolean DEFAULT false NOT NULL,
    sort_order integer DEFAULT 100 NOT NULL,
    captured_at date,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.portfolio_entries ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.portfolio_entries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE ONLY public.portfolio_entries
    ADD CONSTRAINT portfolio_entries_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.portfolio_entries
    ADD CONSTRAINT portfolio_entries_slug_key UNIQUE (slug);

CREATE INDEX portfolio_entries_public_idx ON public.portfolio_entries USING btree (sort_order, created_at DESC) WHERE (consent_flag = true);

ALTER TABLE public.portfolio_entries ENABLE ROW LEVEL SECURITY;
CREATE POLICY portfolio_entries_public_read ON public.portfolio_entries FOR SELECT TO authenticated, anon USING (true);
CREATE POLICY portfolio_entries_service_only ON public.portfolio_entries TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.site_content (
    section text NOT NULL,
    value jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text
);

ALTER TABLE ONLY public.site_content
    ADD CONSTRAINT site_content_pkey PRIMARY KEY (section);

ALTER TABLE public.site_content ENABLE ROW LEVEL SECURITY;
CREATE POLICY site_content_public_read ON public.site_content FOR SELECT TO authenticated, anon USING (true);
CREATE POLICY site_content_service_only ON public.site_content TO service_role USING (true) WITH CHECK (true);

-- chat_history + payments + pending_signups: surfaced by
-- migrations/031_anon_write_truncate_lockdown.sql (same zero-CREATE-anywhere class).
-- Captured read-only via pg_dump on 2026-10-05. Their "service role full access" policy
-- uses auth.role(), which the platform shim (applied before this file) already defines.
CREATE TABLE public.chat_history (
    id bigint NOT NULL,
    chat_id text NOT NULL,
    role text NOT NULL,
    content text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.chat_history ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.chat_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE ONLY public.chat_history
    ADD CONSTRAINT chat_history_pkey PRIMARY KEY (id);

CREATE INDEX idx_chat_history_chat_id ON public.chat_history USING btree (chat_id, created_at DESC);

ALTER TABLE public.chat_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service role full access" ON public.chat_history USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));

CREATE TABLE public.payments (
    id bigint NOT NULL,
    client_id bigint NOT NULL,
    amount_cents integer NOT NULL,
    currency text DEFAULT 'usd'::text NOT NULL,
    provider text DEFAULT 'telegram'::text NOT NULL,
    provider_charge_id text,
    plan_id text,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.payments ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.payments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (id);

-- payments_client_id_fkey deferred: public.clients is created by schema.sql, which
-- runs AFTER this file -- see bedrock_substrate_deferred.sql.

ALTER TABLE public.payments ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service role full access" ON public.payments USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));

CREATE TABLE public.pending_signups (
    id bigint NOT NULL,
    telegram_chat_id text NOT NULL,
    telegram_username text,
    name text,
    company text,
    status text DEFAULT 'pending'::text NOT NULL,
    admin_notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.pending_signups ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.pending_signups_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE ONLY public.pending_signups
    ADD CONSTRAINT pending_signups_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.pending_signups
    ADD CONSTRAINT pending_signups_telegram_chat_id_key UNIQUE (telegram_chat_id);

ALTER TABLE public.pending_signups ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service role full access" ON public.pending_signups USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));

-- ui_events: surfaced by migrations/032_anon_write_revoke_nontelemetry.sql
-- (same zero-CREATE-anywhere class). Captured read-only via pg_dump on 2026-10-05.
CREATE TABLE public.ui_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    repo text NOT NULL,
    user_id uuid,
    session_id text,
    event_name text NOT NULL,
    properties jsonb,
    created_at timestamp with time zone DEFAULT now()
);

ALTER TABLE ONLY public.ui_events
    ADD CONSTRAINT ui_events_pkey PRIMARY KEY (id);

CREATE INDEX idx_ui_events_created ON public.ui_events USING btree (created_at DESC);

CREATE INDEX idx_ui_events_repo_event ON public.ui_events USING btree (repo, event_name);

CREATE INDEX idx_ui_events_session ON public.ui_events USING btree (session_id) WHERE (session_id IS NOT NULL);

CREATE INDEX idx_ui_events_user ON public.ui_events USING btree (user_id) WHERE (user_id IS NOT NULL);

ALTER TABLE public.ui_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY anyone_can_insert_events ON public.ui_events FOR INSERT WITH CHECK (true);
CREATE POLICY service_role_reads_events ON public.ui_events FOR SELECT USING ((auth.role() = 'service_role'::text));

-- mizan_eval_runs / mizan_interactions / mizan_retract_gate / mizan_eval_set /
-- mizan_human_reviews / mizan_user_feedback: surfaced by
-- migrations/032_anon_write_revoke_nontelemetry.sql's gate-tagged DO-block assertion
-- (table names only appear inside an unnest(ARRAY[...]) loop, not as plain
-- `public.<table>` text -- missed by a first-pass grep). Same zero-CREATE-anywhere
-- class as the tables above. mizan_interactions, mizan_retract_gate and the
-- mizan_retract_block() trigger function were themselves undiscovered transitive
-- deps of the 4 gate-block tables (FK targets / trigger function, never
-- referenced by migration text directly) -- captured read-only via pg_dump /
-- pg_get_functiondef on 2026-10-05. No forward-references to clients/profiles/
-- console_readonly -- all self-contained, nothing deferred.

CREATE TABLE public.mizan_eval_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    candidate_model text NOT NULL,
    candidate_prompt_version text NOT NULL,
    judge_model text NOT NULL,
    judge_prompt_version text NOT NULL,
    gold_set_size integer NOT NULL,
    judge_human_agreement numeric(4,3),
    composite_summary jsonb,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.mizan_eval_runs
    ADD CONSTRAINT mizan_eval_runs_pkey PRIMARY KEY (id);

ALTER TABLE public.mizan_eval_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY mizan_eval_runs_admin ON public.mizan_eval_runs TO authenticated USING (((auth.jwt() ->> 'role'::text) = 'super_admin'::text)) WITH CHECK (((auth.jwt() ->> 'role'::text) = 'super_admin'::text));

-- migrations/032_anon_write_revoke_nontelemetry.sql's own gate (3) asserts
-- `authenticated` still has INSERT on this table after its REVOKE -- a
-- real hand-granted GRANT on the live substrate, never issued by any
-- migration, same phantom class as the table itself. Preserved verbatim
-- from the pg_dump capture (anon's grant here is transient: migration 032
-- revokes it from every public table a few statements later anyway).
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,MAINTAIN,UPDATE ON TABLE public.mizan_eval_runs TO anon;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,MAINTAIN,UPDATE ON TABLE public.mizan_eval_runs TO authenticated;
GRANT ALL ON TABLE public.mizan_eval_runs TO service_role;

CREATE TABLE public.mizan_interactions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    telegram_id_hash text NOT NULL,
    thread_id uuid,
    bot_variant text NOT NULL,
    query_type text NOT NULL,
    query_text text NOT NULL,
    query_lang text DEFAULT 'en'::text NOT NULL,
    response_text text NOT NULL,
    output_tier text NOT NULL,
    matched_passage_id uuid,
    retrieval_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    scholar_of_record text,
    model_name text NOT NULL,
    prompt_version text NOT NULL,
    retraction_of uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    retrieval_config jsonb,
    flagged_for_review boolean DEFAULT false NOT NULL,
    flagged_by text,
    flagged_at timestamp with time zone,
    reviewer_note text,
    CONSTRAINT mizan_interactions_bot_variant_check CHECK ((bot_variant = ANY (ARRAY['al-bayan'::text, 'al-mizan'::text]))),
    CONSTRAINT mizan_interactions_output_tier_check CHECK ((output_tier = ANY (ARRAY['quoted'::text, 'paraphrased'::text, 'inferred'::text, 'ai-generated'::text]))),
    CONSTRAINT mizan_interactions_query_type_check CHECK ((query_type = ANY (ARRAY['ruling'::text, 'definition'::text, 'biography'::text, 'language-clarification'::text, 'madhhab-identification'::text, 'tafsir'::text, 'other'::text])))
);

ALTER TABLE ONLY public.mizan_interactions
    ADD CONSTRAINT mizan_interactions_pkey PRIMARY KEY (id);

CREATE INDEX idx_mizan_interactions_flagged ON public.mizan_interactions USING btree (flagged_at DESC) WHERE flagged_for_review;

CREATE INDEX mizan_interactions_created_at_idx ON public.mizan_interactions USING btree (created_at DESC);

CREATE INDEX mizan_interactions_output_tier_idx ON public.mizan_interactions USING btree (output_tier);

CREATE INDEX mizan_interactions_query_type_idx ON public.mizan_interactions USING btree (query_type);

CREATE INDEX mizan_interactions_telegram_id_hash_idx ON public.mizan_interactions USING btree (telegram_id_hash);

ALTER TABLE ONLY public.mizan_interactions
    ADD CONSTRAINT mizan_interactions_retraction_of_fkey FOREIGN KEY (retraction_of) REFERENCES public.mizan_interactions(id);

ALTER TABLE public.mizan_interactions ENABLE ROW LEVEL SECURITY;
CREATE POLICY mizan_interactions_service_all ON public.mizan_interactions TO service_role USING (true) WITH CHECK (true);

CREATE TABLE public.mizan_retract_gate (
    id smallint DEFAULT 1 NOT NULL,
    unlocked boolean DEFAULT false NOT NULL,
    unlocked_at timestamp with time zone,
    unlocked_by_run_id uuid,
    minimum_gold_set_size integer DEFAULT 30 NOT NULL,
    minimum_judge_human_agreement numeric(4,3) DEFAULT 0.800 NOT NULL,
    CONSTRAINT mizan_retract_gate_singleton CHECK ((id = 1))
);

ALTER TABLE ONLY public.mizan_retract_gate
    ADD CONSTRAINT mizan_retract_gate_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.mizan_retract_gate
    ADD CONSTRAINT mizan_retract_gate_unlocked_by_run_id_fkey FOREIGN KEY (unlocked_by_run_id) REFERENCES public.mizan_eval_runs(id);

ALTER TABLE public.mizan_retract_gate ENABLE ROW LEVEL SECURITY;
CREATE POLICY mizan_retract_gate_read ON public.mizan_retract_gate FOR SELECT TO authenticated USING (true);

CREATE OR REPLACE FUNCTION public.mizan_retract_block()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
  g_unlocked boolean;
BEGIN
  IF NEW.retraction_of IS NOT NULL THEN
    SELECT unlocked INTO g_unlocked FROM mizan_retract_gate WHERE id = 1;
    IF NOT g_unlocked THEN
      RAISE EXCEPTION 'mizan_retract_gate closed: judge-human agreement not yet documented per MIZAN-EVAL-001 amendment (CAI-RESP-062). Cannot insert user-facing retraction.'
        USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NEW;
END; $function$;

CREATE TRIGGER mizan_interactions_retract_block BEFORE INSERT ON public.mizan_interactions FOR EACH ROW EXECUTE FUNCTION public.mizan_retract_block();

CREATE TABLE public.mizan_eval_set (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    provenance text NOT NULL,
    source_interaction uuid,
    query_text text NOT NULL,
    expected_tier text NOT NULL,
    expected_answer text NOT NULL,
    scholar_grader text,
    scholar_grade smallint,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT mizan_eval_set_expected_tier_check CHECK ((expected_tier = ANY (ARRAY['quoted'::text, 'paraphrased'::text, 'inferred'::text, 'ai-generated'::text]))),
    CONSTRAINT mizan_eval_set_scholar_grade_check CHECK (((scholar_grade >= 1) AND (scholar_grade <= 5)))
);

ALTER TABLE ONLY public.mizan_eval_set
    ADD CONSTRAINT mizan_eval_set_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.mizan_eval_set
    ADD CONSTRAINT mizan_eval_set_source_interaction_fkey FOREIGN KEY (source_interaction) REFERENCES public.mizan_interactions(id);

ALTER TABLE public.mizan_eval_set ENABLE ROW LEVEL SECURITY;
CREATE POLICY mizan_eval_set_admin ON public.mizan_eval_set TO authenticated USING (((auth.jwt() ->> 'role'::text) = 'super_admin'::text)) WITH CHECK (((auth.jwt() ->> 'role'::text) = 'super_admin'::text));

-- see mizan_eval_runs above: same migrations/032 gate (3) dependency.
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,MAINTAIN,UPDATE ON TABLE public.mizan_eval_set TO anon;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,MAINTAIN,UPDATE ON TABLE public.mizan_eval_set TO authenticated;
GRANT ALL ON TABLE public.mizan_eval_set TO service_role;

CREATE TABLE public.mizan_human_reviews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    interaction_id uuid NOT NULL,
    reviewer text NOT NULL,
    verdict text NOT NULL,
    correction_text text,
    rationale text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT mizan_human_reviews_verdict_check CHECK ((verdict = ANY (ARRAY['ok'::text, 'minor-correction'::text, 'retract'::text, 'escalate'::text])))
);

ALTER TABLE ONLY public.mizan_human_reviews
    ADD CONSTRAINT mizan_human_reviews_pkey PRIMARY KEY (id);

CREATE INDEX mizan_human_reviews_interaction_id_idx ON public.mizan_human_reviews USING btree (interaction_id);

CREATE INDEX mizan_human_reviews_verdict_idx ON public.mizan_human_reviews USING btree (verdict);

ALTER TABLE ONLY public.mizan_human_reviews
    ADD CONSTRAINT mizan_human_reviews_interaction_id_fkey FOREIGN KEY (interaction_id) REFERENCES public.mizan_interactions(id) ON DELETE RESTRICT;

ALTER TABLE public.mizan_human_reviews ENABLE ROW LEVEL SECURITY;
CREATE POLICY mizan_human_reviews_scholar_read ON public.mizan_human_reviews FOR SELECT TO authenticated USING (((auth.jwt() ->> 'role'::text) = ANY (ARRAY['scholar'::text, 'super_admin'::text])));
CREATE POLICY mizan_human_reviews_scholar_write ON public.mizan_human_reviews FOR INSERT TO authenticated WITH CHECK (((auth.jwt() ->> 'role'::text) = ANY (ARRAY['scholar'::text, 'super_admin'::text])));

-- see mizan_eval_runs above: same migrations/032 gate (3) dependency.
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,MAINTAIN,UPDATE ON TABLE public.mizan_human_reviews TO anon;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,MAINTAIN,UPDATE ON TABLE public.mizan_human_reviews TO authenticated;
GRANT ALL ON TABLE public.mizan_human_reviews TO service_role;

CREATE TABLE public.mizan_user_feedback (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    interaction_id uuid NOT NULL,
    telegram_id_hash text NOT NULL,
    rating smallint,
    flag text,
    comment text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT mizan_user_feedback_flag_check CHECK ((flag = ANY (ARRAY['incorrect'::text, 'misattributed'::text, 'hallucinated'::text, 'offensive'::text, 'other'::text]))),
    CONSTRAINT mizan_user_feedback_rating_check CHECK (((rating >= 1) AND (rating <= 5)))
);

ALTER TABLE ONLY public.mizan_user_feedback
    ADD CONSTRAINT mizan_user_feedback_pkey PRIMARY KEY (id);

CREATE INDEX mizan_user_feedback_interaction_id_idx ON public.mizan_user_feedback USING btree (interaction_id);

ALTER TABLE ONLY public.mizan_user_feedback
    ADD CONSTRAINT mizan_user_feedback_interaction_id_fkey FOREIGN KEY (interaction_id) REFERENCES public.mizan_interactions(id) ON DELETE RESTRICT;

ALTER TABLE public.mizan_user_feedback ENABLE ROW LEVEL SECURITY;
CREATE POLICY mizan_user_feedback_insert_own ON public.mizan_user_feedback FOR INSERT TO authenticated WITH CHECK ((telegram_id_hash = (auth.jwt() ->> 'telegram_id_hash'::text)));
CREATE POLICY mizan_user_feedback_read_own ON public.mizan_user_feedback FOR SELECT TO authenticated USING ((telegram_id_hash = (auth.jwt() ->> 'telegram_id_hash'::text)));

-- see mizan_eval_runs above: same migrations/032 gate (3) dependency.
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,MAINTAIN,UPDATE ON TABLE public.mizan_user_feedback TO anon;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,MAINTAIN,UPDATE ON TABLE public.mizan_user_feedback TO authenticated;
GRANT ALL ON TABLE public.mizan_user_feedback TO service_role;

-- escalate_full_tier_without_auditor(numeric): surfaced by
-- migrations/052_escalator_anon_execute_revoke.sql's REVOKE EXECUTE statement.
-- Unlike migrations/011's post_journal_atomic (confirmed dead -- absent from the
-- live substrate too, tolerated via _DEAD_MIGRATION_TARGETS), this one DOES still
-- exist live (confirmed via pg_proc, 2026-10-05) with zero CREATE FUNCTION anywhere
-- in schema.sql/supabase/migrations/migrations -- hand-created on the substrate
-- before migration tracking existed, same phantom class as a bedrock table, just a
-- function. Captured verbatim via pg_get_functiondef so migration 052's REVOKE (and
-- its own post-condition gate re-checking anon/authenticated/public/service_role
-- privileges) has a real target. plpgsql body references strategic_decisions/
-- decision_audits/decision_tier_escalations/agent_messages, but a function body is
-- never resolved against those at CREATE time (only at CALL time, which nothing in
-- the walk does) -- safe to define here regardless of those tables' creation order.
CREATE OR REPLACE FUNCTION public.escalate_full_tier_without_auditor(p_grace_hours numeric DEFAULT 2)
 RETURNS TABLE(decision_ref text, auditor_agent text, action text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
DECLARE
    rec RECORD;
    v_hours numeric;
BEGIN
    FOR rec IN
        SELECT sd.decision_ref, sd.title, sd.decided_at
          FROM strategic_decisions sd
         WHERE sd.audit_tier = 'FULL'
           AND COALESCE(sd.is_test, false) = false
           AND sd.decided_at < now() - (p_grace_hours * interval '1 hour')
           AND NOT EXISTS (SELECT 1 FROM decision_audits da
                            WHERE da.decision_ref = sd.decision_ref)
           AND NOT EXISTS (SELECT 1 FROM decision_tier_escalations e
                            WHERE e.decision_ref = sd.decision_ref)
    LOOP
        v_hours := round(EXTRACT(EPOCH FROM (now() - rec.decided_at)) / 3600.0, 1);
        INSERT INTO agent_messages
            (from_agent, to_agent, message_type, subject, body, requires_response, priority)
        VALUES ('substrate', 'cai', 'blocker',
            'FULL TIER, NO AUDITOR: ' || rec.decision_ref || ' — ' || v_hours || 'h and nobody is assigned',
            'CAI-RESP-1001 §2. The mechanism is raising this about itself.'
                || E'\n\nDecision: ' || rec.decision_ref || ' — ' || COALESCE(rec.title,'(no title)')
                || E'\nTier: FULL' || E'\nDecided: ' || rec.decided_at || ' (' || v_hours || 'h ago)'
                || E'\nAuditors assigned: NONE'
                || E'\n\nA FULL ruling with zero auditors never closes and nothing chases it — it is '
                || 'AUDIT-OWED-NO-AUDITOR, which reads on the board as "in progress" and is in fact '
                || 'nobody''s. This has happened four times (CAI-989, 995, 999, 1000), each caught by '
                || 'a human reading the digest. This path exists so it no longer depends on that.'
                || E'\n\nFIX: name the auditor(s) INLINE per CAI-1001 §1, then orch-console '
                || 'materialises the decision_audits rows from that naming.'
                || E'\n\nIf the decision is DELIBERATELY unassigned (as CAI-999 was), this fires ONCE '
                || 'and will not repeat — the row in decision_tier_escalations records that you were '
                || 'told. It fails toward one unnecessary message, never toward silence.',
            true, 'P2');
        INSERT INTO decision_tier_escalations (decision_ref) VALUES (rec.decision_ref)
            ON CONFLICT (decision_ref) DO NOTHING;
        RETURN QUERY SELECT rec.decision_ref, '(nobody assigned)'::TEXT, 'escalated_full_no_auditor'::TEXT;
    END LOOP;
END;
$function$;

-- migrations/052's own comment: "anon/authenticated held EXPLICIT grants, not
-- just the PUBLIC blanket" pre-revoke, and its post-condition gate requires
-- service_role to STILL have EXECUTE after the REVOKE (negative control: a
-- revoke that also strips service_role would silence the escalator, not fix
-- it). A bare CREATE FUNCTION only gives PUBLIC the implicit default EXECUTE —
-- explicit grants for all 3 are needed so the REVOKE has a real target AND
-- service_role's grant survives it (it's never named in the REVOKE's FROM list).
GRANT EXECUTE ON FUNCTION public.escalate_full_tier_without_auditor(numeric) TO anon, authenticated, service_role;

-- audit_chain_boundaries / revenue_ledger: surfaced by
-- migrations/055_close_anon_read_on_five_rls_off_tables.sql (2 of its 5 target
-- tables -- held_commitments/fleet_proposals/chat_members already exist via
-- migrations 051/046 and bedrock_substrate_core.sql respectively). Same
-- zero-CREATE-anywhere class. No anon/authenticated GRANT replication needed:
-- migration 055 has no post-condition gate (unlike 032/052) -- its REVOKE ALL
-- is a harmless no-op here regardless of whether those roles ever held anything.
CREATE TABLE public.audit_chain_boundaries (
    id bigint NOT NULL,
    project_ref text NOT NULL,
    silo_label text NOT NULL,
    org_id uuid NOT NULL,
    boundary_id bigint NOT NULL,
    below_count integer NOT NULL,
    below_latest_at timestamp with time zone NOT NULL,
    below_status text NOT NULL,
    above_count integer NOT NULL,
    above_earliest_at timestamp with time zone NOT NULL,
    above_status text NOT NULL,
    reason text NOT NULL,
    decision_ref text NOT NULL,
    verified_by text NOT NULL,
    verified_at timestamp with time zone DEFAULT now() NOT NULL,
    reproducing_count integer,
    not_reproducing_count integer,
    reproducing_pre_fix_ids bigint[],
    verified_live_at timestamp with time zone,
    discriminator text,
    reproducible_writers text[],
    non_reproducible_writers text[],
    fully_covered_count integer,
    partially_covered_count integer,
    partially_covered_ids bigint[],
    amnesty_mechanism text,
    amnesty_decision_ref text,
    amnesty_row_id_snapshot_at timestamp with time zone,
    amnesty_snapshot_tip_id bigint,
    amnesty_snapshot_tip_hash text,
    amnesty_row_id_count integer,
    amnesty_row_ids bigint[],
    amnesty_rederivation_ref text,
    amnesty_annotated_by text,
    amnesty_notes text
);

CREATE SEQUENCE public.audit_chain_boundaries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.audit_chain_boundaries_id_seq OWNED BY public.audit_chain_boundaries.id;

ALTER TABLE ONLY public.audit_chain_boundaries ALTER COLUMN id SET DEFAULT nextval('public.audit_chain_boundaries_id_seq'::regclass);

ALTER TABLE ONLY public.audit_chain_boundaries
    ADD CONSTRAINT audit_chain_boundaries_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.audit_chain_boundaries
    ADD CONSTRAINT audit_chain_boundaries_project_ref_org_id_boundary_id_key UNIQUE (project_ref, org_id, boundary_id);

ALTER TABLE public.audit_chain_boundaries ENABLE ROW LEVEL SECURITY;

CREATE TABLE public.revenue_ledger (
    id bigint NOT NULL,
    received_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    client_id bigint,
    payer text,
    amount_minor bigint NOT NULL,
    currency text NOT NULL,
    what_for text,
    method text,
    reference text,
    evidence_ref text,
    status text DEFAULT 'received'::text NOT NULL,
    source text DEFAULT 'operator_screenshot'::text NOT NULL,
    entered_by text DEFAULT 'cc-finance'::text NOT NULL,
    notes text,
    CONSTRAINT revenue_ledger_amount_nonneg CHECK ((amount_minor >= 0)),
    CONSTRAINT revenue_ledger_currency_iso CHECK ((currency ~ '^[A-Z]{3}$'::text))
);

ALTER TABLE public.revenue_ledger ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.revenue_ledger_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE ONLY public.revenue_ledger
    ADD CONSTRAINT revenue_ledger_pkey PRIMARY KEY (id);

CREATE INDEX revenue_ledger_client_id_idx ON public.revenue_ledger USING btree (client_id);

CREATE INDEX revenue_ledger_received_at_idx ON public.revenue_ledger USING btree (received_at);

ALTER TABLE public.revenue_ledger ENABLE ROW LEVEL SECURITY;

-- anchor_queue_console_ro (TO console_readonly) deferred — see bedrock_substrate_deferred.sql.

-- cp#83: migrations/058_close_anon_exec_postgres_owned_half.sql ALTERs the search_path
-- on these 6 SECURITY DEFINER functions (plus get_decision/get_repo_context, already
-- created by supabase/migrations/20260416_arch019_boot_briefing_index.sql / schema.sql).
-- Zero CREATE FUNCTION for any of these 6 exists anywhere in schema.sql/supabase/
-- migrations/migrations (confirmed by grep) — hand-created live before migration
-- tracking existed, same phantom class as the escalator function above, just
-- auth/org-membership helpers instead of an escalation path. Confirmed still alive on
-- the live substrate (pg_proc, all 6 present) via pg_get_functiondef, captured verbatim.
--
-- Unlike the plpgsql captures elsewhere in this file (mizan_retract_block(),
-- escalate_full_tier_without_auditor()), 4 of these 6 are LANGUAGE sql — Postgres
-- parses/plans a SQL-language function body at CREATE FUNCTION time (it has to, to
-- decide inlining), so every table/function THEY reference must already exist, unlike
-- a plpgsql body (opaque string until first CALL). This forces a real ordering below:
-- org_members/persons (also undiscovered phantom tables, zero CREATE anywhere in the
-- 3 committed layers, confirmed by grep) before auth_user_org_ids()/_with_role()/
-- _with_roles(), and their 4 RLS policies (which call those same functions in their
-- USING/WITH CHECK clause — also resolved at CREATE POLICY DDL time, same reasoning
-- as this file's auth.role()/auth.jwt() comment in ci_bootstrap_schema.py) after the
-- functions, not alongside the tables.
CREATE OR REPLACE FUNCTION public.handle_new_user()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
BEGIN
  INSERT INTO public.profiles (id, display_name)
  VALUES (
    NEW.id,
    COALESCE(NEW.raw_user_meta_data->>'display_name', split_part(NEW.email, '@', 1))
  );
  RETURN NEW;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.sync_org_memberships_to_jwt()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
  target_user_id UUID;
  memberships JSONB;
BEGIN
  -- Determine which user to update
  target_user_id := COALESCE(NEW.user_id, OLD.user_id);

  -- Build memberships array from current org_members
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'org_id', om.org_id,
    'role', om.role
  )), '[]'::jsonb)
  INTO memberships
  FROM public.org_members om
  WHERE om.user_id = target_user_id AND om.deleted_at IS NULL;

  -- Update user's app_metadata with org memberships
  UPDATE auth.users
  SET raw_app_meta_data = COALESCE(raw_app_meta_data, '{}'::jsonb) || jsonb_build_object('org_memberships', memberships)
  WHERE id = target_user_id;

  RETURN COALESCE(NEW, OLD);
END;
$function$
;

-- auth_user_hr_employee_id()'s body joins hr_employees -> persons. persons is a real,
-- captured-below phantom table, but hr_employees is confirmed ABSENT from the live
-- substrate too (direct pg_class lookup by name, zero rows, any schema) — this
-- function is already dead/uncallable in production itself, not a CI-only gap.
-- Faithfully replicating that (rather than fabricating a table that doesn't exist
-- anywhere) needs check_function_bodies off for just this one CREATE, the same GUC
-- pg_dump itself sets for exactly this reason (see its own standard preamble); scoped
-- with SET LOCAL so it reverts at this file's implicit transaction boundary and can't
-- mask a real forward-reference bug in any later migrations/*.sql file.
SET LOCAL check_function_bodies = false;
CREATE OR REPLACE FUNCTION public.auth_user_hr_employee_id()
 RETURNS uuid
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
  SELECT he.id FROM hr_employees he
  JOIN persons p ON he.person_id = p.id
  WHERE p.user_id = auth.uid()
    AND he.deleted_at IS NULL
    AND p.deleted_at IS NULL
  LIMIT 1;
$function$
;
SET LOCAL check_function_bodies = true;

-- org_members / persons: surfaced transitively as the LANGUAGE sql auth_user_org_ids*
-- functions' own FROM-clause target. Captured via pg_dump against the live substrate,
-- trimmed per this file's established transitive-dependency convention (see the
-- profiles/organizations/anchor_batches/site_templates comment above):
--   - trg_persons_updated_at dropped: calls public.update_updated_at(), itself
--     uncommitted anywhere in the 3 layers (same gap noted in that earlier comment) —
--     cosmetic for a CI build that writes zero rows.
--   - trg_sync_org_memberships KEPT: calls sync_org_memberships_to_jwt(), captured for
--     real just above, so this one is satisfiable and more faithful to keep.
--   - FKs: org_members -> organizations(id)/profiles(id), persons -> organizations(id)/
--     auth.users(id) — all 3 targets already created earlier in this file.
CREATE TABLE public.org_members (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    user_id uuid NOT NULL,
    role text NOT NULL,
    invited_at timestamp with time zone DEFAULT now() NOT NULL,
    accepted_at timestamp with time zone,
    deleted_at timestamp with time zone,
    CONSTRAINT org_members_role_check CHECK ((role = ANY (ARRAY['org_admin'::text, 'cashier'::text, 'viewer'::text, 'hr_manager'::text, 'teacher'::text, 'parent'::text, 'supplier'::text])))
);

CREATE TABLE public.persons (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    display_name text NOT NULL,
    nric_encrypted text,
    nric_hash text,
    nric_source text,
    email text,
    phone text,
    address text,
    date_of_birth date,
    gender text,
    custom_fields jsonb DEFAULT '{}'::jsonb NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    user_id uuid,
    CONSTRAINT persons_gender_check CHECK ((gender = ANY (ARRAY['male'::text, 'female'::text]))),
    CONSTRAINT persons_nric_source_check CHECK ((nric_source = ANY (ARRAY['singpass_verified'::text, 'self_declared'::text])))
);

ALTER TABLE ONLY public.org_members
    ADD CONSTRAINT org_members_org_id_user_id_key UNIQUE (org_id, user_id);

ALTER TABLE ONLY public.org_members
    ADD CONSTRAINT org_members_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.persons
    ADD CONSTRAINT persons_pkey PRIMARY KEY (id);

CREATE INDEX idx_org_members_org_role ON public.org_members USING btree (org_id, role);
CREATE INDEX idx_org_members_user ON public.org_members USING btree (user_id);
CREATE INDEX idx_persons_org_email ON public.persons USING btree (org_id, email) WHERE (email IS NOT NULL);
CREATE INDEX idx_persons_org_name ON public.persons USING btree (org_id, display_name);
CREATE INDEX idx_persons_user_id ON public.persons USING btree (user_id) WHERE (user_id IS NOT NULL);
CREATE UNIQUE INDEX idx_persons_user_org ON public.persons USING btree (user_id, org_id) WHERE ((user_id IS NOT NULL) AND (deleted_at IS NULL));

CREATE TRIGGER trg_sync_org_memberships AFTER INSERT OR DELETE OR UPDATE ON public.org_members FOR EACH ROW EXECUTE FUNCTION public.sync_org_memberships_to_jwt();

ALTER TABLE ONLY public.org_members
    ADD CONSTRAINT org_members_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);

ALTER TABLE ONLY public.org_members
    ADD CONSTRAINT org_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id);

ALTER TABLE ONLY public.persons
    ADD CONSTRAINT persons_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);

ALTER TABLE ONLY public.persons
    ADD CONSTRAINT persons_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id);

CREATE OR REPLACE FUNCTION public.auth_user_org_ids()
 RETURNS SETOF uuid
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
  SELECT (elem->>'org_id')::UUID
  FROM jsonb_array_elements(
    COALESCE(auth.jwt() -> 'app_metadata' -> 'org_memberships', '[]'::jsonb)
  ) AS elem
  UNION
  -- Fallback: direct query for users whose JWT hasn't been refreshed yet
  SELECT org_id FROM public.org_members
  WHERE user_id = auth.uid() AND deleted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(
      COALESCE(auth.jwt() -> 'app_metadata' -> 'org_memberships', '[]'::jsonb)
    )
  );
$function$
;

CREATE OR REPLACE FUNCTION public.auth_user_org_ids_with_role(required_role text)
 RETURNS SETOF uuid
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
  SELECT (elem->>'org_id')::UUID
  FROM jsonb_array_elements(
    COALESCE(auth.jwt() -> 'app_metadata' -> 'org_memberships', '[]'::jsonb)
  ) AS elem
  WHERE elem->>'role' = required_role
  UNION
  SELECT org_id FROM public.org_members
  WHERE user_id = auth.uid() AND role = required_role AND deleted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(
      COALESCE(auth.jwt() -> 'app_metadata' -> 'org_memberships', '[]'::jsonb)
    )
  );
$function$
;

CREATE OR REPLACE FUNCTION public.auth_user_org_ids_with_roles(required_roles text[])
 RETURNS SETOF uuid
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
  SELECT (elem->>'org_id')::UUID
  FROM jsonb_array_elements(
    COALESCE(auth.jwt() -> 'app_metadata' -> 'org_memberships', '[]'::jsonb)
  ) AS elem
  WHERE elem->>'role' = ANY(required_roles)
  UNION
  SELECT org_id FROM public.org_members
  WHERE user_id = auth.uid() AND role = ANY(required_roles) AND deleted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(
      COALESCE(auth.jwt() -> 'app_metadata' -> 'org_memberships', '[]'::jsonb)
    )
  );
$function$
;

ALTER TABLE public.org_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.persons ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Admins can insert org members" ON public.org_members FOR INSERT WITH CHECK ((org_id IN ( SELECT public.auth_user_org_ids_with_role('org_admin'::text) AS auth_user_org_ids_with_role)));
CREATE POLICY "Admins can update org members" ON public.org_members FOR UPDATE USING ((org_id IN ( SELECT public.auth_user_org_ids_with_role('org_admin'::text) AS auth_user_org_ids_with_role)));
CREATE POLICY "Users can see members in their orgs" ON public.org_members FOR SELECT USING (((deleted_at IS NULL) AND (org_id IN ( SELECT public.auth_user_org_ids() AS auth_user_org_ids))));

CREATE POLICY "Admins and cashiers can insert persons" ON public.persons FOR INSERT WITH CHECK ((org_id IN ( SELECT public.auth_user_org_ids_with_roles(ARRAY['org_admin'::text, 'cashier'::text]) AS auth_user_org_ids_with_roles)));
CREATE POLICY "Admins can update persons" ON public.persons FOR UPDATE USING ((org_id IN ( SELECT public.auth_user_org_ids_with_role('org_admin'::text) AS auth_user_org_ids_with_role)));
CREATE POLICY "Users can see persons in their org" ON public.persons FOR SELECT USING (((deleted_at IS NULL) AND (org_id IN ( SELECT public.auth_user_org_ids() AS auth_user_org_ids))));

-- cp#83: migrations/059_close_bulk_insert_morphology_anon_exec.sql REVOKEs EXECUTE on
-- bulk_insert_morphology(jsonb) -- zero CREATE FUNCTION for it anywhere in schema.sql/
-- supabase/migrations/migrations (confirmed by grep), but still alive on the live
-- substrate (pg_proc count=1), unlike 058's two DROPped targets. Captured verbatim via
-- pg_get_functiondef (plpgsql, so its word_morphology reference below doesn't need to
-- exist yet, but captured anyway for fidelity -- it's a real table, not a CI-only
-- stand-in). word_morphology FKs to ayah_meta, also zero CREATE anywhere in the 3
-- committed layers and not a forward-reference to an object a later migration creates
-- either (confirmed by grep) -- a second, independent phantom table, not a defer
-- candidate.
CREATE TABLE public.ayah_meta (
    ayah_number_quran integer NOT NULL,
    surah_number integer NOT NULL,
    ayah_number_surah integer NOT NULL,
    place_of_revelation text,
    revelation_order integer,
    juz_number integer,
    hizb_quarter integer,
    ruku_number integer,
    is_sajdah boolean DEFAULT false,
    word_count integer,
    text_sha256_uthmani text NOT NULL,
    CONSTRAINT ayah_meta_ayah_number_surah_check CHECK ((ayah_number_surah >= 1)),
    CONSTRAINT ayah_meta_hizb_quarter_check CHECK (((hizb_quarter >= 1) AND (hizb_quarter <= 240))),
    CONSTRAINT ayah_meta_juz_number_check CHECK (((juz_number >= 1) AND (juz_number <= 30))),
    CONSTRAINT ayah_meta_surah_number_check CHECK (((surah_number >= 1) AND (surah_number <= 114)))
);

ALTER TABLE ONLY public.ayah_meta
    ADD CONSTRAINT ayah_meta_pkey PRIMARY KEY (ayah_number_quran);

CREATE INDEX idx_ayah_meta_surah ON public.ayah_meta USING btree (surah_number);
CREATE INDEX idx_ayah_meta_text_hash ON public.ayah_meta USING btree (ayah_number_quran, text_sha256_uthmani) WHERE (text_sha256_uthmani IS NOT NULL);

ALTER TABLE public.ayah_meta ENABLE ROW LEVEL SECURITY;
CREATE POLICY ayah_meta_read ON public.ayah_meta FOR SELECT USING (true);

CREATE TABLE public.word_morphology (
    id integer NOT NULL,
    ayah_number_quran integer,
    word_position integer,
    arabic_text text,
    root text,
    pos_tag text,
    english_gloss text,
    verb_form smallint
);

CREATE SEQUENCE public.word_morphology_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.word_morphology_id_seq OWNED BY public.word_morphology.id;

ALTER TABLE ONLY public.word_morphology ALTER COLUMN id SET DEFAULT nextval('public.word_morphology_id_seq'::regclass);

ALTER TABLE ONLY public.word_morphology
    ADD CONSTRAINT word_morphology_pkey PRIMARY KEY (id);

CREATE INDEX idx_word_morphology_ayah ON public.word_morphology USING btree (ayah_number_quran);
CREATE INDEX idx_word_morphology_root ON public.word_morphology USING btree (root);
CREATE UNIQUE INDEX word_morphology_ayah_word_idx ON public.word_morphology USING btree (ayah_number_quran, word_position);

ALTER TABLE ONLY public.word_morphology
    ADD CONSTRAINT word_morphology_ayah_number_quran_fkey FOREIGN KEY (ayah_number_quran) REFERENCES public.ayah_meta(ayah_number_quran);

ALTER TABLE public.word_morphology ENABLE ROW LEVEL SECURITY;
CREATE POLICY word_morphology_read ON public.word_morphology FOR SELECT USING (true);

CREATE OR REPLACE FUNCTION public.bulk_insert_morphology(data jsonb)
 RETURNS integer
 LANGUAGE plpgsql
AS $function$
DECLARE
  row_count integer := 0;
  item jsonb;
BEGIN
  FOR item IN SELECT * FROM jsonb_array_elements(data)
  LOOP
    INSERT INTO word_morphology (ayah_number_quran, word_position, arabic_text, root, pos_tag, english_gloss)
    VALUES (
      (item->>'a')::integer,
      (item->>'w')::integer,
      item->>'t',
      NULLIF(item->>'r', ''),
      item->>'p',
      item->>'g'
    )
    ON CONFLICT (ayah_number_quran, word_position) DO UPDATE SET
      arabic_text = EXCLUDED.arabic_text,
      root = EXCLUDED.root,
      pos_tag = EXCLUDED.pos_tag,
      english_gloss = EXCLUDED.english_gloss;
    row_count := row_count + 1;
  END LOOP;
  RETURN row_count;
END;
$function$
;
