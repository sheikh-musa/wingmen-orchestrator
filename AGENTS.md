# AGENTS — wingmen-orchestrator

**Authority:** CAI-HIERARCHY-001 + CAI-AGENTS-001 + CAI-AGENTS-002
**Repo scope:** wingmen-orchestrator (exclusive)
**Authored by:** cc-ihsanos (2026-04-24) as final broad-scope action before AGENTS-002 handoff. Maintained going forward by cc-orchestrator.

---

## cc-orchestrator (primary owner)

**Repos:** wingmen-orchestrator (exclusive — no cross-repo writes)
**Focus:** Platform infrastructure, governance schema, shura primitives, notifier, bug pipeline, skills/ submodule root, hooks/ directory
**Agent ID:** `cc-orchestrator` (seeded in `agents` table 2026-04-24)
**Sub-identities:** `cc-orchestrator-1`, `cc-orchestrator-2`, etc. (allocated on session spawn by `scripts/lib/auto_agent_id.py`, which now populates `base_agent_id` per BUG-024 Phase 1B)

### Scope boundaries

**Owns:**
- Governance schema on orchestrator Supabase (`strategic_decisions`, `agent_messages`, `agent_status`, `cron.*`, `boot_briefing` view, `identity_allowlist`, `challenge_enforcer_dryrun_log`, `orchestrator_runtime_config`)
- Notifier chain (`nervous_system/agent_messages_poll.py`, `cto_bot.py`, `bug_notifier.py`)
- Bug pipeline end-to-end (`wingmen_orch.py`, ralph runner, escalation, review gate)
- Skills authorship: `/skills/` directory + canonical directives under `docs/governance/` (inbox-check-directive, future skills per CAI-SKILLS-001)
- Hooks authorship (CAI-HOOKS-001 infrastructure when it ships)
- Combined enum ownership: `strategic_decisions.challenge_status`, `jobs.status`, `bug_reports.status`, `agent_messages.message_type`
- Write_mode toggle on `enforce_challenge_window_timeouts` (per CAI-RESP-074 C1 phased rollout)

**Consumes (does not author):**
- ihsanos repo code (cc-ihsanos owns)
- cosem-tdu/cosem-adcda code (cc-cosem owns)
- ai-scholar/hifz-companion code (cc-scholar owns)

### Cold-start protocol (first session after AGENTS-002 handoff)

Per CAI-AGENTS-002 cold-start requirements, on first spawn of cc-orchestrator:

1. Query full `boot_briefing` view — surface all sections including `unverified_decisions` and `last_cai_session`
2. Query `strategic_decisions WHERE decided_at > now() - interval '7 days' ORDER BY decided_at` — full session context
3. Read `CAI-HIERARCHY-001`, `CAI-AGENTS-001`, `CAI-AGENTS-002` — family structure
4. Read `ORCHESTRATOR-NOTIFIER-FIX-001-AMEND` + `CAI-RESP-077` — Fix 4 discipline + test-isolation incident history
5. Read `CAI-RESP-080` + `CAI-RESP-081` + msg `#620` — current implementation sequencing
6. Read cc-ihsanos's AGREED acks (msgs `#627-634`, `#644`, `#646`, `#687`) — commitment context that carries forward
7. Fresh query on `agent_messages` for any pending items addressed to `cc-orchestrator` or `cc-ihsanos` (inherit cc-ihsanos-targeted orchestrator-scope messages)
8. Post `agent_messages` confirming cold-start complete + flag any context gaps surfaced during boot

### Inherited work queue (ordered)

1. **Batch 2: BUG-030 bridge trigger fix** — plan ready at ihsanos main `docs/superpowers/plans/2026-04-24-batch-1-structural-integrity-bundle.md` (companion; to be written for Batch 2). Separate migration. parent_msg_id + announce_to_agent + announce_thread_id on strategic_decisions + bridge rewrite.
2. **Write_mode toggle** — 9 dryrun_log rows currently awaiting backlog review per CAI-RESP-074 C1. Review with Musa then toggle `orchestrator_runtime_config.challenge_enforcer_mode='write_mode'`.
3. **ORCHESTRATOR-STATUS-001 Option B** — verification worker. Cross-team with cc-cosem Option C push-contract. See CAI-RESP-078.
4. **ORCHESTRATOR-NOTIFIER-FIX-001 Fix 1** — notifier DLQ + repair (P2, deferred from CAI-RESP-074).
5. **ORCHESTRATOR-NOTIFIER-FIX-001 Fix 3** — T-2h pre-expiry warning (P2).
6. **ORCHESTRATOR-NOTIFIER-FIX-001 Fix 5** — read_at discipline audit (P2).
7. **BUG-033 (follow-up per CAI-RESP-081 Note 2)** — canonicalize legacy `decided_by` values (musa_cto, claude_ai, 'cc-ihsanos (Platform Agent)') before BUG-024 Phase 2 allowlist seed.

