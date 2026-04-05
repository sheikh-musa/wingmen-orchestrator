# STATUS — Wingmen Orchestrator (CTO Bot)

Last Updated: 2026-04-05
Phase: Production — unified platform bot
Status: deployed
Deploy URL: Telegram @ihsanosbot (Mac Mini always-on)
Health: green

## What This Is
Wingmen Bot is the single entry point for all clients. One bot, tiered access:
- Free: template site + chat support (10/day)
- Starter ($49): + custom domain, site audits
- Growth ($149): + payments, calendar, bug fixes, data updates
- Scale ($399): + custom builds, dedicated CTO support
- Admin (Musa): everything, persistent sessions, nervous system

ihsanOS is the platform. Wingmen Bot is the interface. Dookana is a module within ihsanOS.

## Completed
- Agent split: Router → Brainstorm / Auditor / Fixer
- Tier-gated capabilities (free/starter/growth/scale)
- Persona-aware routing (admin=technical, client=warm)
- Persistent Claude sessions for admin (30min timeout)
- BRAIN.md + todo list injected into every admin prompt
- Nervous System: brain_sync (4h), morning_brief (6AM), weekly_digest (Sun 9PM), memory_sync (midnight), session_compress (2AM)
- CTO Principles loaded into brainstorming
- Conversational todo list (natural language "remind me")
- Photo/voice analysis, fix verification screenshots
- File saving to ~/wingmen/files/
- Dookana Telegram bot deprecated — all merchants go through Wingmen Bot
- Morning brief + weekly digest message splitting (no truncation)
- 8 active repos scanned by nervous system

## Blocked
- TDU POC hasn't messaged bot — need chat_id (Monday meeting)

## Next Up
- Stripe integration for self-serve plan upgrades via Telegram
- Durable reminders (survive restarts)
- Client onboarding flow polish

## Revenue Signals
- TDU (Growth plan) — first Tier 1 client
- ihsanOS deployed with BAPA seed data — demo-ready
- Single bot model simplifies everything — one product to sell
