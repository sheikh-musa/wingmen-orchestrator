from legacy.ihsanos_drain.poller import inbox_query
import pytest

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



def test_inbox_query_targets_cc_ihsanos_unread_nontest():
    sql, params = inbox_query(limit=50)
    assert "to_agent = %s" in sql
    assert "read_at IS NULL" in sql
    assert "is_test = false" in sql
    assert "skipped_at IS NULL" in sql
    assert "ORDER BY priority ASC, created_at ASC" in sql
    assert params == ("cc-ihsanos", 50)
