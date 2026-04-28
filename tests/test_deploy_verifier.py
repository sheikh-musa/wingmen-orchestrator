"""Tests for nervous_system.deploy_verifier (ORCHESTRATOR-STATUS-001 Option B)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import uuid

import psycopg
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


@pytestmark_integration
def test_bug_reports_has_option_b_columns():
    """AC-B-7 part 1: 5 new columns on bug_reports for verifier state."""
    expected = {
        "verified_at": ("timestamp with time zone", "YES"),
        "verification_started_at": ("timestamp with time zone", "YES"),
        "verification_diagnostic": ("text", "YES"),
        "manual_override_reason": ("text", "YES"),
        "verification_escalated_at": ("timestamp with time zone", "YES"),
    }
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_name = 'bug_reports'
                   AND column_name = ANY(%s)
                """,
                (list(expected.keys()),),
            )
            actual = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    assert actual == expected, f"columns mismatch: expected {expected}, got {actual}"


@pytestmark_integration
def test_bug_reports_status_check_accepts_pr_open():
    """AC-B-9: bug_reports.status CHECK must accept new states pr_open / push_failed / pr_failed.

    INSERTs a row with status='pr_open' inside an explicit transaction, then rolls
    back so no test row is committed. Pre-migration this raises CheckViolation.
    """
    # reporter_source CHECK currently restricts to {'telegram','web'} —
    # use 'telegram' for test rows. CheckViolation surfaces clearly if drift
    # ever happens; this comment saves a future author 60s of digging.
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bug_reports
                  (id, reporter_name, reporter_source, repo_name, description, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), "test", "telegram", "cosem-tdu", "test bug", "pr_open"),
            )
        c.rollback()


@pytestmark_integration
def test_bug_reports_manual_override_reason_check_rejects_short():
    """CAI-PIPELINE-BYPASS-001 AC-1: status='deployed' with manual_override_reason
    shorter than 20 chars (after trim) must raise CheckViolation."""
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    """
                    INSERT INTO bug_reports
                      (id, reporter_name, reporter_source, repo_name, description,
                       status, manual_override_reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (str(uuid.uuid4()), "test", "telegram", "cosem-tdu", "test bug",
                     "deployed", "short"),
                )
        c.rollback()


@pytestmark_integration
def test_bug_reports_manual_override_reason_check_accepts_long():
    """CAI-PIPELINE-BYPASS-001 AC-1: status='deployed' with manual_override_reason
    >=20 chars (after trim) must succeed."""
    long_reason = "operator-authorized bypass for verifier flake under known-degraded firebase"
    assert len(long_reason.strip()) >= 20  # sanity
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bug_reports
                  (id, reporter_name, reporter_source, repo_name, description,
                   status, manual_override_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), "test", "telegram", "cosem-tdu", "test bug",
                 "deployed", long_reason),
            )
        c.rollback()


@pytestmark_integration
def test_jobs_has_option_b_columns():
    """cc-cosem #874 + #955 boundary: I add the columns; she writes pr_number
    + branch_name from publish_job_commit. merged_commit_sha is Option B's
    cache (live-fetched from gh pr view per CAI-RESP-083, written by
    deploy_verifier on first observation per tick)."""
    expected = {
        "pr_number":         ("integer", "YES"),
        "branch_name":       ("text", "YES"),
        "merged_commit_sha": ("text", "YES"),
    }
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_name = 'jobs'
                   AND column_name = ANY(%s)
                """,
                (list(expected.keys()),),
            )
            actual = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    assert actual == expected, f"columns mismatch: expected {expected}, got {actual}"


@pytestmark_integration
def test_boot_briefing_has_manual_override_bugs_section():
    """CAI-PIPELINE-BYPASS-001 AC-3: boot_briefing view body includes the
    manual_override_bugs UNION branch.

    Test inspects pg_views.definition rather than `SELECT DISTINCT source`
    because the latter only surfaces sources with ≥1 row at query time;
    pre-backfill there are zero override rows so the source string would be
    absent from the result set even with the UNION branch correctly defined.
    """
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT definition FROM pg_views WHERE viewname = 'boot_briefing'"
            )
            body = cur.fetchone()[0]
    assert "'manual_override_bugs'::text AS source" in body or "'manual_override_bugs'" in body, \
        f"boot_briefing view body missing manual_override_bugs section"


