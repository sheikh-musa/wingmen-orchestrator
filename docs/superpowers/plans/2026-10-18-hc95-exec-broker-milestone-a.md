# hc#95 exec-broker — milestone (a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the hc#95 token-behind-vault exec-broker daemon (the Python unix-socket service that will become the ONLY holder of the irsyad `SUPABASE_ACCESS_TOKEN`), proven by tests only — milestone (a) of the agreed rollout. This plan does NOT build the `exec_prod.py` `EXEC_TRANSPORT=broker|direct` switch (cc-irsyad-2's separate PR, coordinate not duplicate), does NOT apply the `vault_access_log` migration (orch-console applies via direct psycopg, never `supabase db push`), does NOT install the systemd unit (orch-console/provisioning), and does NOT attempt rollout steps (b)–(e) (overlap/parity reconcile, full cutover, PAT removal+rotation, standing reconcile).

**Architecture:** A self-contained Python package (`scripts/lib/exec_broker/`) with no cross-repo imports — each unit (protocol parsing, DDL-shape detection, gate re-validation, append-only logging, the actual mgmt-API call, SO_PEERCRED ACL) is independently testable and wired together by a thin `BrokerServer` class. A CLI entrypoint (`scripts/exec_broker_daemon.py`) is the systemd `ExecStart` target. A `migrations/drafts/` SQL file and a `deploy/*.service` unit are produced as review artifacts, not applied/installed by this plan.

**Tech Stack:** Python 3.9 (`from __future__ import annotations` everywhere for PEP604 hints), stdlib `socket`/`struct` for the unix socket + SO_PEERCRED, `psycopg` (v3, matching `scripts/lib/fleet_health_lease.py`'s convention) + `python-dotenv` for DB access, `subprocess`+`curl` for the mgmt-API POST (parity with `exec_prod.py`'s own transport, no new HTTP-client dependency), `pytest` (repo convention, `tests/` at root with per-feature subdirectories like `tests/console/`).

**Spec:** `reports/hc95-broker-handoff-pack-20261018.md` (this repo) + bus #56692 (spec) / #56697 (accepted conditions) / #56789 (socket-contract agreement) + `ihsanos-irsyad` main `reports/hc95-exec-broker-contract.md` (squash commit `5053826e`, FROZEN schema).

## Global Constraints

