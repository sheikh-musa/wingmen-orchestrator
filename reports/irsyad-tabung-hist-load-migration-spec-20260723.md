# irsyad historical tabung load — migration build spec (delegation)

**Date:** 2026-07-23 · **Author:** cc-orchestrator (hub) · **Status:** ready to delegate; LOAD HELD
**Governance:** cai CAI-RESP-519 (#10781) + affirm #10790; hub ACK #10786. Load held until (i) ~~authority~~ **✓ CLOSED — operator op#6636 (2026-07-23 11:01Z): "client greenlit channel will suffice because it comes from client directly, they are merely relaying the requests" → op#6605 satisfies pre-cond 5; separate Saddam sign-off waived. Relay this to cai at grant time (cai pre-cond 5 asked to confirm op#6605 is the authorised signatory).** (ii) cai §6.6 grant on migration A, (iii) cai §6.6 grant on migration B. **Build may parallelise now; APPLY nothing without the matching cai grant.**

**Target silo:** goumlyne (irsyad silo) `goumlynecruxrlmzlntp`, postgres-only via `GOUMLYNE_DATABASE_URL` (NO goumlyne auth service-key on hub). Reference: `docs/data-store-registry.md`, TENANT-RESIDENCY-001.

**Source of truth:** `logs/tg_media/Collected Tabung 2020 to 2026.xlsx` — HUB-VERIFIED 2,612 rows, S$1,718,341.46, 2021-05→2026-03. NIZOM file DROPPED (op#6614). Shape: 575 names / 233 contacts / ~1,313 anon (Hamba Allah) / 118 emails; Types Fajar 2374 / Masjid 116 / Kedai 103 / Keluarga 19; 25× S$0; "Tabung Jumaat" S$130k/S$116k = masjid Friday-collection AGGREGATE (pooled, not a person).

Sequence is **A before B** (silo hardened before any PII lands — cai #10790). Both build in an **isolated worktree**; each returns to cai with the proofs below for a §6.6 named-file grant.

---

## VERIFIED goumlyne schema (hub-checked 2026-07-23 — supersede cai's paraphrase where noted)

`donations`: id, org_id, category_id, person_id, amount(numeric), payment_method, reference_no, notes, is_anonymous(bool), is_tax_deductible(bool), **donated_at(timestamptz)**, created_by, created_at, updated_at, deleted_at, **import_ref(text)**.
- ⚠️ **No `source` column.** cai's plan says tag `source='irsyad_tabung_hist_2020_2026'` → **reuse the existing empty `import_ref` column** for the provenance tag (verified currently unused: `SELECT DISTINCT import_ref` = ∅). More anti-takalluf than adding a column. **Flag this substitution to cai in the migration-B grant.**
- ⚠️ Date column is **`donated_at`**, not `donation_date`.
- Baseline: 1 pre-existing donation row (unrelated, import_ref NULL).

`persons`: id, org_id, **display_name(text, PLAINTEXT)**, nric_encrypted, nric_hash, nric_source, **address(text, plaintext)**, date_of_birth, gender, custom_fields(jsonb), tags[], is_active, created_at, updated_at, deleted_at, user_id, **phone_encrypted, phone_hash, phone(plaintext), email_encrypted, email_hash, email(plaintext)**.
- Extensions present: `pgcrypto`, `supabase_vault`. Baseline 1,467 rows.
- ⚠️ **OPEN QUESTION FOR cai (pre-cond 2 "encrypt name/phone/email"):** `phone`/`email` have `_encrypted`+`_hash` columns → loader writes those, leaves plaintext `phone`/`email` NULL **iff** the app reads the encrypted columns (LANE MUST verify the app's PII read path in the ihsanos repo before choosing). **`display_name` is plaintext by app design — there is NO `name_encrypted` column.** Encrypting the name would require a schema + app-read change (large scope cai likely did not intend). Loader CANNOT satisfy "encrypt name" alone. **Surface to cai: is name-at-rest covered by the hardening + RLS + no-anon-write, or does he want a name-encryption schema/app change scoped separately?** Do not guess.

Current grants (VERIFIED wide-open — pre-cond 1 is genuinely required):
`persons`, `donations`, `donation_categories`, `person_roles` — **anon** AND **authenticated** each hold `DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE`.

---

## Migration A — goumlyne PII-hardening (PII-ANON-1)

Orch-side psycopg apply script (direct-apply pattern, per CLAUDE.md PR #41/#42/#44 — NOT `supabase db push`). Build the script; **do not apply** — return for cai §6.6 grant.

Statements:
- `REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON persons, donations, donation_categories, person_roles FROM anon;`
- `REVOKE TRUNCATE ON persons, donations, donation_categories, person_roles FROM authenticated;` (keep authenticated I/U/D = the app path).
- Keep `SELECT` for anon only if the app's anon path needs it; default posture = also revoke anon SELECT unless a read path proves it's needed (LANE verifies; note either way).
- Any new table born RLS-on + no-anon-write (person_relationships absent today — none to create).

**Proof for cai (required):** live `SET ROLE anon;` then attempt INSERT/UPDATE/DELETE/TRUNCATE on each of the 4 tables → all DENIED; `RESET ROLE`. Capture output. Confirm no `qual=true` broken policies (cai: none on goumlyne — reconfirm).

## Migration B — one-time idempotent loader (applied only AFTER A is granted+applied)

**Not** a generalised importer (cai (d) — second client drives generalisation). One-time Python script keyed by **source-row id** (idempotent: re-run = no dupes; safe to re-run to completion).

Transform rules (cai CAI-519 (a)-(d), binding):
- **Reuse `persons` + `donations`** — no separate historical model.
- Tag every donation `import_ref='irsyad_tabung_hist_2020_2026'` (provenance + delete-by-batch reversibility). Set `donated_at` from the row date; `amount` incl the 25× S$0 rows.
- **Dedup name+contact — PREFER SPLIT OVER FALSE-MERGE (cardinal amanah rule):** never merge on name alone; a no-contact row NEVER folds into a named person; normalise contact before compare. Misattributing one donor's gift corrupts a real lifetime total.
- **Anonymous (Hamba Allah)** → `is_anonymous=true`, NO person row.
- **Pooled sources (Tabung Jumaat S$130k/S$116k, masjid collection points)** → load as **labelled collection sources, NEVER as an individual's lifetime total** (no-gharar). Confirm the UI won't render a pooled point as one person's total.
- **PII:** DROP 'Street'/address (lifetime-history doesn't need it). phone/email → encrypted+hash columns per the app's verified pattern (see open question). Name → `display_name` (pending cai on encryption).

**Proof for cai (required):** dry-run reconcile on live goumlyne — loaded `SUM(amount)` **== S$1,718,341.46** AND `COUNT(*)` **== 2,612** (incl 25 S$0), scoped to `import_ref='irsyad_tabung_hist_2020_2026'`; idempotency proof (second run = 0 new rows). Then cai independently re-runs the reconcile live + reads back a sample donor via the real lifetime-history feature (money-path verified by real flow).

---

## Delegation & lane discipline
- Isolated **git worktree** (do not collide with the live console / other lanes). Lane connects to goumlyne **read-only** during build; NO writes until the matching cai grant.
- Lane surfaces the two open questions (name encryption; app PII read path / plaintext-column disposition) to cai **with** migration B, not to the operator.
- Hub verifies every proof before relaying "done"; cai re-verifies live per pre-cond 4. Test end-to-end before calling it live [[test-end-to-end-before-declaring-live]].
