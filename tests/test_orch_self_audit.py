"""Tests for nervous_system.orch_self_audit."""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system import orch_self_audit


_NOW = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


def _supabase_mock_with_responses(*, repo_context_rows=None, notif_rows=None,
                                  dedup_rows=None, schema_mig_rows=None):
    """Build a MagicMock supabase client returning canned responses.
    repo_context_rows / notif_rows / dedup_rows / schema_mig_rows feed the
    sequence of .execute() calls in audit-order.

    The audit makes these queries in this fixed order:
      1. repo_context.select(...).order(...).limit(1).execute()  -> writer_health
      2. notification_log.select(...).eq(...).gte(...).execute()  -> tier3_volume
      3. supabase_migrations.schema_migrations.select(...).execute() -> migration_consistency
      4. notification_log dedup_key checks before any alerts (one per fire).
    """
    sb = MagicMock()
    # We let .table(...).select(...).order(...).limit(...).eq(...).gte(...).execute()
    # return whatever's queued. Each .execute is one response in sequence.
    return sb


def _stub_chain(sb_mock, return_values: list):
    """Make every chained call return the same MagicMock that .execute()
    pulls return_values FIFO from. Self-referential — chain_mock.<any-method>
    returns chain_mock so deeply-chained calls (e.g. supabase.schema(...).table(...))
    stay on the same mock all the way through to .execute()."""
    chain_mock = MagicMock()
    # Self-reference all the chained methods to chain_mock itself
    for method in ("table", "schema", "select", "order", "limit",
                   "eq", "gte", "is_", "in_", "insert", "upsert", "update", "delete"):
        getattr(chain_mock, method).return_value = chain_mock
    # Wire root mock to also return chain_mock on the entry methods.
    sb_mock.table.return_value = chain_mock
    sb_mock.schema.return_value = chain_mock
    queue = list(return_values)

    async def execute_pop():
        if not queue:
            return MagicMock(data=[])
        return queue.pop(0)
    chain_mock.execute = AsyncMock(side_effect=execute_pop)
    return sb_mock


# ----------------------------------------------------------------------------
# Audit 1 — writer_health
# ----------------------------------------------------------------------------

