# Wingmen Orchestrator

Always-on Python orchestrator running on Mac Mini that manages builds, deploys, and jobs across multiple repos via Telegram commands.

## Stack
- Python 3.9 (venv at `.venv/`)
- Supabase (DB: jobs, build_log, repo_memory, clients)
- Vercel (frontend deploys)
- Telegram bot (command interface)
- cloudflared/ngrok (tunnel for webhooks)
- Claude Code (AI-powered build sessions)

## Key Files
- `wingmen_orch.py` — main orchestrator entry point
- `.env` — all secrets (never commit)
- `REPOS.json` — repo registry with paths, URLs, priorities
- `schema.sql` — Supabase table definitions
- `STATUS.md` — current orchestrator state
- `prompts/` — session prompt templates

## Repos Managed
1. ihsandms (active, priority 1)
2. dookana (active, priority 2)
3. hifz-companion (active, priority 3)
4. cosem-video-pipeline (specced, priority 4)
5. dawah-pipeline (specced, priority 5)

## Commands
- `/status` — orchestrator state overview
- `/deploy <repo>` — deploy repo to Vercel
- `/jobs` — query Supabase jobs table
- `/build-log [filter]` — show build logs
- `/repos` — check all repo states
- `/tunnel` — check tunnel status
- `/schema` — compare DB schema vs schema.sql

## Boot Sequence (three-tier memory)

Context is scarce. Load the index first; fetch full content only when you need it.

1. **Index (always loaded)**: `SELECT * FROM boot_briefing` — returns repo_context summaries, decision refs + titles (80 chars), open QA failures, latest session snippets. ~10KB total.
2. **Full decision (on demand)**: `SELECT * FROM get_decision('<ref>')` — returns full decision + reasoning for one row. Call this when actively implementing a specific decision.
3. **Full repo context (on demand)**: `SELECT * FROM get_repo_context('<repo>')` — returns recent_changes, known_debt, architecture_summary. Call when you need deep context on a repo.

**Rule**: Never SELECT * from `strategic_decisions` directly. Use the boot_briefing index + get_decision() on demand.

## Rules
- Never commit `.env`
- Always update `STATUS.md` after state changes
- Use Supabase MCP for DB queries when available
- Use Vercel MCP for deployment operations when available
- Log all build operations to the `build_log` table
- All audit deliverables (build specs, diffs, test results, deploy URLs) must be written to Supabase `work_outputs` table — repo files alone are not sufficient
