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
    "select 1;",                                                    # one statement, trailing ';'
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
    "select 1; select 2",                                           # multi-statement: single-statement only (ruling C amended)
    "SELECT 1; COMMIT; DELETE FROM t",
    "SELECT 1; COMMIT",
    "SELECT 1; SET TRANSACTION READ WRITE",
    "SET TRANSACTION READ WRITE; SELECT 1",
    "SELECT 1; BEGIN",
    "BEGIN; SELECT 1",
    "SELECT 1; RESET ALL",
    "RESET transaction_read_only",
    "SELECT 1; SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
    "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
    "SELECT 1; ROLLBACK",
    "SELECT 1; END",
    "SELECT set_config('transaction_read_only', 'off', false)",
    "SELECT pg_catalog.set_config('default_transaction_read_only', 'off', false)",
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
    # --- whole-branch review Important #2 + Minor #6 (2026-10-28): the
    # direct large-object write primitives and the rest of the advisory-lock /
    # stat-reset family were missing from the denylist.
    "SELECT lo_write(lo_open(12345, 131072), 'data')",               # mutates an existing large object
    "SELECT lo_close(0)",
    "SELECT pg_advisory_xact_lock(1)",
    "SELECT pg_advisory_xact_lock_shared(1)",
    "SELECT pg_try_advisory_lock(1)",
    "SELECT pg_try_advisory_lock_shared(1)",
    "SELECT pg_advisory_lock_shared(1)",
    "SELECT pg_advisory_unlock_shared(1)",
    "SELECT pg_stat_reset_slru()",
    # --- PR #569 review (cc-fleet-health, 2026-10-28): $ is a legal PG
    # identifier CONTINUATION character, so a$x$ is ONE identifier token to
    # PG -- but both the vendored and this module's dollar-open regex match
    # $x$ mid-identifier and open a bogus dollar body, blanking the real code
    # between it and the next $x$. Reproduced by the reviewer (classifier
    # only, nothing executed): is_plain_read of this exact SQL was True.
    "SELECT 1 AS a$x$ INTO TABLE newtab FROM pg_class WHERE relname<>'z$x$'",
    "SELECT 1 AS a$x$ FOR UPDATE b$x$ FROM t",                        # same bypass hiding FOR UPDATE
])
def test_is_plain_read_refuses(sql):
    assert is_plain_read(sql) is False


def test_is_plain_read_refuses_into_inside_a_dollar_body_belt_and_braces():
    # PR #569 review, belt-and-braces half: "into"/"for" were not in
    # _FORBIDDEN_BODY_TOKENS, so a dollar body whose content contains one of
    # those words -- independent of the identifier-$ root-cause fix (this
    # $tag$ follows a space, not an identifier char) -- was NOT refused.
    # A legitimate dollar-quoted string constant containing the word "into"
    # is now a deliberate false refusal, fail-closed, same doctrine.
    assert is_plain_read("SELECT $tag$ x INTO t $tag$ FROM t") is False


def test_is_plain_read_refuses_identifier_dollar_as_doctrine_false_refusal():
    # PR #569 review: an identifier containing $ (e.g. a column alias col$1)
    # is legal PG and not itself a write -- but after the fix, ANY $ directly
    # following an identifier character is treated as hazardous (the same
    # mid-identifier ambiguity the bypass above exploits), so this is a
    # DELIBERATE false refusal, fail-closed, same doctrine as the other
    # hazard-class false refusals in the accept-corpus comments above. The
    # reviewer asked this be recorded explicitly, not left implicit.
    assert is_plain_read("SELECT col$1 FROM t") is False


def test_is_plain_read_refuses_quoted_update_after_ddl_detect_revendor():
    # ihsanos PR #1168 re-vendor (bus #79427, 2026-10-28): the vendored
    # ddl_detect.py gained its own "..." dquote state that BLANKS identifier
    # content (correct for DDL-shape scanning -- a column literally named
    # "DROP" isn't DDL). strip_sql_noise now blanks "update" here too, which
    # would silently flip this module's own documented false-refusal (module
    # docstring, FAIL-CLOSED DOCTRINE) to True if is_plain_read relied on
    # strip_sql_noise's output alone -- pin the compensating re-check.
    assert is_plain_read('SELECT "update" FROM t') is False
    # and the un-poisoned case must still be unaffected:
    assert is_plain_read('SELECT "col one", "col two" FROM t') is True


