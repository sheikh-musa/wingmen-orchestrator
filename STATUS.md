# Wingmen Orchestrator STATUS

Last Updated: 2026-03-30 05:09 SGT
Phase: ready (pending .env secrets)
Build Status: yellow

## Active Jobs
- none

## Completed (Last 5)
- [green] Job #2: ihsandms — Repository: ihsandms
Task: Add Qurban WhatsApp update timeline

ADMIN SIDE — /admin/qurban page:
- Add "Send Update" button per booking row in the table
- Opens a modal with:
  - Booking details at top (donor name, animal type, share count)
  - Dropdown of milestone steps: "Animal Confirmed" → "Animal Purchased" → "Slaughter In Progress" → "Meat Processing" → "Ready for Collection" → "Delivery Dispatched" → "Completed"
  - Live WhatsApp message preview below dropdown, interpolating donor name and booking ref. Style it like a WhatsApp chat bubble (green, rounded, timestamp)
  - "Send Update" confirm button
- On confirm: log timestamped milestone to that booking's record in mock data, show success toast "Update sent via WhatsApp"

DONOR SIDE — /donor/qurban page:
- For each booking, show a vertical stepper timeline below the booking card
- Completed steps: green checkmark + timestamp
- Current/active step: pulsing green dot
- Pending steps: gray circle
- Each step is expandable — shows the WhatsApp message text that was sent
- Pre-populate 2-3 milestones on existing mock bookings in lib/data.ts so timeline is never empty on demo

DATA: Add `milestones: { step: string, timestamp: string, message: string }[]` to qurban booking type in lib/types.ts and seed 2-3 entries on existing bookings in lib/data.ts (8m 18s, deploy: https://ihsandms-iz2erp406-musaaaaaaas-projects.vercel.app)

##  Failed / Blocked
- none

## Session 0 Checklist
- [x] Directory structure created
- [x] Python venv + dependencies installed
- [x] .env template written
- [x] REPOS.json written (sheikh-musa)
- [x] schema.sql written
- [x] LaunchAgent written (orch + cto_bot)
- [x] cloudflared installed

## Session 1 Checklist
- [x] wingmen_orch.py — main async worker loop
- [x] context_loader.py — per-repo context loader
- [x] spec_generator.py — Claude-powered prompt builder
- [x] ralph_runner.py — Claude CLI subprocess runner
- [x] deploy_manager.py — Vercel deployment trigger
- [x] status_reporter.py — STATUS.md updater + Telegram notifier
- [x] cto_bot.py — Telegram bot (long-polling, no tunnel needed)
- [x] Tests: 13/13 passing
- [x] Plugins: context7, superpowers installed
- [x] Supabase schema applied (4 tables: jobs, build_log, repo_memory, clients)
- [x] Repos cloned: ihsandms, hifz-companion, dookana, cosem-video-pipeline, dawah-pipeline
- [x] New repos created on GitHub: dookana, cosem-video-pipeline, dawah-pipeline

## Pending (5 secrets only Musa can provide)
- [ ] ANTHROPIC_API_KEY — console.anthropic.com → API Keys
- [ ] SUPABASE_SERVICE_KEY — Supabase dashboard → Settings → API → service_role
- [ ] TELEGRAM_BOT_TOKEN — existing CTO bot token
- [ ] MUSA_TELEGRAM_ID — message @userinfobot on Telegram
- [ ] VERCEL_TOKEN — vercel.com/account/tokens → Create

## To Go Live
1. Fill the 5 secrets above in ~/wingmen/orchestrator/.env
2. launchctl load ~/Library/LaunchAgents/dev.wingmen.orchestrator.plist
3. launchctl load ~/Library/LaunchAgents/dev.wingmen.ctobot.plist
4. Send /start to the Telegram bot