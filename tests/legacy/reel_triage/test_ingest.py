from reel_triage import ingest


def test_ingest_links_inserts_new_and_skips_dupes(reel_db):
    items = [{"shortcode": "A", "url": "https://instagram.com/reel/A", "source": "share_link"},
             {"shortcode": "A", "url": "https://instagram.com/reel/A", "source": "share_link"}]
    counts = ingest.ingest_items(reel_db, items)
    assert counts == {"applied": 1, "skipped": 1, "failed": 0}
    cur = reel_db.cursor()
    cur.execute("select count(*) c from reel_inbox where shortcode = 'A'")
    assert cur.fetchone()["c"] == 1


def test_ingest_skips_shortcode_already_in_db(reel_db):
    one = [{"shortcode": "B", "url": "u", "source": "dyi_saved"}]
    ingest.ingest_items(reel_db, one)
    counts = ingest.ingest_items(reel_db, one)   # second pass
    assert counts == {"applied": 0, "skipped": 1, "failed": 0}


def test_ingest_counts_failed_on_bad_source(reel_db):
    items = [{"shortcode": "C", "url": "u", "source": "BOGUS"}]  # violates CHECK
    counts = ingest.ingest_items(reel_db, items)
    assert counts == {"applied": 0, "skipped": 0, "failed": 1}
