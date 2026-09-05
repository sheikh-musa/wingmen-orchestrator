#!/usr/bin/env python3
"""Friday reel-triage digest sender (CAI-RESP-216).

Composes the week's top-5 triaged actions, sends them to Musa with
Apply/Discard inline buttons, then marks them shown (which auto-discards rows
seen AUTO_DISCARD_AFTER_DIGESTS times). Fail-closed: does nothing unless the
reel_triage flag is on and both bot-token + Musa-id are set.

Scheduling: invoked Friday 09:00 SGT. Mechanism is documented in STATUS.md —
either the orchestrator scheduler or a launchd StartCalendarInterval.
"""
from __future__ import annotations

import asyncio
import os

from reel_triage import config, db, digest, digest_send


async def main() -> int:
    if not config.reel_triage_enabled():
        print("reel-digest SKIP (WINGMEN_REEL_TRIAGE_ENABLED not set)")
        return 0
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    musa_id = os.environ.get("MUSA_TELEGRAM_ID", "")
    if not token or not musa_id:
        print("reel-digest SKIP (TELEGRAM_BOT_TOKEN / MUSA_TELEGRAM_ID unset)")
        return 0

    with db.connect() as conn:
        text, keyboard, shown = digest_send.compose(conn)
        if not shown:
            print("reel-digest: nothing triaged this week; nothing sent")
            return 0

        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(label, callback_data=data) for label, data in row]
            for row in keyboard])
        bot = Bot(token=token)
        await bot.send_message(chat_id=musa_id, text=text, reply_markup=markup)
        digest.mark_shown(conn, shown)
        print(f"reel-digest: sent {len(shown)} action(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