class TestWriterHealthAudit:

    @pytest.mark.asyncio
    async def test_fresh_writer_no_alert(self):
        fresh = (_NOW - timedelta(minutes=5)).isoformat()
        sb = _stub_chain(MagicMock(), [
            MagicMock(data=[{"repo": "ihsanos", "updated_at": fresh}]),
        ])
        bot = AsyncMock()
        with patch("nervous_system.orch_self_audit.datetime") as dt_mock:
            dt_mock.now.return_value = _NOW
            dt_mock.fromisoformat = datetime.fromisoformat
            await orch_self_audit._audit_writer_health(sb, bot, "123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_writer_alerts(self):
        stale = (_NOW - timedelta(minutes=120)).isoformat()  # well past 60-min threshold
        sb = _stub_chain(MagicMock(), [
            MagicMock(data=[{"repo": "ihsanos", "updated_at": stale}]),
            MagicMock(data=[]),  # dedup check returns empty
            MagicMock(data=[]),  # notification_log insert
        ])
        bot = AsyncMock()
        sent_mock = MagicMock(); sent_mock.message_id = 42
        bot.send_message = AsyncMock(return_value=sent_mock)
        with patch("nervous_system.orch_self_audit.datetime") as dt_mock:
            dt_mock.now.return_value = _NOW
            dt_mock.fromisoformat = datetime.fromisoformat
            await orch_self_audit._audit_writer_health(sb, bot, "123")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[1]["text"]
        assert "stale" in text.lower()
        assert "120 min" in text or "min" in text

    @pytest.mark.asyncio
    async def test_empty_table_silent(self):
        """Empty repo_context = writer never ran. agent_watchdog covers that
        path; orch_self_audit should not double-alert."""
        sb = _stub_chain(MagicMock(), [
            MagicMock(data=[]),  # empty repo_context
        ])
        bot = AsyncMock()
        await orch_self_audit._audit_writer_health(sb, bot, "123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_alerted_no_duplicate(self):
        stale = (_NOW - timedelta(minutes=120)).isoformat()
        sb = _stub_chain(MagicMock(), [
            MagicMock(data=[{"repo": "ihsanos", "updated_at": stale}]),
            MagicMock(data=[{"id": 1}]),  # dedup check says already alerted this hour
        ])
        bot = AsyncMock()
        with patch("nervous_system.orch_self_audit.datetime") as dt_mock:
            dt_mock.now.return_value = _NOW
            dt_mock.fromisoformat = datetime.fromisoformat
            await orch_self_audit._audit_writer_health(sb, bot, "123")
        bot.send_message.assert_not_called()


# ----------------------------------------------------------------------------
# Audit 2 — bridge_tier3_volume
# ----------------------------------------------------------------------------

class TestTier3VolumeAudit:

    @pytest.mark.asyncio
    async def test_below_threshold_no_alert(self):
        sb = _stub_chain(MagicMock(), [
            MagicMock(data=[{"id": i} for i in range(2)]),  # 2 rows ≤ threshold 3
        ])
        bot = AsyncMock()
        await orch_self_audit._audit_bridge_tier3_volume(sb, bot, "123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_above_threshold_alerts(self):
        sb = _stub_chain(MagicMock(), [
            MagicMock(data=[{"id": i} for i in range(7)]),  # 7 > threshold 3
            MagicMock(data=[]),  # dedup empty
            MagicMock(data=[]),  # log insert
        ])
        bot = AsyncMock()
        sent_mock = MagicMock(); sent_mock.message_id = 99
        bot.send_message = AsyncMock(return_value=sent_mock)
        await orch_self_audit._audit_bridge_tier3_volume(sb, bot, "123")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[1]["text"]
        assert "Tier-3" in text
        assert "7" in text


# ----------------------------------------------------------------------------
# Audit 3 — migration_consistency
# ----------------------------------------------------------------------------

class TestMigrationConsistencyAudit:

    @pytest.mark.asyncio
    async def test_all_files_in_db_no_alert(self, tmp_path):
        # Create two fake migration files
        (tmp_path / "20260101_some_migration.sql").write_text("--")
        (tmp_path / "20260102_other_migration.sql").write_text("--")
        sb = _stub_chain(MagicMock(), [
            MagicMock(data=[
                {"name": "some_migration"},
                {"name": "other_migration"},
                {"name": "earlier_unrelated"},  # extras OK
            ]),
        ])
        bot = AsyncMock()
        with patch("nervous_system.orch_self_audit._MIGRATIONS_DIR", tmp_path):
            await orch_self_audit._audit_migration_consistency(sb, bot, "123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_unapplied_file_alerts(self, tmp_path):
        (tmp_path / "20260101_applied.sql").write_text("--")
        (tmp_path / "20260102_NOT_applied.sql").write_text("--")
        sb = _stub_chain(MagicMock(), [
            MagicMock(data=[{"name": "applied"}]),  # only one in DB
            MagicMock(data=[]),  # dedup empty
            MagicMock(data=[]),  # log insert
        ])
        bot = AsyncMock()
        sent_mock = MagicMock(); sent_mock.message_id = 77
        bot.send_message = AsyncMock(return_value=sent_mock)
        with patch("nervous_system.orch_self_audit._MIGRATIONS_DIR", tmp_path):
            await orch_self_audit._audit_migration_consistency(sb, bot, "123")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[1]["text"]
        assert "drift" in text.lower()
        assert "NOT_applied" in text


# ----------------------------------------------------------------------------
# run_orch_audit — orchestrating function
# ----------------------------------------------------------------------------

class TestRunOrchAudit:

    @pytest.mark.asyncio
    async def test_runs_all_four_checks(self):
        sb = MagicMock()
        with patch("nervous_system.orch_self_audit._audit_writer_health",
                   new_callable=AsyncMock) as wh, \
             patch("nervous_system.orch_self_audit._audit_bridge_tier3_volume",
                   new_callable=AsyncMock) as t3, \
             patch("nervous_system.orch_self_audit._audit_migration_consistency",
                   new_callable=AsyncMock) as mc, \
             patch("nervous_system.orch_self_audit._audit_scheduled_sweep_drift",
                   new_callable=AsyncMock) as sd:
            await orch_self_audit.run_orch_audit(sb)
            wh.assert_called_once()
            t3.assert_called_once()
            mc.assert_called_once()
            sd.assert_called_once()

    @pytest.mark.asyncio
    async def test_one_check_failure_does_not_block_others(self):
        sb = MagicMock()
        async def boom(*a, **kw): raise ValueError("simulated")
        async def ok(*a, **kw): pass
        with patch("nervous_system.orch_self_audit._audit_writer_health", side_effect=boom), \
             patch("nervous_system.orch_self_audit._audit_bridge_tier3_volume", side_effect=ok) as t3, \
             patch("nervous_system.orch_self_audit._audit_migration_consistency", side_effect=ok) as mc, \
             patch("nervous_system.orch_self_audit._audit_scheduled_sweep_drift", side_effect=ok) as sd:
            await orch_self_audit.run_orch_audit(sb)  # must not raise
            t3.assert_called_once()
            mc.assert_called_once()
            sd.assert_called_once()


# ----------------------------------------------------------------------------
# Audit 4 — scheduled_sweep_drift (CAI-RESP-108 axis d)
# ----------------------------------------------------------------------------

def _ts(seconds_ago: int) -> str:
    """ISO timestamp N seconds before now."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _drift_supabase_mock(rows):
    """Supabase mock for the .gte/.not_.is_/.execute() chain in drift audit."""
    sb = MagicMock()
    sb.table.return_value = sb
    sb.select.return_value = sb
    sb.gte.return_value = sb
    sb.lt.return_value = sb
    sb.eq.return_value = sb
    sb.not_ = sb           # chains .not_.is_ — both return sb
    sb.is_.return_value = sb
    sb.insert.return_value = sb
    sb.limit.return_value = sb
    sb.execute = AsyncMock(side_effect=[
        MagicMock(data=rows),  # main agent_messages query
        MagicMock(data=[]),    # _check_dedup → no existing
        None,                  # _send_and_log notification_log insert
    ])
    return sb


class TestScheduledSweepDrift:

    @pytest.mark.asyncio
    async def test_no_offenders_no_alert(self):
        """Empty agent_messages query → no Telegram."""
        sb = _drift_supabase_mock([])
        bot = AsyncMock()
        await orch_self_audit._audit_scheduled_sweep_drift(sb, bot, "123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_responded_within_window_fires_alert(self):
        """responded_at landed 5s after read_at → P2 alert."""
        read = _ts(20)
        resp = _ts(15)  # 5s after read
        rows = [{
            "id": 4242, "from_agent": "cai", "to_agent": "cc-orchestrator",
            "subject": "test", "read_at": read, "responded_at": resp,
            "created_at": _ts(60),
        }]
        sb = _drift_supabase_mock(rows)
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        await orch_self_audit._audit_scheduled_sweep_drift(sb, bot, "123")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args.kwargs["text"]
        assert "Section D" in text
        assert "#4242" in text

    @pytest.mark.asyncio
    async def test_responded_outside_window_no_alert(self):
        """responded_at 60s after read_at → outside 30s window, no alert."""
        rows = [{
            "id": 7, "from_agent": "cai", "to_agent": "cc-orchestrator",
            "subject": "legit reply", "read_at": _ts(120),
            "responded_at": _ts(60),  # 60s gap
            "created_at": _ts(180),
        }]
        sb = _drift_supabase_mock(rows)
        bot = AsyncMock()
        await orch_self_audit._audit_scheduled_sweep_drift(sb, bot, "123")
        bot.send_message.assert_not_called()
