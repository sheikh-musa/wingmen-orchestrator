"""hc#95 ruling C (console #89827/#89841): server-side plain-read exemption
for exec_prod's WRAPPED reads, decided from the bytes against the pinned
canonical wrapper -- never from a caller flag."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

from exec_broker import canonical_wrapper  # noqa: E402
from exec_broker.canonical_wrapper import (  # noqa: E402
    PLACEHOLDER,
    WRAPPER_PATH,
    extract_wrapped_inner,
    is_wrapped_plain_read,
)
from exec_broker.gate_validation import BrokerValidationError, require_gate  # noqa: E402
from exec_broker.protocol import ErrorCode  # noqa: E402

# Same pin as ihsanos-irsyad scripts/db/lib/agent-actor-guard.sql; the
# irsyad-side exec_prod test pins the identical value (one source of truth).
PINNED = "7ec3ba9167b321db72cdec03c3868c9a5e628deace012f5b74e0dd20561cff90"
_REF = "syntheticbrokerqfakesilo"
_DEFAULT_ACTOR = "ca2a4a9c-f745-40c3-9536-7a021fd42bb9"


def _wrap(inner: str, wrapper: str | None = None) -> str:
    """Independent re-statement of agent_actor_guard.wrap()'s splice (default
    actor): wrapper.replace(PLACEHOLDER, f"{PLACEHOLDER}\\n{inner.rstrip()}\\n", 1)."""
    w = WRAPPER_PATH.read_text(encoding="utf-8") if wrapper is None else wrapper
    return w.replace(PLACEHOLDER, f"{PLACEHOLDER}\n{inner.rstrip()}\n", 1)


class FakeCursor:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


def test_vendored_wrapper_matches_its_pin():
    assert hashlib.sha256(WRAPPER_PATH.read_bytes()).hexdigest() == PINNED
    assert canonical_wrapper.WRAPPER_SHA256 == PINNED
    assert canonical_wrapper.wrapper_parts() is not None


@pytest.mark.parametrize("inner", [
    "SELECT count(*) FROM tabung_tins;",
    "select id, amount from t where id = 42",
    "WITH x AS (SELECT id FROM t) SELECT count(*) FROM x;",
    "SELECT 1; SELECT 2;",
    "  SELECT 'a;b' AS s; -- trailing comment",
])
def test_wrapped_plain_read_on_gated_ref_needs_no_gate(inner):
    sql = _wrap(inner)
    assert is_wrapped_plain_read(sql)
    cur = FakeCursor()
    assert require_gate(sql, None, _REF, cur=cur) is False
    assert cur.calls == []


@pytest.mark.parametrize("inner", [
    "UPDATE tabung_tins SET amount = 12.50 WHERE id = 42;",
    "INSERT INTO t (a) VALUES (1);",
    "DELETE FROM t WHERE id = 1;",
    "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE;",
    "DO $$ BEGIN UPDATE t SET a = 1; END $$;",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d;",
    "SELECT * FROM t FOR UPDATE;",
    "SELECT 1 INTO tmp_t;",
    "SELECT set_config('request.jwt.claim.sub', 'x', true);",
    "SELECT nextval('s');",
    "SELECT 1; UPDATE t SET a = 1;",
    "SELECT pg_advisory_lock(1);",
    "CALL do_something();",
    "COPY t FROM '/tmp/x';",
])
def test_dml_inside_the_wrapper_is_gate_required(inner):
    sql = _wrap(inner)
    assert not is_wrapped_plain_read(sql)
    cur = FakeCursor()
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(sql, None, _REF, cur=cur)
    assert exc.value.code == ErrorCode.GATE_REQUIRED
    assert cur.calls == []


@pytest.mark.parametrize("inner", [
    "CREATE TABLE z (a int);",
    "DROP TABLE t;",
    "ALTER TABLE t ADD COLUMN x int;",
    "GRANT SELECT ON t TO anon;",
    "TRUNCATE t;",
    "DO $$ BEGIN EXECUTE 'DROP TABLE t'; END $$;",
    "SELECT 1; DROP TABLE t;",
])
def test_ddl_inside_the_wrapper_is_gate_required(inner):
    sql = _wrap(inner)
    assert not is_wrapped_plain_read(sql)
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(sql, None, _REF, cur=FakeCursor())
    assert exc.value.code == ErrorCode.GATE_REQUIRED


@pytest.mark.parametrize("inner", [
    "SELECT '",                      # open string would swallow the postamble
    "SELECT 1 /*",                   # open block comment
    "SELECT $q$",                    # open dollar body
    'SELECT "x--y" FROM t',          # lexical hazard (dquote class)
    "SELECT E'\\x41'",               # lexical hazard (backslash class)
    "SELECT col$1 FROM t",           # lexical hazard (identifier-$ class)
])
def test_unclosed_or_hazardous_inner_gets_no_exemption(inner):
    sql = _wrap(inner)
    assert not is_wrapped_plain_read(sql)
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(sql, None, _REF, cur=FakeCursor())
    assert exc.value.code == ErrorCode.GATE_REQUIRED


def _expect_gated(sql):
    assert not is_wrapped_plain_read(sql)
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(sql, None, _REF, cur=FakeCursor())
    assert exc.value.code == ErrorCode.GATE_REQUIRED


def test_one_byte_tampered_wrapper_gets_no_exemption():
    w = WRAPPER_PATH.read_text(encoding="utf-8")
    _expect_gated(_wrap("SELECT 1;", w.replace("REPEATABLE READ;", "READ COMMITTED;", 1)))
    _expect_gated(_wrap("SELECT 1;", w.replace("RAISE EXCEPTION 'ABORT", "RAISE NOTICE 'ABORT", 1)))


def test_retargeted_actor_wrapper_gets_no_exemption():
    w = WRAPPER_PATH.read_text(encoding="utf-8")
    other = w.replace(f"v_actor uuid := '{_DEFAULT_ACTOR}';",
                      "v_actor uuid := '00000000-0000-0000-0000-000000000001';", 1)
    assert other != w
    _expect_gated(_wrap("SELECT 1;", other))


def test_statements_outside_the_wrapper_get_no_exemption():
    sql = _wrap("SELECT 1;")
    _expect_gated(sql + "\nUPDATE t SET a = 1;")
    _expect_gated("UPDATE t SET a = 1;\n" + sql)
    _expect_gated(sql + " ")


def test_non_canonical_inner_forms_get_no_exemption():
    prefix, suffix = canonical_wrapper.wrapper_parts()
    assert extract_wrapped_inner(prefix + "SELECT 1;  " + suffix) is None   # trailing ws
    assert extract_wrapped_inner(prefix + "   " + suffix) is None            # blank
    assert extract_wrapped_inner(prefix + suffix) is None                    # empty
    assert extract_wrapped_inner(prefix + "SELECT 1;" + suffix) == "SELECT 1;"


def test_bare_wrapper_halves_are_not_reads():
    prefix, suffix = canonical_wrapper.wrapper_parts()
    _expect_gated(prefix)
    _expect_gated(suffix)


def test_drifted_or_missing_vendored_wrapper_disables_the_exemption(monkeypatch):
    sql = _wrap("SELECT 1;")
    monkeypatch.setattr(canonical_wrapper, "_PARTS", None)
    _expect_gated(sql)


def test_load_parts_refuses_a_drifted_file(monkeypatch, tmp_path):
    drifted = tmp_path / "agent-actor-guard.sql"
    drifted.write_bytes(WRAPPER_PATH.read_bytes() + b"\n")
    monkeypatch.setattr(canonical_wrapper, "WRAPPER_PATH", drifted)
    assert canonical_wrapper._load_parts() is None
    monkeypatch.setattr(canonical_wrapper, "WRAPPER_PATH", tmp_path / "missing.sql")
    assert canonical_wrapper._load_parts() is None


def test_raw_plain_read_exemption_unchanged():
    cur = FakeCursor()
    assert require_gate("select 1", None, _REF, cur=cur) is False
    assert cur.calls == []
