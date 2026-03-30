# Wingmen Orchestrator STATUS

Last Updated: 2026-03-30 13:18 SGT
Phase: production
Build Status: green

## System Architecture

```
Telegram (Musa + Clients)
    ↓
cto_bot.py (long-polling, multi-client, voice support)
    ↓ 3-tier action system:
    ├── DATA: instant Supabase ops (prices, inventory)
    ├── CONFIG: provisioning (new storefront, DNS)
    └── BUILD: full code pipeline ↓
                                    ↓
Supabase jobs table (with client_id)
    ↓
wingmen_orch.py (polls every 30s, parallel per-repo, sequential within)
    ↓
context_loader.py → CLAUDE.md + STATUS.md + git log + repo_memory
spec_generator.py → Claude Sonnet 4.6 generates build spec
ralph_runner.py   → Claude Code CLI (--dangerously-skip-permissions)
    ↓ on success:
git commit + push → deploy_manager.py (Vercel API) → status_reporter.py
    ↓
Telegram notification to admin + client
```

## Active Jobs
- #3 ihsandms [running] — Tabung barcode scan + parent WhatsApp notifications
- #4 ihsandms [queued after #3] — Donor invite link + public /donate page

## Completed
- #2 ihsandms — Qurban WhatsApp timeline (8m 18s, deployed)
- #1 dookana — Order form verification (already done)

## Core Files (~/wingmen/orchestrator/)
| File | Purpose |
|------|---------|
| wingmen_orch.py | Main async loop — parallel per-repo, CAS job picking, stale recovery |
| cto_bot.py | Multi-client Telegram bot — admin/client modes, voice, 3-tier actions |
| context_loader.py | Loads CLAUDE.md, STATUS.md, git log, file tree, repo_memory |
| spec_generator.py | Claude Sonnet 4.6 → structured build spec from job + context |
| ralph_runner.py | Shells to `claude` CLI, logs to Supabase, redacts secrets |
| deploy_manager.py | Vercel API deploy + polling |
| status_reporter.py | Updates STATUS.md, sends Telegram notifications to admin + client |

## Supabase Tables (project: tscuymavysscrvoberrr)
| Table | Purpose |
|-------|---------|
| jobs | Build queue (id, repo_name, description, status, priority, client_id, fail_count) |
| build_log | Per-job execution log (phase, message, level) |
| repo_memory | Persistent key-value context per repo |
| clients | Registered clients (name, telegram_chat_id, plan, active) |
| client_repos | Links clients to their repos |
| chat_history | Persisted conversation history per chat_id |
| usage_log | Token usage, build duration, action counts per client |
| audit_log | Admin action audit trail |

## GitHub Repos (all under sheikh-musa)
| Repo | Priority | Status | Stack |
|------|----------|--------|-------|
| ihsandms | 1 | active | Next.js + Supabase (live: ihsandms.vercel.app) |
| dookana (was bayt) | 2 | active | Next.js + Python backend + Supabase |
| hifz-companion (hifz) | 3 | active | PWA + Supabase |
| cosem-video-pipeline | 4 | specced | Python + WaveSpeedAI + ffmpeg |
| dawah-pipeline | 5 | specced | Python + Claude + MagiHuman |

## Bot Features
- **Admin**: Technical brainstorm, /build, /addclient, /linkrepo, /clients, /usage, /pause, /cancel, /priority
- **Client**: Conversational AI assistant — no slash commands needed, confirms before acting
- **Voice**: Send voice notes, bot transcribes via Claude audio input, processes as chat
- **3 Action Tiers**: DATA (instant DB ops), CONFIG (provisioning), BUILD (code changes)
- **Multi-action**: Can queue multiple builds from one confirmation
- **Context-aware**: Loads real codebase (CLAUDE.md, STATUS.md, git log, file tree) into brainstorm
- **Persistent**: Chat history survives restarts (Supabase-backed)

## Orchestrator Features
- Parallel execution across repos (max 3 concurrent, configurable)
- Sequential within same repo (prevents git conflicts)
- Atomic job picking with CAS pattern
- Stale job recovery (>2hr running → auto-requeue)
- Git pull before build, git push + Vercel deploy after
- Secret redaction in all logs
- Whitelisted env vars for Claude CLI subprocess
- Progress notifications to admin + client at each pipeline stage
- Usage metering (tokens, duration per client)
- Audit logging for all admin actions

## Infrastructure
- Mac Mini (always-on, Singapore)
- LaunchAgents: dev.wingmen.orchestrator, dev.wingmen.ctobot
- GitHub: sheikh-musa (all repos)
- Supabase: tscuymavysscrvoberrr (shared project)
- Vercel: team_fgnTFpfA3HElR8jK4vSQ5HYo
- Domain: wingmen.dev (SiteGround NS, Cloudflare migration pending)

## Hard Constraints
- async Python only, no blocking calls
- All secrets via .env, never hardcoded
- RLS enabled on every Supabase table
- BIGINT GENERATED ALWAYS AS IDENTITY on all PKs
- RTL-first CSS with logical properties (for Arabic content)
- 150KB page weight max ("Yemen/Sumatra Rule")
- Max 3 consecutive failures before pausing + alerting
- Never delete repos or Supabase tables without admin confirmation
- Arabic text must be RTL, diacritics-correct
- No riba, zakat-transparent, Islamic economic constraints

## Completed (Last 5)
- [green] Job #7: ihsandms — Redirect old donor/parent routes to unified /my portal:

- `app/donor/page.tsx` → redirect to `/my/dashboard`
- `app/donor/donations/page.tsx` → redirect to `/my/donations`
- `app/donor/qurban/page.tsx` → redirect to `/my/qurban`
- `app/donor/profile/page.tsx` → redirect to `/my/profile`
- `app/parent/page.tsx` → redirect to `/my/dashboard`
- `app/parent/tabung/page.tsx` → redirect to `/my/tabung`
- `app/parent/profile/page.tsx` → redirect to `/my/profile`

Use Next.js `redirect()` from `next/navigation` in each page.tsx (server-side redirect). Keep the old layout files for now but they should be unused after redirects.

Update any internal links in admin pages that point to `/donor/*` or `/parent/*` to use `/my/*` instead. Update `app/page.tsx` landing page if it links to donor/parent portals — point to `/my/dashboard`.

Test: every old URL should 302 to the new /my equivalent. No dead links. (3m 32s, deploy: https://ihsandms-92ve36t7x-musaaaaaaas-projects.vercel.app)
- [green] Job #6: ihsandms — Create unified portal at `/my` merging donor + parent portals:
**Layout — `app/my/layout.tsx`:**
- Mobile-first bottom tab bar (same pattern as current donor portal)
- Tabs: Home, Donations, Tabung, Qurban, Profile (Lucide icons)
