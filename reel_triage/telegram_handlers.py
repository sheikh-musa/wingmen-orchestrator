from __future__ import annotations

import os

from reel_triage import config, digest, dyi, ingest, links

MUSA_TG_ID = int(os.environ.get("MUSA_TELEGRAM_ID", "0"))


def _conn():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(config.reel_inbox_dsn(), row_factory=dict_row, autocommit=True)


def _is_musa(update) -> bool:
    u = getattr(update, "effective_user", None)
    return bool(u) and u.id == MUSA_TG_ID


async def handle_message(update, ctx) -> bool:
    """Returns True iff this was a reel message we ingested. The bot wiring uses
    that to stop propagation (ApplicationHandlerStop) so non-reel text/docs still
    fall through to the normal chat handlers."""
    if not config.reel_triage_enabled() or not _is_musa(update):
        return False                             # silent ignore (identity doctrine)
    msg = update.message
    conn = _conn()
    # ZIP path (DYI export)
    if getattr(msg, "document", None) and str(msg.document.file_name).endswith(".zip"):
        file = await ctx.bot.get_file(msg.document.file_id)
        data = bytes(await file.download_as_bytearray())
        counts = ingest.ingest_items(conn, dyi.parse(data))
    else:                                        # link path
        found = [{"shortcode": links.shortcode(u), "url": u, "source": "share_link"}
                 for u in links.find_links(msg.text or "")]
        found = [f for f in found if f["shortcode"]]
        if not found:
            return False
        counts = ingest.ingest_items(conn, found)
    await msg.reply_text(
        f"Reel triage: applied {counts['applied']}, skipped {counts['skipped']}, "
        f"failed {counts['failed']}.")
    return True


async def handle_callback(update, ctx):
    """Routes apply:<code> / discard:<code> / done:<code> from digest buttons."""
    if not _is_musa(update):
        return
    q = update.callback_query
    action, _, code = q.data.partition(":")
    conn = _conn()
    if action == "apply":
        ok, current = digest.apply(conn, code)
        if ok:
            await q.answer("Applied — added to your 3 in-progress.")
        else:
            lines = "\n".join(f"- {r['action']}" for r in current)
            await q.answer("At WIP cap (3).", show_alert=True)
            await q.message.reply_text("You already have 3 in progress:\n" + lines)
    elif action == "discard":
        digest.discard(conn, code)
        await q.answer("Discarded.")
    elif action == "done":
        digest.mark_done(conn, code)
        await q.answer("Marked done.")
