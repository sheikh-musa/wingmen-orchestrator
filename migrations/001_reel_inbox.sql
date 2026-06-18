-- Reel Triage v1 (CAI-RESP-216) — reel_inbox table.
-- Target project: tscuymavysscrvoberrr (NOT orch prod ceayjeamtmcyzzvqflus).
-- digests_shown column approved by cai (RE #2110, CAI-RESP-218) to track the
-- "untouched after 2 digests -> auto-discard" rule.

create extension if not exists pgcrypto;

create table if not exists reel_inbox (
  id             uuid primary key default gen_random_uuid(),
  shortcode      text not null unique,
  url            text not null,
  source         text not null check (source in ('share_link','dyi_saved','dyi_dm')),
  saved_at       timestamptz,
  ingested_at    timestamptz not null default now(),
  caption        text,
  transcript     text,
  ocr_text       text,
  topic          text,
  claim          text,
  evidence_grade text check (evidence_grade in ('cited','anecdote','vibes')),
  action         text,
  effort         text check (effort in ('5min','habit','project')),
  impact         int  check (impact between 1 and 5),
  confidence     numeric check (confidence >= 0 and confidence <= 1),
  priority       numeric,
  status         text not null default 'inbox'
                   check (status in ('inbox','triaged','applying','done','discarded')),
  error          text,
  raw_json       jsonb,
  digests_shown  int not null default 0
);

create index if not exists idx_reel_inbox_status on reel_inbox (status);
-- worker claim predicate: untriaged, not yet errored
create index if not exists idx_reel_inbox_pending
  on reel_inbox (ingested_at) where transcript is null and error is null;
