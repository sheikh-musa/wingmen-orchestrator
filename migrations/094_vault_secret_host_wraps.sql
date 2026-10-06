-- 094_vault_secret_host_wraps.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate, same store as vault_secrets/065)
--
-- assert: no_table_privilege anon public.vault_secret_host_wraps SELECT
-- assert: no_table_privilege authenticated public.vault_secret_host_wraps SELECT
--
-- Multi-host vault portability (bus #53277 -> #53291 -> #53347 -> #53351 ->
-- #53582 -> #53588, orch-console P1, Musa op#26219): the hub (cc-orchestrator)
-- moves from the Mini to GLM, and GLM_CODING_KEY is wrapped only for
-- kek_host='mini' -- vault.get() on hub-vps (gzb) raises WrongHostError before
-- it ever reaches a KEK lookup (065's vault_secrets.kek_host is single-valued
-- per secret). This is the agreed, orch-console-ACCEPTED (#53588) fix: an
-- ADDITIVE per-(secret, host) wrap table, not a redesign of vault_secrets.
--
-- Design shape (settled on #53351, re-confirmed #53588 "no redesign, go
-- straight to migration + gzb proof"):
--   - vault_secrets.ciphertext is UNTOUCHED -- the DEK-encrypted secret value
--     never changes or gets re-encrypted when a new host needs to read it.
--   - a row here holds the SAME DEK, re-wrapped under a DIFFERENT host's own
--     local KEK. vault.get() tries the primary (vault_secrets.kek_host) path
--     first (fast path, zero behavior change for the Mini); only on a
--     WrongHostError does it fall back to looking here for a row matching
--     (secret_name, this host's local_host_id()).
--   - a row here is written by vault.wrap_for_host(name, plaintext_dek, reason),
--     which runs ON the target host (never a cross-host KEK read -- the
--     target host wraps under ITS OWN local KEK, using a plaintext DEK that
--     was hand-delivered to it over the same channel any vault secret already
--     crosses hosts on when first put() -- never a KEK, never logged, never
--     through a visible transcript).
--
-- Same RLS/grant convention as 065/089 (CAI-1018): service-role-only, deny-all
-- RLS with no exception, explicit REVOKE from anon/authenticated verified by
-- the -- assert: no_table_privilege lines above (CAI-RESP-1397 #5 -- an
-- unverified REVOKE can be a silent no-op against this store's
-- pg_default_acl, which auto-grants SELECT to anon / full CRUD to
-- authenticated on every new table). This closes 065's own documented gap
-- (065's header: "there is no table-privilege assert kind" at the time it was
-- written) -- apply_migration.py gained no_table_privilege afterward.

create table if not exists public.vault_secret_host_wraps (
  secret_name      text        not null references public.vault_secrets(name) on delete cascade,
  kek_host         text        not null,  -- the host whose local KEK wraps wrapped_dek below
  wrapped_dek      bytea       not null,  -- SAME dek as vault_secrets.wrapped_dek unwraps to, re-wrapped
  created_at       timestamptz not null default now(),
  created_by_agent text        not null,
  primary key (secret_name, kek_host)
);

comment on table public.vault_secret_host_wraps is
  'Per-(secret, host) DEK re-wraps for multi-host vault portability (op#26219 hub GLM move, '
  'bus #53277/#53351/#53588). Additive to vault_secrets (065) -- never touches ciphertext. A row '
  'here is written only by vault.wrap_for_host(), which runs ON the target host and never reads '
  'another host''s KEK. vault.get() falls back here only on a WrongHostError against the primary '
  'vault_secrets.kek_host row.';

alter table public.vault_secret_host_wraps enable row level security;

drop policy if exists deny_all_vault_secret_host_wraps on public.vault_secret_host_wraps;
create policy deny_all_vault_secret_host_wraps
  on public.vault_secret_host_wraps for all to public using (false);

revoke all on public.vault_secret_host_wraps from public, anon, authenticated;
