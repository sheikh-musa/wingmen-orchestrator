from __future__ import annotations


def ingest_items(conn, items: list[dict]) -> dict:
    """Insert new reels; skip shortcodes already present (in-batch or in-DB).

    Returns {applied, skipped, failed}. `on conflict (shortcode) do nothing`
    makes the insert idempotent against the unique constraint. Requires an
    autocommit connection so a failed row does not poison the next.
    """
    applied = skipped = failed = 0
    seen_in_batch: set[str] = set()
    cur = conn.cursor()
    for it in items:
        code = it["shortcode"]
        if code in seen_in_batch:
            skipped += 1
            continue
        seen_in_batch.add(code)
        try:
            cur.execute(
                "insert into reel_inbox (shortcode, url, source) values (%s, %s, %s) "
                "on conflict (shortcode) do nothing",
                (code, it["url"], it["source"]))
            if cur.rowcount == 1:
                applied += 1
            else:
                skipped += 1
        except Exception:
            failed += 1
    return {"applied": applied, "skipped": skipped, "failed": failed}
