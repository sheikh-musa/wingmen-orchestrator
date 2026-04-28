"""Tests for nervous_system.deploy_verifier (ORCHESTRATOR-STATUS-001 Option B)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)
# Apply via @pytestmark_integration on individual tests that hit live DB.
# Pure-unit tests (parser logic, mock-based state machine) skip the decorator
# so they run without DATABASE_URL set (CI-safe).
# Pattern matches tests/test_auto_agent_id.py + tests/test_repo_context_writer.py.
