# BUG-024 Phase 2 — agent_messages Identity Enforcement (Design / Draft Spec)

**Date:** 2026-06-11
**Owner:** cc-orchestrator (substrate); credential provisioning OPERATOR-gated (Musa)
**Authority:** BUG-024 (P0 impersonation incident), BUG-024-PHASE2-001 (verified 2026-06-10, NOT closed)
**Status:** DRAFT — buildable now (trigger + RLS + tests); cannot be *applied* until per-agent credentials exist.

## Problem

On 2026-04-18 a non-cai actor posted agent_message #252 with `from_agent='cai'`
self-authorising a decision. BUG-024 Phase 1 shipped the *forensic scaffolding*
(`posted_by_identity`, `from_agent_verified`, a provenance trigger, an
`identity_allowlist` table) but NOT structural enforcement. The impersonation
vector is still open: any holder of the shared service-role key can post as any
agent and self-authorise. This is the exact "convention not invariant" failure
the BUG-020/021/022 family was meant to end, and it is the hard dependency
gating CC-ORCH-INBOX-LOOP-001 Phase 2 (autonomous Telegram) — until identity is
enforced, escalate-only-in-session is the containment line.

## Current state (verified 2026-06-10)

**Trigger `populate_agent_messages_provenance` (SECURITY DEFINER):**
```sql
IF NEW.posted_by_identity IS NULL THEN
    NEW.posted_by_identity := current_user;   -- caller can pre-set ANY value
END IF;
IF NEW.from_agent_verified IS NULL THEN        -- caller can pre-set true
    NEW.from_agent_verified := EXISTS (allowlist match) ? true : NULL;
END IF;
```

**Three structural defects:**
1. **`posted_by_identity` is caller-overridable.** The `IF … IS NULL` guard
   means a caller may supply any value; it only falls back to `current_user`
   (which is `postgres`/`service_role` on the shared path — not an agent).
2. **`from_agent_verified` is caller-overridable and NULL-by-default.** Evidence:
   `identity_allowlist` is **empty**, yet 47 rows carry
   `from_agent='cai', from_agent_verified=true`. With an empty allowlist the
   trigger can only produce `true` if the **caller supplied it** — so the
   "verified" flag currently carries no trust signal at all.
3. **No INSERT-time enforcement.** RLS on `agent_messages` is a single
   `service_role`-only ALL policy (`auth.role() = 'service_role'`). The
   service-role key **bypasses RLS** entirely, so nothing checks that
   `from_agent` matches the posting identity.

