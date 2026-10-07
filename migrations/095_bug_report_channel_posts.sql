-- 095_bug_report_channel_posts.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Idempotency marker for the bug-report → irsyad client Telegram channel poller
-- (Musa op#27000/#27004, orch-console #57120/#57143). The poller
-- (nervous_system/bug_report_channel_poll.py) reads coord_dispatch_queue rows
-- with spec_ref LIKE 'bug-report:%' (inserted by the ihsanOS /api/bug-report
-- endpoint, PR#1064) and posts a one-line 🐞 notice to the gazzabyte-irsyad
-- channel so Shuq/Wan see new in-app bug reports. This table records which
-- queue rows have already been posted, so a row is posted EXACTLY ONCE
-- (idempotency), including across the interim coord-posts-by-hand path (B):
-- a hand-post inserts a row here too, so the poller skips it.
--
-- All additive (new table only). REVERT: DROP TABLE bug_report_channel_posts;

CREATE TABLE IF NOT EXISTS bug_report_channel_posts (
  queue_id       bigint PRIMARY KEY,          -- coord_dispatch_queue.id (PK ⇒ indexed; dedupe key)
  bug_id         uuid,                         -- bug_reports.id parsed from spec_ref (NULL if the row was malformed)
  posted_at      timestamptz NOT NULL DEFAULT now(),
  tg_out_id      bigint,                       -- tg_out queue id returned by enqueue() (NULL for a skip/hand-post)
  note           text                          -- e.g. 'bug_reports row missing' or 'coord hand-post (B)'
);

-- RLS on the new table (pg_default_acl grants anon/authenticated on every new
-- table — a public table without RLS is reachable via the anon/authenticated
-- API key). service_role-only; REVOKE the default grants. Re-run-safe.
-- assert: no_table_privilege anon public.bug_report_channel_posts SELECT
-- assert: no_table_privilege authenticated public.bug_report_channel_posts SELECT
-- assert: no_table_privilege authenticated public.bug_report_channel_posts INSERT
-- assert: no_table_privilege authenticated public.bug_report_channel_posts UPDATE
-- assert: no_table_privilege authenticated public.bug_report_channel_posts DELETE
ALTER TABLE bug_report_channel_posts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bug_report_channel_posts_service_only ON bug_report_channel_posts;
CREATE POLICY bug_report_channel_posts_service_only ON bug_report_channel_posts
  FOR ALL TO service_role USING (true) WITH CHECK (true);
REVOKE ALL ON bug_report_channel_posts FROM anon, authenticated;
