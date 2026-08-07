"""Tests for schema_gate — blocking jobs with unapplied schema.sql changes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nervous_system.schema_gate import check_and_block, extract_schema_ddl


SAMPLE_DDL_DIFF = """\
diff --git a/schema.sql b/schema.sql
index abc..def 100644
--- a/schema.sql
+++ b/schema.sql
@@ -10,6 +10,9 @@ create table foo (
   id int
 );
+alter table foo add column if not exists category text;
+-- a comment line, should be skipped
+create index if not exists idx_foo_cat on foo(category);
+
"""

NOOP_DIFF = ""
COMMENT_ONLY_DIFF = """\
diff --git a/schema.sql b/schema.sql
+-- just a comment
+
"""


def _mock_supabase(dedup_exists=False):
    supabase = MagicMock()
    dedup_data = [{"id": "x"}] if dedup_exists else []
    dedup_execute = AsyncMock(return_value=MagicMock(data=dedup_data))
    insert_execute = AsyncMock(return_value=MagicMock(data=[]))
    update_execute = AsyncMock(return_value=MagicMock(data=[]))

    def table_router(name):
        t = MagicMock()
        t.select.return_value.eq.return_value.limit.return_value.execute = dedup_execute
        t.insert.return_value.execute = insert_execute
        t.update.return_value.eq.return_value.execute = update_execute
        return t

    supabase.table = MagicMock(side_effect=table_router)
    return supabase


class TestExtractSchemaDdl:
    @pytest.mark.asyncio
    async def test_extracts_alter_and_create(self):
        with patch("nervous_system.schema_gate._git_stdout", AsyncMock(return_value=SAMPLE_DDL_DIFF)):
            ddl = await extract_schema_ddl("/tmp/repo")
        assert any("alter table foo" in line for line in ddl)
        assert any("create index" in line for line in ddl)
        assert not any(line.startswith("--") for line in ddl)

    @pytest.mark.asyncio
    async def test_empty_diff_returns_empty(self):
        with patch("nervous_system.schema_gate._git_stdout", AsyncMock(return_value=NOOP_DIFF)):
            ddl = await extract_schema_ddl("/tmp/repo")
        assert ddl == []

    @pytest.mark.asyncio
    async def test_comment_only_diff_returns_empty(self):
        with patch("nervous_system.schema_gate._git_stdout", AsyncMock(return_value=COMMENT_ONLY_DIFF)):
            ddl = await extract_schema_ddl("/tmp/repo")
        assert ddl == []


class TestCheckAndBlock:
    @pytest.mark.asyncio
    async def test_blocks_and_pings_when_ddl_found(self):
        supabase = _mock_supabase()
        bot = AsyncMock()
        fake_cfg = {"supabase_project_ref": "tscuymavysscrvoberrr"}

        # Delivery moved off `bot.send_message` to notify_operator() (the
        # @wingmennorchbot bridge); mock it so the test is hermetic and assert on
        # the message the gate actually sends.
        with patch("nervous_system.schema_gate.get_repo_config", return_value=fake_cfg), \
             patch("nervous_system.schema_gate.extract_schema_ddl",
                   AsyncMock(return_value=["alter table foo add column bar text;"])), \
             patch("nervous_system.schema_gate.notify_operator", return_value=True) as notify, \
             patch("nervous_system.schema_gate.get_chat_id", return_value="cto_chat"):
            blocked = await check_and_block(
                supabase, "/tmp/repo", "wingmen-orchestrator", 42,
                "TASK-032 something", bot,
            )

        assert blocked is True
        notify.assert_called_once()
        msg = notify.call_args.args[0]
        assert "42" in msg
        assert "alter table foo" in msg
        assert "tscuymavysscrvoberrr" in msg

    @pytest.mark.asyncio
    async def test_skips_when_no_ddl(self):
        supabase = _mock_supabase()
        bot = AsyncMock()
        fake_cfg = {"supabase_project_ref": "tscuymavysscrvoberrr"}

        with patch("nervous_system.schema_gate.get_repo_config", return_value=fake_cfg), \
             patch("nervous_system.schema_gate.extract_schema_ddl", AsyncMock(return_value=[])):
            blocked = await check_and_block(
                supabase, "/tmp/repo", "wingmen-orchestrator", 42, "something", bot,
            )

        assert blocked is False
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_repos_without_supabase_project_ref(self):
        supabase = _mock_supabase()
        bot = AsyncMock()

        with patch("nervous_system.schema_gate.get_repo_config",
                   return_value={"supabase_project_ref": None}):
            blocked = await check_and_block(
                supabase, "/tmp/repo", "cosem-tdu", 42, "something", bot,
            )

        assert blocked is False
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_suppresses_second_ping(self):
        supabase = _mock_supabase(dedup_exists=True)
        bot = AsyncMock()
        fake_cfg = {"supabase_project_ref": "tscuymavysscrvoberrr"}

        with patch("nervous_system.schema_gate.get_repo_config", return_value=fake_cfg), \
             patch("nervous_system.schema_gate.extract_schema_ddl",
                   AsyncMock(return_value=["alter table foo add column bar text;"])), \
             patch("nervous_system.schema_gate.get_chat_id", return_value="cto_chat"):
            blocked = await check_and_block(
                supabase, "/tmp/repo", "wingmen-orchestrator", 42,
                "TASK-032 something", bot,
            )

        assert blocked is True
        bot.send_message.assert_not_called()
