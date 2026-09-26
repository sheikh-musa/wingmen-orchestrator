import os
import shutil
import socket
import subprocess
import tempfile
import time

import psycopg
import pytest

PG_BIN = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")

# Force a C locale for initdb/pg_ctl. On macOS, spawning the postmaster under a
# non-C locale can trip "FATAL: postmaster became multithreaded during startup"
# (the getaddrinfo/locale path goes multithreaded before fork), which aborts the
# ephemeral cluster and made this harness fail machine-wide (bus #43331/#43345).
# The CAI-1342 throwaway-pg method sets LC_ALL=C for exactly this reason.
_PG_ENV = {**os.environ, "LC_ALL": "C", "LANG": "C"}


def _free_port() -> str:
    """Pick a currently-free TCP port. The cluster runs socket-only
    (listen_addresses=''), so this only discriminates the socket filename and
    avoids any fixed-port clash with a local service (e.g. a dev pg on 54329)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return str(s.getsockname()[1])


def _bin(name: str) -> str:
    path = os.path.join(PG_BIN, name)
    if not os.path.exists(path):
        pytest.skip(f"PG17 binary missing: {path} (set WINGMEN_PG17_BIN)")
    return path


@pytest.fixture(scope="session")
def pg_dsn():
    datadir = tempfile.mkdtemp(prefix="wingmen-pgtest-")
    shutil.rmtree(datadir)  # initdb wants to create it
    sockdir = tempfile.mkdtemp(prefix="wingmen-pgsock-")
    port = _free_port()
    subprocess.run(
        [_bin("initdb"), "-D", datadir, "-U", "postgres",
         "--auth=trust", "--locale=C", "--encoding=UTF8"],
        check=True, capture_output=True, env=_PG_ENV,
    )
    subprocess.run(
        [_bin("pg_ctl"), "-D", datadir, "-l", os.path.join(datadir, "log"),
         "-o", f"-p {port} -k {sockdir} -c listen_addresses='' "
               f"-c timezone=UTC -c log_timezone=UTC",
         "-w", "start"],
        check=True, capture_output=True, env=_PG_ENV,
    )
    dsn = f"host={sockdir} port={port} user=postgres dbname=postgres"
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
                       capture_output=True, env=_PG_ENV)
        shutil.rmtree(datadir, ignore_errors=True)
        shutil.rmtree(sockdir, ignore_errors=True)


@pytest.fixture
def fresh_db(pg_dsn):
    """A clean public schema per test."""
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        # PG15+ does not re-grant USAGE on a recreated public schema; prod's
        # public schema grants it, so restore the default or non-superuser
        # roles see "relation does not exist" instead of the table.
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
        cur.execute('DROP ROLE IF EXISTS "cc-ihsanos"')
        cur.execute('DROP ROLE IF EXISTS "musa"')
    return pg_dsn
