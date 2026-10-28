# hc#95 — exec broker socket contract, §2 amendment (Part A / rollout step (c))

**Status:** PROPOSED, built and tested against this spec on the broker side (orchestrator
PR TBD). Additive to the frozen contract (ihsanos-irsyad `reports/hc95-exec-broker-contract.md`,
squash `5053826e`) — that document is **not edited**; this is a separate, additive §2 amendment
for cc-fleet-health to hand to cc-irsyad-coord as the client-side spec to build `exec_prod.py`
against (console stays out of irsyad execution; coord routes the irsyad-side change).

Context: bus #79024 item 2 (rollout step (c): DDL behind the broker, due 2026-11-11), design
approved bus #79465 (replying to proposal bus #79464).

## §2 amendment: two new OPTIONAL request keys

```json
{
  "project_ref": "goumlynecruxrlmzlntp",
  "guarded_sql": "<unchanged>",
  "reason": "<unchanged>",
  "gate_id": "56485",
  "caller": "cc-irsyad-2",
  "exec_ledger_id": "<unchanged>",
  "migration": "481",
  "pr": "1072"
}
```

- `migration` and `pr` are **optional keys** — the key MAY be absent entirely from the request.
  This is a deliberate departure from `gate_id`'s required-but-nullable shape: `gate_id`'s
  absence-vs-null distinguishes "malformed caller" from "no gate needed" (a real ambiguity);
  `migration`/`pr` have no such ambiguity, so there is no reason to force every un-upgraded
  (pre-amendment) caller into a parse failure on every call, including plain DML, the moment
  the broker deploys this.
- If a key IS present: value must be `null` or a non-empty string, never `""` (same rule as
  `gate_id`). Absent is treated identically to an explicit `null`.
- **Deploy-safety property this amendment rests on:** an old 6-key caller (no `migration`/`pr`
  at all) gets **byte-identical behaviour** to today — no `MalformedRequest`, and DDL-shaped
  SQL is refused exactly as it is today (migration/pr absent → unconditional `GATE_INVALID`,
  unchanged). Pinned end-to-end by `test_old_six_key_dml_caller_unaffected_by_migration_pr_amendment_end_to_end`
  (`tests/exec_broker/test_server.py`). No synchronized cutover is required between the broker
  deploy and the exec_prod-side deploy.
- No new §5 error codes from this amendment. `GATE_INVALID` is reused for a migration/PR
  mismatch exactly as it already covers every other gate re-validation failure.

## §4 amendment: re-validation behaviour

When `migration` and/or `pr` are supplied on the wire, the broker's existing
`require_gate()` — which already carried optional `migration`/`pr` kwargs reserved for this
moment — performs the SAME independent digit-boundary match `exec_prod._require_gate` itself
uses (ported verbatim: `gate_validation._text_mentions_migration_or_pr`, confirmed
byte-identical in logic to `exec_prod.py`'s own function of the same name by direct
comparison), instead of refusing all DDL-shaped SQL unconditionally:

- Gate is shape-valid (right agent/type/past/silo) AND its text mentions the given
  `migration`/`pr` (digit-boundary, same anti-prefix-collision fix as cc-quality #56681) →
  **passes**.
- Gate is shape-valid but its text does NOT mention the given `migration`/`pr` → `GATE_INVALID`.
- Neither `migration` nor `pr` supplied (old-caller shape) → today's unconditional
  `GATE_INVALID` refusal, unchanged.

## Explicitly OUT of scope for this amendment — do not assume it is covered

**Lexically hazardous SQL** (backslash / double-quote-identifier hazard / identifier-`$`, per
`is_lexically_hazardous`) is **still refused unconditionally**, even when `migration`/`pr` are
supplied and match the gate text. A text-only match is not treated as sufficient authorization
for input the vendored lexer cannot reliably classify at all. orch-console ruled (bus #79468)
that hazardous SQL may pass ONLY behind an additional byte-binding check (the executed bytes'
sha12 matching a sha12 the gate cites) — that check is **not built**, pending resolution of a
real feasibility gap (bus #79471: the broker has no filesystem access to the migration file,
and `guarded_sql` is not byte-identical to the raw file once `agent_actor_guard.wrap()` has run
over it, so the citation convention itself needs a ruling before any code is written). This
amendment deliberately does not touch that path. Do not build `exec_prod.py`-side support for
sending a hazardous statement through the broker via `migration`/`pr` alone — it will still be
refused.

## Tests (broker side, all green)

- `tests/exec_broker/test_protocol.py`: absent/null/present/empty-string/non-string cases for
  both new keys (7 tests).
- `tests/exec_broker/test_gate_validation.py::test_require_gate_hazardous_still_refused_even_with_matching_migration_pr`:
  pins the explicitly-out-of-scope item above so a future byte-binding PR has to deliberately
  change this test, not drift into loosening it by accident.
- `tests/exec_broker/test_server.py`: end-to-end through a real socket — matching
  migration passes, mismatched migration refused, and the old 6-key DML caller is unaffected.
