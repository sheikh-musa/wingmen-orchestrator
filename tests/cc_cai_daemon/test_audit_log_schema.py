"""INV-5 hard ship condition test: cc_cai_audit_log table schema present."""
from __future__ import annotations
import os, sys
from pathlib import Path
import psycopg, pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")


def test_cc_cai_audit_log_table_exists_with_required_columns():
    expected = {
        "id", "logged_at", "event_type", "agent_message_id",
        "classification", "classification_reason", "confidence",
        "tool_name", "tool_input_summary", "tool_output_summary",
        "escalated_to_operator", "telegram_message_id",
        "kill_switch_state", "session_id",
    }
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='cc_cai_audit_log'"
        )
        actual = {r[0] for r in cur.fetchall()}
    missing = expected - actual
    assert not missing, f"missing INV-5 columns: {missing}"
