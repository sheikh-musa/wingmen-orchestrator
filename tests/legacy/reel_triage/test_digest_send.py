from reel_triage import digest_send


def test_compose_digest_has_one_line_per_action_and_buttons(reel_db):
    cur = reel_db.cursor()
    for i in range(5):
        cur.execute("insert into reel_inbox (shortcode,url,source,status,action,priority) "
                    "values (%s,%s,'share_link','triaged',%s,%s)",
                    (f"D{i}", f"D{i}", f"action {i}", float(i)))
    text, keyboard, shown = digest_send.compose(reel_db)
    assert text.count("\n") >= 5
    assert len(keyboard) == 5            # one [Apply][Discard] row per action
    assert len(shown) == 5


def test_compose_empty_when_nothing_triaged(reel_db):
    text, keyboard, shown = digest_send.compose(reel_db)
    assert keyboard == []
    assert shown == []
