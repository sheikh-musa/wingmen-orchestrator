# hc#95 — exec broker socket contract, amendments for Part A / rollout step (c)

**Status:** PROPOSED, built and tested against this spec on the broker side (orchestrator
PRs #575 = 1/2, this branch = 2/2). Additive to the frozen contract (ihsanos-irsyad
`reports/hc95-exec-broker-contract.md`, squash `5053826e`) — that document is **not edited**;
this is a separate, additive amendment for cc-fleet-health to hand to cc-irsyad-coord as the
client-side spec to build `exec_prod.py` against (console stays out of irsyad execution; coord
routes the irsyad-side change).

Context: bus #79024 item 2 (rollout step (c): DDL behind the broker, due 2026-11-11), design
approved bus #79465 (replying to proposal bus #79464); byte binding ruled bus #79477
(resolving bus #79471).

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

## §5 amendment (additive): `GATE_MISMATCH`

One new error code, additive exactly like `READ_ONLY_OVERRIDE`: response shape unchanged, and a
caller that does not know the code yet must still parse the response. It is used for **one**
axis only — the byte binding below — never in place of `GATE_INVALID` for every other gate
re-validation failure.

## §4 amendment (2/2): byte binding for lexically hazardous SQL

orch-console ruled this (bus #79477, resolving the feasibility question in bus #79471). It
applies **only** to broker-executed SQL that `is_lexically_hazardous` flags (backslash /
double-quote-identifier hazard / identifier-`$`) — the class the vendored lexer cannot reliably
classify, which is why a text match alone was never enough for it.

**The rule.** The gater cites `sha256(wrap(file))[:12]` — the hash of the **wrapped** payload,
i.e. exactly the bytes `exec_prod.py` sends as `guarded_sql` — in the gate row. The broker
refuses unless `sha256(guarded_sql)[:12]` (over the request's `guarded_sql` string encoded as
UTF-8) equals that cited sha12.

- `apply_migration.py` and its raw-file citation convention are **unchanged**. The raw-file
  sha12 keeps its existing meaning there; it is *not* what binds a broker call.
- The broker never calls `agent_actor_guard.wrap()` and never reads the migration file. In
  console's words: *"The broker proves the executed bytes equal what the gater hashed; the
  file identity is attested by the gater (via `--show-gate-sha` at the cited commit), not
  verified by the broker."*

**Citation format (the one convention both sides must agree on).** The gate row records the
commit, the raw-file sha12, and the wrapped sha12, with the wrapped one **labelled**:

```
commit 5053826e; raw-sha12: 0123456789ab
wrapped-sha12: 3f9c1e07be42
```

- The broker matches `wrapped[\s_-]*sha12\s*[:=]\s*<12 hex>` (case-insensitive). The label is
  required, so a bare 12-hex token — including the row's own raw-file sha12 — is **not** a
  citation and does not authorize anything.
- Exactly 12 hex chars; a full 64-char digest is not accepted as a citation.
- Two *different* labelled values in one gate row are treated as ambiguous and refused —
  never resolved by picking one.
- No labelled citation at all → refused. Absence is not "nothing to compare, allow it".

**Conditions on `exec_prod.py --show-gate-sha`** (the irsyad-side PR, routed to cc-irsyad-coord):

1. It hashes the file **as it exists at the cited commit** (`git show <sha>:<path>`), never a
   working-tree copy, and prints **both** sha12s (raw for cross-checking against the
   `apply_migration.py` convention, wrapped for the broker).
2. It computes `wrap()` by **importing `agent_actor_guard`**, never a copy of its logic. Its pin
   test must prove that the bytes it hashes equal the `guarded_sql` `exec_prod.py` actually
   sends for the same inner file, so a wrapper-template change fails a **test** before it can
   fail a live gate.

**Both conditions hold, always:** byte binding is *additional* to the migration/PR specificity
match described above, never a substitute for it. A hazardous statement whose gate cites the
right bytes but does not mention the migration/PR the caller claims is still `GATE_INVALID`.
Everything in the ruling's unchanged list still refuses: byte drift, fragments, concatenation,
expired/out-of-scope/unauthorized gates, and any hazardous statement with no gate.

## Tests (broker side, all green)

- `tests/exec_broker/test_protocol.py`: absent/null/present/empty-string/non-string cases for
  both new keys (7 tests), plus `GATE_MISMATCH` existing and encoding.
- `tests/exec_broker/test_gate_validation.py`: the four pin tests named in bus #79477
  (byte-identical passes; one-byte-different refused; name-match-but-different-sha refused;
  ungated refused), plus the fail-closed edges above (no citation, ambiguous citation,
  unlabelled hex token, byte binding not replacing specificity, and the non-hazardous branch
  not becoming reachable via a citation).
- `tests/exec_broker/test_server.py`: end-to-end through a real socket — a byte-bound hazardous
  statement passes, a one-byte-different one is refused as `GATE_MISMATCH` (and the audit row
  is finalized with that code), matching/mismatched `migration` behaves as in 1/2, and the old
  6-key DML caller is unaffected.