@pytestmark_integration
def test_boot_briefing_existing_sections_preserved():
    """Section 5 DROP+CREATE must preserve all 8 prior sections.

    Catches the class of bug where a view rebuild silently loses a UNION
    branch. Inspects pg_views.definition (not `SELECT DISTINCT source`)
    because empty branches produce no source row even when correctly defined.
    """
    expected = ("repo_context", "repo_snapshot", "active_decision",
                "open_qa_failure", "latest_cc_session", "latest_digest",
                "last_cai_session", "unverified_decisions",
                "manual_override_bugs")
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT definition FROM pg_views WHERE viewname = 'boot_briefing'"
            )
            body = cur.fetchone()[0]
    missing = [s for s in expected if f"'{s}'" not in body]
    assert not missing, f"boot_briefing view body missing sections: {missing}"


# ============================================================================
# Worker tests (Tasks 9-15) — pure-unit + mocked async tests
# ============================================================================


def test_query_predicate_excludes_overridden_and_escalated():
    """Task 9 — _query_pending_predicate returns SQL that filters to bugs
    awaiting verification: status='pr_open', verified_at IS NULL,
    manual_override_reason IS NULL, verification_escalated_at IS NULL.
    Order ASC + LIMIT 20 to bound per-tick API calls.
    """
    from nervous_system.deploy_verifier import _query_pending_predicate
    sql = _query_pending_predicate()
    assert "status = 'pr_open'" in sql or "status='pr_open'" in sql
    assert "verified_at IS NULL" in sql
    assert "manual_override_reason IS NULL" in sql
    assert "verification_escalated_at IS NULL" in sql
    assert "ORDER BY" in sql.upper()
    assert "created_at" in sql.lower()
    assert "LIMIT 20" in sql.upper()


# ============================================================================
# Task 9: query predicate
# ============================================================================

def test_query_predicate_excludes_overridden_and_escalated():
    """CAI-RESP-083: predicate must exclude overridden + escalated rows."""
    from nervous_system.deploy_verifier import _query_pending_predicate
    sql = _query_pending_predicate()
    assert "status = 'pr_open'" in sql
    assert "verified_at IS NULL" in sql
    assert "manual_override_reason IS NULL" in sql
    assert "verification_escalated_at IS NULL" in sql
    assert "ORDER BY created_at" in sql


# ============================================================================
# Task 10: GitHub PR state lookup (_fetch_pr_state, _verify_commit_on_main)
# ============================================================================


class _FakeProc:
    """Minimal stand-in for asyncio.subprocess.Process — communicate() returns
    pre-baked (stdout, stderr) bytes; returncode is preset."""
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return (self._stdout, self._stderr)


@pytest.mark.asyncio
async def test_fetch_pr_state_merged():
    """CASE 3: gh returns mergeCommit with oid → merged=True + sha + merged_at."""
    from nervous_system import deploy_verifier
    payload = json.dumps({
        "mergeCommit": {"oid": "abc123def456"},
        "mergedAt": "2026-04-28T10:00:00Z",
        "createdAt": "2026-04-28T09:00:00Z",
    }).encode()
    fake = _FakeProc(0, stdout=payload)
    with patch.object(deploy_verifier.asyncio, "create_subprocess_exec",
                      AsyncMock(return_value=fake)):
        result = await deploy_verifier._fetch_pr_state("acme", "repo", 42)
    assert result == {
        "merged": True,
        "merge_commit_sha": "abc123def456",
        "merged_at": "2026-04-28T10:00:00Z",
        "created_at": "2026-04-28T09:00:00Z",
    }


@pytest.mark.asyncio
async def test_fetch_pr_state_open():
    """CASE 2: PR open — mergeCommit null, created_at set."""
    from nervous_system import deploy_verifier
    payload = json.dumps({
        "mergeCommit": None, "mergedAt": None, "createdAt": "2026-04-28T09:00:00Z",
    }).encode()
    with patch.object(deploy_verifier.asyncio, "create_subprocess_exec",
                      AsyncMock(return_value=_FakeProc(0, stdout=payload))):
        result = await deploy_verifier._fetch_pr_state("acme", "repo", 42)
    assert result["merged"] is False
    assert result["merge_commit_sha"] is None
    assert result["created_at"] == "2026-04-28T09:00:00Z"


