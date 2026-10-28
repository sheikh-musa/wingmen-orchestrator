"""hc#95 exec-broker is_plain_read classifier + read-only-override check
(orch-console GO #78974, assignment bus #79024): while the bounded ref
override is live, the broker refuses any guarded_sql that is not a plain
read — fail-closed. The classifier reuses the vendored ddl_detect lexer
(strip_sql_noise/extract_dollar_bodies/strip_comments_only) and
is_ddl_shaped itself; it never re-derives the lexing and never edits the
sha-pinned vendored file."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

from exec_broker.read_only import is_plain_read  # noqa: E402


@pytest.mark.parametrize("sql", [
    "select 1",
    "SELECT count(*) FROM t WHERE x = 1",
    "with x as (select id from t) select count(*) from x",          # CTE
    "SELECT md5(body), current_setting('server_version') FROM t",   # read-only functions
    "TABLE t",
    "VALUES (1), (2)",
    "SELECT CASE WHEN a THEN 1 ELSE 2 END FROM t",                  # END is not denied (CASE...END)
    "SELECT * FROM t ORDER BY id OFFSET 5 LIMIT 2",                 # OFFSET does not trip a SET scan
    "select 1; select 2",                                           # multi-statement, all reads
    "(SELECT 1) UNION (SELECT 2)",                                  # parenthesized heads
    "SELECT 'insert into t values (1)' FROM t",                     # write words in a string -> blanked
    "-- delete from t\nSELECT 1",                                   # write words in a comment -> blanked
    'SELECT "set" FROM t',                                          # quoted column named set (no SET token in code)
    "SELECT do FROM t",                                             # unreserved-word column named do
])
def test_is_plain_read_accepts(sql):
    assert is_plain_read(sql) is True


@pytest.mark.parametrize("sql", [
    "insert into t values (1)",
    "UPDATE t SET x = 1",
    "delete from t",
    "DO $$ BEGIN UPDATE t SET x = 1; END $$",                        # DO-with-DML (assignment case)
    "SELECT set_config('a', 'b', false)",                            # write via function
    "SELECT nextval('exec_broker_access_log_id_seq')",               # sequence state write
    "SELECT setval('s', 1)",
    "SELECT pg_terminate_backend(123)",
    "SELECT dblink_exec('c1', 'insert into t values (1)')",
    "SELECT * INTO new_table FROM t",                                # table creation as a read
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",         # data-modifying CTE
    "SELECT * FROM t FOR UPDATE",                                    # locking read
    "SET work_mem = '1GB'",
    "LOCK TABLE t",
    "EXPLAIN SELECT 1",                                              # fail-closed: not a whitelisted head
    "COPY t FROM STDIN",
    "select 1; set x = 1",                                           # one write poisons the batch
    "CALL p()",
    "", "   ",                                                       # empty is never a plain read
    "SELECT $$insert into t values (1)$$",                           # dollar-quoted string constant -- false refusal, fail-closed
    'SELECT "update" FROM t',                                        # quoted column named update -- false refusal, fail-closed
])
def test_is_plain_read_refuses(sql):
    assert is_plain_read(sql) is False
