from __future__ import annotations

from legacy.reel_triage import digest


def compose(conn) -> tuple[str, list, list]:
    """Returns (text, inline_keyboard, shown_shortcodes). Keyboard is a list of
    button-rows: [[Apply(code), Discard(code)], ...]. Caller turns rows into the
    telegram InlineKeyboardMarkup and calls digest.mark_shown(shown) after send."""
    rows = digest.top_actions(conn)
    if not rows:
        return "No triaged reels this week.", [], []
    lines, keyboard, shown = ["This week's top actions:"], [], []
    for r in rows:
        lines.append(f"- {r['action']}")
        keyboard.append([("Apply", f"apply:{r['shortcode']}"),
                         ("Discard", f"discard:{r['shortcode']}")])
        shown.append(r["shortcode"])
    # needs-manual footer (rows that errored)
    errs = conn.cursor().execute(
        "select count(*) c from reel_inbox where error is not null and status='inbox'").fetchone()
    if errs and errs["c"]:
        lines.append(f"\n({errs['c']} reel(s) need manual fetch — see error column.)")
    return "\n".join(lines), keyboard, shown