@pytest.mark.asyncio
async def test_fetch_pr_state_gh_error_returns_none():
    """gh nonzero exit (e.g. PR not found) → None, not exception."""
    from nervous_system import deploy_verifier
    with patch.object(deploy_verifier.asyncio, "create_subprocess_exec",
                      AsyncMock(return_value=_FakeProc(1, stderr=b"not found"))):
        result = await deploy_verifier._fetch_pr_state("acme", "repo", 99)
    assert result is None


@pytest.mark.asyncio
async def test_verify_commit_on_main_identical_or_ahead():
    """CAI-RESP-083: gh api compare status='identical' or 'ahead' → on-main."""
    from nervous_system import deploy_verifier
    for status in ("identical", "ahead"):
        body = json.dumps({"status": status}).encode()
        with patch.object(deploy_verifier.asyncio, "create_subprocess_exec",
                          AsyncMock(return_value=_FakeProc(0, stdout=body))):
            result = await deploy_verifier._verify_commit_on_main("acme", "repo", "abc123")
        assert result is True, f"status={status} should be on-main"


@pytest.mark.asyncio
async def test_verify_commit_on_main_behind_or_diverged():
    """status='behind' or 'diverged' → not on-main."""
    from nervous_system import deploy_verifier
    for status in ("behind", "diverged"):
        body = json.dumps({"status": status}).encode()
        with patch.object(deploy_verifier.asyncio, "create_subprocess_exec",
                          AsyncMock(return_value=_FakeProc(0, stdout=body))):
            result = await deploy_verifier._verify_commit_on_main("acme", "repo", "abc123")
        assert result is False, f"status={status} should not be on-main"


@pytest.mark.asyncio
async def test_verify_commit_on_main_empty_sha_short_circuits():
    """Empty target_sha → False without subprocess call."""
    from nervous_system import deploy_verifier
    result = await deploy_verifier._verify_commit_on_main("acme", "repo", "")
    assert result is False


# ============================================================================
# Task 11: Vercel verification (target=production filter, CHALLENGE-2)
# ============================================================================


class _FakeResp:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.text = json.dumps(body or {})

    def json(self):
        return self._body


class _FakeAsyncClient:
    """Async context-manager stand-in for httpx.AsyncClient — yields self,
    .get() returns a pre-baked _FakeResp, captures the params for assertion."""
    def __init__(self, resp: _FakeResp):
        self._resp = resp
        self.last_params: dict | None = None
        self.last_url: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.last_url = url
        self.last_params = params
        return self._resp


@pytest.mark.asyncio
async def test_verify_vercel_deploy_match_production():
    """target=production deploy with matching SHA → (True, deploy_url)."""
    from nervous_system import deploy_verifier
    body = {"deployments": [
        {"target": "production", "url": "myapp-abc.vercel.app",
         "meta": {"githubCommitSha": "abc123"}},
    ]}
    fake = _FakeAsyncClient(_FakeResp(200, body))
    with patch.dict(os.environ, {"VERCEL_TOKEN": "tok"}, clear=False), \
         patch.object(deploy_verifier.httpx, "AsyncClient", return_value=fake):
        verified, url = await deploy_verifier._verify_vercel_deploy("proj_x", "abc123")
    assert verified is True
    assert url == "https://myapp-abc.vercel.app"
    # CHALLENGE-2: must filter on target=production
    assert fake.last_params.get("target") == "production"


@pytest.mark.asyncio
async def test_verify_vercel_deploy_preview_only_does_not_satisfy():
    """CHALLENGE-2: a preview-target deploy of the same SHA must NOT satisfy.
    Even if API returned both, the function must reject non-production rows."""
    from nervous_system import deploy_verifier
    body = {"deployments": [
        {"target": "preview", "url": "myapp-preview.vercel.app",
         "meta": {"githubCommitSha": "abc123"}},
    ]}
    fake = _FakeAsyncClient(_FakeResp(200, body))
    with patch.dict(os.environ, {"VERCEL_TOKEN": "tok"}, clear=False), \
         patch.object(deploy_verifier.httpx, "AsyncClient", return_value=fake):
        verified, url = await deploy_verifier._verify_vercel_deploy("proj_x", "abc123")
    assert verified is False
    assert url is None


