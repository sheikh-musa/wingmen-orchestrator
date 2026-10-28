# hc#95 SELECT-Only Override Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While the bounded ref override (`EXEC_BROKER_REF_OVERRIDE_ALIAS` + `_EXPIRES_AT`) is armed and unexpired, the broker refuses any guarded_sql that is not a plain read — fail-closed, with new error code `READ_ONLY_OVERRIDE`.

**Architecture:** A pure text classifier `is_plain_read()` (new module `read_only.py`) reuses the vendored `ddl_detect` lexer (`strip_sql_noise`, `extract_dollar_bodies`, `strip_comments_only`, `is_ddl_shaped`) — never a hand-rolled parser, never an edit to the sha-pinned vendored file. A state check `check_read_only_override()` keys off the SAME override pair `check_project_ref` already uses, via one shared `ref_override_is_live()` helper, so arm/disarm/expiry semantics cannot drift between the ref widening and the RO mode. Wired into `server.py`'s existing validation block so the audit row is finalized and the error returned through the same path as every other validation refusal.

**Tech Stack:** Python 3.9 stdlib only (regex, no parser lib); pytest; existing BrokerServer socket test harness.

**Spec:** bus #79024 (cc-fleet-health assignment: RO override due 11-04, step (c) due 11-11, "I review every PR before merge", "include the exact file list in the PR"), orch-console GO #78974 ("while an override is active, refuse any statement that is not a plain read, fail-closed"), arm op #78968 (the goumlyne deep-dive that had to be reported as "RO promised, not enforced" — this plan makes it enforced). My design ack to cc-fleet-health: bus #79037.

## Global Constraints

