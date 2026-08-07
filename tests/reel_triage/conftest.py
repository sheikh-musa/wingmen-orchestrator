import os
import pathlib
import shutil
import subprocess
import tempfile
import time

import psycopg
import pytest
from psycopg.rows import dict_row

PG_BIN = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")
PORT = "54330"  # distinct from tests/migrations (54329) to avoid cluster collision

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION_SQL = (_REPO_ROOT / "migrations" / "001_reel_inbox.sql").read_text()


def _bin(name: str) -> str:
    path = os.path.join(PG_BIN, name)
    if not os.path.exists(path):
        pytest.skip(f"PG17 binary missing: {path} (set WINGMEN_PG17_BIN)")
    return path


@pytest.fixture(scope="session")
def pg_dsn():
    datadir = tempfile.mkdtemp(prefix="wingmen-reeltest-")
    shutil.rmtree(datadir)  # initdb wants to create it
    sockdir = tempfile.mkdtemp(prefix="wingmen-reelsock-")
    subprocess.run(
        [_bin("initdb"), "-D", datadir, "-U", "postgres",
         "--auth=trust", "--locale=C", "--encoding=UTF8"],
        check=True, capture_output=True,
    )
    subprocess.run(
        [_bin("pg_ctl"), "-D", datadir, "-l", os.path.join(datadir, "log"),
         "-o", f"-p {PORT} -k {sockdir} -c listen_addresses='' "
               f"-c timezone=UTC -c log_timezone=UTC",
         "-w", "start"],
        check=True, capture_output=True,
    )
    dsn = f"host={sockdir} port={PORT} user=postgres dbname=postgres"
    for _ in range(50):
        try:
            with psycopg.connect(dsn):
                break
        except psycopg.OperationalError:
            time.sleep(0.1)
    try:
        yield dsn
    finally:
        subprocess.run([_bin("pg_ctl"), "-D", datadir, "-w", "stop"],
                       capture_output=True)
        shutil.rmtree(datadir, ignore_errors=True)
        shutil.rmtree(sockdir, ignore_errors=True)


@pytest.fixture
def fresh_db(pg_dsn):
    """A clean public schema per test."""
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
    return pg_dsn


@pytest.fixture
def reel_db(fresh_db):
    """Clean schema with migrations/001_reel_inbox.sql applied (dict rows)."""
    conn = psycopg.connect(fresh_db, autocommit=True, row_factory=dict_row)
    with conn.cursor() as cur:
        cur.execute(MIGRATION_SQL)
    yield conn
    conn.close()
