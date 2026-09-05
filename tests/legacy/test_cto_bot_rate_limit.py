"""Tests for cto_bot bug report rate limiting."""

from unittest.mock import patch

from cto_bot import check_bug_rate_limit, _bug_rate_limits, MAX_BUG_REPORTS_PER_HOUR


class TestBugRateLimit:

    def setup_method(self):
        _bug_rate_limits.clear()

    def test_allows_within_limit(self):
        results = [check_bug_rate_limit("chat-1") for _ in range(MAX_BUG_REPORTS_PER_HOUR)]
        assert all(results)

    def test_blocks_at_limit(self):
        results = [check_bug_rate_limit("chat-1") for _ in range(MAX_BUG_REPORTS_PER_HOUR + 1)]
        assert results[:-1] == [True] * MAX_BUG_REPORTS_PER_HOUR
        assert results[-1] is False

    def test_old_timestamps_expire(self):
        with patch("cto_bot.time.monotonic") as mock_mono:
            mock_mono.return_value = 1000.0
            for _ in range(MAX_BUG_REPORTS_PER_HOUR):
                assert check_bug_rate_limit("chat-1") is True
            assert check_bug_rate_limit("chat-1") is False

            mock_mono.return_value = 4601.0
            assert check_bug_rate_limit("chat-1") is True
