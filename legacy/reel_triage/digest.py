from __future__ import annotations

from reel_triage import config


def top_actions(conn, n: int = config.DIGEST_TOP_N) -> list[dict]:
    return conn.cursor().execute(
        "select id, shortcode, action, priority from reel_inbox "
        "where status = 'triaged' order by priority desc nulls last, ingested_at limit %s",
        (n,)).fetchall()


def _applying_rows(conn) -> list[dict]:
    return conn.cursor().execute(
        "select id, shortcode, action from reel_inbox where status = 'applying' "
        "order by ingested_at").fetchall()


def apply(conn, shortcode: str) -> tuple[bool, list[dict]]:
    """Move a triaged reel to 'applying' iff under WIP cap. At cap: no change,
    return the current applying rows so the bot can offer Done/Discard."""
    current = _applying_rows(conn)
    if len(current) >= config.WIP_CAP:
        return False, current
    conn.cursor().execute(
        "update reel_inbox set status='applying' where shortcode=%s and status='triaged'",
        (shortcode,))
    return True, _applying_rows(conn)


def discard(conn, shortcode: str) -> None:
    conn.cursor().execute(
        "update reel_inbox set status='discarded' where shortcode=%s "
        "and status in ('triaged','applying')", (shortcode,))


def mark_done(conn, shortcode: str) -> None:
    conn.cursor().execute(
        "update reel_inbox set status='done' where shortcode=%s and status='applying'",
        (shortcode,))


def mark_shown(conn, shortcodes: list[str]) -> None:
    """Increment digest counter; auto-discard once a still-triaged row has been
    shown AUTO_DISCARD_AFTER_DIGESTS times without being applied/discarded."""
    cur = conn.cursor()
    cur.execute("update reel_inbox set digests_shown = digests_shown + 1 "
                "where shortcode = any(%s) and status = 'triaged'", (shortcodes,))
    cur.execute("update reel_inbox set status='discarded' "
                "where status='triaged' and digests_shown >= %s",
                (config.AUTO_DISCARD_AFTER_DIGESTS,))
