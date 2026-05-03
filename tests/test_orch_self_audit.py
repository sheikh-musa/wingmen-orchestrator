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
        assert "mis-routed" in text.lower() or "Tier-3" in text or "tier-3" in text.lower()
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
        assert "drift" in text.lower() or "merged but not applied" in text.lower()
        assert "not_applied" in text.lower() or "NOT_applied" in text


# ----------------------------------------------------------------------------
# run_orch_audit — orchestrating function
# ----------------------------------------------------------------------------

class TestRunOrchAudit:

    @pytest.mark.asyncio
    async def test_runs_all_five_checks(self):
        sb = MagicMock()
        with patch("nervous_system.orch_self_audit._audit_writer_health",
                   new_callable=AsyncMock) as wh, \
             patch("nervous_system.orch_self_audit._audit_bridge_tier3_volume",
                   new_callable=AsyncMock) as t3, \
             patch("nervous_system.orch_self_audit._audit_migration_consistency",
                   new_callable=AsyncMock) as mc, \
             patch("nervous_system.orch_self_audit._audit_scheduled_sweep_drift",
                   new_callable=AsyncMock) as sd, \
             patch("nervous_system.orch_self_audit._audit_anthropic_sdk_direct_call_sites",
                   new_callable=AsyncMock) as la:
            await orch_self_audit.run_orch_audit(sb)
            wh.assert_called_once()
            t3.assert_called_once()
            mc.assert_called_once()
            sd.assert_called_once()
            la.assert_called_once()

    @pytest.mark.asyncio
    async def test_one_check_failure_does_not_block_others(self):
        sb = MagicMock()
        async def boom(*a, **kw): raise ValueError("simulated")
        async def ok(*a, **kw): pass
        with patch("nervous_system.orch_self_audit._audit_writer_health", side_effect=boom), \
             patch("nervous_system.orch_self_audit._audit_bridge_tier3_volume", side_effect=ok) as t3, \
             patch("nervous_system.orch_self_audit._audit_migration_consistency", side_effect=ok) as mc, \
             patch("nervous_system.orch_self_audit._audit_scheduled_sweep_drift", side_effect=ok) as sd, \
             patch("nervous_system.orch_self_audit._audit_anthropic_sdk_direct_call_sites", side_effect=ok) as la:
            await orch_self_audit.run_orch_audit(sb)  # must not raise
            t3.assert_called_once()
            mc.assert_called_once()
            sd.assert_called_once()
            la.assert_called_once()


# ----------------------------------------------------------------------------
# Audit 4 — scheduled_sweep_drift (CAI-RESP-108 axis d)
# ----------------------------------------------------------------------------

