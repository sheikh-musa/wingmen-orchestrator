import psycopg
import pytest

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



def test_status_defaults_to_inbox(reel_db):
    cur = reel_db.cursor()
    cur.execute("insert into reel_inbox (shortcode, url, source) "
                "values ('abc', 'https://instagram.com/reel/abc', 'share_link') returning status")
    assert cur.fetchone()["status"] == "inbox"


def test_source_check_rejects_bad_value(reel_db):
    cur = reel_db.cursor()
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute("insert into reel_inbox (shortcode, url, source) "
                    "values ('x', 'u', 'bogus')")


def test_shortcode_is_unique(reel_db):
    cur = reel_db.cursor()
    cur.execute("insert into reel_inbox (shortcode, url, source) values ('dup','u','share_link')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        cur.execute("insert into reel_inbox (shortcode, url, source) values ('dup','u2','share_link')")


def test_digests_shown_defaults_zero(reel_db):
    cur = reel_db.cursor()
    cur.execute("insert into reel_inbox (shortcode, url, source) "
                "values ('d0','u','share_link') returning digests_shown")
    assert cur.fetchone()["digests_shown"] == 0


def test_evidence_grade_and_effort_checks(reel_db):
    cur = reel_db.cursor()
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute("insert into reel_inbox (shortcode, url, source, evidence_grade) "
                    "values ('e','u','share_link','BOGUS')")
    cur.execute("reset role")  # autocommit: prior failed stmt is isolated
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute("insert into reel_inbox (shortcode, url, source, effort) "
                    "values ('f','u','share_link','BOGUS')")
