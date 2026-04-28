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

Future skills slot in as separate `<name>.md` files. Naming convention: `<verb-or-noun>-<scope>.md` (e.g., `inbox-check-protocol.md`, `tafsir-defense-funnel.md`).