@pytest.mark.asyncio
async def test_verify_vercel_deploy_sha_mismatch():
    """Production deploy of a different SHA → (False, None)."""
    from nervous_system import deploy_verifier
    body = {"deployments": [
        {"target": "production", "url": "u", "meta": {"githubCommitSha": "OLD"}},
    ]}
    fake = _FakeAsyncClient(_FakeResp(200, body))
    with patch.dict(os.environ, {"VERCEL_TOKEN": "tok"}, clear=False), \
         patch.object(deploy_verifier.httpx, "AsyncClient", return_value=fake):
        verified, _url = await deploy_verifier._verify_vercel_deploy("proj_x", "abc123")
    assert verified is False


@pytest.mark.asyncio
async def test_verify_vercel_deploy_no_token_fails_loud():
    """CAI-RESP-083: VERCEL_TOKEN missing → fail-loud, return False."""
    from nervous_system import deploy_verifier
    with patch.dict(os.environ, {}, clear=True):
        verified, url = await deploy_verifier._verify_vercel_deploy("proj_x", "abc123")
    assert verified is False
    assert url is None


# ============================================================================
# Task 12: Firebase degraded mode
# ============================================================================


def test_verify_firebase_deploy_degraded_returns_true_with_diag():
    """ARCH-FIREBASE-DEPLOY-SHA: degraded mode returns True + diagnostic.
    Caller is responsible for first verifying commit-on-main."""
    from nervous_system.deploy_verifier import _verify_firebase_deploy
    verified, diag = _verify_firebase_deploy("https://myapp.web.app")
    assert verified is True
    assert "firebase-degraded" in diag
    assert "not independently verified" in diag


# ============================================================================
# Task 13: dual-window timeouts
# ============================================================================


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_check_timeouts_case3_within_30min():
    """CASE 3 merged 10 min ago → ok."""
    from nervous_system.deploy_verifier import _check_timeouts
    now = _utc("2026-04-28T10:10:00Z")
    bug = {"job_id": "j1", "verification_started_at": "2026-04-28T09:00:00Z"}
    pr_state = {"merged": True, "merge_commit_sha": "abc",
                "merged_at": "2026-04-28T10:00:00Z", "created_at": "2026-04-28T09:00:00Z"}
    status, _ = _check_timeouts(bug, pr_state, None, now=now)
    assert status == "ok"


def test_check_timeouts_case3_deploy_lag_breach():
    """CASE 3 merged 31 min ago → deploy_lag_timeout."""
    from nervous_system.deploy_verifier import _check_timeouts
    now = _utc("2026-04-28T10:31:00Z")
    bug = {"job_id": "j1"}
    pr_state = {"merged": True, "merge_commit_sha": "abc",
                "merged_at": "2026-04-28T10:00:00Z", "created_at": "2026-04-28T09:00:00Z"}
    status, diag = _check_timeouts(bug, pr_state, None, now=now)
    assert status == "deploy_lag_timeout"
    assert "30 min" in diag


def test_check_timeouts_case2_within_24h():
    """CASE 2 PR open 12h → ok."""
    from nervous_system.deploy_verifier import _check_timeouts
    now = _utc("2026-04-28T21:00:00Z")
    bug = {"job_id": "j1", "verification_started_at": "2026-04-28T09:00:00Z"}
    pr_state = {"merged": False, "merge_commit_sha": None,
                "merged_at": None, "created_at": "2026-04-28T09:00:00Z"}
    status, _ = _check_timeouts(bug, pr_state, None, now=now)
    assert status == "ok"


