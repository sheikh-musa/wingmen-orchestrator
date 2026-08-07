# THE IHSAN GATE — standing pre-deploy doctrine

> Operator mandate (2026-07-10): "make the factory ihsan so the products we deploy are also ihsan." Anything deployed into the world must be rock-solid. This gate is the factory's quality floor — **not optional**.

**Scope:** every product/change that reaches a **client** or **prod** passes this gate first. The hub (cc-orchestrator) enforces it before greenlighting any lane's ship; a lane may not self-declare "show-ready" or "prod-ready" without it. Quality/tokens are worth spending here (see `ihsan-quality-bar`).

## The gate — ALL must hold before client/prod

1. **Code review — cc-reviewer.** Independent, adversarial review of the diff (logic, correctness, edge cases). For **money/PII/auth** paths this is mandatory + pairs with cai design ratification and the money-gate (DB-proof artifacts, `--expect-ref`, never `supabase db push` vs prod).
2. **UI/UX review — cc-uiux, EVERY page, mobile + desktop.** Capture (`scripts/spawn_uiux_review.sh` / `uiux_capture.mjs`) and actually *read* every page at 390 and 1440. Review **architecture/nav**, not just per-page render (the one-pager lesson). Iterate to genuinely excellent — 2-3 passes expected, no rushing. Terminal-blind ≠ ship-blind: screenshots are how we see.
3. **CI green — tests + lint + typecheck.** Unit + e2e pass; lint (incl. architecture/module-boundary lint) 0; typecheck 0. Red CI = not shipped.
4. **Security review for money/PII/auth.** Least-privilege verified; RLS/permission gates DB-enforced not UI-only; no secrets in the repo/remote; residency gate (TENANT-RESIDENCY-001) for any real client data — cai review before first client-data write.
5. **Ihsan polish bar met.** On-brand, content-complete, no glitches, responsive, fast. A half-right artifact in front of a client is worse than none.
6. **Reproducible + tracked.** Committed on a branch, migrations tracked (no out-of-band schema), the deploy source verified (`git ls-remote`/rev-parse == expected SHA — the stale-tracking-ref trap), work-outputs/proofs on the bus.

## Enforcement
- The hub confirms each applicable item before relaying anything as client-ready or applying to prod.
- Lanes report the gate evidence (review verdicts, screenshots, CI status, DB-proofs) — the hub does not take "verified" on faith (re-check the high-risk items).
- Not every item applies to every change (a docs tweak isn't a money-gate) — but the applicable subset is a hard floor, and money/PII/client-facing always pulls the full gate.

## Machine-readable projection (Head of Quality, Phase 1)

This doctrine (the bar as prose) has a versioned, machine-readable **projection**:
`docs/ihsan-gate-manifest.yaml`, read by the pure reader `nervous_system/ihsan_gate.py`.
The manifest maps each **change class** (docs/copy, UI/frontend, backend/action,
DB migration, money/payment, PII/gov-data, deploy-to-prod, deploy-to-client) →
the required **gate items** (the six items above, taxonomized as G1–G10), each
tagged `deterministic` (auto-checkable) or `judgment` (needs a reviewer).

**Phase 1 = CODIFY ONLY (per `reports/head-of-quality-spec.md` §8).** The manifest
is INERT DATA + a read-only reader. It enforces NOTHING — no merge/deploy/ship
path reads it, nothing blocks, no reviewer is auto-invoked. Enforcement (a
fail-closed gate) is Phase 2+, gated on cai.

**SYNC CONTRACT (don't drift):** this file stays the human doctrine; the manifest
is its data projection. When this doctrine's gate items or risk-scaling change,
update `docs/ihsan-gate-manifest.yaml` in the SAME commit and bump its `v`. Each
manifest gate item carries a `doctrine_ref` back to a numbered item here.

## Related doctrine
`ihsan-quality-bar` (UI every-page, iterate), `ihsan-factory-mandate` (the 4 pillars), CAI-RESP money-gate, TENANT-RESIDENCY-001, LAYER-VOCAB-001, `stale-tracking-ref-review-trap`, `docs/ihsan-gate-manifest.yaml` (this doctrine as data).
