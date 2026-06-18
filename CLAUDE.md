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
1. ihsanos (active, priority 1) — owns storefront backend + Telegram surface per IHSANOS-STOREFRONT-TG-001
2. hifz-companion (active, priority 3)
3. cosem-video-pipeline (specced, priority 4)
4. dawah-pipeline (specced, priority 5)
5. dookana (frozen-maintenance, priority 4) — sunset path per IHSANOS-STOREFRONT-TG-001; P0 + security only; storefront work moved to ihsanos repo

## Commands
- `/status` — orchestrator state overview
- `/deploy <repo>` — deploy repo to Vercel
- `/jobs` — query Supabase jobs table
- `/build-log [filter]` — show build logs
- `/repos` — check all repo states
- `/tunnel` — check tunnel status
- `/schema` — compare DB schema vs schema.sql

## Fleet lanes (tmux)

Start the other CC agents from this (cc-orchestrator) session via tmux. Both
launchers `unset ANTHROPIC_API_KEY` after sourcing `.env`, so every lane runs on
the Mac Mini's Claude **Max** subscription, not metered API billing.

- **Engineer lanes** (mirror, etc.): `scripts/lanes.sh ls` (status), `scripts/lanes.sh up [lane]` (boot down lanes, each in its own tmux session), `scripts/lanes.sh attach <lane>`. Idempotent: skips a lane that already has a `claude` running in its dir. Each lane runs `scripts/launch_dangerous_cc.sh` in a worktree.
- **cc-cai** (singleton strategic node, agent_id='cai' exactly): boot via `scripts/boot_cai.sh` under tmux — `tmux new-session -d -s cai -c ~/wingmen/wingmen-cai scripts/boot_cai.sh`. NOT lanes.sh-managed (fleet_lanes registry desired_state='down'); operator-booted. The live copy at `~/wingmen/wingmen-cai/boot_cai.sh` should stay in sync with `scripts/boot_cai.sh` (canonical, tracked).

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
- Restart orchestrator only via `scripts/restart_orch.sh` (launchctl). Never `nohup`.
- **Never run `supabase db push` against production** (project_ref `ceayjeamtmcyzzvqflus`). Per CC-SUBSTRATE-VIEW-INTEGRITY-001-FINDINGS (decision 962): the CLI's shadow-diff path re-applies historic `CREATE OR REPLACE VIEW` statements from migrations whose view bodies pre-date current arms, silently stripping later arms from `boot_briefing`. Use the orch's direct psycopg-apply pattern instead — see PR #41 / #42 / #44 migration apply scripts. The shadow path is for local dev only.