# --- check_read_only_override (state check wired in Task 5) ------------------

_FUTURE = datetime.now(timezone.utc) + timedelta(hours=1)
_PAST = datetime.now(timezone.utc) - timedelta(seconds=1)
_REF = "goumlynecruxrlmzlntp"  # == SILO_ALIASES["goumlyne"], already committed


def test_check_read_only_noop_when_not_armed():
    # No override armed: writes are NOT the RO check's business (behavior
    # unchanged is asserted end-to-end in test_server.py).
    check_read_only_override(
        "insert into t values (1)", project_ref=_REF,
        ref_override=None, ref_override_expires_at=None,
    )


def test_check_read_only_noop_when_expired():
    # Expiry reverts BOTH the ref widening and the RO mode, by construction.
    check_read_only_override(
        "insert into t values (1)", project_ref=_REF,
        ref_override=_REF, ref_override_expires_at=_PAST,
    )


def test_check_read_only_raises_for_write_to_override_ref_when_live():
    with pytest.raises(BrokerValidationError) as exc_info:
        check_read_only_override(
            "insert into t values (1)", project_ref=_REF,
            ref_override=_REF, ref_override_expires_at=_FUTURE,
        )
    assert exc_info.value.code is ErrorCode.READ_ONLY_OVERRIDE
    assert "read-only" in exc_info.value.message


def test_check_read_only_passes_plain_read_when_live():
    check_read_only_override(
        "select count(*) from t", project_ref=_REF,
        ref_override=_REF, ref_override_expires_at=_FUTURE,
    )


def test_check_read_only_scoped_baseline_ref_write_passes_when_live():
    # SCOPE RULING (cc-fleet-health bus #79039, 2026-10-28 — supersedes the
    # broker-wide reading of GO #78974): RO applies only to requests targeting
    # the override's ref. A write to a BASELINE (always-allowed) ref behaves
    # exactly as today even while the override is armed.
    check_read_only_override(
        "insert into t values (1)", project_ref="oovskwogvefnznfowjky",  # QA baseline ref
        ref_override=_REF, ref_override_expires_at=_FUTURE,
    )


# --- console amended ruling C + addendum (bus #89846/#89875) -----------------
# Gate-free reads run as READ_ONLY_PROLOGUE + sql in ONE upstream query string,
# so a plain read must be a single statement with no transaction-control or
# session-setting keyword in code position, and nothing left unclosed.

@pytest.mark.parametrize("sql", [
    # required by bus #89875
    "select 1; commit; insert into t values (1)",
    "commit; delete from t",
    "set transaction read write; update t set x=1",
    "select 1; select 2",
    # each listed keyword, in code position, inside an otherwise read-shaped statement
    "select 1 commit",
    "select rollback from t",
    "select begin from t",
    "select 1 from t end",
    "select abort from t",
    "select savepoint from t",
    "select release from t",
    "select 1 from t where x = set",
    "select reset from t",
    "select discard from t",
    "select 1 start transaction",
    "select 1 prepare transaction",
    "START TRANSACTION",
    "SET SESSION AUTHORIZATION x",
    "SET search_path = evil",
    "SELECT CASE WHEN a THEN 1 END END FROM t",   # one END too many for its CASE
])
def test_gate_free_read_refuses_txn_control_and_multi_statement(sql):
    assert is_plain_read(sql) is False


@pytest.mark.parametrize("sql", [
    "select 'commit'",
    "select 1 -- rollback",
    "select 'set transaction read write; begin; end'",
    "select $q$ commit $q$",
    "/* reset all */ select 1",
    'select "commit" from t',
    "SELECT CASE WHEN a THEN 1 ELSE 2 END FROM t",
    "SELECT CASE WHEN a THEN CASE WHEN b THEN 1 END ELSE 2 END FROM t",
    "SELECT offset_col, settings FROM t",
])
def test_keywords_inside_literals_comments_or_identifiers_are_not_refused(sql):
    assert is_plain_read(sql) is True


@pytest.mark.parametrize("sql", [
    "select 'unclosed",
    "select 1 /* unclosed",
    'select "unclosed',
    "select $q$ unclosed",
    "select $$ unclosed",
])
def test_unclosed_regions_are_refused(sql):
    assert is_plain_read(sql) is False
