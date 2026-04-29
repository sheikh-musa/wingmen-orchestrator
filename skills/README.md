# skills/

Canonical directives consumed via transclusion by all CC families (cc-cosem, cc-ihsanos, cc-orchestrator, cc-scholar, future).

## Authorship + ownership

cc-orchestrator owns this directory per CAI-AGENTS-002. Other CC families consume directives by reference — they do NOT add files here directly. Cross-family additions route via cc-orchestrator (file an agent_messages question or a strategic_decisions row that names the new directive).

## Transclusion model

CC family CLAUDE.md files reference directives like:

> Apply policy per `skills/bypass-approval-policy.md`.

The directive content is the source of truth. CLAUDE.md files paraphrase only when transclusion would obscure (e.g., domain-specific examples).

## Current contents

- `bypass-approval-policy.md` — CAI-PIPELINE-BYPASS-001 procedure for operator-authorized bypasses.
- `inbox-check-protocol.md` — Fix 4 (ORCHESTRATOR-NOTIFIER-FIX-001-AMEND) inbox-status freshness discipline; points at canonical text in `docs/governance/inbox-check-directive.md`.
- `inbox-monitor-pattern.md` — CAI-PROCESS-INBOX-CADENCE-001 Section B Architecture A (optional in-session Monitor for sub-cadence reactivity to incoming agent_messages).
- `scheduled-sweep-prompt.md` — CAI-PROCESS-INBOX-CADENCE-001 Section E Phase 3 bounded prompt for non-interactive scheduled CC sessions; applies Section A semantics + Section D guardrails to own family inbox.
- `scheduled-sweep-registration.md` — per-family runbook for registering with the launchctl-based scheduled-sweep substrate.

Future skills slot in as separate `<name>.md` files. Naming convention: `<verb-or-noun>-<scope>.md` (e.g., `tafsir-defense-funnel.md`).

## Two-tier pattern

This directory holds short SKILL.md trigger files (when-to-invoke + brief description + reference). The substantive procedure text lives in `docs/governance/<name>-directive.md`. Reasons:

- SKILL.md: optimized for LLM auto-invoke pattern matching (terse, "when to invoke" upfront)
- governance directive: optimized for human + LLM full-procedure consumption (long-form, examples, audit context)

A skill points at exactly one directive. Both are versioned via this repo's git history.

## Cross-CC-family domain skills (CAI-RESP-073)

10 critical/non-critical domain skills are AUTHORED by domain CCs in their own repos under `.claude/skills/<skill-name>/`:

- cc-ihsanos: `rls-migration`, `ledger-posting`, `ihsan-finance`, `qurban-compliance`, `islamic-design`, `vocabulary` (in ihsanos repo)
- cc-scholar: `quranic-text-integrity`, `tafsir-defense-funnel`, `hifz-fsrs-invariants`, `4-tier-transparency` (in ai-scholar + hifz-companion repos)

When the submodule mount infrastructure lands per CAI-RESP-073 GAP 3, those skills' content gets transcluded INTO this directory at build/sync time. Until then, this directory holds the cross-family process skills that cc-orchestrator authors directly (bypass-approval, inbox-check, etc.).