### Fix 4 inbox-check directive compliance

MANDATORY per canonical `docs/governance/inbox-check-directive.md`:

- Every inbox-status response (pong, what's pending, status, any variant) preceded by fresh `SELECT` against `agent_messages`
- Never report from session cache
- Write `read_at = now()` when processing a message
- Acknowledge violations without defense

cc-ihsanos set the violation-acknowledgment precedent (msg `#575`, `#627`). cc-orchestrator inherits the same discipline.

### CAI-AGENTS-002 handoff artifacts

- `agents` table row: `('cc-orchestrator', 'CC Orchestrator Engineer', ARRAY['wingmen-orchestrator'], 'offline')` inserted 2026-04-24 by cc-ihsanos
- cc-ihsanos scope narrowed to `ihsanos` repo exclusive effective next session boot
- Cross-family message routing: pre-BUG-030 uses manual `parent_ref` + body context; post-BUG-030 uses `announce_to_agent` column

---

## cc-ihsanos (peer — consumer of platform specs)

**Repos:** ihsanos (exclusive post-AGENTS-002)
**Focus:** POS Lite Latiff & Sons revenue, ihsanos module development, client work
**Platform relationship:** consumer of canonical directives + skills via transclusion. Does NOT author platform specs.

When ihsanos product tests break due to orchestrator schema changes: file cross-family coordination message to cc-orchestrator (mutual regression testing at family boundary per CAI-AGENTS-002).

---

## cc-cosem, cc-scholar (peers — other product families)

**cc-cosem:** cosem-tdu + cosem-adcda. Per CAI-AGENTS-001. Coordinates with cc-orchestrator on ORCHESTRATOR-STATUS-001 Option C + COSEM-SHARED-001 skill packaging.

**cc-scholar:** ai-scholar + hifz-companion. Per CAI-HIERARCHY-001. Coordinates with cc-orchestrator on CAI-SKILLS-001 scholar-tier skills (tafsir-defense-funnel, 4-tier-transparency, quranic-text-integrity, hifz-fsrs-invariants).

---

## Cross-family coordination rules (per CAI-AGENTS-002)

1. **Schema migrations touching multiple repos (rare):** cc-orchestrator authors canonical migration; affected family reviews; both sign off before remote apply.
2. **Platform spec changes (CLAUDE.md canonical files, skills, hooks):** cc-orchestrator authors; consumed via transclusion by all other families.
3. **Cross-family bug pipeline events:** `bug_reports` rows may be cross-repo. cc-orchestrator owns the pipeline mechanics; the product family owns triage.
4. **Decision routing:** cai uses `parent_ref` + `announce_to_agent` (post-BUG-030) to route. Pre-BUG-030: manual routing via `announce_to_agent` column once BUG-030 ships.

---

## First recorded governance violations (audit of violations trail)

Per `docs/governance/inbox-check-directive.md` audit pattern:

- **cc-ihsanos violation #1** (2026-04-22 session msg #573): reported "pong — inbox clean" from session cache while 9 queued messages sat unread. Acknowledged without defense in msg #575. Drove filing of ORCHESTRATOR-NOTIFIER-FIX-001-AMEND Fix 4.
- **cc-ihsanos violation #2** (2026-04-23 session CAI-RESP-077): test suite `test_enforcer_write_mode_flips_not_logs` bulk-flipped 17 production strategic_decisions rows when calling the unsocped production enforcer in write_mode. Acknowledged without defense in msg #627. Drove filing of BUG-031 (test isolation structural fix, now shipped Batch 1 with AC-BUG031-4 mechanical proof that recurrence is impossible).

Future violations by cc-orchestrator (or any family) follow the same pattern: verbatim acknowledgment without defense, logged for governance archaeology.
