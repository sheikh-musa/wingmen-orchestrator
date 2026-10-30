"""scripts/backlog_done_checker.py — evidence-driven auto-complete for
operator_backlog (op#9187/9190). Everything is mocked: no real DB, no real
HTTP, no real `gh` call, and never a fall-through to _dsn()/main() (which would
read the repo's real .env). Each signal evaluator and check_once take `conn`
as a parameter, so they're exercised directly with fakes passed in."""
import importlib
from types import SimpleNamespace

bdc = importlib.import_module("scripts.backlog_done_checker")


# ── fakes ─────────────────────────────────────────────────────────────────────
class _FakeCursor:
    """Shares one store dict with its FakeConn so every execute()/fetch across
    the multiple `with conn.cursor() as cur` blocks in check_once is visible."""

    def __init__(self, store):
        self.store = store

    def execute(self, sql, params=None):
        self.store["executes"].append((sql, params))

    def fetchall(self):
        return self.store["rows"]

    def fetchone(self):
        return self.store["one"]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows=None, one=None):
        self.store = {"executes": [], "rows": rows or [], "one": one}
        self.commits = 0

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        self.commits += 1


class _RaisingConn:
    def cursor(self):
        raise RuntimeError("DB down")


def _urlopen_returning(status):
    class _Resp:
        def __init__(self):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=None):
        return _Resp()

    return _urlopen


# ── _sig_deploy_live ────────────────────────────────────────────────────────
def test_deploy_live_met_on_matching_status(monkeypatch):
    monkeypatch.setattr(bdc.urllib.request, "urlopen", _urlopen_returning(200))
    met, ev = bdc._sig_deploy_live({"url": "https://x.test/"})
    assert met is True
    assert "200" in ev


def test_deploy_live_met_on_custom_expect_status(monkeypatch):
    monkeypatch.setattr(bdc.urllib.request, "urlopen", _urlopen_returning(204))
    met, _ = bdc._sig_deploy_live({"url": "https://x.test/", "expect_status": 204})
    assert met is True


def test_deploy_live_not_met_on_status_mismatch(monkeypatch):
    monkeypatch.setattr(bdc.urllib.request, "urlopen", _urlopen_returning(503))
    met, ev = bdc._sig_deploy_live({"url": "https://x.test/"})
    assert met is False
    assert "503" in ev


