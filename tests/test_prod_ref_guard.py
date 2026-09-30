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


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); ok += 1; print(f"PASS {fn.__name__}")
        except Exception:
            print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"{ok}/{len(fns)} passed")
    raise SystemExit(0 if ok == len(fns) else 1)