def test_check_timeouts_case2_pr_open_breach():
    """CASE 2 PR open 25h → pr_open_timeout."""
    from nervous_system.deploy_verifier import _check_timeouts
    now = _utc("2026-04-29T10:00:00Z")
    bug = {"job_id": "j1"}
    pr_state = {"merged": False, "merge_commit_sha": None,
                "merged_at": None, "created_at": "2026-04-28T09:00:00Z"}
    status, diag = _check_timeouts(bug, pr_state, None, now=now)
    assert status == "pr_open_timeout"
    assert "24h" in diag


def test_check_timeouts_case1_no_pr_no_sha():
    """CASE 1 no PR + no last_commit_sha → no_pr_no_sha."""
    from nervous_system.deploy_verifier import _check_timeouts
    now = _utc("2026-04-28T10:00:00Z")
    bug = {"job_id": None}
    status, diag = _check_timeouts(bug, None, None, now=now)
    assert status == "no_pr_no_sha"


# ============================================================================
# Task 14: escalation (UPDATE tombstone + P1 agent_messages + Telegram dedup)
# ============================================================================


class _FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class _FakeQuery:
    """Records each chained call as (method, args, kwargs); execute() returns
    a pre-baked _FakeResult. Mirrors supabase-py PostgrestBuilder shape."""
    def __init__(self, calls: list, exec_result=None):
        self.calls = calls
        self._exec_result = exec_result or _FakeResult()

    def _record(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))
        return self

    def update(self, *a, **kw):  return self._record("update", a, kw)
    def insert(self, *a, **kw):  return self._record("insert", a, kw)
    def select(self, *a, **kw):  return self._record("select", a, kw)
    def eq(self, *a, **kw):      return self._record("eq", a, kw)
    def is_(self, *a, **kw):     return self._record("is_", a, kw)
    def order(self, *a, **kw):   return self._record("order", a, kw)
    def limit(self, *a, **kw):   return self._record("limit", a, kw)

    async def execute(self):
        return self._exec_result


class _FakeSupabase:
    """Builds a fresh _FakeQuery per .table() call, recording all chains in
    self.calls keyed by table name."""
    def __init__(self, results: dict | None = None):
        self.calls: dict[str, list] = {}
        # results: {table_name: _FakeResult-or-list}
        self._results = results or {}

    def table(self, name: str):
        chain = self.calls.setdefault(name, [])
        result = self._results.get(name)
        if isinstance(result, list) and result:
            # pop one per .table() call (so different chained reads can return
            # different data on the same table)
            r = result.pop(0)
            return _FakeQuery(chain, r if isinstance(r, _FakeResult) else _FakeResult(r))
        if isinstance(result, _FakeResult):
            return _FakeQuery(chain, result)
        return _FakeQuery(chain, _FakeResult())


@pytest.mark.asyncio
async def test_escalate_bug_tombstones_and_p1_messages():
    """AC: escalation must (1) UPDATE bug_reports with verification_escalated_at +
    diagnostic, (2) INSERT P1 agent_messages addressed to cai with announce_to_agent."""
    from nervous_system import deploy_verifier
    sb = _FakeSupabase()
    bug = {"id": "00000000-0000-0000-0000-000000000001",
           "repo_name": "ihsandms", "verification_started_at": "2026-04-28T09:00:00Z",
           "job_id": "job-1"}
    await deploy_verifier._escalate_bug(sb, bug, "deploy-lag 45 min since pr.mergedAt")

    # bug_reports UPDATE chain
    br_calls = sb.calls.get("bug_reports", [])
    assert any(c[0] == "update" for c in br_calls), f"missing update on bug_reports: {br_calls}"
    update_payload = next(c[1][0] for c in br_calls if c[0] == "update")
    assert "verification_escalated_at" in update_payload
    assert "verification_diagnostic" in update_payload

    # agent_messages INSERT chain
    am_calls = sb.calls.get("agent_messages", [])
    assert any(c[0] == "insert" for c in am_calls), f"missing insert on agent_messages: {am_calls}"
    insert_payload = next(c[1][0] for c in am_calls if c[0] == "insert")
    assert insert_payload["to_agent"] == "cai"
    assert insert_payload["priority"] == "P1"
    assert insert_payload["from_agent"] == "cc-orchestrator"


