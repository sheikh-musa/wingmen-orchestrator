"""Tests for Mac Studio endpoint probes in watchdog.py.

Per CAI-WATCHDOG-MAC-STUDIO-001 — covers probe HTTP behavior, hysteresis,
and alert-once semantics.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestMacStudioProbes:
    @pytest.mark.asyncio
    async def test_probe_encoder_success_returns_true(self):
        import watchdog
        with patch.object(watchdog, "httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_response = type("R", (), {"status_code": 200})()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await watchdog.check_mac_studio_encoder()
            assert result is True

    @pytest.mark.asyncio
    async def test_probe_encoder_timeout_returns_false(self):
        import watchdog
        with patch.object(watchdog, "httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(side_effect=Exception("timeout"))
            result = await watchdog.check_mac_studio_encoder()
            assert result is False

    @pytest.mark.asyncio
    async def test_probe_encoder_non_200_returns_false(self):
        import watchdog
        with patch.object(watchdog, "httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_response = type("R", (), {"status_code": 500})()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await watchdog.check_mac_studio_encoder()
            assert result is False

    @pytest.mark.asyncio
    async def test_probe_mlx_distinct_url(self):
        """check_mac_studio_mlx must hit :8081 (not :8080)."""
        import watchdog
        captured_urls = []
        with patch.object(watchdog, "httpx") as mock_httpx:
            mock_client = AsyncMock()

            async def fake_get(url):
                captured_urls.append(url)
                return type("R", (), {"status_code": 200})()

            mock_client.get = fake_get
            mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)
            await watchdog.check_mac_studio_mlx()
        assert captured_urls and "8081" in captured_urls[0], \
            f"check_mac_studio_mlx should hit :8081, got {captured_urls}"


class TestHysteresisHelper:
    """Per-endpoint hysteresis tracker — only alerts after sustained N seconds of failure."""

    def test_first_failure_does_not_yet_qualify(self):
        from watchdog import MacStudioEndpointState
        st = MacStudioEndpointState(name="encoder", alert_after_seconds=300)
        triggered = st.record_probe(alive=False, now_epoch=1000.0)
        assert triggered is False
        assert st.first_failure_at == 1000.0

    def test_consecutive_failures_for_full_window_triggers(self):
        from watchdog import MacStudioEndpointState
        st = MacStudioEndpointState(name="encoder", alert_after_seconds=300)
        st.record_probe(alive=False, now_epoch=1000.0)
        st.record_probe(alive=False, now_epoch=1100.0)
        triggered = st.record_probe(alive=False, now_epoch=1305.0)
        assert triggered is True, "5min+5s of continuous failure should trigger"

    def test_recovery_resets_state_and_signals_recovery(self):
        from watchdog import MacStudioEndpointState
        st = MacStudioEndpointState(name="encoder", alert_after_seconds=300)
        st.record_probe(alive=False, now_epoch=1000.0)
        st.record_probe(alive=False, now_epoch=1400.0)
        # now triggered; record recovery
        recovery = st.record_probe(alive=True, now_epoch=1500.0)
        assert recovery is True, "recovery from previously-alerted state should signal"
        # next probe (still alive) returns False (no transition)
        assert st.record_probe(alive=True, now_epoch=1600.0) is False

    def test_recovery_without_prior_alert_does_not_signal(self):
        from watchdog import MacStudioEndpointState
        st = MacStudioEndpointState(name="encoder", alert_after_seconds=300)
        # one failure but not long enough to alert
        st.record_probe(alive=False, now_epoch=1000.0)
        # recovery before alert threshold
        recovery = st.record_probe(alive=True, now_epoch=1100.0)
        assert recovery is False, "recovery from never-alerted state should not signal"

    def test_alert_fires_only_once_per_outage(self):
        from watchdog import MacStudioEndpointState
        st = MacStudioEndpointState(name="encoder", alert_after_seconds=300)
        st.record_probe(alive=False, now_epoch=1000.0)
        first_trigger = st.record_probe(alive=False, now_epoch=1400.0)
        assert first_trigger is True
        # subsequent failures during same outage should NOT re-alert
        second_trigger = st.record_probe(alive=False, now_epoch=1500.0)
        assert second_trigger is False