Current `posted_by_identity` distribution: `postgres` 873, NULL 416 (pre-trigger),
`cai` 80 (47 verified=true via caller override), `musa_cto` 6, `cai-session` 2,
`unknown_impersonator` 1 (the #252 forensic tag).

## Closure design

Two tracks. The credential track is the operator's; everything else
cc-orchestrator builds now and applies the moment credentials land.

### Track A — OPERATOR-gated: per-agent authenticated identity

Pick ONE substrate (both make `from_agent` enforceable):

- **(a) Per-agent DB roles / API keys** — a restricted Postgres role per logical
  agent (`cai`, `cc-ihsanos`, `cc-orchestrator`, `cc-scholar`, `cc-cosem`,
  `cc-wing`, `substrate`), **none with BYPASSRLS**, each with its own connection
  string. `current_user` then equals the agent. Faster; no JWT infra.
- **(b) Per-agent JWTs** carrying a verified `agent_id` claim. Cleaner long-run
  (Phase 3); the trigger reads `request.jwt.claims ->> 'agent_id'`.

**Critical dependency:** enforcement only bites if agents connect as roles that
do **NOT** bypass RLS. Today every writer uses the shared service-role. Track A
is therefore *also* an inserter-migration: each writer process must connect
under its agent role. (See "Inserter migration" below — this is the real cost.)

### Track B — cc-orchestrator buildable now

**B1. Harden the provenance trigger to OVERWRITE, not fill-if-null.**
```sql
-- posted_by_identity is ALWAYS the authenticated identity; caller input ignored.
NEW.posted_by_identity := coalesce(
    current_setting('request.jwt.claims', true)::json ->> 'agent_id',  -- option (b)
    current_user                                                        -- option (a)
);

-- from_agent_verified is ALWAYS computed, never NULL, never caller-set.
NEW.from_agent_verified := (
    NEW.from_agent = NEW.posted_by_identity
    OR EXISTS (SELECT 1 FROM identity_allowlist
               WHERE posted_by = NEW.posted_by_identity
                 AND allowed_from_agent = NEW.from_agent)
);
```
Notes:
- `from_agent_verified` must stay **trigger-computed**, not a true
  `GENERATED … STORED` column — generated columns cannot reference another table
  (the allowlist) or use subqueries. (cai's "GENERATED/trigger-computed" → it is
  the latter, for this reason.)
- Drop the `IF … IS NULL` guards entirely; provenance is no longer a hint.

**B2. RLS INSERT policy (the enforcement).**
```sql
CREATE POLICY "from_agent must match posting identity" ON agent_messages
  FOR INSERT WITH CHECK (
    from_agent = coalesce(
        current_setting('request.jwt.claims', true)::json ->> 'agent_id',
        current_user)
    OR EXISTS (SELECT 1 FROM identity_allowlist
               WHERE posted_by = coalesce(
                        current_setting('request.jwt.claims', true)::json ->> 'agent_id',
                        current_user)
                 AND allowed_from_agent = from_agent)
  );
```
BEFORE-trigger output is what RLS `WITH CHECK` evaluates, so the resolved
identity is consistent between B1 and B2. Impersonation becomes impossible at
the auth layer: a `cc-ihsanos` role inserting `from_agent='cai'` fails the
`WITH CHECK` unless an allowlist row explicitly permits it.

**B3. The operator→cai exception.** cai posts are operator-initiated (Musa acts
as cai in-session). Seed `identity_allowlist`:
| posted_by | allowed_from_agent | note |
|---|---|---|
| `musa` (or the operator role) | `cai` | operator posts ratifications as cai |
| `musa` | `musa` | operator's own posts |

The allowlist is the *only* sanctioned cross-identity path and is itself
operator-controlled. No agent role is allowlisted to post as another agent.

**B4. Mirror to `strategic_decisions`.** The same overridable pattern exists in
`populate_strategic_decisions_provenance` (`decided_by_verified`). The #252
impersonation's *payload* was decision-authority, so `decided_by` needs the
identical OVERWRITE + always-compute + RLS treatment, or the vector simply moves
from the bus to the decision table. In scope for closure.

### Inserter migration (the real work behind Track A)

Every current writer uses the shared service-role and frequently posts as a
*different* `from_agent` than its own process identity. Examples to resolve:
- `nervous_system/*` pollers post as `cc-orchestrator` → run under the
  `cc-orchestrator` role: clean.
- The cc-cai daemon operator-button path posts `from_agent='musa'` → needs the
  `musa` allowlist entry (B3), since the process is not Musa's personal identity.
- `ralph_runner` and `arch-030-escalation` post under non-canonical identities
  (the same writers that block the SUBSTRATE-COHERENCE-001 E `from_agent` CHECK)
  — these MUST be mapped to a real agent identity or allowlisted before
  enforcement, or they break at INSERT. **This work item is shared with E's
  deferred CHECK** — do both together.

## Acceptance criteria (to CLOSE BUG-024)

1. `posted_by_identity` is non-overridable — a caller-supplied value is ignored;
   it always equals the authenticated identity. (test)
2. `from_agent_verified` is always computed, never NULL, never caller-set. (test)
3. An INSERT with `from_agent` ≠ the posting identity (and no allowlist row)
   **fails** at the RLS layer. (test: a `cc-*` role cannot post as `cai`.)
4. The operator→cai allowlist path still works for genuine ratifications. (test)
5. `strategic_decisions.decided_by` carries the same enforcement. (test)
6. All live inserters migrated to agent roles / allowlist entries; no writer
   broken. (integration soak)

## Phasing recommendation (the challenge cai invited)

cai framed (a) per-agent roles as a "faster Phase 2 stopgap" and (b) JWT as the
"cleaner Phase 3." **Challenge: (a) is not a stopgap — it is the closure.**
Per-agent restricted DB roles already make impersonation impossible at the auth
layer (criteria 1–4 hold under `current_user`). JWT (b) is an *ergonomics /
portability* upgrade (claims travel with the request, no per-role connection
strings), not a *security* upgrade. So:

- **Phase 2 = closure:** per-agent roles (a) + B1–B4 + inserter migration →
  close BUG-024.
- **Phase 3 = ergonomics:** swap `current_user` for the JWT claim (the trigger
  and RLS are already written with `coalesce(jwt_claim, current_user)`, so Phase
  3 is a no-op on the SQL — only the connection layer changes).

The dominant cost is the **inserter migration**, not the JWT infra. Sequencing
roles-first lets us close the security finding without blocking on JWT tooling.

## Apply approach

All trigger/RLS/allowlist changes ship as a psycopg-apply script under
`scripts/` (dry-run then `--apply`), matching the repo pattern. Never
`supabase db push` to prod (CLAUDE.md decision-962). The script is written and
tested against a per-agent role NOW (using `SET ROLE` to simulate), and applied
to prod once the operator provisions the real roles/keys.

## Out of scope

- Creating the credentials themselves (operator).
- Retroactively re-verifying the 416 pre-trigger NULL rows / 47 caller-override
  `cai` rows — they stay flagged untrusted per BUG-024 Phase 1 interim
  discipline; enforcement is forward-looking.
