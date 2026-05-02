# wingmen-orchestrator

<!-- PIPELINE-TEST-001: pipeline marker 2026-04-17 -->

Always-on Python orchestrator running on Mac Mini that manages builds, deploys, and jobs across multiple repos via Telegram commands. See `CLAUDE.md` for the full stack overview, repo registry, and operating rules.

## Architectural discipline (read before adding new code)

If you're adding a new module that calls Claude programmatically, read **[`docs/governance/api-vs-cli-architecture.md`](docs/governance/api-vs-cli-architecture.md)** first.

Short version: default to spawning `claude -p` via subprocess (or `ai_provider.call_ai`, which routes through CLI by default). The Anthropic SDK (`anthropic.Anthropic(api_key=...)`) is reserved for 5 specific carve-outs — using it outside those carve-outs burns auto-recharge credits when prepaid Max-plan compute is sitting idle.

`nervous_system/orch_self_audit.py` Audit 5 enforces this at runtime; violations fire P2 Telegram alerts.

Other governance directives live alongside it in `docs/governance/`. Skill-style trigger pointers live in `skills/` per the SKILLS-SUBSTRATE-001 two-tier pattern.
