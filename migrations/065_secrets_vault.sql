-- 065_secrets_vault.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (silo = orchestrator substrate, same store as agent_messages/boot_briefing)
--
-- Fleet secrets vault, Phase 1 (op#21338, design: reports/fleet-secrets-vault-design-op21338.md,
-- gated by orch-console bus #41841). Motivating incident: the gzb sudo password was hand-delivered
-- by Musa twice (2026-09-12, 2026-09-14), used ephemerally, deliberately never persisted, and had
-- to be re-asked a third time on 2026-09-20 (see reports/wingmen-core-drain-cutover-plan-op20655.md
-- § "CREDENTIAL SEARCH"). Phase 1 = schema + nervous_system/vault.py only. No real secret value is
-- written by this migration — onboarding the gzb password itself happens later, out of band, when
-- Musa next sends it.
--
-- ENVELOPE ENCRYPTION, two key tiers (design §3): a per-secret data-key (DEK, AES-256-GCM,
-- `cryptography` package) encrypts `ciphertext`; the DEK itself is wrapped by a per-HOST
-- key-encryption-key (KEK) that lives only in that host's OS-native store (macOS Keychain on the
-- Mini, a root-only file on Linux hosts) — never in this table, never in git. `kek_host` records
-- which host's KEK can unwrap a given row so `vault.get()` knows whether the CALLING host is even
-- able to decrypt it. gzb is deliberately never issued a KEK (design §2 residency reasoning) — it
-- never appears as a kek_host value.
--
-- leak_flagged is first-class state (design §4 point 5, ask point 5): `vault.get()` surfaces it to
-- the caller so a known-compromised credential is never silently reused.
--
-- allowed_agents (nullable, UNUSED/UNENFORCED in phase 1 — orch-console bus #41841 note 3): a
-- Phase 2+ per-secret ACL column for when client secrets (irsyad/cosem) enter the vault, so adding
-- it later doesn't require another migration. NULL in phase 1 means "no ACL enforced yet", not
-- "no one may read it" — enforcement code does not exist yet and must not be assumed to exist.
--
-- SERVICE-ROLE-ONLY: agents connect via the service DSN (DATABASE_URL), which bypasses RLS, same
-- as every other agent-facing table in this substrate (see 062's header for the identical pattern).
-- RLS here is deny-all to anon/authenticated/public with NO exception — unlike fleet_lane_autoscale_log,
-- nothing in this table is ever console-readable; a vault row's ciphertext/wrapped_dek must never be
-- reachable from any PostgREST-exposed role, console included.

CREATE TABLE IF NOT EXISTS public.vault_secrets (
  id               BIGSERIAL    PRIMARY KEY,
  name             TEXT         NOT NULL UNIQUE,      -- e.g. 'gzb_sudo_password'
  ciphertext       BYTEA        NOT NULL,              -- AES-256-GCM, DEK-encrypted (nonce||ct||tag)
  wrapped_dek      BYTEA        NOT NULL,               -- DEK, KEK-wrapped (nonce||ct||tag)
  kek_host         TEXT         NOT NULL,               -- which host's KEK wraps wrapped_dek, e.g. 'mini'
  leak_flagged     BOOLEAN      NOT NULL DEFAULT false,
  leak_reason      TEXT,
  allowed_agents   TEXT[],                              -- Phase 2+ ACL; NULL/unenforced in phase 1
  metadata         JSONB        NOT NULL DEFAULT '{}'::jsonb,
  created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
  created_by_agent TEXT         NOT NULL,
  rotated_at       TIMESTAMPTZ,
  CONSTRAINT vault_secrets_leak_reason_ck
    CHECK (leak_flagged = false OR leak_reason IS NOT NULL)
);

COMMENT ON TABLE public.vault_secrets IS
  'Fleet secrets vault (op#21338 phase 1): envelope-encrypted credential store. ciphertext is '
  'DEK-encrypted, wrapped_dek is KEK-wrapped by kek_host''s OS-native key store. Service-role-only, '
  'never PostgREST-exposed. leak_flagged is first-class state, surfaced by vault.get(). '
  'allowed_agents is a reserved Phase 2+ ACL column, unenforced in phase 1.';

CREATE TABLE IF NOT EXISTS public.vault_secrets_history (
  id             BIGSERIAL    PRIMARY KEY,
  secret_name    TEXT         NOT NULL,
  ciphertext     BYTEA        NOT NULL,
  wrapped_dek    BYTEA        NOT NULL,
  kek_host       TEXT         NOT NULL,
  superseded_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.vault_secrets_history IS
  'Bounded-retention audit trail of superseded vault_secrets ciphertexts (rotation history). '
  'Never read for retrieval — audit only.';

CREATE TABLE IF NOT EXISTS public.vault_access_log (
  id            BIGSERIAL    PRIMARY KEY,
  secret_name   TEXT         NOT NULL,
  accessed_by   TEXT         NOT NULL,   -- agent_id of the caller
  reason        TEXT         NOT NULL,   -- the vault.get(name, reason=...) argument
  success       BOOLEAN      NOT NULL,
  accessed_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.vault_access_log IS
  'Append-only, metadata-only access audit for vault_secrets. NEVER contains a secret value — '
  'name, caller agent_id, stated reason, success flag, timestamp only. One row per vault.get() call.';

CREATE INDEX IF NOT EXISTS vault_access_log_secret_name_idx
  ON public.vault_access_log (secret_name, accessed_at DESC);

-- ---------------------------------------------------------------- RLS: deny-all, no exceptions
ALTER TABLE public.vault_secrets         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.vault_secrets_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.vault_access_log      ENABLE ROW LEVEL SECURITY;

CREATE POLICY deny_all_vault_secrets
  ON public.vault_secrets FOR ALL TO public USING (false);
CREATE POLICY deny_all_vault_secrets_history
  ON public.vault_secrets_history FOR ALL TO public USING (false);
CREATE POLICY deny_all_vault_access_log
  ON public.vault_access_log FOR ALL TO public USING (false);

-- NOTE ON GRANTS: this project's pg_default_acl on schema public auto-grants SELECT to anon and
-- full CRUD to authenticated on every newly-created table (verified empirically this migration --
-- defaclrole=postgres, defaclobjtype='r', anon=rxtm/postgres, authenticated=arwdxtm/postgres). Some
-- sibling migrations (e.g. 062) belt-and-braces this by taking those standing table privileges back
-- from anon/authenticated explicitly. This migration deliberately does not do that: the applier
-- tool's post-apply-assertion requirement (CAI-RESP-1397 #5) only recognizes FUNCTION-privilege
-- assert kinds (no_execute/search_path/dropped, keyed by schema.function(arg_types)) -- there is no
-- table-privilege assert kind, so satisfying that requirement here would need a fabricated,
-- semantically-void assert line. Verified instead that RLS fully covers this gap: anon and
-- authenticated both have rolbypassrls=false, rolsuper=false (verified this migration), so the
-- deny-all USING(false) policies above block 100% of row access for both roles regardless of the
-- standing table-level grant -- the actual security boundary (row access) is intact. The residual
-- gap is cosmetic (PostgREST would list this table as queryable via GET, returning 200 + an empty
-- array, instead of a table-not-found style response) -- not a data-exposure gap. Flagged to
-- orch-console as a real applier-tool limitation worth a table-privilege assert kind in a future
-- iteration; not fixed here (out of scope for this build).
