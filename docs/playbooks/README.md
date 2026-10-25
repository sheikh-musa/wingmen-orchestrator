# Build-rigor playbooks — index

**What this is:** the canonical, versioned procedures for recurring high-risk
build ops, each PAIRED with an enforcing hook. A building lane loads this index
at boot. The rule of the track: **a doc-only playbook is a failure** — the doc
carries the *why* + the residual human steps; the **hook** is what survives a
reset and holds the line by default.

**Owner:** cc-quality (build-rigor track, op#32734/32735). Parallel track:
cc-substrate owns coherence-SSOT (#73169). Both gate through orch-console.

> **STATUS: APPROVED (orch-console #73186/#73191) — canonical set building.**
> Approach + 6-set + RED-first + CI-lint classifications all gated. Canonical home
> is here (`docs/playbooks/`). Enforcement (the to-build lints + discovery wiring)
> ships as separate reviewed PRs to orch-console's gate — see the status column.

## Playbooks
| id | title | risk | enforcement | status |
|----|-------|------|-------------|--------|
| [PB-01](01-playbook-migration-ddl.md) | Migration / DDL apply to prod | P0 | `secrets_transcript_guard`→`ddl_detect` **🟢 existing** (gate-required); +1 mechanical precheck candidate | **ready** |
| [PB-02](02-playbook-client-data-path.md) | Client-data / residency path | P0 | `client_media_read_guard` **🟢**, `lane_db_residency_guard` (detect-only nudge); hard residency block **cai-declined** (CAI-RESP-1308) | **ready** |
| [PB-03](03-playbook-rls-grants.md) | RLS / grants / lifecycle-column change | P0 | `rls_grant_lint.py` **🟢 existing** (live anon-write/TRUNCATE net) + `migration_rls_grant_lint.py` **CI-lint (WARN), TO-BUILD** (the column-grant delta) — **first PR** | ready; delta-lint building |
| [PB-04](04-playbook-ship-to-live.md) | Ship-to-live completeness + test bar | P1 | **checklist / CI** (not a pre-tool hook); partial `deploy_safety_guard.sh` | ready |
| [PB-05](05-playbook-secrets-transcript.md) | Secrets & transcript hygiene | P1 | `secrets_transcript_guard` + `secrets_output_scanner` **🟢 existing** | ready |
| [PB-06](06-playbook-framework-footguns.md) | Framework footguns (use-server/SSR …) | P1 | **static lint** (the one clean hook-able RED row) — TO-BUILD | ready; lint planned |
| [PB-07](07-playbook-frontend-ux-completeness.md) | Frontend UX-completeness (operator-scale actions) | P1 | **review-recipe lens** (🔴 review-time) + `frontend_bulk_action_lint.py` **heuristic WARN-lint, TO-BUILD** | ready; lens live, lint planned |

> **PB-07 added post-approval** (op#33974 Musa IHSAN flag → orch-console #76194):
> a NEW class — frontend UX-completeness — not in the original substrate/data/security
> inventory. Review-time dimension (like PB-03's adequacy checks), landing as a
> standing review lens + a heuristic WARN-lint candidate, not a fail-closed hook.

See [00-INVENTORY.md](00-INVENTORY.md) for the full inventory + the mapping from
~60 scattered memories / CLAUDE.md rules / hooks into these canonical playbooks, and
[TEMPLATE.md](TEMPLATE.md) for the authoring contract.

## How a lane discovers a playbook (enforced, 3 layers — Layer A works everywhere)
1. **Layer A (load-bearing):** every enforcing hook's refuse `stderr` ends with
   `— see PB-NN` → a lane that attempts a high-risk op is handed the playbook at the
   moment of action. The fleet guards are in GLOBAL `~/.claude/settings.json`, so
   this fires in every session/repo (shipped by the discovery-wiring PR).
2. **Layer B:** `session_start_reconstitute.py` `_lane_identity_notice` injects a
   one-line pointer to this index at the boot of any orchestrator-worktree lane.
3. **Layer C:** a pointer in the ihsanos/cosem lane CLAUDE.md seed (those lanes don't
   boot in an orchestrator checkout, so Layers A+C cover them).

**Honest limit:** Layer A only teaches when a hook/lint fires, so it covers the
enforced ops at the point of action; the checklist playbook (PB-04) leans on B+C +
its CI assertions.

## Versioning
Each playbook carries `version` + `last-reviewed`. This table tracks the current
version so a lane can tell if a cached copy is stale. Bump on every change.
