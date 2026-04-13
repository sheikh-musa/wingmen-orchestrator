# wingmen-orchestrator STATUS

Last Updated: 2026-04-14 SGT
Build Status: green
Deploy: N/A

## Last Completed Job
- Job #16: Notification routing — CTO group + Ops group split with MUSA_TELEGRAM_ID fallback

## Result Summary
Created notification_router.py as single source of truth. Routed CTO notifications (builds, council, briefs, escalations, approvals) via CTO_GROUP_ID and ops alerts (watchdog) via OPS_GROUP_ID. Both fall back to MUSA_TELEGRAM_ID when env vars are unset.

## Previous Next Up
- Stripe integration for self-serve plan upgrades via Telegram
- Durable reminders (survive restarts)
- Client onboarding flow polish

## Completed (Last 5)
- [green] Job #16: wingmen-orchestrator — Notification routing: CTO group + Ops group split
- [green] Job #15: wingmen-orchestrator — [TASK-012] Client group template: per-client Telegram group with scoped bot commands (10m 16s, deploy: N/A)