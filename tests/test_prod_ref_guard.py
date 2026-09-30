"""Prod-ref refusal guard (root conftest.py) — pure-function proof (backlog#68 #46576).

The guard (pytest_configure -> pytest.exit) can't be asserted in-process without aborting
the session, and on a fleet host whose .env holds the prod DSN a bare pytest self-aborts,
so the DECISION logic (effective_dsn / prod_ref) is pure and tested here. PRECEDENCE
(#46576): env var wins; .env file only when the env var is unset. Cases:
  env = local non-prod DSN            -> allowed (and .env is NOT consulted)
  env unset + .env file = prod        -> refused
  env = prod DSN                      -> refused
Runnable via plain python (`python tests/test_prod_ref_guard.py`) AND pytest.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("_root_conftest", _ROOT / "conftest.py")
_c = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_c)


def test_env_local_dsn_is_allowed_and_env_file_not_consulted():
    # env var set to a LOCAL dsn -> effective is that dsn; .env (even if prod) is NOT read.
    sentinel = {"called": False}
    orig = _c._env_file_dsn
    _c._env_file_dsn = lambda: (sentinel.__setitem__("called", True) or "postgresql://x@h/tscuymavysscrvoberrr")
    try:
        dsn = _c.effective_dsn({"DATABASE_URL": "postgresql://postgres@localhost:5432/wingmen_test"})
        assert dsn == "postgresql://postgres@localhost:5432/wingmen_test"
        assert _c.prod_ref(dsn) is None
        assert sentinel["called"] is False, "env-var-set must short-circuit the .env file"
    finally:
        _c._env_file_dsn = orig


def test_env_unset_falls_back_to_env_file_and_prod_is_refused():
    orig = _c._env_file_dsn
    _c._env_file_dsn = lambda: "postgresql://u:p@aws-1.pooler.supabase.com/postgres?options=project%3Dtscuymavysscrvoberrr"
    try:
        dsn = _c.effective_dsn({})  # env unset
        assert _c.prod_ref(dsn) == "tscuymavysscrvoberrr"
    finally:
        _c._env_file_dsn = orig


def test_env_prod_dsn_is_refused():
    assert _c.prod_ref(_c.effective_dsn({"DATABASE_URL": "postgresql://x@h/ceayjeamtmcyzzvqflus"})) == "ceayjeamtmcyzzvqflus"
    assert _c.prod_ref(_c.effective_dsn({"SUPABASE_DB_URL": "postgresql://x@h/goumlynecruxrlmzlntp"})) == "goumlynecruxrlmzlntp"


def test_env_unset_no_env_file_is_allowed():
    orig = _c._env_file_dsn
    _c._env_file_dsn = lambda: None
    try:
        assert _c.effective_dsn({}) is None
        assert _c.prod_ref(None) is None
    finally:
        _c._env_file_dsn = orig


# ── scripts/pytest_local.sh wrapper (bug #46781): DATABASE_URL unset + prod .env must ALLOW ──
# These drive the REAL wrapper + REAL conftest end-to-end in a synthetic repo, so they need
# pytest's tmp_path fixture (the plain-python __main__ runner below skips them).

def _synth_repo(tmp_path, env_file_dsn):
    """A minimal repo: the real conftest + wrapper, a prod .env, a DB-gated dummy test, and
    a .venv symlink so the wrapper's hard-coded `.venv/bin/python3` resolves to THIS venv."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    shutil.copy(_ROOT / "conftest.py", tmp_path / "conftest.py")
    shutil.copy(_ROOT / "scripts" / "pytest_local.sh", tmp_path / "scripts" / "pytest_local.sh")
    os.chmod(tmp_path / "scripts" / "pytest_local.sh", 0o755)
    (tmp_path / ".env").write_text(f"DATABASE_URL={env_file_dsn}\n")
    # a DB-gated test that FAILS if it is NOT skipped — proves DB-requiring tests skip.
    (tmp_path / "tests" / "test_dummy_db.py").write_text(
        "import os\n"
        "import pytest\n"
        "_DSN = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')\n"
        "@pytest.mark.skipif(not _DSN, reason='no DATABASE_URL')\n"
        "def test_needs_db():\n"
        "    raise AssertionError('DB test ran — it should have been skipped')\n")
    venv_root = os.path.dirname(os.path.dirname(sys.executable))  # <venv>/bin/python -> <venv>
    os.symlink(venv_root, tmp_path / ".venv")


def _run_wrapper(tmp_path, extra_env=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("DATABASE_URL", "SUPABASE_DB_URL", "PYTEST_NO_DB")}
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(
        ["bash", str(tmp_path / "scripts" / "pytest_local.sh"), "tests/test_dummy_db.py", "-q"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True)
    return r, (r.stdout + r.stderr)


_PROD_ENV_DSN = ("postgresql://u:p@aws-1.pooler.supabase.com/postgres"
                 "?options=project%3Dtscuymavysscrvoberrr")


def test_wrapper_env_unset_with_prod_env_file_is_allowed_and_db_tests_skip(tmp_path):
    # bug #46781: DATABASE_URL UNSET on a fleet host whose .env holds the prod DSN. The
    # wrapper must ALLOW (not fall back to the prod .env and REFUSE) and DB tests must SKIP.
    _synth_repo(tmp_path, _PROD_ENV_DSN)
    r, out = _run_wrapper(tmp_path)  # DATABASE_URL unset in the child env
    assert r.returncode != 2, f"wrapper REFUSED (rc=2) — bug #46781 not fixed:\n{out}"
    assert "REFUSED" not in out, f"guard refused the run:\n{out}"
    assert "1 skipped" in out, f"DB test did not skip (guard/skip broken):\n{out}"


def test_wrapper_does_not_weaken_real_prod_refusal(tmp_path):
    # The other side: an EXPLICITLY exported prod DSN (env var wins) must STILL be REFUSED.
    _synth_repo(tmp_path, _PROD_ENV_DSN)
    r, out = _run_wrapper(tmp_path, extra_env={"DATABASE_URL": _PROD_ENV_DSN})
    assert r.returncode == 2, f"real-prod refusal was weakened (rc={r.returncode}):\n{out}"
    assert "REFUSED" in out and "tscuymavysscrvoberrr" in out, out


if __name__ == "__main__":
    import inspect
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)
           and not inspect.signature(v).parameters]  # skip fixture-taking tests
    ok = 0
    for fn in fns:
        try:
            fn(); ok += 1; print(f"PASS {fn.__name__}")
        except Exception:
            print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"{ok}/{len(fns)} passed")
    raise SystemExit(0 if ok == len(fns) else 1)
