-- 112_coffeemedia_hamhuskies_onboarding_e2e.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Musa's TG2.0 client-ONBOARDING e2e proof (op#31462, bus #70499/#70642),
-- run through the gated flow from the just-approved onboarding design
-- (reports/tg2.0-client-onboarding-design-20261017/design.md). Musa /start'd
-- wingmendevbot from his OTHER Telegram account (chat_id 8494278755, name
-- "ham huskies", username hamhuskies) to role-play a brand-new client
-- contacting the shared bot cold, with zero pre-existing bot_channels row.
--
-- Chat_id captured the SAME way as the brandit-coffeemedia proof (migration
-- 111) -- no manual getUpdates call against the live WINGMENDEVBOT_TOKEN
-- poller (would itself 409, bus #70399's incident class). The Mini's
-- existing pinned poller already durably logged the /start:
-- operator_messages id 31494, chat_id=8494278755, tag='coffeemedia' (the
-- group's first/only enabled-at-the-time row at capture time, per
-- match_channel_in_group's documented fallback-to-first-row-for-audit
-- behavior), 2026-10-18 01:00:56 UAE. Checked: SELECT channel_key FROM
-- bot_channels WHERE 8494278755 = ANY(allowed_chat_ids) -> empty, confirming
-- no row claims this chat_id yet.
--
-- OWN single-chat_id row, NOT added to coffeemedia's existing
-- allowed_chat_ids={286619815} -- per design section 1 step 2 and bus
-- #70499's explicit instruction, so the cross-wire proof (section 4) stays
-- meaningful: a message from 8494278755 must resolve to a tag distinct from
-- both coffeemedia and brandit-coffeemedia after this row ships, and neither
-- sibling tag may ever show a row from this chat_id.
--
-- mode='agent-session' (unlike brandit-coffeemedia's log-and-route): this is
-- testing the onboarding flow end-to-end through to a LIVE reply, not just
-- routing-dispatch -- inject_target='coffeemedia' reuses the SAME lane that
-- already handles the real coffeemedia DM and the brandit-coffeemedia
-- routing proof, per bus #70499's "tag routing to the brandit/coffeemedia
-- (coffeemedia lane) handler".
--
-- group_routing.agent_phase='supervised' (RESOLVED 2026-10-18, bus #70653 --
-- supersedes #70499's earlier "direct is acceptable" call): orch-console's
-- gate-pass #70650 named this "the SUPERVISED e2e TEST" and its condition 4
-- requires outbound to 8494278755 to route through the reviewer
-- (orch-console) before it reaches the alt account -- that is supervised
-- behavior (lane_reply.sh files a draft to agent_reviewer, who forwards),
-- not direct (lane sends immediately). Caught the direct-vs-supervised
-- contradiction between #70499 and #70650/#70653 before merging, flagged it
-- (bus #70652) instead of picking a side, and #70653 resolved it explicitly:
-- supervised wins. agent_reviewer stays 'orch-console'.
--
-- group_routing.powers=['reply_text']: least-privilege metadata per design
-- section 3. NOTE -- the PreToolUse-style enforcement guard for `powers` is
-- still BUILD-PHASE, not yet written (design only, build held pending
-- Musa's policy sign-off). This key is recorded now as the correct
-- least-privilege value so the row is right the moment the guard ships,
-- but today nothing reads or enforces it yet -- say so plainly rather than
-- imply a protection that isn't live.
--
-- enabled=false: being added to the Mini's INGEST_CHANNELS pin
-- (scripts/boot_nazim_ingest.sh, same commit) alongside 'coffeemedia' and
-- 'brandit-coffeemedia' -- same WINGMENDEVBOT_TOKEN group, same single
-- poller (the Mini), so the gzb hub's unscoped `WHERE enabled` poll never
-- touches this token at all. Same invariant as migrations 109/110/111;
-- tests/migrations/test_ingest_channels_enabled_false.py enforces this
-- statically.
--
-- --store: none. Pure Telegram-routing test, no product/business data
-- involved or written anywhere -- per design section 5's explicit
-- none-is-a-valid-answer carve-out (TENANT-RESIDENCY-001/LAYER-VOCAB-001).
--
-- NOT APPLIED YET -- drafted for orch-console's gate per bus #70642's
-- explicit "Bring the onboarding artifact to MY gate BEFORE anything is
-- enabled." Dry-run sha256 computed and reported on the bus; apply only
-- after a gate grant citing that hash, via the usual gated
-- apply_migration.py (never `supabase db push`, decision 962).
--
-- REVERT: DELETE FROM public.bot_channels WHERE channel_key = 'coffeemedia-hamhuskies-test';

INSERT INTO public.bot_channels
  (channel_key, token_env_key, mode, inject_target, inject_prefix, responder_ref,
   allowed_chat_ids, allowed_usernames, group_routing, channel_tag, log_target, enabled, poll_offset,
   audience, owner_lane)
VALUES
  ('coffeemedia-hamhuskies-test', 'WINGMENDEVBOT_TOKEN', 'agent-session', 'coffeemedia',
   NULL, NULL,
   '{8494278755}', '{}',
   '{"agent_phase": "supervised", "agent_reviewer": "orch-console", "powers": ["reply_text"]}'::jsonb,
   'coffeemedia-hamhuskies-test', 'substrate', false, NULL,
   'internal', 'coffeemedia')
ON CONFLICT (channel_key) DO NOTHING;
