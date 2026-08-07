-- 033_agent_status_auth_attribution.sql
--
-- WHY (op#7094, 2026-07-25): the fleet console displayed every lane as if it ran on the
-- operator's account. It never knew — it doesn't track accounts at all, it assumed. That
-- assumption was wrong for months: the Studio authenticated against a DIFFERENT Claude
-- account than the Mini (different userID, different session-limit pool), which only
-- surfaced when the Studio hit a session cap while the Mini kept working.
--
-- Fix the SOURCE, not the display: each session stamps what it actually inherited at boot.
--   host         — which machine the session runs on (Mini vs Studio).
--   auth_account — human label for the account, from CLAUDE_ACCOUNT_LABEL in that host's .env.
--   auth_fp      — sha256(token)[:12]. The label is a CLAIM; the fingerprint is the FACT.
--                  Two hosts claiming the same label with different fingerprints are on
--                  different accounts, and the console can say so instead of guessing.
--
-- The token itself is never stored — only its fingerprint, which is not reversible and is
-- useless to an attacker.
--
-- Additive and nullable: existing rows and any writer that doesn't set these keep working.

ALTER TABLE agent_status
  ADD COLUMN IF NOT EXISTS host         text,
  ADD COLUMN IF NOT EXISTS auth_account text,
  ADD COLUMN IF NOT EXISTS auth_fp      text;

COMMENT ON COLUMN agent_status.host IS
  'Machine this session runs on (hostname -s), stamped at boot by launch_dangerous_cc.sh.';
COMMENT ON COLUMN agent_status.auth_account IS
  'Human label for the Claude account this session authenticated with (CLAUDE_ACCOUNT_LABEL). A claim — verify against auth_fp.';
COMMENT ON COLUMN agent_status.auth_fp IS
  'sha256(CLAUDE_CODE_OAUTH_TOKEN)[:12] — the fact behind auth_account. Never the token itself.';