def _ts(seconds_ago: int) -> str:
    """ISO timestamp N seconds before now."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _drift_supabase_mock(rows):
    """Supabase mock for the .gte/.like/.not_.is_/.execute() chain in drift audit."""
    sb = MagicMock()
    sb.table.return_value = sb
    sb.select.return_value = sb
    sb.gte.return_value = sb
    sb.lt.return_value = sb
    sb.eq.return_value = sb
    sb.like.return_value = sb  # to_agent LIKE 'cc-%'
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
        assert "Section D" in text or "guardrail" in text.lower() or "scheduled" in text.lower()
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

    @pytest.mark.asyncio
    async def test_query_filters_to_cc_recipients_only(self):
        """Audit only fires on messages addressed to cc-* families (the
        scheduled-sweep population). Messages addressed to cai are
        legitimate dialogue closes — cai writing read_at + responded_at
        in one transaction is correct Section A behavior, not a violation.
        Verified by asserting .like('to_agent', 'cc-%') is in the chain.
        """
        calls = []
        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.gte.return_value = sb
        sb.lt.return_value = sb
        sb.eq.return_value = sb
        sb.not_ = sb
        sb.is_.return_value = sb
        sb.insert.return_value = sb
        sb.limit.return_value = sb
        def _record_like(*args, **kw):
            calls.append(("like", args, kw)); return sb
        sb.like.side_effect = _record_like
        sb.execute = AsyncMock(return_value=MagicMock(data=[]))
        await orch_self_audit._audit_scheduled_sweep_drift(sb, None, None)
        like_calls = [c for c in calls if c[0] == "like"]
        assert any(args[0] == "to_agent" and args[1] == "cc-%"
                   for _, args, _ in like_calls), \
            f"missing .like('to_agent', 'cc-%') filter — calls: {calls}"


# ----------------------------------------------------------------------------
# Audit 5 — llm_routing_drift (CAI-PROCESS-MAX-FIRST-001)
# ----------------------------------------------------------------------------

class TestClassifyFinding:

    def test_haiku_auto_passes(self):
        f = {"model": "claude-haiku-4-5-20251001", "exempt_reason": None}
        assert orch_self_audit._classify_finding(f) == "ok_haiku"

    def test_haiku_substring_match(self):
        f = {"model": "claude-haiku-4-5", "exempt_reason": None}
        assert orch_self_audit._classify_finding(f) == "ok_haiku"

    def test_sonnet_with_valid_exempt_passes(self):
        f = {"model": "claude-sonnet-4-20250514",
             "exempt_reason": "tool_use_with_caller_defined_tools"}
        assert orch_self_audit._classify_finding(f) == "ok_exempt"

    def test_sonnet_no_exempt_violation(self):
        f = {"model": "claude-sonnet-4-20250514", "exempt_reason": None}
        assert orch_self_audit._classify_finding(f) == "violation_no_exempt"

    def test_sonnet_invalid_exempt_violation(self):
        f = {"model": "claude-sonnet-4-20250514",
             "exempt_reason": "i_just_felt_like_it"}
        assert orch_self_audit._classify_finding(f) == "violation_invalid_exempt"

    def test_unknown_model_no_exempt_violation(self):
        """Model literal not detected (None) AND no exempt → violation."""
        f = {"model": None, "exempt_reason": None}
        assert orch_self_audit._classify_finding(f) == "violation_no_exempt"

    def test_all_5_carve_out_reasons_accepted(self):
        for reason in ("latency_budget_under_3s", "streaming_structured_output",
                       "vision_multimodal", "tool_use_with_caller_defined_tools"):
            f = {"model": "claude-sonnet-4-20250514", "exempt_reason": reason}
            assert orch_self_audit._classify_finding(f) == "ok_exempt", \
                f"reason {reason!r} should be valid"


class TestScanCallSites:

    def test_scan_finds_legit_call_sites(self):
        """Live repo scan must detect known SDK instantiations."""
        findings = orch_self_audit._scan_call_sites()
        files_found = {f["file"] for f in findings}
        # These are known direct-API call sites in the repo
        assert "ralph_runner.py" in files_found
        assert "nervous_system/ecosystem_auditor.py" in files_found
        assert "nervous_system/council_agent.py" in files_found
        assert "ai_provider.py" in files_found

    def test_scan_excludes_tests_dir(self):
        """tests/ excluded from audit (test files may import anthropic for mock)."""
        findings = orch_self_audit._scan_call_sites()
        assert not any(f["file"].startswith("tests/") for f in findings), \
            f"tests/ files leaked into audit: {[f for f in findings if f['file'].startswith('tests/')]}"

    def test_scan_skips_comment_only_self_match(self):
        """Lines starting with `#` containing 'anthropic.Anthropic' must NOT
        match — handles the regex-docstring self-match in orch_self_audit.py."""
        findings = orch_self_audit._scan_call_sites()
        # Self-match would show line ~329 (the regex docstring) — that line
        # is a comment so should be excluded
        own_findings = [f for f in findings if f["file"].endswith("orch_self_audit.py")]
        assert not own_findings, \
            f"orch_self_audit.py self-matched (false positive): {own_findings}"

    def test_council_agent_classified_ok_exempt(self):
        findings = orch_self_audit._scan_call_sites()
        council = next((f for f in findings if f["file"].endswith("council_agent.py")), None)
        assert council is not None, "council_agent.py not detected"
        assert orch_self_audit._classify_finding(council) == "ok_exempt"
        assert council["exempt_reason"] == "tool_use_with_caller_defined_tools"


def _audit_supabase_mock(notif_existing=None):
    """Mock for the dedup-check + send_and_log chain in audit 5."""
    sb = MagicMock()
    sb.table.return_value = sb
    sb.select.return_value = sb
    sb.eq.return_value = sb
    sb.limit.return_value = sb
    sb.insert.return_value = sb
    sb.execute = AsyncMock(side_effect=[
        MagicMock(data=notif_existing or []),  # dedup check
        None,                                    # notif insert
    ] * 10)  # plenty of slots
    return sb


class TestLlmRoutingAudit:

    @pytest.mark.asyncio
    async def test_no_violations_no_alert(self):
        """Patched scan returning all-pass findings → no Telegram."""
        sb = _audit_supabase_mock()
        bot = AsyncMock()
        with patch.object(orch_self_audit, "_scan_call_sites", return_value=[
            {"file": "x.py", "line": 1, "model": "claude-haiku-4-5", "exempt_reason": None}
        ]):
            await orch_self_audit._audit_anthropic_sdk_direct_call_sites(sb, bot, "123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_violation_fires_alert(self):
        sb = _audit_supabase_mock()
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        with patch.object(orch_self_audit, "_scan_call_sites", return_value=[
            {"file": "bad.py", "line": 42,
             "model": "claude-sonnet-4-20250514", "exempt_reason": None}
        ]):
            await orch_self_audit._audit_anthropic_sdk_direct_call_sites(sb, bot, "123")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args.kwargs["text"]
        assert "CAI-PROCESS-MAX-FIRST-001" in text
        assert "bad.py:42" in text
        assert "violation_no_exempt" in text

    @pytest.mark.asyncio
    async def test_invalid_exempt_token_flagged(self):
        sb = _audit_supabase_mock()
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        with patch.object(orch_self_audit, "_scan_call_sites", return_value=[
            {"file": "x.py", "line": 1,
             "model": "claude-opus-4-7", "exempt_reason": "creative_writing"}
        ]):
            await orch_self_audit._audit_anthropic_sdk_direct_call_sites(sb, bot, "123")
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args.kwargs["text"]
        assert "violation_invalid_exempt" in text

    @pytest.mark.asyncio
    async def test_dedup_skips_already_alerted(self):
        sb = _audit_supabase_mock(notif_existing=[{"id": "prev"}])
        bot = AsyncMock()
        with patch.object(orch_self_audit, "_scan_call_sites", return_value=[
            {"file": "x.py", "line": 1,
             "model": "claude-sonnet-4-20250514", "exempt_reason": None}
        ]):
            await orch_self_audit._audit_anthropic_sdk_direct_call_sites(sb, bot, "123")
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_failure_isolated(self):
        sb = MagicMock()
        bot = AsyncMock()
        with patch.object(orch_self_audit, "_scan_call_sites",
                          side_effect=RuntimeError("scan blew up")):
            # Must not raise
            await orch_self_audit._audit_anthropic_sdk_direct_call_sites(sb, bot, "123")
