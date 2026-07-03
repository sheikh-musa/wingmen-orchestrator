# DB Decomposition — split the 163-table monolith into isolated DBs

**Date:** 2026-07-01 · **Owner:** cc-orchestrator · **Governance:** cai (up; owns architecture)
**Trigger:** substrate ultracode audit (reports/substrate-ultracode-audit-2026-07-01.md) — 15 confirmed (9×P1); the DB RLS/grant layer is open across ALL 163 tables (one anon key exposes fleet + all client PII). Operator decision: split the database.

## Current state
- ONE Supabase project `tscuymavysscrvoberrr` (ap-southeast-2 / Sydney) holds **163 tables** spanning the fleet substrate + ~8 client verticals, all behind one anon key with permissive `USING(true)`/`PUBLIC` grants.
- Security hole is LIVE but latent (anon key not published anywhere yet). Blocker posted to cai: agent_messages #5008.
- Contradicts memory `project_pdpa_data_residency`: this Sydney DB HAS PII (clients, sch_* student data, Zahidah private notes) — the "substrate is no-PII/Sydney" assumption is wrong.

## Coupling (feasibility)
- Cross-boundary FKs = **6, all → `clients`** (+ site_templates): jobs, provisions(→clients,site_templates), usage_log, bug_reports, client_repos → clients. `clients`/`site_templates` are the fleet↔business hub — resolve first (likely stays fleet-side; verticals get own customer tables; FKs become soft refs).

## Relevant cai decisions (already filed)
- **BOT-INGEST-TOPOLOGY-001** (ratified): bot channels = config rows → one ingestion layer → substrate → concern-agent drains async; never through orch.
- **ZAHIDAH-DATA-ISOLATION-001**: OPERATOR-RESERVED — Zahidah private-data isolation awaits Musa's button.
- **CAI-RESP-355**: Zahidah second-brain (mamadah_notes, pgvector/BGE-M3 via Mac Studio) — live, challenge_window.

## Two parallel tracks
### Track A — close the hole NOW (independent of any split)
RLS/grant lockdown: substrate tables → service_role-only policy + `REVOKE FROM PUBLIC, anon, authenticated` (safe — fleet connects as owner/service_role, bypasses/passes); client tables → proper org-scoped RLS (NOT blanket lock). Dry-run→apply per CLAUDE.md, never `supabase db push`. NOTE from dry-run: grants come via PUBLIC, so REVOKE must target PUBLIC (revoke-from-anon alone is a no-op).

