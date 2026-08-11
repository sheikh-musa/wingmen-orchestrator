# Secrets & DR Registry

Canonical registry of the fleet's CRITICAL secrets/keys + their disaster-recovery (DR) backup status.

**Purpose: make "is secret X backed up?" a 5-second LOOKUP — not a memory-hunt, and not an operator re-ask.** Born from the 2026-08-11 NRIC-key continuity gap: the fact (the goumlyne key was already recovered + backed up under blocker #6692) was sitting in our own records, but an agent re-asked the operator instead of searching. Storage was fine; *recall* was the failure. This doc is the recall fix.

## Rules
- **NEVER store secret VALUES here.** Only metadata: what it protects, where it lives, backup location + status. This file is committed to git — values would be a leak.
- **Search THIS FIRST** before asking the operator whether something is backed up. (reconstitute-from-records, don't re-ask.)
- **Verify, don't assume** a backup exists — confirm the backup file is present and, for an encryption key, that it actually decrypts real rows (GCM-auth) before marking VERIFIED.
- **Catastrophic-loss** secrets (master encryption keys) MUST have an off-Vercel backup — lose it and the encrypted data is permanently unrecoverable. **Rotation-recoverable** secrets (peppers, session/signing secrets) are lower-risk but still tracked.
- **Backing up a key via a lane = no-echo only**: pull → save to a 0600 fleet file → never print the value to any bus/log/transcript (the #6692 precedent). Prefer TWO copies. If the Vercel var is stored *sensitive/write-only*, an agent cannot read it → operator copies it manually.
- Update this doc whenever a secret is created, rotated, backed up, or recovered.

## Critical secrets

| Secret | Protects | Lives in | Off-Vercel backup | Status |
|---|---|---|---|---|
| `NRIC_ENCRYPTION_KEY` (goumlyne / Irsyad) | AES-GCM master key — fr.irsyad donor NRIC/phone/email (849 rows) | goumlyne (`ihsanos-irsyad`) Vercel project env | `~/.wingmen/keys/irsyad-nric-key` (0600, Mini) | **VERIFIED** — recovered under blocker #6692 (2026-08-06); decrypts the 849 rows GCM-auth; extraction torn down (no probe/bypass left) |
| `NRIC_ENCRYPTION_KEY` (ceayj / ihsanos) | AES-GCM master key — ceayj NRIC/phone/email (66 real rows) | ceayj (`ihsanos`) Vercel project env (production entry = `encrypted`/readable; preview = sensitive/write-only) | `~/.wingmen/keys/ceayj-nric-key` + `.bak` (0600 ×2, Mini) | **VERIFIED** — CAI-848; #6692-style no-echo pull (2026-08-11), GCM-decrypts 66/66 real rows (fail=0) |
| `NRIC_HASH_PEPPER_V1` / `PII_HASH_PEPPER_V1` (ceayj) | HMAC pepper for NRIC/PII hashes (dedup) — **rotation-recoverable** | ceayj Vercel env (sensitive) | n/a (rotation-recoverable) | **PLACED** 2026-08-11 (cc-ihsanos, no-echo) — CAI-845 |
| `NRIC_HASH_PEPPER_V1` / `PII_HASH_PEPPER_V1` (goumlyne) | same, goumlyne silo (distinct values from ceayj) | goumlyne Vercel env | n/a (rotation-recoverable) | **IN PROGRESS** (cc-irsyad, no-echo) — CAI-845 |

## TODO (v1 → complete)
- **Sweep the fleet for ALL other critical secrets** not yet listed — session/signing secrets (e.g. the shipforge `SESSION_SECRET`/`REVALIDATE_SECRET` fallback), service-role keys, bot tokens, OAuth tokens, DB creds — + their backup status. *(Deferred: needs a lane sweep; not during the token-conservation window.)*
- **Off-MACHINE redundancy** for the NRIC key backups — both goumlyne (`irsyad-nric-key`) and ceayj (`ceayj-nric-key`) copies live on the Mini (`~/.wingmen/keys/`); a Mini loss takes them all. True DR wants a copy on a second machine / offline store.
- Sibling of `docs/data-store-registry.md` (stores) — keep them cross-referenced.

**Refs:** CAI-845 (peppers), CAI-848 (AES-key DR), blocker #6692 (goumlyne key recovery, op#10934).