- No live writes anywhere: no real prod, no client silos, no actual PAT in any test — synthetic/mocked only (kickoff brief, hard boundary).
- Fail-closed everywhere: an unreachable DB, a malformed request, a bug, or an unsupported platform must always produce an `error` response or refuse, NEVER silently fall through to "allow."
- `project_ref` re-validation is an **exact-string allowlist, QA-ONLY for milestone (a)** (orch-console gate ruling, PR#434/bus #70919 — enforced in code, not by provisioning alone): `gate_validation.ALLOWED_PROJECT_REFS = {SILO_ALIASES["qa"]}`, even though `SILO_ALIASES` (the full reference table, used for silo-mention matching) names all 3 real irsyad refs — confirmed by reading `exec_prod.py`'s `_SILO_ALIASES` on `ihsanos-irsyad` origin/main (`goumlyne`→`goumlynecruxrlmzlntp`, `ceayj`→`ceayjeamtmcyzzvqflus`, `qa`/`irsyad-qa`→`oovskwogvefnznfowjky`) and independently re-verified by cc-irsyad-coord-1 (bus #70875). Widening `ALLOWED_PROJECT_REFS` to the other 2 refs must not happen before rollout step (c)'s option (2) hardening lands (see the gate re-validation bullet below).
- DDL-shape detection reuses the vendored `ddl_detect.py` (sha256 `a0895e0370751f113fc14dbde3611df53a3360700419ffecf7f3ef3630f9ae25`, verified byte-identical to `ihsanos-irsyad` main's `scripts/db/lib/ddl_detect.py` as of commit `eba13a7b`/#1051) — vendor a **second** pinned copy inside `scripts/lib/exec_broker/` (not a cross-import of `scripts/hooks/ddl_detect.py`) so the broker package is self-contained and deployable to the dedicated `irsyad-exec` unix user without depending on that user having read access into the rest of this checkout.
- Gate re-validation rules are vendored from `ihsanos-irsyad` main's `exec_prod.py` (sha256 `9bc04456e03430f500ce6748524208c49c3eccd394f14cc7239e70f2af348a03`, commit `5053826e`): `from_agent ∈ {orch-console, cai}`, `message_type ∈ {decision, agreed}`, `created_at` in the past, whole-word silo mention.
- **RESOLVED (orch-console ruling, bus #70876, answering #70870):** the frozen wire contract has no `migration`/`pr` field, so the "digit-boundary PR/migration match" rule from `exec_prod._require_gate` cannot be re-checked by the broker as specified. **Milestone (a) (QA ref only): OPTION (1)** — skip that one sub-check, re-check everything else (from-agent/type/is_past/silo-mention); `require_gate()`'s signature already reserves optional `migration`/`pr` kwargs so adding the check later is additive. **HARD REQUIREMENT, carried forward (not this plan's to implement): OPTION (2) — the full independent migration/PR re-check — is MANDATORY before the broker ever touches a REAL ref (rollout step (c))**, since at that point the broker must not trust that the caller already did the match locally (the whole never-trust-the-caller principle). That requires amending the frozen §2 contract to add a `migration`/`pr` field (cc-irsyad-2 populates it) — orch-console will route that gated contract change through coord + cc-fleet-health + cc-irsyad-2 when rollout reaches step (c). This PR does not implement option (2); it must not be treated as closing this item.
- Confirmed (cc-irsyad-coord-1, bus #70875, independently re-verified against `exec_prod.py` L233-238 and `docs/irsyad-ground-truth.md`): the 3 refs are current/correct/complete (no 4th silo, no rotation pending) — note the shape: 4 alias KEYS (`goumlyne`, `ceayj`, `qa`, `irsyad-qa`) but only 3 DISTINCT ref VALUES (`qa`/`irsyad-qa` are two aliases for the same QA ref). `gate_validation.ALLOWED_PROJECT_REFS` is built as `set(SILO_ALIASES.values())`, so it already collapses to 3, not 4. The real socket caller on gzb is the `gazzai` OS user (uid 1000) — orch-console provisions `irsyad-exec` with `gazzai` as the sole group member; this plan does not action that (provisioning is orch-console's pen).
- Never write an explicit `id` into a serial/identity-column insert (standing fleet rule) — `vault_access_log.id` is `GENERATED BY DEFAULT AS IDENTITY`; the writer always uses `RETURNING id`.
- `vault_access_log` logs the row **before** executing (`status=started`) and finalizes the same row after, on every outcome including a validation refusal — a rejected call must still produce an audited row, not a silent no-op.
- Timeouts: broker's own read-one-line from an accepted connection: 30s (matches the contract's request/response round-trip budget). Broker's own mgmt-API POST: `curl --max-time 25` (leaves headroom under the 30s so the broker can still finalize+respond within its own 30s window) plus a `subprocess.run(timeout=28)` backstop.
- Python 3.9 compatibility: no runtime-evaluated `X | Y` outside annotations (the repo's existing files all rely on `from __future__ import annotations` for this — follow the same pattern, never `isinstance(x, int | None)`).

## Review Focus

- A connection from a non-allowlisted unix UID must get `ACL_DENIED` and the attempt must be visible in the daemon's own log (not silently dropped) — Task 6/7's tests exercise a denied UID.
- A `gate_id` key that is *entirely absent* from the JSON (not `null`) must be treated as malformed, distinct from a present-but-`null` `gate_id` (DML, no gate needed) — Task 1's tests cover both shapes explicitly, since conflating them would make a buggy caller's omission silently pass through as "no gate needed."
- A gate row that exists, is from the right agent/type, and is in the past, but mentions the WRONG silo (e.g. a goumlyne decision cited to authorize a ceayj apply) must be rejected — Task 3's tests include a cross-silo gate to catch the "any valid-shaped gate authorizes anything" trap this repo has hit before (cc-quality #56681).
- The mgmt-API call failing with an HTTP-error-shaped JSON body (not a network exception) must still be classified `UPSTREAM_ERROR`, not silently treated as success because curl itself exited 0 — Task 5's tests include this body shape, not just a network-down case.
- A `vault_access_log` row logged for a REFUSED request (bad project_ref / gate required / gate invalid) must exist and be finalized to `status=error` — not skipped because the call never reached the mgmt-API — Task 7's tests assert the log call sequence on every refusal path, not just the success path.

---

### Task 1: Wire protocol (request parse / response encode)

**Files:**
- Create: `scripts/lib/exec_broker/__init__.py`
- Create: `scripts/lib/exec_broker/protocol.py`
- Test: `tests/exec_broker/__init__.py`
- Test: `tests/exec_broker/test_protocol.py`

**Interfaces:**
- Produces: `ErrorCode(str, Enum)` with members `ACL_DENIED, BAD_PROJECT_REF, GATE_REQUIRED, GATE_INVALID, UPSTREAM_ERROR, INTERNAL_ERROR`; `class MalformedRequest(ValueError)`; `@dataclass(frozen=True) class BrokerRequest(project_ref: str, guarded_sql: str, reason: str, gate_id: str | None, caller: str, exec_ledger_id: str)`; `def parse_request(line: str) -> BrokerRequest`; `def encode_ok(rows: list) -> bytes`; `def encode_error(code: ErrorCode, message: str) -> bytes`.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_request_valid_dml_no_gate():
    line = '{"project_ref":"oovskwogvefnznfowjky","guarded_sql":"select 1","reason":"r","gate_id":null,"caller":"cc-irsyad-2","exec_ledger_id":"abc-123"}'
    req = parse_request(line)
    assert req.gate_id is None
    assert req.project_ref == "oovskwogvefnznfowjky"

def test_parse_request_valid_ddl_with_gate():
    line = '{"project_ref":"goumlynecruxrlmzlntp","guarded_sql":"alter table t add column x int","reason":"r","gate_id":"56485","caller":"cc-irsyad-2","exec_ledger_id":"abc-124"}'
    assert parse_request(line).gate_id == "56485"

def test_parse_request_missing_gate_id_key_raises():
    line = '{"project_ref":"x","guarded_sql":"select 1","reason":"r","caller":"c","exec_ledger_id":"e"}'
    with pytest.raises(MalformedRequest):
        parse_request(line)

def test_parse_request_missing_other_required_field_raises():
    line = '{"project_ref":"x","guarded_sql":"select 1","reason":"r","gate_id":null,"exec_ledger_id":"e"}'  # no caller
    with pytest.raises(MalformedRequest):
        parse_request(line)

def test_parse_request_bad_json_raises():
    with pytest.raises(MalformedRequest):
        parse_request("not json")

def test_encode_ok_roundtrip():
    payload = json.loads(encode_ok([{"a": 1}]).decode())
    assert payload == {"status": "ok", "rows": [{"a": 1}]}

def test_encode_error_roundtrip():
    payload = json.loads(encode_error(ErrorCode.BAD_PROJECT_REF, "nope").decode())
    assert payload == {"status": "error", "error": {"code": "BAD_PROJECT_REF", "message": "nope"}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/exec_broker/test_protocol.py -v`
Expected: FAIL (module `exec_broker.protocol` not found)

- [ ] **Step 3: Implement `protocol.py`**

`parse_request` does `json.loads` (catch `json.JSONDecodeError` → `MalformedRequest`), then checks all 6 keys (`project_ref`, `guarded_sql`, `reason`, `gate_id`, `caller`, `exec_ledger_id`) are present as dict keys (missing key → `MalformedRequest`, even if the value would've been falsy), validates `project_ref`/`guarded_sql`/`caller`/`exec_ledger_id` are non-empty strings and `reason` is a string (may be empty), `gate_id` is `None` or a non-empty string. Each response encoder returns `json.dumps({...}, sort_keys=True).encode("utf-8") + b"\n"` (newline-delimited per contract §1).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/exec_broker/test_protocol.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/exec_broker/__init__.py scripts/lib/exec_broker/protocol.py tests/exec_broker/__init__.py tests/exec_broker/test_protocol.py
git commit -m "feat(exec-broker): wire protocol parse/encode"
```

---

### Task 2: Vendor `ddl_detect.py` into the broker package

**Files:**
- Create: `scripts/lib/exec_broker/ddl_detect.py` (verbatim copy of `ihsanos-irsyad` main's `scripts/db/lib/ddl_detect.py`, i.e. byte-identical to this repo's existing `scripts/hooks/ddl_detect.py`)
- Test: `tests/exec_broker/test_ddl_detect_vendor_pin.py`

**Interfaces:**
- Produces: `is_ddl_shaped(sql: str) -> bool` (used by Task 3).

- [ ] **Step 1: Copy the file**

`cp scripts/hooks/ddl_detect.py scripts/lib/exec_broker/ddl_detect.py` — then add one line at the top of the new copy's docstring: `# Vendored verbatim from ihsanos-irsyad scripts/db/lib/ddl_detect.py (sha256 a0895e0370751f113fc14dbde3611df53a3360700419ffecf7f3ef3630f9ae25) into exec_broker's own package so the broker stays self-contained (see plan Global Constraints). Re-sync by re-copying verbatim + updating this pin; never hand-edit.`

- [ ] **Step 2: Write the failing self-check test**

```python
def test_vendored_ddl_detect_matches_its_own_pinned_hash():
    import hashlib, re
    src = VENDORED_PATH.read_text()
    m = re.search(r"sha256 ([0-9a-f]{64})\)", src)
    assert m, "no sha256 pin comment found -- did it move?"
    assert hashlib.sha256(VENDORED_PATH.read_bytes()).hexdigest() == m.group(1) or True
    # the pin targets the UPSTREAM source hash, not this file's own hash (this file
    # has an extra docstring line) -- instead diff logic against scripts/hooks/ddl_detect.py
    # byte-for-byte MINUS the added pin line:
    hooks_copy = hashlib.sha256(HOOKS_PATH.read_bytes()).hexdigest()
    assert hooks_copy == "a0895e0370751f113fc14dbde3611df53a3360700419ffecf7f3ef3630f9ae25"
```

(Write the real version in Step 2 per the actual diff strategy chosen in Step 3 below — the snippet above names the two files to compare and the exact upstream hash to assert; finalize the assertion shape once the vendored file's exact header is written.)

- [ ] **Step 3: Decide and implement the self-check**

Compare `scripts/lib/exec_broker/ddl_detect.py`'s content, with the one added pin-comment line stripped out, against `scripts/hooks/ddl_detect.py`'s content (which is itself pinned to `a0895e03...` by the existing `test_vendored_ddl_detect_matches_its_own_pinned_hash` test in `tests/test_secrets_transcript_guard.py`). Assert equality, so a hand-edit of either vendored copy is caught by drift between the two, not just drift from upstream.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/exec_broker/test_ddl_detect_vendor_pin.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/exec_broker/ddl_detect.py tests/exec_broker/test_ddl_detect_vendor_pin.py
git commit -m "feat(exec-broker): vendor pinned ddl_detect copy"
```

---

### Task 3: Gate + project_ref re-validation

**Files:**
- Create: `scripts/lib/exec_broker/gate_validation.py`
- Test: `tests/exec_broker/test_gate_validation.py`

**Interfaces:**
- Consumes: `is_ddl_shaped` from Task 2's `exec_broker.ddl_detect`; `ErrorCode` from Task 1's `exec_broker.protocol`.
- Produces: `class BrokerValidationError(Exception)` with `.code: ErrorCode` and `.message: str`; `SILO_ALIASES: dict[str, str]`; `ALLOWED_PROJECT_REFS: set[str]`; `def check_project_ref(project_ref: str) -> None`; `def require_gate(guarded_sql: str, gate_id: str | None, project_ref: str, cur, migration: str | None = None, pr: str | None = None) -> None` (raises `BrokerValidationError`; no-op if `guarded_sql` is not DDL-shaped). `cur` is a `psycopg` cursor opened with `row_factory=psycopg.rows.dict_row` — `require_gate` calls `cur.execute(...)` then `cur.fetchone()` and indexes the result by column name (`row["from_agent"]`, etc.).

- [ ] **Step 1: Write the failing tests**

```python
def test_check_project_ref_allows_the_three_refs():
    for ref in ("goumlynecruxrlmzlntp", "ceayjeamtmcyzzvqflus", "oovskwogvefnznfowjky"):
        check_project_ref(ref)  # no raise

def test_check_project_ref_rejects_unknown_ref():
    with pytest.raises(BrokerValidationError) as exc:
        check_project_ref("some-other-ref")
    assert exc.value.code == ErrorCode.BAD_PROJECT_REF

def test_require_gate_noop_for_dml():
    require_gate("select 1", None, "oovskwogvefnznfowjky", cur=None)  # no raise, cur never touched

def test_require_gate_required_for_ddl_without_gate():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("alter table t add column x int", None, "goumlynecruxrlmzlntp", cur=FakeCursor(None))
    assert exc.value.code == ErrorCode.GATE_REQUIRED

def test_require_gate_invalid_when_gate_not_found():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "999999", "goumlynecruxrlmzlntp", cur=FakeCursor(None))
    assert exc.value.code == ErrorCode.GATE_INVALID

def test_require_gate_invalid_when_wrong_from_agent():
    row = {"from_agent": "cc-hc95-broker", "message_type": "decision", "subject": "goumlyne", "body": "", "is_past": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))
    assert exc.value.code == ErrorCode.GATE_INVALID

def test_require_gate_invalid_when_wrong_silo_mentioned():
    # cross-silo trap: a real, valid-shaped gate for a DIFFERENT silo must not authorize this one
    row = {"from_agent": "orch-console", "message_type": "decision", "subject": "apply to ceayj", "body": "", "is_past": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))
    assert exc.value.code == ErrorCode.GATE_INVALID

def test_require_gate_passes_with_valid_gate():
    row = {"from_agent": "orch-console", "message_type": "decision", "subject": "apply to goumlyne", "body": "", "is_past": True}
    require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))  # no raise
```

`FakeCursor` is a tiny test helper (in the test file) whose `.execute()` records the call and whose `.fetchone()` returns the dict passed to its constructor (or `None`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/exec_broker/test_gate_validation.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `gate_validation.py`**

Port `_GATE_AGENTS`, `_GATE_TYPES`, `_SILO_ALIASES` (renamed `SILO_ALIASES`, public), and the whole-word silo-mention check (`_text_mentions_silo` equivalent, ported verbatim in logic from `exec_prod.py`'s `_needles_for_silo`/`_text_mentions_silo`) from the vendored source (pin comment citing sha256 `9bc04456e03430f500ce6748524208c49c3eccd394f14cc7239e70f2af348a03`, commit `5053826e`). `require_gate`: return early if `not is_ddl_shaped(guarded_sql)`; raise `GATE_REQUIRED` if no `gate_id`; raise `GATE_INVALID` if not all-digits; `cur.execute("SELECT from_agent, message_type, subject, body, (created_at < now()) AS is_past FROM agent_messages WHERE id = %s", (int(gate_id),))` then `cur.fetchone()`; raise `GATE_INVALID` (with a specific message each) for: row is `None`, `from_agent` not in `_GATE_AGENTS`, `message_type` not in `_GATE_TYPES`, not `is_past`, or `_text_mentions_silo` is `False` for `project_ref`. The `migration`/`pr` params are accepted but unused in this version (see Global Constraints note) — do not call `_text_mentions_migration_or_pr` yet.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/exec_broker/test_gate_validation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/exec_broker/gate_validation.py tests/exec_broker/test_gate_validation.py
git commit -m "feat(exec-broker): gate + project_ref re-validation"
```

---

### Task 4: Append-only vault_access_log writer

**Files:**
- Create: `scripts/lib/exec_broker/vault_log.py`
- Test: `tests/exec_broker/test_vault_log.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class VaultLogEntry(exec_ledger_id: str, caller: str, project_ref: str, guarded_sql: str, reason: str, gate_id: str | None)`; `def log_started(cur, entry: VaultLogEntry) -> int` (returns the new row's `id`, read back via `RETURNING id` — never writes an explicit id); `def log_finalized(cur, row_id: int, status: str, error_code: str | None) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_log_started_inserts_with_sha256_and_returns_id():
    cur = FakeCursor(fetchone_result={"id": 42})
    entry = VaultLogEntry("e1", "cc-irsyad-2", "goumlynecruxrlmzlntp", "select 1", "reason", None)
    row_id = log_started(cur, entry)
    assert row_id == 42
    sql, params = cur.calls[0]
    assert "INSERT INTO vault_access_log" in sql
    assert "RETURNING id" in sql
    assert "id" not in sql.split("(")[1].split(")")[0]  # column list never includes id
    assert params[3] == hashlib.sha256(b"select 1").hexdigest()

def test_log_finalized_updates_same_row_never_inserts():
    cur = FakeCursor()
    log_finalized(cur, 42, "ok", None)
    sql, params = cur.calls[0]
    assert sql.strip().upper().startswith("UPDATE")
    assert 42 in params

def test_log_finalized_error_records_code():
    cur = FakeCursor()
    log_finalized(cur, 42, "error", "GATE_INVALID")
    _, params = cur.calls[0]
    assert "GATE_INVALID" in params
```

`FakeCursor` records every `(sql, params)` pair passed to `.execute()` in `self.calls`, and `.fetchone()` returns a preset dict.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/exec_broker/test_vault_log.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `vault_log.py`**

`log_started`: `cur.execute("INSERT INTO vault_access_log (exec_ledger_id, caller, project_ref, sql_sha256, reason, gate_id, status, ts_start) VALUES (%s,%s,%s,%s,%s,%s,'started',now()) RETURNING id", (entry.exec_ledger_id, entry.caller, entry.project_ref, hashlib.sha256(entry.guarded_sql.encode()).hexdigest(), entry.reason, entry.gate_id))` then `return cur.fetchone()["id"]`. `log_finalized`: `cur.execute("UPDATE vault_access_log SET status=%s, error_code=%s, ts_end=now() WHERE id=%s", (status, error_code, row_id))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/exec_broker/test_vault_log.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/exec_broker/vault_log.py tests/exec_broker/test_vault_log.py
git commit -m "feat(exec-broker): append-only vault_access_log writer"
```

---

### Task 5: mgmt-API transport

**Files:**
- Create: `scripts/lib/exec_broker/mgmt_api.py`
- Test: `tests/exec_broker/test_mgmt_api.py`

**Interfaces:**
- Produces: `MGMT_QUERY_URL: str` (`"https://api.supabase.com/v1/projects/{ref}/database/query"`, same template as `exec_prod.py`); `class UpstreamError(Exception)`; `def post_to_mgmt_api(guarded_sql: str, project_ref: str, token: str, timeout: float = 25.0) -> list`.

- [ ] **Step 1: Write the failing tests**

```python
def test_post_success_returns_rows(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(0, '[{"a":1}]', ""))
    assert post_to_mgmt_api("select 1", "ref", "tok") == [{"a": 1}]

def test_post_curl_nonzero_exit_raises_upstream_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(7, "", "boom"))
    with pytest.raises(UpstreamError):
        post_to_mgmt_api("select 1", "ref", "tok")

def test_post_malformed_json_raises_upstream_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(0, "not json", ""))
    with pytest.raises(UpstreamError):
        post_to_mgmt_api("select 1", "ref", "tok")

def test_post_error_shaped_body_raises_upstream_error(monkeypatch):
    # curl exits 0 (HTTP-level 4xx/5xx isn't a curl failure) but the body is an error dict, not a rows list
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(0, '{"message":"not found"}', ""))
    with pytest.raises(UpstreamError):
        post_to_mgmt_api("select 1", "ref", "tok")

def test_post_timeout_raises_upstream_error(monkeypatch):
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="curl", timeout=28)
    monkeypatch.setattr(subprocess, "run", _raise)
    with pytest.raises(UpstreamError):
        post_to_mgmt_api("select 1", "ref", "tok")

def test_post_never_includes_token_in_a_raised_message(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(7, "", "boom"))
    with pytest.raises(UpstreamError) as exc:
        post_to_mgmt_api("select 1", "ref", "secret-token-value")
    assert "secret-token-value" not in str(exc.value)
```

`FakeProc` is a tiny namedtuple-like test helper with `.returncode`, `.stdout`, `.stderr`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/exec_broker/test_mgmt_api.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `mgmt_api.py`**

Build the same curl invocation shape as `exec_prod.py`'s `_post_to_mgmt_api` (POST, Bearer token header, JSON body via stdin `-d @-`), add `"--max-time", "25"` to the curl argv, and wrap the `subprocess.run(..., timeout=28)` call in `try/except subprocess.TimeoutExpired` → `UpstreamError` (fixed message, never includes `stderr`/token). On `returncode != 0` → `UpstreamError` (fixed message + exit code only, never `stderr` verbatim — same host/port-leak discipline as `exec_prod.py`'s `_fetch_gate_message`). Parse `stdout` with `json.loads` (catch `json.JSONDecodeError` → `UpstreamError`); if the parsed value is not a `list` → `UpstreamError("upstream returned an error-shaped response")`; else `return parsed`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/exec_broker/test_mgmt_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/exec_broker/mgmt_api.py tests/exec_broker/test_mgmt_api.py
git commit -m "feat(exec-broker): mgmt-API transport"
```

---

### Task 6: SO_PEERCRED ACL

**Files:**
- Create: `scripts/lib/exec_broker/acl.py`
- Test: `tests/exec_broker/test_acl.py`

**Interfaces:**
- Produces: `class AclDenied(Exception)`; `def peer_uid(conn: socket.socket) -> int`; `def check_acl(uid: int, allowed_uids: frozenset[int]) -> None` (raises `AclDenied`).

- [ ] **Step 1: Write the failing tests**

```python
def test_check_acl_allows_listed_uid():
    check_acl(1000, frozenset({1000, 1001}))  # no raise

def test_check_acl_denies_unlisted_uid():
    with pytest.raises(AclDenied):
        check_acl(2000, frozenset({1000, 1001}))

@pytest.mark.skipif(sys.platform != "linux", reason="SO_PEERCRED is Linux-only")
def test_peer_uid_over_real_socketpair():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert peer_uid(a) == os.getuid()
    finally:
        a.close(); b.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/exec_broker/test_acl.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `acl.py`**

`peer_uid`: `creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")); return struct.unpack("3i", creds)[1]`. `check_acl`: `if uid not in allowed_uids: raise AclDenied(f"uid {uid} is not on the broker's allowlist")`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/exec_broker/test_acl.py -v`
Expected: PASS (on macOS dev machine the skipif test is skipped; it runs for real in Linux CI)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/exec_broker/acl.py tests/exec_broker/test_acl.py
git commit -m "feat(exec-broker): SO_PEERCRED ACL"
```

---

### Task 7: BrokerServer (ties it together) + end-to-end test

**Files:**
- Create: `scripts/lib/exec_broker/server.py`
- Test: `tests/exec_broker/test_server.py`

**Interfaces:**
- Consumes: everything from Tasks 1, 3, 4, 5, 6 by their exact names above.
- Produces: `class BrokerServer(sock_path: str, allowed_uids: frozenset[int], db_conn_factory: Callable[[], ContextManager], token: str, mgmt_api_fn: Callable = post_to_mgmt_api)`; `.serve_forever() -> None`; `.handle_connection(conn: socket.socket) -> None` (one request/response cycle; never raises — always writes exactly one response line then closes `conn`).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.skipif(sys.platform != "linux", reason="SO_PEERCRED is Linux-only")
def test_dml_only_success_end_to_end(tmp_path):
    server, client_sock_factory = _make_server(tmp_path, allowed_uids=frozenset({os.getuid()}),
                                                 db=FakeDb(), mgmt_api_fn=lambda *a, **k: [{"a": 1}])
    resp = _send(server, client_sock_factory, {"project_ref": "oovskwogvefnznfowjky", "guarded_sql": "select 1",
                                                "reason": "r", "gate_id": None, "caller": "cc-irsyad-2", "exec_ledger_id": "e1"})
    assert resp == {"status": "ok", "rows": [{"a": 1}]}
    assert server_db_log_calls(db) == [("started", None), ("ok", None)]  # exact shape asserted in implementation, see step 3 note

def test_acl_denied_for_unlisted_uid(tmp_path):
    server, client_sock_factory = _make_server(tmp_path, allowed_uids=frozenset({os.getuid() + 1}), db=FakeDb(), mgmt_api_fn=None)
    resp = _send(server, client_sock_factory, {"project_ref": "x", "guarded_sql": "select 1", "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "ACL_DENIED"

def test_malformed_json_returns_internal_error(tmp_path):
    server, client_sock_factory = _make_server(tmp_path, allowed_uids=frozenset({os.getuid()}), db=FakeDb(), mgmt_api_fn=None)
    resp = _send_raw(server, client_sock_factory, b"not json\n")
    assert resp["error"]["code"] == "INTERNAL_ERROR"

def test_bad_project_ref_logs_started_then_error(tmp_path):
    db = FakeDb()
    server, client_sock_factory = _make_server(tmp_path, allowed_uids=frozenset({os.getuid()}), db=db, mgmt_api_fn=None)
    resp = _send(server, client_sock_factory, {"project_ref": "not-a-real-ref", "guarded_sql": "select 1", "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "BAD_PROJECT_REF"
    assert db.rows[0]["status"] == "error"  # started, then finalized to error -- never a silent no-op

def test_gate_required_for_ddl_without_gate(tmp_path):
    db = FakeDb()
    server, client_sock_factory = _make_server(tmp_path, allowed_uids=frozenset({os.getuid()}), db=db, mgmt_api_fn=None)
    resp = _send(server, client_sock_factory, {"project_ref": "goumlynecruxrlmzlntp", "guarded_sql": "drop table t", "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "GATE_REQUIRED"

def test_upstream_error_propagates(tmp_path):
    def _boom(*a, **k): raise UpstreamError("upstream down")
    server, client_sock_factory = _make_server(tmp_path, allowed_uids=frozenset({os.getuid()}), db=FakeDb(), mgmt_api_fn=_boom)
    resp = _send(server, client_sock_factory, {"project_ref": "oovskwogvefnznfowjky", "guarded_sql": "select 1", "reason": "", "gate_id": None, "caller": "c", "exec_ledger_id": "e"})
    assert resp["error"]["code"] == "UPSTREAM_ERROR"
```

`_make_server`/`_send`/`_send_raw`/`FakeDb` are test-file helpers: `_make_server` builds a `BrokerServer` bound to a real `AF_UNIX` socket at `tmp_path / "broker.sock"`, runs `serve_forever` in a background thread; `_send` connects a real client socket, writes the JSON-encoded request + `b"\n"`, reads one line back, `json.loads`s it; `FakeDb` is a context-manager-returning factory whose cursor is the same `FakeCursor` shape used in Tasks 3/4, additionally recording every `log_started`/`log_finalized` call into `.rows` (list of dicts with at least `status`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/exec_broker/test_server.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `server.py`**

`handle_connection`: (1) `uid = acl.peer_uid(conn)`; on `check_acl` raising `AclDenied` → log via `logging.warning(...)` (uid, no vault row — no parsed request yet), write `encode_error(ACL_DENIED, str(e))`, return. (2) Read one line with `conn.settimeout(30)` then `conn.makefile("rb").readline()`; on empty/timeout → `encode_error(INTERNAL_ERROR, "request timeout")`, return. (3) `parse_request(line)`; on `MalformedRequest` → `encode_error(INTERNAL_ERROR, "malformed request")`, return. (4) Open `with self.db_conn_factory() as db_conn, db_conn.cursor() as cur:` — `row_id = log_started(cur, VaultLogEntry(...))`; `db_conn.commit()` (started row must be durable before any mgmt-API call). (5) `try: check_project_ref(...); require_gate(...)` — on `BrokerValidationError as e`: `log_finalized(cur, row_id, "error", e.code.value); db_conn.commit(); encode_error(e.code, e.message)`, return. (6) `try: rows = self.mgmt_api_fn(req.guarded_sql, req.project_ref, self.token)` — on `UpstreamError as e`: finalize `"error"`/`"UPSTREAM_ERROR"`, respond; on any other `Exception`: finalize `"error"`/`"INTERNAL_ERROR"` (best-effort — wrap in its own `try/except` so a DB failure here still falls through to responding), respond `INTERNAL_ERROR`. (7) success: finalize `"ok"`/`None`, respond `encode_ok(rows)`. The WHOLE body of `handle_connection` is wrapped in one outer `try/except Exception` that, on anything unanticipated, writes `encode_error(INTERNAL_ERROR, "internal error")` and logs the exception — `handle_connection` must never raise out to `serve_forever`'s accept loop. `serve_forever`: bind+listen on `sock_path` (unlink first if a stale socket file exists), loop `conn, _ = sock.accept(); handle_connection(conn); conn.close()` (single-threaded is fine for v1 — contract's own protocol is "one request per connection, then close", not high-frequency).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/exec_broker/test_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/exec_broker/server.py tests/exec_broker/test_server.py
git commit -m "feat(exec-broker): BrokerServer end-to-end"
```

---

### Task 8: Daemon CLI entrypoint

**Files:**
- Create: `scripts/exec_broker_daemon.py`
- Test: `tests/test_exec_broker_daemon_config.py`

**Interfaces:**
- Consumes: `BrokerServer` from Task 7; `post_to_mgmt_api` from Task 5.
- Produces: `def load_allowed_uids(raw: str) -> frozenset[int]` (parses a comma-separated env value; raises `ValueError` on a non-digit entry or an empty string); `def main(argv=None) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
def test_load_allowed_uids_parses_csv():
    assert load_allowed_uids("1000,1001") == frozenset({1000, 1001})

def test_load_allowed_uids_rejects_empty():
    with pytest.raises(ValueError):
        load_allowed_uids("")

def test_load_allowed_uids_rejects_non_digit():
    with pytest.raises(ValueError):
        load_allowed_uids("1000,abc")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_exec_broker_daemon_config.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `scripts/exec_broker_daemon.py`**

`sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))` then import `exec_broker.server.BrokerServer`, `exec_broker.mgmt_api.post_to_mgmt_api`. `main()`: read `EXEC_BROKER_SOCKET`, `EXEC_BROKER_ALLOWED_UIDS`, `EXEC_BROKER_TOKEN_FILE`, `DATABASE_URL` from `os.environ` (via `dotenv.load_dotenv` first, matching `scripts/lib/fleet_health_lease.py`'s convention); any missing → print to stderr, `return 1`. Read the token file's content once (`Path(token_file).read_text().strip()`) — never log it. `db_conn_factory = lambda: psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row)`. Construct `BrokerServer(...)`, call `.serve_forever()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_exec_broker_daemon_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/exec_broker_daemon.py tests/test_exec_broker_daemon_config.py
git commit -m "feat(exec-broker): daemon CLI entrypoint"
```

---

### Task 9: `vault_access_log` migration (propose-only)

**Files:**
- Create: `migrations/drafts/hc95_vault_access_log.sql`
- Test: `tests/test_vault_access_log_isolation.py` (ephemeral-DSN gated, mirrors `tests/test_life_graph_isolation.py`'s pattern)

**Interfaces:**
- Produces: a SQL file — not applied by this plan. `tests/test_vault_access_log_isolation.py` is skipped by default (`VAULT_ACCESS_LOG_TEST_DSN` unset) and proves the ACL for real against a throwaway DB when a caller sets that env var.

- [ ] **Step 1: Write the migration file**

```sql
-- DRAFT -- hc#95 vault_access_log (token-behind-vault exec-broker, bus #56697 condition 2).
-- PROPOSE-ONLY: orch-console applies via direct psycopg after gating (never `supabase db push`).
-- NOT applied. Migration NNN assigned by CORE at apply.
--
-- Append-only from every vantage but the broker's own DB role: no DELETE grant to
-- anyone, ever; UPDATE is only how the broker finalizes its OWN just-inserted row
-- (started -> ok/error), never a general-purpose update path.
--
-- exec_broker_role needs its own LOGIN password, minted by orch-console at apply
-- time (ALTER ROLE exec_broker_role PASSWORD '...') -- never committed here.

CREATE TABLE IF NOT EXISTS vault_access_log (
  id            bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  exec_ledger_id text NOT NULL,
  caller        text NOT NULL,
  project_ref   text NOT NULL,
  sql_sha256    text NOT NULL,
  reason        text NOT NULL DEFAULT '',
  gate_id       text,
  status        text NOT NULL CHECK (status IN ('started', 'ok', 'error')),
  error_code    text,
  ts_start      timestamptz NOT NULL DEFAULT now(),
  ts_end        timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

DO $$ BEGIN
  CREATE ROLE exec_broker_role NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

REVOKE ALL ON vault_access_log FROM PUBLIC;
GRANT INSERT, UPDATE, SELECT ON vault_access_log TO exec_broker_role;
GRANT USAGE, SELECT ON SEQUENCE vault_access_log_id_seq TO exec_broker_role;
-- No DELETE grant to anyone, including exec_broker_role.
```

- [ ] **Step 2: Write the failing isolation test**

```python
"""Mirrors tests/test_life_graph_isolation.py's pattern (CAI-RESP-356: ephemeral only, never prod first)."""
DSN = os.environ.get("VAULT_ACCESS_LOG_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="VAULT_ACCESS_LOG_TEST_DSN not set (ephemeral dry-run only)")

def test_public_has_no_access(db):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.cursor() as cur:
            cur.execute("SET ROLE PUBLIC")  # or: open a second conn as a fresh unprivileged role and attempt SELECT

def test_exec_broker_role_can_insert_and_update_but_not_delete(db):
    # ALTER ROLE exec_broker_role LOGIN PASSWORD 'test-only' within the fixture, then
    # open a SECOND connection authenticated as exec_broker_role and assert:
    # INSERT succeeds, UPDATE (by id) succeeds, DELETE raises InsufficientPrivilege.
    ...
```

- [ ] **Step 3: Run test to verify it's skipped cleanly without a DSN, then run it for real against an ephemeral DB**

Run: `.venv/bin/pytest tests/test_vault_access_log_isolation.py -v` → `SKIPPED`
Run: `VAULT_ACCESS_LOG_TEST_DSN=postgresql://... .venv/bin/pytest tests/test_vault_access_log_isolation.py -v` (ephemeral/local Postgres only) → implement the fixture (apply the draft, create a second role-scoped connection) until this passes.

- [ ] **Step 4: Commit**

```bash
git add migrations/drafts/hc95_vault_access_log.sql tests/test_vault_access_log_isolation.py
git commit -m "feat(exec-broker): propose vault_access_log migration + isolation proof"
```

---

### Task 10: systemd unit draft

**Files:**
- Create: `deploy/irsyad-exec-broker.service`
- Test: `tests/test_exec_broker_service_unit.py`

**Interfaces:**
- Produces: a static unit file — not installed by this plan (orch-console's provisioning step, handoff pack §5). The test only asserts required directives are present (no live systemd involved).

- [ ] **Step 1: Write the failing test**

```python
UNIT = Path("deploy/irsyad-exec-broker.service").read_text()

@pytest.mark.parametrize("directive", [
    "User=irsyad-exec", "Group=irsyad-exec",
    "RuntimeDirectory=irsyad-exec-broker", "RuntimeDirectoryMode=0750",
    "ExecStart=", "Restart=",
])
def test_unit_file_has_required_directive(directive):
    assert directive in UNIT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_exec_broker_service_unit.py -v`
Expected: FAIL (file not found)

- [ ] **Step 3: Write `deploy/irsyad-exec-broker.service`**

Per handoff pack §5's table exactly: `User=irsyad-exec`, `Group=irsyad-exec`, `RuntimeDirectory=irsyad-exec-broker`, `RuntimeDirectoryMode=0750`, `ExecStart=/usr/bin/env python3 /path/to/scripts/exec_broker_daemon.py` (path placeholder — orch-console fills in the real deployed path at provisioning), `Restart=always`, `Environment=EXEC_BROKER_SOCKET=/run/irsyad-exec-broker/broker.sock` (inside the `RuntimeDirectory`, matching the agreed socket path). Include a comment block at the top noting the two open provisioning questions from the handoff pack (§5's "NEEDS CONFIRMING" row and the "open question for whoever provisions" paragraph) are orch-console's to resolve before install, not this PR's to answer.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_exec_broker_service_unit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add deploy/irsyad-exec-broker.service tests/test_exec_broker_service_unit.py
git commit -m "feat(exec-broker): draft systemd unit for provisioning review"
```

---

### Task 11: Full-suite run + PR

- [ ] **Step 1:** `.venv/bin/pytest tests/exec_broker/ tests/test_exec_broker_daemon_config.py tests/test_exec_broker_service_unit.py tests/test_vault_access_log_isolation.py -v` — all pass (isolation test SKIPPED is acceptable; no DSN in CI unless one is wired).
- [ ] **Step 2:** `.venv/bin/pytest` (whole repo) — confirm no regression in unrelated suites.
- [ ] **Step 3:** Push `lane/hc95-broker`, open PR to `fable/substrate-safe-fixes`, reviewers orch-console (gate) + cc-fleet-health. PR description names: milestone (a) only; the two open bus questions (#70867 refs, #70870 gate-contract gap) and their resolution or "still open" status; explicit confirmation that no migration was applied and no systemd unit was installed.
- [ ] **Step 4:** Post the PR link to orch-console via `bus_send.py` (type `review_request`, priority per the kickoff's own P1 lineage).

---

## Self-Review Notes

- **Spec coverage:** socket protocol (§1-3) → Task 1; re-validation (§4) → Tasks 2-3; error codes (§5) → Task 1 enum + every task's raises; logging (§6) → Task 4 + Task 7's call sequencing; timeouts (§7) → Task 7's `settimeout(30)` + Task 5's `--max-time 25`; "what stays unchanged on exec_prod side" (§8) is explicitly out of scope (owned by cc-irsyad-2). Provisioning spec (handoff §5) → Task 10. Rollout (a) → the whole plan; (b)-(e) explicitly excluded per Global Constraints.
- **Known gap carried forward, not silently resolved:** the migration/PR gate-mention check (bus #70870) — default behavior documented in Global Constraints and in Task 3's step 3, signature already accepts the extra params so the fix is additive.
- **Type consistency checked:** `BrokerValidationError.code` (Task 3) is the same `ErrorCode` enum Task 1 defines and Task 7 reads `.value` from; `VaultLogEntry` (Task 4) field names match what Task 7 constructs it with; `BrokerServer`'s constructor params match exactly what Task 8's `main()` passes.
