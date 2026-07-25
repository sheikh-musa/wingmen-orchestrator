# Execution-Reliability Layer — MVP design (op#4711 LOOK + CAI-RESP-464 RATIFIED)

**Author:** orch-console (Nazim) per CAI-RESP-461 split (op#4556/op#4683). Build authored-unapplied → hub gate → Nazim live-verify. **Grants nothing** (execution_status stays NULL); runner-as-primary-path returns to cai with proof.

## Frame (CAI-RESP-461)
Separate DECIDING from EXECUTING. Once a decision is GRANTED (`execution_status='granted'` + challenge window closed + exact named artifact), it drains onto a durable work-item that a disposable, stateless RUNNER claims + executes. The runner is a PURE post-gate executor — borrows authority from the grant, acts under its own agent_id, fails closed, never decides. Watchdogs demote to safety net. Honors EXEC-1..5.

**Root-fix rationale:** the recurring stall class (hub idle left an authorized go UNREAD ~40min; the re-entry-miss; cai's wedged composer) are delivery/pickup failures, not decision failures. Route authorized work through a durable queue → those failures structurally disappear.

## 1. `exec_work_items` schema (substrate DB, guarded --expect-ref migration)
- `id` pk; `grant_ref` FK→strategic_decisions.decision_ref (authorizing grant — EXEC-1)
- `consumer_type` (MVP: one type); `named_artifact` jsonb (EXACT thing to run — EXEC-1)
- `idempotency_key` text UNIQUE (dedupe — EXEC-2)
- `state` enum: `pending → claimed → running → done | failed | skipped | bounced`
- `claimed_by` (agent_id), `claimed_at`, `lease_expires_at` (EXECUTE lease, scoped to grant.repos_affected — EXEC-4)
- `attempts`, `max_attempts`, `last_error`
- `pre_verify_result` jsonb, `post_proof` jsonb (EXEC-5, money/irreversible)
- `result` jsonb, `created_at`, `updated_at`
- RLS: runner may claim/update rows it owns; CANNOT flip `execution_status` or write `strategic_decisions` (EXEC-4)

## 2. Enqueue (decision → work-item)
When cai flips a decision to `granted` (+ challenge closed), enqueue an exec_work_item with grant_ref + named_artifact + idempotency_key. MVP: granting path writes the row (or a small poller watches newly-granted decisions). The grant is the ONLY thing that creates authorized work (EXEC-1).

## 3. Runner loop (disposable, stateless, fresh context per run)
1. CLAIM atomically: `UPDATE … SET state='claimed', claimed_by=me, lease_expires_at=now()+ttl WHERE state='pending' … RETURNING` via `SELECT FOR UPDATE SKIP LOCKED`. One item.
2. RE-CHECK grant still valid (`granted`, not revoked) each cycle. Revoked → skip/bounce.
3. FAIL-CLOSED (EXEC-3): if executing needs ANY decision the grant didn't settle → `bounced` → back to hub. Runner never improvises.
4. EXEC-5 (money/irreversible only): run the NAMED pre-verify gate; fail → bounce.
5. EXECUTE the exact named_artifact (idempotent — EXEC-2; key already done → no-op).
6. RECORD `done|failed` + result + (EXEC-5) post_proof. Report on bus (own agent_id, attributable).
7. Lease renew-in-heartbeat while running; on expiry (runner death) → item returns to `pending`, re-claimable (EXEC-4).

## 4. Demoted watchdogs (safety net, not primary)
Stall-watchdogs demote from primary-recovery to safety-net: (a) re-enqueue on runner death (lease expired + stuck past ttl → pending); (b) alert hub on stuck items (state unchanged > threshold or attempts=max) — no infinite auto-retry. Replaces the babysitting watchdogs (re-entry-nudge, IDLE_UNSENT-submit).

## 5. First consumer = dispatch/notification RELAY
Idempotent + verifiable (idempotency key + delivery confirmation, re-send deduped); low blast (worst case a duplicate); directly kills the composer-wedge. Second consumer (generalization) = guarded --expect-ref migration-apply (after relay proves the pattern).

## 6. Nazim runtime: keep headless-launchd (self-healing) for now; revisit only if the lean console still bloats.

## 7. Invariant traceability
EXEC-1 grant-gated · EXEC-2 idempotency_key exactly-once · EXEC-3 fail-closed bounce · EXEC-4 borrowed+scoped authority (RLS + repos-scoped lease) · EXEC-5 pre-verify + post-proof for money/irreversible.

## 8. cai hardening conditions (CAI-RESP-464 — BINDING before build-complete)
1. Relay sends = at-least-once-WITH-DEDUP, NOT exactly-once. Check delivery ledger before send; dedup by idempotency_key on retry. Crash-after-send-before-record: a rare DUPLICATE is the accepted failure mode; a SILENT DROP is not. Bias to re-deliver, never swallow.
2. BRIGHT LINE — delivery ≠ execution. The relay DELIVERS the notification that an authorized op exists; NEVER executes the underlying op.
3. Non-repo-work scoping (EXEC-4): for work not scoped to a repo, define the lease/authority scope explicitly (don't default to unscoped).
- EXEC-3 bounce must post to bus AND leave zero side-effects (assert in a NEGATIVE test); EXEC-5 binding when the migration-apply consumer lands.
