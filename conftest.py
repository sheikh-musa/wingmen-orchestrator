"""Repo-root conftest — PROD-REF REFUSAL GUARD (backlog#68, Nazim #46564/#46566/#46576).

SAFETY-CRITICAL, no opt-out. A pytest session refuses to start if the EFFECTIVE
DATABASE_URL points at any known PRODUCTION store. This fires in BOTH local runs AND
CI: the 2026-09-28/29 substrate pooler lockout was a LOCAL `pytest tests/` run whose
stale-password env hammered the prod substrate, and the CI job had been exporting the
prod DATABASE_URL secret to the whole test run. Tests must only ever touch an
ephemeral/local database; a prod DSN is a hard refuse.

PRECEDENCE (#46576): resolve ENV-VAR FIRST. If DATABASE_URL (or SUPABASE_DB_URL) is
SET in the environment, that is the effective DSN and the .env file is NOT consulted —
so a lane that exports a LOCAL DSN for TDD is ALLOWED even though the fleet .env holds
the prod DSN. ONLY when the env var is unset do we fall back to the .env file value
(the rotation's single push-point, per #214). Net:
  env = local DSN            -> ALLOW (never reads .env)
  env unset + .env = prod    -> REFUSE
  env = prod DSN             -> REFUSE
  env unset + no .env / local -> ALLOW (DB-integration tests skip as before)
  env unset + PYTEST_NO_DB=1 -> ALLOW (guard skips the .env fallback; DB tests skip)

TWO HARDENINGS (bus #46781 / #46880):
  * PYTEST_NO_DB opt-out: `scripts/pytest_local.sh` exports it when DATABASE_URL is
    unset, so a fleet host whose .env holds the prod DSN can still run pure unit tests
    (guard ALLOWS, DB-requiring tests SKIP) instead of the whole session self-aborting.
  * DSN-key seal + mid-session re-check: many test modules call `load_dotenv()` at
    import time. python-dotenv's load_dotenv(override=False) never overwrites a key
    that is already PRESENT — even when empty — so we place an empty placeholder for the
    DSN keys at session start, and a module's import-time load_dotenv can no longer leak
    the prod .env DSN into os.environ and change the DSN another test module sees. A key
    the operator EXPLICITLY exported is left untouched. `pytest_runtest_setup` re-verifies
    the effective DSN before EVERY test as a fail-closed belt against any residual leak.
"""
import os

# The three production stores (docs/data-store-registry.md). A DSN containing any of
# these project refs is prod — never a test target.
_PROD_REFS = (
    "tscuymavysscrvoberrr",   # orchestrator substrate
    "ceayjeamtmcyzzvqflus",   # ihsanos multi-tenant DB
    "goumlynecruxrlmzlntp",   # irsyad silo (goumlyne)
)

# SEAL the DSN env keys at session start (this runs at conftest import — BEFORE any test
# module's import-time load_dotenv). An empty placeholder is PRESENT, so load_dotenv(
# override=False) will not overwrite it with the prod .env value: importing one test
# module can no longer change the DSN another module reads (bus #46880). setdefault leaves
# an EXPLICITLY-exported local DSN untouched (the sanctioned TDD path), and an empty
# placeholder is falsy — effective_dsn() still falls through to the .env file when neither
# PYTEST_NO_DB nor an explicit env var is set, so the real-prod refusal is NOT weakened.
for _dsn_key in ("DATABASE_URL", "SUPABASE_DB_URL"):
    os.environ.setdefault(_dsn_key, "")


def _env_dsn(environ):
    """The DSN from the ENVIRONMENT (DATABASE_URL wins over SUPABASE_DB_URL), or None."""
    return environ.get("DATABASE_URL") or environ.get("SUPABASE_DB_URL") or None


def _env_file_dsn():
    """The first DATABASE_URL / SUPABASE_DB_URL from the repo's .env file, or None."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
                    return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    except (FileNotFoundError, OSError):
        pass
    return None


def effective_dsn(environ=None):
    """The ONE DSN a test run will actually use: ENV-VAR FIRST (DATABASE_URL /
    SUPABASE_DB_URL); only if the env var is UNSET, the .env file — UNLESS PYTEST_NO_DB
    is set, in which case the .env fallback is skipped and the result is None (guard
    ALLOWS, DB-requiring tests skip). Pure + injectable so it's unit-testable. Returns
    None when neither source yields a DSN."""
    environ = os.environ if environ is None else environ
    env = _env_dsn(environ)
    if env:
        return env
    # env var unset: an explicit no-DB opt-out (scripts/pytest_local.sh) suppresses the
    # .env fallback so a prod-.env fleet host can still run pure unit tests (bus #46781).
    if environ.get("PYTEST_NO_DB"):
        return None
    return _env_file_dsn()


def prod_ref(dsn):
    """The prod store ref contained in `dsn`, or None. Never returns the full DSN
    (it carries a password)."""
    if not dsn:
        return None
    for ref in _PROD_REFS:
        if ref in dsn:
            return ref
    return None


def pytest_configure(config):
    ref = prod_ref(effective_dsn())
    if ref:
        import pytest
        pytest.exit(
            f"REFUSED: the effective DATABASE_URL points at the PRODUCTION store "
            f"'{ref}'. Tests must NEVER run against prod (backlog#68 / bus #46566 — a "
            f"local run once tripped the substrate pooler circuit breaker). Export "
            f"DATABASE_URL to a LOCAL/ephemeral Postgres (see scripts/pytest_local.sh) "
            f"and re-run. No opt-out.",
            returncode=2,
        )


def pytest_runtest_setup(item):
    """Fail-closed belt (bus #46880): re-verify the effective DSN before EVERY test, not
    only at session start. A test module's import-time load_dotenv can mutate os.environ
    mid-collection; if a prod DSN leaked in after pytest_configure, abort here so no test
    silently touches prod. The DSN-key seal above should prevent the leak; this catches
    anything that slips past it. No opt-out."""
    ref = prod_ref(effective_dsn())
    if ref:
        import pytest
        pytest.exit(
            f"REFUSED (mid-session): the effective DATABASE_URL now points at the "
            f"PRODUCTION store '{ref}'. An import-time env mutation leaked a prod DSN "
            f"AFTER session start (bus #46880). Aborting so no test touches prod. Export "
            f"a LOCAL DATABASE_URL (see scripts/pytest_local.sh) and re-run. No opt-out.",
            returncode=2,
        )
