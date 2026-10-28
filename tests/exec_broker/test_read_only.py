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

from exec_broker.gate_validation import BrokerValidationError  # noqa: E402
from exec_broker.protocol import ErrorCode  # noqa: E402
from exec_broker.read_only import check_read_only_override, is_plain_read  # noqa: E402


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
    "SELECT \"col one\", \"col two\" FROM t",                       # clean quoted identifiers: no hazard chars inside
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
    # --- whole-branch review Critical #1 (2026-10-28): legal SQL that BLINDS the
    # vendored lexer -- a ', --, /* or $tag$ INSIDE a double-quoted identifier
    # opens a bogus noise region that swallows the real code, and an E'' escape
    # closes a string early, so denylisted writes vanish from every scan. The
    # hazard pre-screen refuses the whole input class.
    "SELECT 1 AS \"it's\", set_config('a', 'b', false)",             # ' inside quoted alias -> bogus string
    "SELECT 1 AS \"a--b\", nextval('exec_broker_access_log_id_seq')",  # -- inside quoted alias eats the line
    "SELECT \"x--y\"; DELETE FROM t",                                # also hides the ';' from the head split
    "SELECT \"a/*b\", pg_terminate_backend(123)",                    # bogus block comment swallows to EOF
    "SELECT E'\\'' AS x, set_config('a', 'b', false)",               # \' closes the lexer's string early
    # same class, benign payloads -- documented false refusals (fail-closed):
    "SELECT \"it's\" FROM t",                                        # legal apostrophe identifier
    "SELECT 'a\\b' FROM t",                                          # backslash in a plain string
    "SELECT E'\\x41' FROM t",                                        # E-string escape, harmless
])
def test_is_plain_read_refuses(sql):
    assert is_plain_read(sql) is False


# --- check_read_only_override (state check wired in Task 5) ------------------

_FUTURE = datetime.now(timezone.utc) + timedelta(hours=1)
_PAST = datetime.now(timezone.utc) - timedelta(seconds=1)
_REF = "goumlynecruxrlmzlntp"  # == SILO_ALIASES["goumlyne"], already committed


def test_check_read_only_noop_when_not_armed():
    # No override armed: writes are NOT the RO check's business (behavior
    # unchanged is asserted end-to-end in test_server.py).
    check_read_only_override(
        "insert into t values (1)", ref_override=None, ref_override_expires_at=None
    )


def test_check_read_only_noop_when_expired():
    # Expiry reverts BOTH the ref widening and the RO mode, by construction.
    check_read_only_override(
        "insert into t values (1)", ref_override=_REF, ref_override_expires_at=_PAST
    )


def test_check_read_only_raises_for_write_when_live():
    with pytest.raises(BrokerValidationError) as exc_info:
        check_read_only_override(
            "insert into t values (1)", ref_override=_REF, ref_override_expires_at=_FUTURE
        )
    assert exc_info.value.code is ErrorCode.READ_ONLY_OVERRIDE
    assert "read-only" in exc_info.value.message


def test_check_read_only_passes_plain_read_when_live():
    check_read_only_override(
        "select count(*) from t", ref_override=_REF, ref_override_expires_at=_FUTURE
    )