- **Never edit `scripts/lib/exec_broker/ddl_detect.py`** — it is vendored verbatim from ihsanos-irsyad and sha256-pinned (`tests/exec_broker/test_ddl_detect_vendor_pin.py` enforces the pin). New code may IMPORT from it only.
- **No new env vars, no daemon changes.** RO mode keys off the already-loaded `ref_override`/`ref_override_expires_at` pair (PR#513's env plumbing); arming/disarming/expiry reverts BOTH the ref widening and the RO mode together, by construction.
- **Scope ruling (flagged to and pending cc-fleet-health's review, #79037):** RO applies broker-wide while the REF override is live — a QA-ref write is also refused for the window (per #78974's wording). The uid override alone does NOT trigger RO (#79024 names `EXEC_BROKER_REF_OVERRIDE_ALIAS` only).
- **New error code is additive on the response side.** Response JSON shape is unchanged (`{"status":"error","error":{"code","message"}}`); callers must tolerate an unknown code string — called out for coord in the PR + contract-delta note.
- Python 3.9: `from __future__ import annotations` style as in the existing package; no 3.10+ syntax outside annotations.
- Fail-closed doctrine (inherited from ddl_detect): false refusals are acceptable; a missed write is not. A text screen, not a semantic analyzer — a read-shaped statement calling an existing user-defined write-capable function is indistinguishable from a read by text alone; that limitation goes in the module docstring and the PR (compensating controls: bounded ≤24h window, full-SQL audit row per call, console-gated arming).
- Test SQL uses only the QA ref `oovskwogvefnznfowjky` and the already-committed `goumlynecruxrlmzlntp` constant from `SILO_ALIASES`; no client rows/data anywhere.
- PR targets `fable/substrate-safe-fixes` (deployed trunk, never `main`); cc-fleet-health reviews every PR before merge; PR body lists the exact deploy file set for the `/opt/irsyad-exec-broker` hand-copy.

## Review Focus

1. **Write smuggled inside a read-shaped statement via a data-modifying CTE** (`WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d`) — a reasonable person expects refusal; head-keyword whitelisting alone would pass it. → Task 3 test.
2. **Write via a function call in a SELECT** (`set_config`, `nextval`/`setval`, `pg_terminate_backend`, `dblink_exec`, `lo_import`) — expected refusal; no keyword scan catches these. → Task 3 tests.
3. **`SELECT ... INTO`** — table creation syntactically disguised as a read. Expected refusal. → Task 3 test.
4. **Lookalike tokens in legal reads** — `OFFSET` (contains "SET"), `CASE ... END` (END is a commit keyword as a statement head), a quoted column named `"set"` — must NOT be refused (no over-refusal of plain reads beyond the documented fail-closed set). → Task 3 tests.
5. **Override state boundaries** — an EXPIRED ref override must lift RO (not just the ref widening); a uid-only override must NOT impose RO; with no override armed, INSERT behavior is byte-for-byte unchanged (mgmt API still called). → Tasks 4/5 tests.

---

### Task 1: `READ_ONLY_OVERRIDE` error code

**Files:**
- Modify: `scripts/lib/exec_broker/protocol.py` (ErrorCode enum + the "exactly these six" comment)
- Test: `tests/exec_broker/test_protocol.py` (append)

**Interfaces:**
- Produces: `ErrorCode.READ_ONLY_OVERRIDE` with value `"READ_ONLY_OVERRIDE"` — consumed by Task 4's raiser and asserted by Tasks 4/5 tests.

- [ ] **Step 1: Write the failing test** (append to `tests/exec_broker/test_protocol.py`)

```python
def test_read_only_override_error_code_exists_and_encodes():
    assert ErrorCode.READ_ONLY_OVERRIDE.value == "READ_ONLY_OVERRIDE"
    payload = json.loads(encode_error(ErrorCode.READ_ONLY_OVERRIDE, "broker is read-only").decode("utf-8"))
    assert payload == {"status": "error", "error": {"code": "READ_ONLY_OVERRIDE", "message": "broker is read-only"}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sheikhmusa/wingmen/orchestrator/.venv/bin/python -m pytest tests/exec_broker/test_protocol.py -v`
Expected: FAIL with `AttributeError` (`READ_ONLY_OVERRIDE` not a member of ErrorCode)

- [ ] **Step 3: Add the enum member**

In `scripts/lib/exec_broker/protocol.py`, add `READ_ONLY_OVERRIDE = "READ_ONLY_OVERRIDE"` to `ErrorCode` and amend the comment above the enum: the six v1 codes were frozen at 5053826e per bus #56789; `READ_ONLY_OVERRIDE` is the additive seventh, added by the RO-override follow-up (orch-console GO #78974, assignment bus #79024) — response shape unchanged, callers must tolerate unknown code strings.

- [ ] **Step 4: Run test to verify it passes**

Run: same command. Expected: PASS (all protocol tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/exec_broker/protocol.py tests/exec_broker/test_protocol.py
git commit -m "hc95 exec-broker: add READ_ONLY_OVERRIDE error code (GO #78974, bus #79024)"
```

### Task 2: shared `ref_override_is_live()` in gate_validation

**Files:**
- Modify: `scripts/lib/exec_broker/gate_validation.py` (extract from `check_project_ref`'s inline liveness test, ~lines 109-115)
- Test: `tests/exec_broker/test_gate_validation.py` (append)

**Interfaces:**
- Produces: `ref_override_is_live(override_ref: str | None, override_expires_at: datetime | None, now: datetime | None = None) -> bool` — True iff BOTH are non-None AND (now or current UTC time) is strictly `< override_expires_at`. Consumed by Task 4; `check_project_ref` behavior is byte-identical for all existing callers/tests (pure refactor).

- [ ] **Step 1: Write the failing tests** (append to `tests/exec_broker/test_gate_validation.py`; import `ref_override_is_live` alongside the existing gate_validation imports)

```python
def test_ref_override_is_live_requires_both_halves():
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert ref_override_is_live("goumlynecruxrlmzlntp", future) is True
    assert ref_override_is_live(None, future) is False
    assert ref_override_is_live("goumlynecruxrlmzlntp", None) is False

def test_ref_override_is_live_expiry_is_strict():
    ref = "goumlynecruxrlmzlntp"
    boundary = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert ref_override_is_live(ref, boundary, now=boundary - timedelta(seconds=1)) is True
    assert ref_override_is_live(ref, boundary, now=boundary) is False  # == expiry is expired
    assert ref_override_is_live(ref, boundary, now=boundary + timedelta(seconds=1)) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/sheikhmusa/wingmen/orchestrator/.venv/bin/python -m pytest tests/exec_broker/test_gate_validation.py -v`
Expected: FAIL with ImportError (`ref_override_is_live` not importable)

- [ ] **Step 3: Implement the helper and refactor `check_project_ref` to use it**

Add `ref_override_is_live` immediately above `check_project_ref`, docstring noting it is the ONE definition of "the bounded ref override is currently in force", shared by `check_project_ref` (ref widening) and `read_only.check_read_only_override` (Task 4) so the two can never drift. Replace the inline `override_ref is not None and override_expires_at is not None and ... < override_expires_at` conjunction in `check_project_ref` with `ref_override_is_live(override_ref, override_expires_at, now=now) and project_ref == override_ref` (liveness first — a live override_ref is non-None by construction, so the equality can never pass for a None project_ref).

- [ ] **Step 4: Run tests to verify they pass — including every pre-existing check_project_ref/require_gate test unchanged**

Run: same command. Expected: PASS (new tests + all existing gate_validation tests green — proves the refactor is behavior-identical).

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/exec_broker/gate_validation.py tests/exec_broker/test_gate_validation.py
git commit -m "hc95 exec-broker: extract shared ref_override_is_live (single liveness definition)"
```

### Task 3: the `is_plain_read` classifier

**Files:**
- Create: `scripts/lib/exec_broker/read_only.py`
- Test: `tests/exec_broker/test_read_only.py` (new file)

**Interfaces:**
- Consumes: `ddl_detect.is_ddl_shaped`, `ddl_detect.strip_sql_noise`, `ddl_detect.extract_dollar_bodies`, `ddl_detect.strip_comments_only` (import-only — the vendored file is never edited).
- Produces: `is_plain_read(sql: str) -> bool` — pure function, no I/O. True iff (a) `is_ddl_shaped(sql)` is False, (b) no token of `_FORBIDDEN_CODE_TOKENS` appears in the top-level code text (`strip_sql_noise` output: comments/strings/dollar-bodies already blanked), (c) no token of `_FORBIDDEN_FUNCTION_TOKENS` appears in the top-level code text NOR in any dollar body (comment-stripped), (d) no token of `_FORBIDDEN_BODY_TOKENS` appears in any dollar body, and (e) EVERY non-empty top-level statement (split the stripped text on `;`, strip whitespace and leading `(`) starts with a keyword in `_READ_FIRST_KEYWORDS`. Empty/whitespace-only SQL → False.

Exact constant values (decided — these ARE the security boundary):

```python
_READ_FIRST_KEYWORDS = frozenset({"select", "with", "table", "values"})
# Mid-statement tokens that can turn a read-shaped statement into a write.
# Head-only statement keywords (SET, LOCK, COPY, CALL, DO, EXPLAIN, BEGIN,
# COMMENT, VACUUM, ...) are deliberately NOT here: they cannot appear
# mid-statement legally, the head whitelist already refuses them, and many
# are unreserved words that would false-refuse plain reads of columns named
# e.g. "set"/"lock"/"copy"/"do". Quoted identifiers like "update" in a
# SELECT ARE false-refused by this scan — fail-closed by design.
_FORBIDDEN_CODE_TOKENS = frozenset({"insert", "update", "delete", "merge", "into", "for"})
# Write-capable or state-changing functions callable from inside a SELECT.
# Conservative, not exhaustive; the head whitelist is the primary control.
# nextval/setval included: they mutate persistent sequence state.
_FORBIDDEN_FUNCTION_TOKENS = frozenset({
    "set_config", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_notify", "dblink", "dblink_exec", "dblink_connect", "dblink_disconnect",
    "lo_import", "lo_export", "lo_create", "lo_put", "lo_truncate", "lo_unlink",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "pg_file_write", "pg_file_rename", "pg_file_unlink", "pg_file_read",
    "pg_advisory_lock", "pg_advisory_unlock", "pg_advisory_unlock_all",
    "pg_stat_reset", "pg_stat_reset_shared", "nextval", "setval",
})
# Inside a dollar-quoted body (reachable today only as a dollar-quoted string
# constant in a read-shaped statement, since DO/CREATE heads are already
# refused — kept as belt-and-braces): dynamic SQL + DML writes.
_FORBIDDEN_BODY_TOKENS = frozenset({"insert", "update", "delete", "merge", "execute"})
```

Word-boundary, case-insensitive regex per token (same `(?i)\b…\b` style as ddl_detect). `for` is included in `_FORBIDDEN_CODE_TOKENS` to refuse locking reads (`FOR UPDATE`/`FOR SHARE`); `into` refuses `SELECT … INTO`.

Module docstring must carry: purpose (GO #78974 / bus #79024 — turn the override window's "RO promised" into "RO enforced"); the reuse-not-reimplement rule against the pinned ddl_detect; the KNOWN LIMITATION (text screen, not semantic analyzer — an existing user-defined write-capable function called by name from a SELECT is indistinguishable from a read; compensating controls: bounded ≤24h window, per-call full-SQL audit row, console-gated arming); the false-refusal doctrine (quoted columns named "update"/"into"/"for" and dollar-quoted string constants containing write words are refused — fail-closed, same doctrine as ddl_detect's "false refusal is fail-safe").

- [ ] **Step 1: Write the failing tests** (`tests/exec_broker/test_read_only.py`, new file, same `sys.path` bootstrap header as the other exec_broker tests)

Positives (all must return True):

```python
@pytest.mark.parametrize("sql", [
    "select 1",
    "SELECT count(*) FROM t WHERE x = 1",
    "with x as (select id from t) select count(*) from x",          # CTE
    "SELECT md5(body), current_setting('server_version') FROM t",   # read-only functions
    "TABLE t",
    "VALUES (1), (2)",
    "SELECT CASE WHEN a THEN 1 ELSE 2 END FROM t",                  # END is not denied (CASE…END)
    "SELECT * FROM t ORDER BY id OFFSET 5 LIMIT 2",                 # OFFSET does not trip a SET scan
    "select 1; select 2",                                           # multi-statement, all reads
    "(SELECT 1) UNION (SELECT 2)",                                  # parenthesized heads
    "SELECT 'insert into t values (1)' FROM t",                     # write words in a string → blanked
    "-- delete from t\nSELECT 1",                                   # write words in a comment → blanked
    'SELECT "set" FROM t',                                          # quoted column named set (no SET token in code)
    "SELECT do FROM t",                                             # unreserved-word column named do
])
def test_is_plain_read_accepts(sql):
    assert is_plain_read(sql) is True
```

Negatives (all must return False):

```python
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
    "SELECT $$insert into t values (1)$$",                           # dollar-quoted string constant — false refusal, fail-closed
    'SELECT "update" FROM t',                                        # quoted column named update — false refusal, fail-closed
])
def test_is_plain_read_refuses(sql):
    assert is_plain_read(sql) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/sheikhmusa/wingmen/orchestrator/.venv/bin/python -m pytest tests/exec_broker/test_read_only.py -v`
Expected: FAIL with ModuleNotFoundError (no module `exec_broker.read_only`)

- [ ] **Step 3: Implement `read_only.py`**

Structure: module docstring (as specified in Interfaces), imports (`re`, `datetime`, `from .ddl_detect import …`, `from .protocol import ErrorCode` is NOT needed in this task — Task 4 adds the raiser), the four constant frozensets verbatim from Interfaces, then:

```python
def is_plain_read(sql: str) -> bool:
```

Body: the five checks (a)-(e) from the Interfaces block, in that order — DDL check first (reuses the vendored classifier itself), then the two token scans compiled once at module level into one alternation regex per set (`(?i)\b(?:insert|update|…)\b`), then the statement-head walk. Splitting detail: `strip_sql_noise` output split on `";"`, each fragment `.strip().lstrip("(").strip()`, first keyword via `re.match(r"[A-Za-z_][A-Za-z_0-9]*", fragment)`, lowercased, must be in `_READ_FIRST_KEYWORDS`; a fragment with no keyword match (e.g. starts with `)` or a digit) is not a read.

- [ ] **Step 4: Run tests to verify they pass**

Run: same command. Expected: PASS (all parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/exec_broker/read_only.py tests/exec_broker/test_read_only.py
git commit -m "hc95 exec-broker: is_plain_read classifier (reuse vendored ddl_detect lexer)"
```

### Task 4: `check_read_only_override` state check

**Files:**
- Modify: `scripts/lib/exec_broker/read_only.py` (append the raiser)
- Test: `tests/exec_broker/test_read_only.py` (append)

**Interfaces:**
- Consumes: `ref_override_is_live` from Task 2; `BrokerValidationError` from `gate_validation`; `ErrorCode.READ_ONLY_OVERRIDE` from Task 1; `is_plain_read` from Task 3.
- Produces: `check_read_only_override(guarded_sql: str, *, ref_override: str | None, ref_override_expires_at: datetime | None, now: datetime | None = None) -> None` — returns silently when the ref override is not live, or when live and `is_plain_read(guarded_sql)` is True; raises `BrokerValidationError(ErrorCode.READ_ONLY_OVERRIDE, ...)` when live and not a plain read. Exact message: `"ref override is active -- broker is read-only for this window; statement is not a plain read (SELECT/WITH/TABLE/VALUES)"`.

- [ ] **Step 1: Write the failing tests** (append; imports: `BrokerValidationError` from `exec_broker.gate_validation`, `ErrorCode` from `exec_broker.protocol`)

```python
_FUTURE = datetime.now(timezone.utc) + timedelta(hours=1)
_PAST = datetime.now(timezone.utc) - timedelta(seconds=1)
_REF = "goumlynecruxrlmzlntp"

def test_check_read_only_noop_when_not_armed():
    check_read_only_override("insert into t values (1)", ref_override=None, ref_override_expires_at=None)

def test_check_read_only_noop_when_expired():
    check_read_only_override("insert into t values (1)", ref_override=_REF, ref_override_expires_at=_PAST)

def test_check_read_only_raises_for_write_when_live():
    with pytest.raises(BrokerValidationError) as exc_info:
        check_read_only_override("insert into t values (1)", ref_override=_REF, ref_override_expires_at=_FUTURE)
    assert exc_info.value.code is ErrorCode.READ_ONLY_OVERRIDE
    assert "read-only" in exc_info.value.message

def test_check_read_only_passes_plain_read_when_live():
    check_read_only_override("select count(*) from t", ref_override=_REF, ref_override_expires_at=_FUTURE)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/sheikhmusa/wingmen/orchestrator/.venv/bin/python -m pytest tests/exec_broker/test_read_only.py -v -k check_read_only`
Expected: FAIL with ImportError (`check_read_only_override` not importable)

- [ ] **Step 3: Implement the raiser in `read_only.py`**

One guard clause (`ref_override_is_live(...)` false → return), one `is_plain_read` call, one raise with the exact message from Interfaces. Docstring: the design ruling — RO applies broker-wide while the REF override is live (GO #78974's wording), NOT only to calls targeting the override ref; the uid override does NOT trigger RO (bus #79024 names `EXEC_BROKER_REF_OVERRIDE_ALIAS` only).

- [ ] **Step 4: Run tests to verify they pass**

Run: same command. Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/exec_broker/read_only.py tests/exec_broker/test_read_only.py
git commit -m "hc95 exec-broker: check_read_only_override (RO while ref override is live)"
```

### Task 5: wire into BrokerServer + end-to-end socket tests

**Files:**
- Modify: `scripts/lib/exec_broker/server.py` (validation block, after `check_project_ref(...)`, before `require_gate(...)`; add the import)
- Test: `tests/exec_broker/test_server.py` (append in the bounded-override section, reusing `_make_server`/`_send`/`_FUTURE`/`_PAST`)

**Interfaces:**
- Consumes: `check_read_only_override` from Task 4. Server constructor signature is UNCHANGED — `self.ref_override`/`self.ref_override_expires_at` (already present from PR#513) are the inputs; no daemon/env changes.
- Produces: wire behavior — while the ref override is live, any non-plain-read request gets `{"status":"error","error":{"code":"READ_ONLY_OVERRIDE",...}}`, the audit row finalizes to `error`/`READ_ONLY_OVERRIDE`, and the mgmt API is never called.

- [ ] **Step 1: Write the failing tests** (append)

```python
_GOUMLYNE = "goumlynecruxrlmzlntp"  # == SILO_ALIASES["goumlyne"], already committed in gate_validation.py

@pytest.mark.parametrize("guarded_sql", [
    "insert into t values (1)",
    "update t set x = 1",
    "delete from t",
    "DO $$ BEGIN UPDATE t SET x = 1; END $$",
    "SELECT set_config('a', 'b', false)",
])
def test_ref_override_readonly_refuses_writes_end_to_end(tmp_path, guarded_sql):
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db,
        ref_override=_GOUMLYNE, ref_override_expires_at=_FUTURE,
    )
    resp = _send(sock_path, {"project_ref": _GOUMLYNE, "guarded_sql": guarded_sql,
                             "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "READ_ONLY_OVERRIDE"
    row = list(db.vault_rows.values())[0]
    assert row["status"] == "error" and row["error_code"] == "READ_ONLY_OVERRIDE"

def test_ref_override_readonly_allows_cte_read_end_to_end(tmp_path):
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [{"n": 1}],
        ref_override=_GOUMLYNE, ref_override_expires_at=_FUTURE,
    )
    resp = _send(sock_path, {
        "project_ref": _GOUMLYNE,
        "guarded_sql": "with x as (select id from t) select count(*) from x",
        "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp == {"status": "ok", "rows": [{"n": 1}]}

def test_ref_override_readonly_is_broker_wide_not_only_override_ref(tmp_path):
    # Design ruling (GO #78974 wording, flagged in bus #79037): while the
    # override is armed, a write to the ALWAYS-ALLOWED QA ref is refused too.
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db,
        ref_override=_GOUMLYNE, ref_override_expires_at=_FUTURE,
    )
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky",
                             "guarded_sql": "insert into t values (1)",
                             "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "READ_ONLY_OVERRIDE"

def test_expired_ref_override_lifts_readonly_for_qa_write(tmp_path):
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda *a, **k: [],
        ref_override=_GOUMLYNE, ref_override_expires_at=_PAST,
    )
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky",
                             "guarded_sql": "insert into t values (1)",
                             "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp == {"status": "ok", "rows": []}   # QA write unaffected once the override expired

def test_uid_override_alone_does_not_impose_readonly(tmp_path):
    # bus #79024 ties RO to the REF override only; a uid-override window
    # must not silently turn the broker read-only.
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID + 1}), db=db, mgmt_api_fn=lambda *a, **k: [],
        uid_override=frozenset({TEST_UID}), uid_override_expires_at=_FUTURE,
    )
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky",
                             "guarded_sql": "insert into t values (1)",
                             "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp == {"status": "ok", "rows": []}

def test_no_override_write_behavior_unchanged_end_to_end(tmp_path):
    # No override armed at all: an INSERT reaches the mgmt API exactly as
    # before this feature existed (byte-for-byte unchanged behavior).
    seen = []
    db = FakeDb()
    _, sock_path, db = _make_server(
        tmp_path, frozenset({TEST_UID}), db=db, mgmt_api_fn=lambda sql, ref, tok: seen.append((sql, ref)) or [],
    )
    resp = _send(sock_path, {"project_ref": "oovskwogvefnznfowjky",
                             "guarded_sql": "insert into t values (1)",
                             "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp == {"status": "ok", "rows": []}
    assert seen == [("insert into t values (1)", "oovskwogvefnznfowjky")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/sheikhmusa/wingmen/orchestrator/.venv/bin/python -m pytest tests/exec_broker/test_server.py -v -k readonly`
Expected: FAIL — `READ_ONLY_OVERRIDE` never returned (first test fails on code `"INTERNAL_ERROR"`/`"GATE_INVALID"`-adjacent behavior or succeeds wrongly; the allow-tests fail only if wiring is missing... at this step ALL the read_only-refusal asserts fail since nothing refuses).

- [ ] **Step 3: Wire the check into `server.py`**

Add `from .read_only import check_read_only_override` to the imports. In `_handle_connection_inner`'s validation block, immediately after the `check_project_ref(...)` call and before `require_gate(...)`, add (same `now` the other checks use):

```python
check_read_only_override(
    req.guarded_sql,
    ref_override=self.ref_override,
    ref_override_expires_at=self.ref_override_expires_at,
    now=now,
)
```

It raises `BrokerValidationError`, so the existing `except BrokerValidationError` handler already finalizes the row and responds — no other change. Order note (docstring/comment): ref allowlist first, then RO mode, then gate — so an armed+expired override still yields BAD_PROJECT_REF for its ref, and RO refusals are only reachable for refs that passed the allowlist.

- [ ] **Step 4: Run tests to verify they pass — the whole server module**

Run: `/Users/sheikhmusa/wingmen/orchestrator/.venv/bin/python -m pytest tests/exec_broker/test_server.py -v`
Expected: PASS (new tests + every pre-existing server test green — proves no regression in the unchanged paths).

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/exec_broker/server.py tests/exec_broker/test_server.py
git commit -m "hc95 exec-broker: enforce read-only while ref override is live (end-to-end)"
```

### Task 6: full suite, deploy file list, PR

**Files:**
- Modify: none (verification + PR)
- Produces: PR to `fable/substrate-safe-fixes` with the exact deploy file list in the body.

- [ ] **Step 1: Run the full exec-broker + daemon-config + service-unit test set**

Run: `/Users/sheikhmusa/wingmen/orchestrator/.venv/bin/python -m pytest tests/exec_broker/ tests/test_exec_broker_daemon_config.py tests/test_exec_broker_service_unit.py tests/test_exec_broker_access_log_isolation.py -v`
Expected: PASS (daemon-config and service-unit tests must be green UNCHANGED — no env/daemon/unit edits are part of this feature; if either fails, STOP and investigate before proceeding: it means this feature leaked somewhere it shouldn't).

- [ ] **Step 2: Run the full repo suite**

Run: `/Users/sheikhmusa/wingmen/orchestrator/.venv/bin/python -m pytest tests/ -x -q --ignore=tests/console` (same invocation as the PR#434/#442/#443 cycles)
Expected: exactly the one pre-existing unrelated failure (`tests/test_console_deploy_quality_gate_shadow.py::test_shadow_wrapper_produces_a_parseable_verdict_with_real_python`, confirmed pre-existing during PR#434) — anything else is a regression from this branch.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin lane/hc95-readonly-override
gh pr create --base fable/substrate-safe-fixes --title "hc#95 exec-broker: SELECT-only override mode (RO enforced while armed)" --body-file <scratchpad-file>
```

PR body must contain, verbatim:
- Assignment refs: bus #79024, GO #78974, arm op #78968 ("RO promised, not enforced" → now enforced); design ack #79037.
- The two flagged design rulings: broker-wide RO while armed (QA writes also refused for the window); uid override does NOT trigger RO.
- The KNOWN LIMITATION: text screen, not semantic analyzer — an existing user-defined write-capable function called by name from a SELECT is indistinguishable; compensating controls (bounded ≤24h window, full-SQL audit row per call, console-gated arming).
- The wire note for coord: `READ_ONLY_OVERRIDE` is an additive seventh §5 error code; response shape unchanged; exec_prod's client must tolerate an unknown code string.
- **Exact deploy file list for the `/opt/irsyad-exec-broker` hand-copy (console: copy + sha-verify + restart):**
  - `scripts/lib/exec_broker/read_only.py` (NEW)
  - `scripts/lib/exec_broker/gate_validation.py` (modified)
  - `scripts/lib/exec_broker/protocol.py` (modified)
  - `scripts/lib/exec_broker/server.py` (modified)
  - `scripts/exec_broker_daemon.py` — UNCHANGED (verified; listed so the sha-verify is explicit)
  - All other `tests/exec_broker/*`, `tests/test_exec_broker_*.py`, plan doc: repo-only, not in /opt.

End with the Co-Authored-By line per repo policy, plus the Claude Code attribution line.

- [ ] **Step 4: CI green, then notify reviewers**

Monitor `gh pr checks` until green (use `gh pr checks <n> --json bucket --jq 'all(.[]; .bucket != "pending")'` — the plain-text grep form once returned prematurely). Then bus cc-fleet-health (P2, review request, PR link + one-line summary of the two design rulings + the file list) and orch-console (P2, informational: PR open, cc-fleet-health reviewing per #79024). Do NOT merge before cc-fleet-health's review (assignment: "I review every PR before merge").

- [ ] **Step 5: Merge on approval (CI green + head-SHA re-check immediately before merge)**

Re-verify `gh pr view <n> --json headRefOid` matches local HEAD exactly, then `gh pr merge <n> --squash --delete-branch=false`. Post-merge: confirm to cc-fleet-health and orch-console on the bus with the squash SHA.

---

## Amendment (2026-10-28, post-execution — appends, does not rewrite, the rulings above)

1. **SCOPE REVERSAL (cc-fleet-health bus #79039):** the "RO applies broker-wide while armed" ruling (line 17 / Task 4's docstring spec / Task 6's review-focus line) is REVERSED. RO binds only requests whose `project_ref` equals the live override ref; baseline (always-allowed) refs behave exactly as today even while armed. A ref that cannot be determined never reaches the RO check — `parse_request` requires `project_ref` and the server runs the ref allowlist check FIRST (ordering pinned end-to-end per bus #79103). Implemented in commit 8e73440 with `check_read_only_override` gaining a required `project_ref` kwarg; the broker-wide e2e test was replaced by `test_ref_override_readonly_scoped_to_override_ref_baseline_write_passes`.
2. **Whole-branch review findings (fix pass, RED→GREEN each):** Critical #1 — the vendored lexer can be blinded by legal SQL (', --, /*, $tag$ inside double-quoted identifiers; E'' backslash escapes), hiding denylisted writes from every scan; fixed by the `_lexically_hazardous` pre-screen in `read_only.py` (commit 5ffebf5), vendored pin untouched. Important #2 + Minor #6 — `lo_open`/`lo_write`/`lo_close` and the rest of the advisory/stat-reset family added to the denylist (commit d0f1d1a). Minor #3 (`seen == []` on refusal shapes) and Minor #5 (docstring reuse list) folded into the same pass. The upstream lexer fix + re-vendor under a new pin is queued post-pool-flip (irsyad HOLD, bus #79103/#79105).
3. **Guard ruling:** console_irsyad_guard fired on the #79103 pin-test edit (real irsyad ref literal as fixture); the fixture was switched to a synthetic non-allowlisted ref — the property under test is allowlist-before-RO ordering, which any non-allowlisted ref proves. Disclosed to cc-fleet-health in the PR notification.