def test_deploy_live_not_met_when_get_raises(monkeypatch):
    def _boom(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(bdc.urllib.request, "urlopen", _boom)
    met, ev = bdc._sig_deploy_live({"url": "https://x.test/"})
    assert met is False
    assert "error" in ev


def test_deploy_live_not_met_when_no_url():
    met, ev = bdc._sig_deploy_live({})
    assert met is False
    assert "no url" in ev


# ── _sig_pr_merged ────────────────────────────────────────────────────────────
def test_pr_merged_met_when_merged(monkeypatch):
    def _run(cmd, **k):
        return SimpleNamespace(
            returncode=0, stdout='{"state":"MERGED","mergedAt":"2026-10-30T00:00:00Z"}')

    monkeypatch.setattr(bdc.subprocess, "run", _run)
    met, ev = bdc._sig_pr_merged({"repo": "o/r", "pr": 7})
    assert met is True
    assert "MERGED" in ev


def test_pr_merged_not_met_when_open(monkeypatch):
    def _run(cmd, **k):
        return SimpleNamespace(returncode=0, stdout='{"state":"OPEN","mergedAt":null}')

    monkeypatch.setattr(bdc.subprocess, "run", _run)
    met, ev = bdc._sig_pr_merged({"repo": "o/r", "pr": 7})
    assert met is False
    assert "OPEN" in ev


def test_pr_merged_not_met_when_merged_state_but_no_mergedAt(monkeypatch):
    # Defence in depth: state=MERGED but a falsy mergedAt must NOT auto-complete.
    def _run(cmd, **k):
        return SimpleNamespace(returncode=0, stdout='{"state":"MERGED","mergedAt":null}')

    monkeypatch.setattr(bdc.subprocess, "run", _run)
    met, _ = bdc._sig_pr_merged({"repo": "o/r", "pr": 7})
    assert met is False


def test_pr_merged_not_met_on_nonzero_gh_exit(monkeypatch):
    def _run(cmd, **k):
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(bdc.subprocess, "run", _run)
    met, ev = bdc._sig_pr_merged({"repo": "o/r", "pr": 7})
    assert met is False
    assert "rc=1" in ev


def test_pr_merged_not_met_when_gh_call_raises(monkeypatch):
    def _run(cmd, **k):
        raise FileNotFoundError("gh not installed")

    monkeypatch.setattr(bdc.subprocess, "run", _run)
    met, ev = bdc._sig_pr_merged({"repo": "o/r", "pr": 7})
    assert met is False
    assert "error" in ev


def test_pr_merged_not_met_when_missing_repo_or_pr():
    assert bdc._sig_pr_merged({"repo": "o/r"})[0] is False
    assert bdc._sig_pr_merged({"pr": 7})[0] is False


# ── _sig_deploy_stage ─────────────────────────────────────────────────────────
def test_deploy_stage_met_when_stage_live():
    conn = _FakeConn(one=("live",))
    met, ev = bdc._sig_deploy_stage(conn, {"workstream": "ihsanos"})
    assert met is True
    assert "live" in ev


def test_deploy_stage_not_met_when_stage_other():
    conn = _FakeConn(one=("building",))
    met, ev = bdc._sig_deploy_stage(conn, {"workstream": "ihsanos"})
    assert met is False
    assert "building" in ev


def test_deploy_stage_not_met_when_no_row():
    conn = _FakeConn(one=None)
    met, ev = bdc._sig_deploy_stage(conn, {"workstream": "ihsanos"})
    assert met is False
    assert "None" in ev


def test_deploy_stage_not_met_when_query_raises():
    met, ev = bdc._sig_deploy_stage(_RaisingConn(), {"workstream": "ihsanos"})
    assert met is False
    assert "error" in ev


def test_deploy_stage_not_met_when_no_workstream():
    met, ev = bdc._sig_deploy_stage(_FakeConn(), {})
    assert met is False
    assert "no workstream" in ev


# ── _evaluate: dispatch + the never-auto-complete types ───────────────────────
def test_evaluate_dispatches_to_deploy_live(monkeypatch):
    monkeypatch.setattr(bdc.urllib.request, "urlopen", _urlopen_returning(200))
    met, _ = bdc._evaluate(None, {"type": "deploy_live", "url": "https://x.test/"})
    assert met is True


def test_evaluate_dispatches_to_pr_merged(monkeypatch):
    monkeypatch.setattr(
        bdc.subprocess, "run",
        lambda cmd, **k: SimpleNamespace(
            returncode=0, stdout='{"state":"MERGED","mergedAt":"2026-10-30T00:00:00Z"}'))
    met, _ = bdc._evaluate(None, {"type": "pr_merged", "repo": "o/r", "pr": 7})
    assert met is True


def test_evaluate_dispatches_to_deploy_stage():
    met, _ = bdc._evaluate(_FakeConn(one=("live",)), {"type": "deploy_stage", "workstream": "x"})
    assert met is True


def test_evaluate_not_met_for_unknown_manual_and_missing_type():
    # None of these ever auto-complete, regardless of which evaluator exists.
    for sig in ({"type": "manual"}, {"type": "something_new"}, {}, None):
        met, ev = bdc._evaluate(None, sig)
        assert met is False
        assert "unsupported/manual" in ev


# ── check_once: loop skips manual, updates only met non-manual rows ───────────
def _rows():
    # (id, ask, done_signal) tuples exactly as the SELECT would return them.
    return [
        (1, "ship A", {"type": "deploy_live", "met": True}),
        (2, "ship B", {"type": "pr_merged", "met": False}),
        (3, "curated C", {"type": "manual"}),
    ]


def _patch_evaluate(monkeypatch):
    """Record which signals get evaluated; a signal's own `met` flag decides
    the result. Lets us assert the manual row is never passed to _evaluate."""
    seen = []

    def _fake(conn, sig):
        seen.append(sig)
        return bool((sig or {}).get("met")), "fake-evidence"

    monkeypatch.setattr(bdc, "_evaluate", _fake)
    return seen


def _updates(conn):
    return [e for e in conn.store["executes"] if "UPDATE operator_backlog" in e[0]]


def test_check_once_updates_only_met_non_manual_row(monkeypatch):
    seen = _patch_evaluate(monkeypatch)
    conn = _FakeConn(rows=_rows())
    completed = bdc.check_once(conn, dry_run=False)

    assert completed == 1
    ups = _updates(conn)
    assert len(ups) == 1
    assert ups[0][1][1] == 1  # the id bound into the UPDATE is the met row's id
    assert conn.commits == 1


def test_check_once_never_evaluates_the_manual_row(monkeypatch):
    seen = _patch_evaluate(monkeypatch)
    bdc.check_once(_FakeConn(rows=_rows()), dry_run=False)
    types = [s.get("type") for s in seen]
    assert "manual" not in types
    assert types == ["deploy_live", "pr_merged"]


def test_check_once_dry_run_issues_no_update_but_still_counts(monkeypatch):
    _patch_evaluate(monkeypatch)
    conn = _FakeConn(rows=_rows())
    completed = bdc.check_once(conn, dry_run=True)

    assert completed == 1  # it still counts what it WOULD complete
    assert _updates(conn) == []
    assert conn.commits == 0


def test_check_once_skips_rows_with_null_signal(monkeypatch):
    seen = _patch_evaluate(monkeypatch)
    rows = [(1, "no signal", None), (2, "met", {"type": "deploy_live", "met": True})]
    conn = _FakeConn(rows=rows)
    completed = bdc.check_once(conn, dry_run=False)

    assert completed == 1
    assert [s.get("type") for s in seen] == ["deploy_live"]  # the None row never evaluated