### Track B — decompose, leaves-first
CORRECTED PROJECT MAP (2 projects in operator's account + 1 partner):
- **orchestrator** = tscuymavysscrvoberrr (Sydney) — THE MONOLITH: substrate + non-ihsanos verticals (school/qurban/HR/accounting/scholar/mizan/Zahidah). Open-RLS problem lives here. Anon key NOT published (latent).
- **ihsanos** = ceayjeamtmcyzzvqflus (Singapore) — ALREADY its own project (ARCH-017). RLS enabled on all 90 tables (structurally OK; policy-quality audit still owed). Anon key IS public (NEXT_PUBLIC).
- **irsyad silo** = under partner account sales@gazzabyte.sg — STAYS THERE (Gazzabyte got us irsyad as client; their account/DPA boundary). Fleet consumes, does not own/migrate.

So ihsanos + irsyad are already separated. Remaining decomposition targets = the non-ihsanos verticals still in the monolith.

Extraction order (retargeted; ihsanos already done):
| Vertical | Risk | Approach | Status |
|---|---|---|---|
| ~~ihsanos~~ | — | already own Singapore project | DONE (pre-existing) |
| ~~irsyad~~ | — | stays under Gazzabyte partner account | DECIDED — leave |
| **Zahidah** (mamadah_*) | HIGH (private notes, Sydney) | isolate → own DB; ZAHIDAH-DATA-ISOLATION-001 operator-reserved | pending operator button |
| school (sch_*) | student PII but PILOT-SCALE (17 students, org-scoped, 6 orgs) | **org-RLS IN PLACE** (has org_id). Separate DB is optional PDPA-residency nicety — NOT worth a migration at this scale. Operator questioned the DB proposal 2026-07-01; recalibrated. | RLS in place |
| qurban/HR/accounting/scholar corpus | varies, mostly org-scoped | org-RLS in place; extract only if a real driver appears | RLS in place |

DECISION (2026-07-01): default = lock verticals down with org-scoped RLS IN PLACE, not per-vertical DB extraction. DB extraction is reserved for genuine drivers (privacy class = Zahidah; or real scale/residency need), not applied reflexively by table-prefix.
| **substrate** (agent_*/jobs/repo_*/operator_messages/...) | the hub | stays put; monolith becomes pure substrate DB as verticals leave | Track A lockdown dry-run-PROVEN, ready to apply |

REGION ISSUE: monolith is Sydney (ap-southeast-2) but holds PII (student, Zahidah private). PDPA-sensitive data belongs in Singapore (where ihsanos already is). Sensitive-vertical extraction doubles as the region fix.

## Blockers / needs from operator
1. **Supabase Management API token** (`SUPABASE_ACCESS_TOKEN` in .env) — lets cc-orch create/provision projects programmatically. OR operator creates the ihsanos project + sends connection string + service_role + anon keys.
2. Region for client DBs: Singapore (ap-southeast-1) proposed.
3. ihsanos: fresh-schema (recommended, no users) vs data-migration — confirm.

## Classification snapshot (heuristic, needs cai/operator validation)
- SUB ~50, CLIENT ~50, UNCLASSIFIED 63 (incl. scholar corpus, mizan_*, gl_/inv_/donations, orgs/persons, cto_council [SUB], review_dimensions [SUB], agents [SUB], wingmen_config/features [SUB]). Full per-table map: TODO before any vertical extraction.

## Way forward (2026-07-02) — proposed for cai consensus + operator button

**Blocker 1 is CLEARED:** `SUPABASE_ACCESS_TOKEN` is present in `.env` (verified 2026-07-02) — programmatic project provisioning via the Management API is available now.

**Proposal: ONE new Singapore project — `wingmen-personal` (ap-southeast-1)** for the privacy-class data only, per the ratified default (org-RLS in place for verticals; DB extraction reserved for genuine drivers — privacy class IS the genuine driver here):

1. **What moves in / gets created there:**
   - `mamadah_*` (Zahidah second-brain — the ZAHIDAH-DATA-ISOLATION-001 operator-reserved item; tiny: 2 tables, ~33+ rows, trivial pg_dump/restore)
   - `life_*` (operator life graph — **created there from day one**, never touches the Sydney monolith; pinned draft at `migrations/drafts/life_graph_p1.sql` + isolation proof-test at `tests/test_life_graph_isolation.py`)
2. **What stays:** verticals stay in the monolith under org-RLS (recalibrated 2026-07-01 decision stands); monolith continues trending toward pure-substrate DB. ihsanos already SG; irsyad stays Gazzabyte.
3. **Region logic:** personal/PDPA-sensitive → Singapore. The monolith (Sydney) retains substrate + org-scoped vertical data; the worst PII class leaves it, which also shrinks the residency problem flagged in the audit.
4. **Migration mechanics (mamadah):** provision project via Mgmt API → apply schema (012-equivalent + life_graph P1) → pg_dump/restore mamadah rows → repoint the mamadah/nutri responders' DSN (one env var each) → verify (row counts + a live Zahidah round-trip) → keep monolith copy frozen 7 days as rollback → backup + drop.
5. **New secrets:** `PERSONAL_DATABASE_URL` (+ service key) in `.env`; Mac Studio BGE-M3 embedding endpoint unchanged (Tailscale, stays up).
6. **Classification stragglers** (not blocking the personal-DB step): orgs/persons, donations (HELD), ui_events, scholar corpus (224k rows, scholarly content — likely stays substrate-side). Full per-table map still owed before any FURTHER extraction.

**Consensus gates:** (a) cai ruling on this way-forward + the life_graph P1 DDL (§6.6); (b) operator button on ZAHIDAH-DATA-ISOLATION-001 + project creation. Operator has signalled: on consensus, he restarts cc-orch in dangerous mode for autonomous execution.

## Log
- 2026-07-01: audit landed; operator chose split; ihsanos-first (no users); cai context folded in; awaiting mgmt token.
- 2026-07-01: Track A APPLIED (migration 013 — 50 substrate tables locked, verified, operator-authorized). cai ruled CAI-RESP-356 (findings valid; anon-key gate relayed to cc-ihsanos #5017/cc-storefront #5018; apply operator-button-gated; finish 010/011 not fresh; ephemeral-non-prod dry-run mandated).
- 2026-07-01: CLEANUP — dropped 51 test/seed vertical tables (sch_/qbn_/hr_/pos_/gl_/inv_/fee_/bot_) after operator confirm; pg_dump backup at backups/monolith_cleanup_backup_20260701T155736Z.sql (reversible). Monolith 163→112 tables. KEPT: scholar corpus (224k), mizan, Zahidah mamadah_, orgs/persons, substrate. HELD: donations (money?), ui_events (telemetry).
- School-DB idea retired (pilot-scale 17 students; org-RLS in place, not a migration). Default = org-RLS in place, not per-vertical DB extraction.