@pytest.mark.asyncio
async def test_escalate_bug_telegram_dedup_skips_when_logged():
    """notification_log already has dedup_key → bot.send_message NOT called."""
    from nervous_system import deploy_verifier
    bug = {"id": "00000000-0000-0000-0000-000000000002",
           "repo_name": "ihsandms", "verification_started_at": None, "job_id": None}
    sb = _FakeSupabase(results={
        "notification_log": [_FakeResult(data=[{"id": "existing"}])]
    })
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    await deploy_verifier._escalate_bug(sb, bug, "diag", bot=bot, musa_chat_id="123")
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_escalate_bug_skips_p1_if_tombstone_fails():
    """If UPDATE tombstone fails, do NOT INSERT P1 — would re-fire next tick.

    Use a supabase double whose UPDATE chain raises on .execute()."""
    from nervous_system import deploy_verifier

    class _FailingUpdateQuery(_FakeQuery):
        async def execute(self):
            raise RuntimeError("simulated tombstone write failure")

    class _FailingSupabase:
        def __init__(self):
            self.tables_touched: list[str] = []

        def table(self, name):
            self.tables_touched.append(name)
            if name == "bug_reports":
                return _FailingUpdateQuery([])
            return _FakeQuery([])

    sb = _FailingSupabase()
    bug = {"id": "x", "repo_name": "r", "verification_started_at": None, "job_id": None}
    await deploy_verifier._escalate_bug(sb, bug, "diag")
    assert "agent_messages" not in sb.tables_touched, \
        "P1 must NOT fan out when tombstone failed"


# ============================================================================
# Task 15: run_deploy_verifier (env-flag gate + 3-case dispatch)
# ============================================================================


@pytest.mark.asyncio
async def test_run_deploy_verifier_disabled_by_default():
    """CAI-RESP-080 CHALLENGE-3: ORCHESTRATOR_VERIFY_ENABLED unset/false → no-op."""
    from nervous_system import deploy_verifier
    sb = _FakeSupabase()
    with patch.dict(os.environ, {}, clear=True):
        await deploy_verifier.run_deploy_verifier(sb)
    assert sb.calls == {}, "must not query DB when verifier disabled"


@pytest.mark.asyncio
async def test_run_deploy_verifier_enabled_queries_pending():
    """When enabled, query is issued on bug_reports with the pending predicate."""
    from nervous_system import deploy_verifier
    sb = _FakeSupabase(results={"bug_reports": _FakeResult(data=[])})
    with patch.dict(os.environ, {"ORCHESTRATOR_VERIFY_ENABLED": "true"}, clear=False):
        await deploy_verifier.run_deploy_verifier(sb)
    br_calls = sb.calls.get("bug_reports", [])
    methods = [c[0] for c in br_calls]
    assert "select" in methods
    # must filter on status=pr_open
    eq_args = [c[1] for c in br_calls if c[0] == "eq"]
    assert any(args == ("status", "pr_open") for args in eq_args), f"eq calls: {eq_args}"


@pytest.mark.asyncio
async def test_run_deploy_verifier_per_bug_isolation():
    """Per-bug exception must not abort the sweep — failing bug logs and the
    rest of the batch still processes."""
    from nervous_system import deploy_verifier
    sb = _FakeSupabase(results={"bug_reports": _FakeResult(data=[
        {"id": "bug-1", "repo_name": "r1", "job_id": None, "created_at": "2026-04-28T00:00:00Z",
         "verification_started_at": None},
        {"id": "bug-2", "repo_name": "r2", "job_id": None, "created_at": "2026-04-28T00:00:00Z",
         "verification_started_at": None},
    ])})
    call_count = {"n": 0}

    async def fake_verify(supabase, bug, **kw):
        call_count["n"] += 1
        if bug["id"] == "bug-1":
            raise RuntimeError("simulated per-bug failure")
        return "wait"

    with patch.dict(os.environ, {"ORCHESTRATOR_VERIFY_ENABLED": "true"}, clear=False), \
         patch.object(deploy_verifier, "_verify_one_bug", side_effect=fake_verify):
        await deploy_verifier.run_deploy_verifier(sb)
    assert call_count["n"] == 2, "second bug must process despite first raising"
